from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_review_packet import (  # noqa: E402
    collect_shard_receipts,
    collect_unresolved_questions,
)
from reduction_contract import (  # noqa: E402
    MAX_RECORD_REASON_CHARS,
    project_reducer_input,
    reducer_output_shape_errors,
    reducer_projection_errors,
    reduction_closure,
)
from verification_contract import (  # noqa: E402
    SOURCE_RELATIONSHIPS,
    action_gate_errors,
)


def packet() -> dict:
    return {
        "observations": [
            {"observation_id": "o1"},
            {"observation_id": "o2"},
        ],
        "unresolved_questions": [
            {"unresolved_id": "u1"},
            {"unresolved_id": "u2"},
        ],
        "shard_receipts": [
            {
                "receipt_id": "s1:receipt",
                "status": "complete",
                "overflow_state": "false",
            },
            {
                "receipt_id": "s2:receipt",
                "status": "complete",
                "overflow_state": "unknown",
            },
        ],
    }


def record(
    observation_id: str,
    *,
    status: str = "verify",
    cluster_index: int | None = 0,
    role: str | None = "candidate",
    canonical: str | None = None,
) -> dict:
    return {
        "observation_id": observation_id,
        "accounting_status": status,
        "cluster_index": cluster_index,
        "role": role,
        "canonical_observation_id": canonical,
        "reason": "Question-relevant outcome",
        "falsifier": None,
    }


def valid_output() -> dict:
    return {
        "schema_version": 2,
        "records": [
            record("o1", role="canonical"),
            record("o2", role="counterevidence"),
        ],
        "clusters": [
            {
                "route": "source_verification",
                "required_verification_depth": "source_context",
                "requested_source_relationships": ["replacement_owner"],
                "reason": "Decision-driving conflict",
            }
        ],
        "unresolved_records": [
            {"unresolved_id": "u1", "disposition": "preserved", "reason": "open"},
            {"unresolved_id": "u2", "disposition": "preserved", "reason": "open"},
        ],
        "shard_records": [
            {
                "receipt_id": "s1:receipt",
                "disposition": "accounted",
                "reason": "covered",
            },
            {
                "receipt_id": "s2:receipt",
                "disposition": "blocked",
                "reason": "legacy overflow state remains unknown",
            },
        ],
    }


class ReductionClosureTests(unittest.TestCase):
    def assert_red(
        self, output: dict, expected: str, packet_value: dict | None = None
    ) -> None:
        errors, queue = reduction_closure(packet_value or packet(), output)
        self.assertTrue(errors)
        self.assertTrue(any(expected in error for error in errors), errors)
        self.assertEqual([], queue)

    def test_green_closure_derives_queue_from_cluster_members(self) -> None:
        errors, queue = reduction_closure(packet(), valid_output())

        self.assertEqual([], errors)
        self.assertEqual(1, len(queue))
        self.assertRegex(queue[0]["cluster_id"], r"^cluster-[0-9a-f]{64}$")
        self.assertEqual(
            ["o1", "o2"],
            [item["observation_id"] for item in queue[0]["members"]],
        )

    def test_green_summary_only_merge_ends_at_non_verify_canonical(self) -> None:
        output = valid_output()
        output["clusters"] = [
            {
                "route": "summary_only",
                "required_verification_depth": None,
                "requested_source_relationships": [],
                "reason": "Related facts are useful only for the summary.",
            }
        ]
        output["records"] = [
            record(
                "o1",
                status="merged",
                cluster_index=0,
                role="supporting",
                canonical="o2",
            ),
            record(
                "o2",
                status="non_decision_driving",
                cluster_index=0,
                role="canonical",
            ),
        ]

        errors, queue = reduction_closure(packet(), output)

        self.assertEqual([], errors)
        self.assertEqual([], queue)

    def test_cluster_routes_reject_incompatible_accounting_statuses(self) -> None:
        non_verify_source = valid_output()
        non_verify_source["records"][1]["accounting_status"] = (
            "non_decision_driving"
        )
        self.assert_red(non_verify_source, "source_verification cluster")

        verify_summary = valid_output()
        verify_summary["clusters"][0] = {
            "route": "summary_only",
            "required_verification_depth": None,
            "requested_source_relationships": [],
            "reason": "Summary grouping",
        }
        self.assert_red(verify_summary, "summary_only cluster")

    def test_all_id_universes_reject_missing_duplicate_and_foreign(self) -> None:
        cases = []
        for field, id_field, duplicate, foreign in (
            ("records", "observation_id", "o1", "o3"),
            ("unresolved_records", "unresolved_id", "u1", "u3"),
            ("shard_records", "receipt_id", "s1:receipt", "s3:receipt"),
        ):
            duplicate_output = valid_output()
            duplicate_output[field][1][id_field] = duplicate
            cases.append((f"{field} duplicate", duplicate_output, "missing ids"))
            foreign_output = valid_output()
            foreign_output[field][1][id_field] = foreign
            cases.append((f"{field} foreign", foreign_output, "unexpected ids"))
        for name, output, expected in cases:
            with self.subTest(name=name):
                self.assert_red(output, expected)

    def test_cluster_reference_role_and_membership_errors_are_red(self) -> None:
        cases = {}
        out_of_range = valid_output()
        out_of_range["records"][1]["cluster_index"] = 1
        cases["out of range"] = (out_of_range, "out of range")
        clustered_without_role = valid_output()
        clustered_without_role["records"][1]["role"] = None
        cases["cluster without role"] = (
            clustered_without_role,
            "cluster without role",
        )
        unclustered_with_role = valid_output()
        unclustered_with_role["records"][1]["cluster_index"] = None
        cases["role without cluster"] = (
            unclustered_with_role,
            "role without cluster",
        )
        empty_cluster = valid_output()
        empty_cluster["clusters"].append(
            {
                "route": "summary_only",
                "required_verification_depth": None,
                "requested_source_relationships": [],
                "reason": "Unused cluster",
            }
        )
        cases["empty cluster"] = (empty_cluster, "has no members")
        duplicate_membership = valid_output()
        duplicate_membership["records"][1]["observation_id"] = "o1"
        cases["duplicate membership"] = (
            duplicate_membership,
            "duplicate memberships",
        )
        for name, (output, expected) in cases.items():
            with self.subTest(name=name):
                self.assert_red(output, expected)

    def test_route_depth_and_relationship_invariants_are_red(self) -> None:
        cases = {}
        source_without_depth = valid_output()
        source_without_depth["clusters"][0]["required_verification_depth"] = None
        cases["source depth"] = (source_without_depth, "lacks depth")
        source_without_relationship = valid_output()
        source_without_relationship["clusters"][0][
            "requested_source_relationships"
        ] = []
        cases["source relationships"] = (
            source_without_relationship,
            "lacks relationships",
        )
        summary_with_depth = valid_output()
        summary_with_depth["clusters"][0]["route"] = "summary_only"
        cases["summary depth"] = (summary_with_depth, "must not have depth")
        summary_with_relationship = copy.deepcopy(summary_with_depth)
        summary_with_relationship["clusters"][0][
            "required_verification_depth"
        ] = None
        cases["summary relationships"] = (
            summary_with_relationship,
            "must not request relationships",
        )
        duplicate_relationship = valid_output()
        duplicate_relationship["clusters"][0][
            "requested_source_relationships"
        ] *= 2
        cases["duplicate relationship"] = (
            duplicate_relationship,
            "duplicate source relationships",
        )
        invalid_route = valid_output()
        invalid_route["clusters"][0]["route"] = "invented"
        cases["invalid route"] = (invalid_route, "route is invalid")
        for name, (output, expected) in cases.items():
            with self.subTest(name=name):
                self.assert_red(output, expected)

    def test_merge_absent_self_and_cycle_are_red(self) -> None:
        cases = {}
        absent = valid_output()
        absent["records"] = [
            record(
                "o1",
                status="merged",
                cluster_index=0,
                role=None,
                canonical="missing",
            ),
            record(
                "o2",
                status="verify",
                cluster_index=0,
                role="canonical",
            ),
        ]
        cases["absent"] = absent
        self_merge = copy.deepcopy(absent)
        self_merge["records"][0]["canonical_observation_id"] = "o1"
        cases["self"] = self_merge
        cycle = copy.deepcopy(absent)
        cycle["records"][0]["canonical_observation_id"] = "o2"
        cycle["records"][1] = record(
            "o2",
            status="merged",
            cluster_index=0,
            role="supporting",
            canonical="o1",
        )
        cases["cycle"] = cycle

        for name, output in cases.items():
            with self.subTest(name=name):
                errors, queue = reduction_closure(packet(), output)
                self.assertTrue(errors, name)
                self.assertEqual([], queue)

    def test_merge_must_end_at_same_cluster_canonical_record(self) -> None:
        output = valid_output()
        output["records"] = [
            record(
                "o1",
                status="merged",
                cluster_index=0,
                role="supporting",
                canonical="o2",
            ),
            record("o2", role="supporting"),
        ]

        self.assert_red(output, "terminates at non-canonical role")

    def test_merge_cross_cluster_counterevidence_and_minority_are_red(self) -> None:
        cross_cluster = valid_output()
        cross_cluster["clusters"].append(copy.deepcopy(cross_cluster["clusters"][0]))
        cross_cluster["records"] = [
            record("o1", status="merged", role="supporting", canonical="o2"),
            record("o2", cluster_index=1, role="canonical"),
        ]
        counterevidence = valid_output()
        counterevidence["records"] = [
            record(
                "o1", status="merged", role="counterevidence", canonical="o2"
            ),
            record("o2", role="canonical"),
        ]
        minority = valid_output()
        minority["records"] = [
            record("o1", status="merged", role="minority", canonical="o2"),
            record("o2", role="canonical"),
        ]
        for name, output, expected in (
            ("cross cluster", cross_cluster, "differ in cluster"),
            ("counterevidence", counterevidence, "non-mergeable role"),
            ("minority", minority, "non-mergeable role"),
        ):
            with self.subTest(name=name):
                self.assert_red(output, expected)

    def test_inconclusive_requires_falsifier_and_verification_requires_relationship(self) -> None:
        inconclusive = valid_output()
        inconclusive["records"][1] = record(
            "o2", status="inconclusive", cluster_index=None, role=None
        )
        self.assert_red(inconclusive, "requires a falsifier")

        no_relationship = valid_output()
        no_relationship["clusters"][0]["requested_source_relationships"] = []
        self.assert_red(no_relationship, "lacks relationships")

    def test_dropped_duplicate_text_unresolved_or_empty_shard_receipt_is_red(self) -> None:
        missing_unresolved = valid_output()
        missing_unresolved["unresolved_records"].pop()
        self.assert_red(missing_unresolved, "unresolved_records count")

        missing_empty_shard = valid_output()
        missing_empty_shard["shard_records"].pop()
        self.assert_red(missing_empty_shard, "shard_records count")

    def test_output_bounds_reject_overlong_reason(self) -> None:
        output = valid_output()
        output["records"][0]["reason"] = "x" * (MAX_RECORD_REASON_CHARS + 1)
        errors = reducer_output_shape_errors(output, packet())
        self.assertTrue(any("exceeds" in error for error in errors), errors)

    def test_packet_schema_avoids_unsupported_unique_items_keyword(self) -> None:
        from accounting_wire import accounting_wire_schema
        schema = accounting_wire_schema()
        relationships = schema["properties"]["clusters"]["items"][
            "properties"
        ]["requested_source_relationships"]

        self.assertNotIn("uniqueItems", relationships)

    def test_v2_rejects_removed_echoes_and_model_authored_cluster_members(self) -> None:
        removed_record_field = valid_output()
        removed_record_field["records"][0]["finding_kind"] = "contract"
        cluster_echoes = valid_output()
        cluster_echoes["clusters"][0]["cluster_id"] = "model-id"
        cluster_echoes["clusters"][0]["members"] = []
        receipt_echoes = valid_output()
        receipt_echoes["shard_records"][0]["status"] = "complete"
        receipt_echoes["shard_records"][0]["overflow_state"] = "false"
        for name, output in (
            ("record", removed_record_field),
            ("cluster", cluster_echoes),
            ("receipt", receipt_echoes),
        ):
            with self.subTest(name=name):
                errors = reducer_output_shape_errors(output, packet())
                self.assertTrue(
                    any("fields mismatch" in error for error in errors), errors
                )

    def test_v2_output_shape_materially_removes_repeated_facts(self) -> None:
        current = valid_output()
        legacy = copy.deepcopy(current)
        legacy["schema_version"] = 1
        for record_value in legacy["records"]:
            record_value["cluster_id"] = "C1"
            record_value.pop("cluster_index")
            record_value["required_verification_depth"] = "source_context"
            record_value["finding_kind"] = "contract"
            record_value["decision_direction"] = "retain"
        legacy["clusters"][0]["cluster_id"] = "C1"
        legacy["clusters"][0]["members"] = [
            {"observation_id": "o1", "role": "canonical"},
            {"observation_id": "o2", "role": "counterevidence"},
        ]
        for index, receipt in enumerate(legacy["shard_records"]):
            receipt["status"] = "complete"
            receipt["overflow_state"] = "false" if index == 0 else "unknown"
        compact = lambda value: json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

        self.assertLess(len(compact(current)), len(compact(legacy)) * 0.85)


def projection_packet() -> dict:
    return {
        "question": "矛盾を一件も落とさず判断できるか？",
        "question_sha256": "q" * 64,
        "root": "/private/audit/root",
        "plan_path": "/private/scan/plan.json",
        "validation_path": "/private/scan/validation.json",
        "validated_observation_count": 2,
        "observations": [
            {
                "observation_id": "o:多数",
                "category": "conflict",
                "title": "Majority claim",
                "claim": "Feature is safe",
                "why_it_matters": "Would permit deletion",
                "scout_confidence": "high",
                "evidence": [
                    {
                        "path": "資料/同じ名前.txt",
                        "absolute_path": "/private/audit/root/資料/同じ名前.txt",
                        "file_sha256": "a" * 64,
                        "locator": "L1-L2",
                        "quote": "多数派 says \"safe\" ✅",
                    }
                ],
            },
            {
                "observation_id": "o:少数",
                "category": "conflict",
                "title": "Minority counterexample",
                "claim": "Feature loses data",
                "why_it_matters": "Minority evidence blocks deletion",
                "scout_confidence": "low",
                "evidence": [
                    {
                        "path": "別/同じ名前.txt",
                        "absolute_path": "/private/audit/root/別/同じ名前.txt",
                        "file_sha256": "b" * 64,
                        "locator": "§ unicode",
                        "quote": "少数派: line\\nwith JSON marker } ]",
                    },
                    {
                        "path": "資料/同じ名前.txt",
                        "absolute_path": "/private/audit/root/資料/同じ名前.txt",
                        "file_sha256": "a" * 64,
                        "locator": "L9",
                        "quote": "conflicting quote",
                    },
                ],
            },
        ],
        "unresolved_questions": [
            {"unresolved_id": "u1", "text": "same unresolved text"},
            {"unresolved_id": "u2", "text": "same unresolved text"},
        ],
        "shard_receipts": [
            {
                "receipt_id": "empty:receipt",
                "status": "complete",
                "overflow_state": "unknown",
                "observation_ids": [],
                "unresolved_ids": [],
                "result_sha256": "c" * 64,
            }
        ],
    }


class ReducerProjectionTests(unittest.TestCase):
    def test_projection_exactly_preserves_every_semantic_leaf_once(self) -> None:
        source = projection_packet()
        projection_bytes = project_reducer_input(source)
        projection = json.loads(projection_bytes)

        self.assertEqual([], reducer_projection_errors(source, projection_bytes))
        self.assertEqual(source["question"], projection["q"])
        self.assertEqual(sorted(set(projection["c"])), projection["c"])
        self.assertEqual(sorted(set(projection["f"])), projection["f"])
        self.assertEqual(len(source["observations"]), len(projection["o"]))
        for original, row in zip(source["observations"], projection["o"]):
            self.assertEqual(
                [
                    original["observation_id"],
                    original["category"],
                    original["scout_confidence"],
                    original["title"],
                    original["claim"],
                    original["why_it_matters"],
                ],
                [
                    row[0],
                    projection["c"][row[1]],
                    ("low", "medium", "high")[row[2]],
                    row[3],
                    row[4],
                    row[5],
                ],
            )
            self.assertEqual(
                [
                    (evidence["path"], evidence["locator"], evidence["quote"])
                    for evidence in original["evidence"]
                ],
                [
                    (projection["f"][evidence[0]], evidence[1], evidence[2])
                    for evidence in row[6]
                ],
            )
        self.assertEqual(
            [[item["unresolved_id"], item["text"]] for item in source["unresolved_questions"]],
            projection["u"],
        )
        self.assertEqual(
            [["empty:receipt", "complete", "unknown"]], projection["r"]
        )
        rendered = projection_bytes.decode("utf-8")
        for dropped in (
            "/private/audit/root",
            "/private/scan/plan.json",
            "file_sha256",
            "result_sha256",
            "observation_ids",
        ):
            self.assertNotIn(dropped, rendered)

    def test_duplicate_unresolved_ids_are_not_silently_deduplicated(self) -> None:
        source = projection_packet()
        source["unresolved_questions"][1]["unresolved_id"] = "u1"

        projection = json.loads(project_reducer_input(source))

        self.assertEqual(["u1", "u1"], [row[0] for row in projection["u"]])

    def test_confidence_routing_hint_and_tampered_or_stale_bytes_are_red(self) -> None:
        source = projection_packet()
        confidence_changed = copy.deepcopy(source)
        confidence_changed["observations"][1]["scout_confidence"] = "high"
        self.assertNotEqual(
            project_reducer_input(source), project_reducer_input(confidence_changed)
        )

        projection = json.loads(project_reducer_input(source))
        self.assertEqual(0, projection["o"][1][2])
        projection["o"][1][6][0][0] = 1
        tampered = json.dumps(
            projection, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.assertTrue(reducer_projection_errors(source, tampered))

        pretty_same_value = json.dumps(
            json.loads(project_reducer_input(source)), ensure_ascii=False, indent=2
        ).encode("utf-8")
        self.assertTrue(reducer_projection_errors(source, pretty_same_value))

        stale = copy.deepcopy(source)
        stale["observations"][1]["evidence"][0]["quote"] = "source changed"
        self.assertTrue(
            reducer_projection_errors(stale, project_reducer_input(source))
        )


class PacketCoverageTests(unittest.TestCase):
    def test_duplicate_text_empty_receipt_and_legacy_unknown_are_preserved(self) -> None:
        results = [
            {
                "schema_version": 6,
                "shard_id": "shard-001",
                "status": "complete",
                "analysis": {
                    "observations": [],
                    "unresolved_questions": ["same", "same"],
                    "overflowed": False,
                },
            },
            {
                "schema_version": 5,
                "shard_id": "shard-002",
                "status": "complete",
                "analysis": {
                    "observations": [],
                    "unresolved_questions": [],
                },
            },
        ]
        artifacts = {
            "results/shard-001.json": "1" * 64,
            "results/shard-002.json": "2" * 64,
        }

        unresolved = collect_unresolved_questions(results)
        receipts = collect_shard_receipts(results, artifacts)

        self.assertEqual(
            ["shard-001:unresolved:1", "shard-001:unresolved:2"],
            [item["unresolved_id"] for item in unresolved],
        )
        self.assertEqual(["same", "same"], [item["text"] for item in unresolved])
        self.assertEqual([], receipts[1]["observation_ids"])
        self.assertEqual([], receipts[1]["unresolved_ids"])
        self.assertEqual("unknown", receipts[1]["overflow_state"])
        self.assertEqual("false", receipts[0]["overflow_state"])


def action_record() -> dict:
    return {
        "verification_id": "V1",
        "cluster_id": "C1",
        "verification_queue_sha256": "1" * 64,
        "verification_bundle_sha256": "2" * 64,
        "source_snapshot_id": "snapshot-1",
        "disposition": "confirmed",
        "required_depth": "behavioral",
        "actual_depth": "behavioral",
        "checked_dimensions": {name: "checked" for name in SOURCE_RELATIONSHIPS},
        "verified_claim": "The replacement catches the lost scenario.",
        "authority": "Production and test owners",
        "consequence": "Deletion removes a redundant oracle.",
        "current_evidence": [{"path": "test.py", "locator": "L1"}],
        "falsifier": "The negative case stays green after breaking behavior.",
        "action": "delete",
        "lost_scenario": "Nondeterministic persisted bytes",
        "replacement_owner": "tests/persisted-oracle.test.ts",
        "negative_case": "Inject random bytes and observe red",
        "persisted_boundary": True,
        "security_boundary": False,
    }


class ActionGateTests(unittest.TestCase):
    def test_complete_delete_record_is_green(self) -> None:
        self.assertEqual([], action_gate_errors([action_record()]))

    def test_delete_missing_owner_negative_case_or_dimensions_is_red(self) -> None:
        cases = {}
        missing_owner = action_record()
        missing_owner["replacement_owner"] = ""
        cases["owner"] = missing_owner
        missing_negative = action_record()
        missing_negative["negative_case"] = ""
        cases["negative"] = missing_negative
        missing_dimension = action_record()
        missing_dimension["checked_dimensions"]["replacement_owner"] = "missing"
        cases["dimension"] = missing_dimension
        persisted_gap = action_record()
        persisted_gap["checked_dimensions"]["history"] = "not_required"
        cases["persisted"] = persisted_gap
        inconclusive = action_record()
        inconclusive["disposition"] = "inconclusive"
        cases["disposition"] = inconclusive

        for name, record_value in cases.items():
            with self.subTest(name=name):
                self.assertTrue(action_gate_errors([record_value]))

    def test_source_verified_boolean_is_not_accepted(self) -> None:
        value = action_record()
        value["source_verified"] = True
        errors = action_gate_errors([value])
        self.assertTrue(any("source_verified" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
