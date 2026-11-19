"""Reducer semantic input projection, output shape, closure, and public clusters."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable

from scan_artifacts import sha256_text
from scan_contract import CONFIDENCES
from verification_contract import SOURCE_RELATIONSHIPS, VERIFICATION_DEPTHS

REDUCER_INPUT_PROJECTION_SCHEMA_VERSION = 1
REDUCER_NORMAL_FORM_SCHEMA_VERSION = 2

ACCOUNTING_STATUSES = (
    "verify",
    "merged",
    "non_decision_driving",
    "inconclusive",
    "stale",
    "blocked",
)
CLUSTER_ROLES = (
    "candidate",
    "canonical",
    "supporting",
    "counterevidence",
    "minority",
)
CLUSTER_ROUTES = ("source_verification", "summary_only")

MAX_REDUCTION_ITEMS = 50_000
MAX_RECORD_REASON_CHARS = 192
MAX_CLUSTER_REASON_CHARS = 512
MAX_UNRESOLVED_REASON_CHARS = 192
MAX_RECEIPT_REASON_CHARS = 192
MAX_FALSIFIER_CHARS = 256


def canonical_sha256(value: Any) -> str:
    return sha256_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )


def reducer_input_value(packet: dict[str, Any]) -> dict[str, Any]:
    observations = packet["observations"]
    categories = sorted({item["category"] for item in observations})
    relative_paths = sorted(
        {
            evidence["path"]
            for item in observations
            for evidence in item["evidence"]
        }
    )
    category_refs = {value: index for index, value in enumerate(categories)}
    path_refs = {value: index for index, value in enumerate(relative_paths)}
    return {
        "v": REDUCER_INPUT_PROJECTION_SCHEMA_VERSION,
        "q": packet["question"],
        "c": categories,
        "f": relative_paths,
        "o": [
            [
                item["observation_id"],
                category_refs[item["category"]],
                CONFIDENCES.index(item["scout_confidence"]),
                item["title"],
                item["claim"],
                item["why_it_matters"],
                [
                    [
                        path_refs[evidence["path"]],
                        evidence["locator"],
                        evidence["quote"],
                    ]
                    for evidence in item["evidence"]
                ],
            ]
            for item in observations
        ],
        "u": [
            [item["unresolved_id"], item["text"]]
            for item in packet["unresolved_questions"]
        ],
        "r": [
            [item["receipt_id"], item["status"], item["overflow_state"]]
            for item in packet["shard_receipts"]
        ],
    }


def project_reducer_input(packet: dict[str, Any]) -> bytes:
    """Return the exact compact UTF-8 bytes used by every reducer stage."""
    return json.dumps(
        reducer_input_value(packet), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def reducer_projection_errors(
    packet: dict[str, Any], projection_bytes: bytes
) -> list[str]:
    """Prove canonical bytes and exact correspondence to the authoritative packet."""
    try:
        actual = json.loads(projection_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"reducer input projection is not canonical UTF-8 JSON: {exc}"]
    try:
        expected_bytes = project_reducer_input(packet)
        expected = json.loads(expected_bytes.decode("utf-8"))
    except (KeyError, TypeError, ValueError) as exc:
        return [f"authoritative packet cannot produce a projection: {exc}"]
    errors: list[str] = []
    if actual != expected:
        errors.append("reducer input projection does not exactly match the packet")
    if projection_bytes != expected_bytes:
        errors.append("reducer input projection bytes are not the canonical bound bytes")
    return errors


def _packet_output_limits(packet: dict[str, Any] | None) -> tuple[int, int, int, int]:
    if packet is None:
        return (
            MAX_REDUCTION_ITEMS,
            MAX_REDUCTION_ITEMS,
            MAX_REDUCTION_ITEMS,
            512,
        )
    observation_ids = [item["observation_id"] for item in packet["observations"]]
    unresolved_ids = [
        item["unresolved_id"] for item in packet["unresolved_questions"]
    ]
    receipt_ids = [item["receipt_id"] for item in packet["shard_receipts"]]
    identifier_max = max(
        [len(value) for value in observation_ids + unresolved_ids + receipt_ids],
        default=1,
    )
    return (
        len(observation_ids),
        len(unresolved_ids),
        len(receipt_ids),
        identifier_max,
    )


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _exact_keys(value: Any, keys: Iterable[str], label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} is not an object"]
    expected = set(keys)
    actual = set(value)
    if actual == expected:
        return []
    return [
        f"{label} fields mismatch: missing={sorted(expected - actual)}, "
        f"unexpected={sorted(actual - expected)}"
    ]


def reducer_output_shape_errors(
    value: Any, packet: dict[str, Any] | None = None
) -> list[str]:
    """Validate internal v2 normal form, not the model transport wire."""
    top = (
        "schema_version",
        "records",
        "clusters",
        "unresolved_records",
        "shard_records",
    )
    errors = _exact_keys(value, top, "output")
    if not isinstance(value, dict):
        return errors
    if value.get("schema_version") != REDUCER_NORMAL_FORM_SCHEMA_VERSION:
        errors.append("output schema_version mismatch")
    for field in top[1:]:
        if not isinstance(value.get(field), list):
            errors.append(f"output {field} is not an array")
    if errors:
        return errors

    observation_limit, unresolved_limit, receipt_limit, identifier_max = (
        _packet_output_limits(packet)
    )
    expected_lengths = {
        "records": observation_limit,
        "unresolved_records": unresolved_limit,
        "shard_records": receipt_limit,
    }
    for field, limit in expected_lengths.items():
        actual = len(value[field])
        if packet is not None and actual != limit:
            errors.append(f"output {field} count {actual} does not equal {limit}")
        elif actual > limit:
            errors.append(f"output {field} count {actual} exceeds {limit}")
    if len(value["clusters"]) > observation_limit:
        errors.append(
            f"output clusters count {len(value['clusters'])} exceeds {observation_limit}"
        )

    def check_string(
        item: dict[str, Any],
        field: str,
        label: str,
        maximum: int,
        *,
        nullable: bool = False,
    ) -> None:
        field_value = item.get(field)
        if nullable and field_value is None:
            return
        if not _is_nonempty_string(field_value):
            errors.append(f"{label} {field} is empty or invalid")
        elif len(field_value) > maximum:
            errors.append(f"{label} {field} exceeds {maximum} characters")

    record_keys = (
        "observation_id",
        "accounting_status",
        "cluster_index",
        "role",
        "canonical_observation_id",
        "reason",
        "falsifier",
    )
    for index, record in enumerate(value["records"]):
        label = f"record[{index}]"
        errors.extend(_exact_keys(record, record_keys, label))
        if not isinstance(record, dict):
            continue
        check_string(record, "observation_id", label, identifier_max)
        if record.get("accounting_status") not in ACCOUNTING_STATUSES:
            errors.append(f"{label} accounting_status is invalid")
        cluster_index = record.get("cluster_index")
        if cluster_index is not None and (
            type(cluster_index) is not int or cluster_index < 0
        ):
            errors.append(f"{label} cluster_index is invalid")
        elif isinstance(cluster_index, int) and cluster_index >= len(value["clusters"]):
            errors.append(f"{label} cluster_index is out of range")
        if record.get("role") not in (*CLUSTER_ROLES, None):
            errors.append(f"{label} role is invalid")
        check_string(
            record,
            "canonical_observation_id",
            label,
            identifier_max,
            nullable=True,
        )
        check_string(record, "reason", label, MAX_RECORD_REASON_CHARS)
        check_string(
            record,
            "falsifier",
            label,
            MAX_FALSIFIER_CHARS,
            nullable=True,
        )

    cluster_keys = (
        "route",
        "required_verification_depth",
        "requested_source_relationships",
        "reason",
    )
    for index, cluster in enumerate(value["clusters"]):
        label = f"cluster[{index}]"
        errors.extend(_exact_keys(cluster, cluster_keys, label))
        if not isinstance(cluster, dict):
            continue
        if cluster.get("route") not in CLUSTER_ROUTES:
            errors.append(f"{label} route is invalid")
        if cluster.get("required_verification_depth") not in (
            *VERIFICATION_DEPTHS,
            None,
        ):
            errors.append(f"{label} required_verification_depth is invalid")
        check_string(cluster, "reason", label, MAX_CLUSTER_REASON_CHARS)
        relationships = cluster.get("requested_source_relationships")
        if not isinstance(relationships, list) or not all(
            item in SOURCE_RELATIONSHIPS for item in relationships
        ):
            errors.append(f"{label} requested_source_relationships is invalid")
        elif len(relationships) > len(SOURCE_RELATIONSHIPS):
            errors.append(f"{label} has too many source relationships")
        elif len(relationships) != len(set(relationships)):
            errors.append(f"{label} has duplicate source relationships")

    unresolved_keys = ("unresolved_id", "disposition", "reason")
    for index, record in enumerate(value["unresolved_records"]):
        label = f"unresolved_record[{index}]"
        errors.extend(_exact_keys(record, unresolved_keys, label))
        if not isinstance(record, dict):
            continue
        check_string(record, "unresolved_id", label, identifier_max)
        if record.get("disposition") not in ("preserved", "resolved", "blocked"):
            errors.append(f"{label} disposition is invalid")
        check_string(record, "reason", label, MAX_UNRESOLVED_REASON_CHARS)

    shard_keys = ("receipt_id", "disposition", "reason")
    for index, record in enumerate(value["shard_records"]):
        label = f"shard_record[{index}]"
        errors.extend(_exact_keys(record, shard_keys, label))
        if not isinstance(record, dict):
            continue
        check_string(record, "receipt_id", label, identifier_max)
        if record.get("disposition") not in ("accounted", "blocked"):
            errors.append(f"{label} disposition is invalid")
        check_string(record, "reason", label, MAX_RECEIPT_REASON_CHARS)
    return errors


def _multiset_errors(expected: list[Any], actual: list[Any], label: str) -> list[str]:
    expected_counts = Counter(expected)
    actual_counts = Counter(actual)
    errors: list[str] = []
    duplicate_expected = sorted(
        str(key) for key, count in expected_counts.items() if count != 1
    )
    duplicate_actual = sorted(
        str(key) for key, count in actual_counts.items() if count != 1
    )
    if duplicate_expected:
        errors.append(f"{label} expected ids are not unique: {duplicate_expected}")
    if duplicate_actual:
        errors.append(f"{label} actual ids are not unique: {duplicate_actual}")
    missing = sorted(str(key) for key in (expected_counts - actual_counts).elements())
    unexpected = sorted(str(key) for key in (actual_counts - expected_counts).elements())
    if missing:
        errors.append(f"{label} missing ids: {missing}")
    if unexpected:
        errors.append(f"{label} unexpected ids: {unexpected}")
    return errors


def derive_public_clusters(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive stable safe IDs and the sole public member lists from v2 records."""
    members_by_index: list[list[dict[str, str]]] = [
        [] for _ in output["clusters"]
    ]
    for record in output["records"]:
        cluster_index = record["cluster_index"]
        if cluster_index is None:
            continue
        members_by_index[cluster_index].append(
            {"observation_id": record["observation_id"], "role": record["role"]}
        )
    public: list[dict[str, Any]] = []
    for index, cluster in enumerate(output["clusters"]):
        members = sorted(
            members_by_index[index],
            key=lambda item: (item["observation_id"], item["role"]),
        )
        cluster_id = f"cluster-{canonical_sha256(members)}"
        public.append(
            {
                "cluster_id": cluster_id,
                "route": cluster["route"],
                "members": members,
                "required_verification_depth": cluster[
                    "required_verification_depth"
                ],
                "requested_source_relationships": sorted(
                    cluster["requested_source_relationships"]
                ),
                "reason": cluster["reason"],
            }
        )
    return public


def reduction_closure(
    packet: dict[str, Any], output: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Close every packet universe and derive the only verification queue."""
    errors = reducer_output_shape_errors(output, packet)
    if errors:
        return errors, []
    expected_observations = [
        item.get("observation_id") for item in packet.get("observations", [])
    ]
    records = output["records"]
    actual_observations = [item["observation_id"] for item in records]
    errors.extend(
        _multiset_errors(expected_observations, actual_observations, "observations")
    )

    expected_unresolved = [
        item.get("unresolved_id") for item in packet.get("unresolved_questions", [])
    ]
    actual_unresolved = [
        item["unresolved_id"] for item in output["unresolved_records"]
    ]
    errors.extend(
        _multiset_errors(expected_unresolved, actual_unresolved, "unresolved")
    )

    receipts = packet.get("shard_receipts", [])
    expected_receipts = [item.get("receipt_id") for item in receipts]
    actual_receipts = [item["receipt_id"] for item in output["shard_records"]]
    errors.extend(_multiset_errors(expected_receipts, actual_receipts, "receipts"))

    clusters = output["clusters"]
    members_by_index: list[list[tuple[str, str]]] = [[] for _ in clusters]
    for index, cluster in enumerate(clusters):
        relationships = cluster["requested_source_relationships"]
        if len(relationships) != len(set(relationships)):
            errors.append(
                f"cluster[{index}] has duplicate source relationships"
            )
        if cluster["route"] == "source_verification":
            if not cluster["required_verification_depth"]:
                errors.append(f"cluster[{index}] source_verification lacks depth")
            if not relationships:
                errors.append(
                    f"cluster[{index}] source_verification lacks relationships"
                )
        else:
            if cluster["required_verification_depth"] is not None:
                errors.append(f"cluster[{index}] summary_only must not have depth")
            if relationships:
                errors.append(
                    f"cluster[{index}] summary_only must not request relationships"
                )

    for record in records:
        observation = record["observation_id"]
        cluster_index = record["cluster_index"]
        role = record["role"]
        if cluster_index is None:
            if role is not None:
                errors.append(f"record {observation} has role without cluster")
        else:
            if role is None:
                errors.append(f"record {observation} has cluster without role")
            else:
                members_by_index[cluster_index].append((observation, role))
        status = record["accounting_status"]
        if status == "verify" and cluster_index is None:
            errors.append(f"verify record {observation} requires a cluster")
        if (
            status == "verify"
            and cluster_index is not None
            and clusters[cluster_index]["route"] != "source_verification"
        ):
            errors.append(
                f"verify record {observation} cluster is not routed to source verification"
            )
        if cluster_index is not None:
            route = clusters[cluster_index]["route"]
            if route == "source_verification" and status not in (
                "verify",
                "merged",
            ):
                errors.append(
                    f"{status} record {observation} must not belong to a "
                    "source_verification cluster"
                )
            if route == "summary_only" and status == "verify":
                errors.append(
                    f"verify record {observation} must not belong to a "
                    "summary_only cluster"
                )
        if status == "inconclusive" and not _is_nonempty_string(record["falsifier"]):
            errors.append(f"inconclusive record {observation} requires a falsifier")
        if status == "merged":
            if cluster_index is None:
                errors.append(f"merged record {observation} requires a cluster")
            if role in ("canonical", "counterevidence", "minority"):
                errors.append(
                    f"merged record {observation} has non-mergeable role {role}"
                )
            target = record["canonical_observation_id"]
            if target is None:
                errors.append(f"merged record {observation} lacks canonical target")
            elif target == observation:
                errors.append(f"merged record {observation} targets itself")
            elif target not in set(actual_observations):
                errors.append(f"merged record {observation} targets absent {target}")
        elif record["canonical_observation_id"] is not None:
            errors.append(f"non-merged record {observation} has canonical target")

    for index, members in enumerate(members_by_index):
        if not members:
            errors.append(f"cluster[{index}] has no members")
            continue
        member_ids = [observation_id for observation_id, _ in members]
        duplicates = sorted(
            observation_id
            for observation_id, count in Counter(member_ids).items()
            if count != 1
        )
        if duplicates:
            errors.append(f"cluster[{index}] has duplicate memberships: {duplicates}")

    merge_edges = {
        record["observation_id"]: record["canonical_observation_id"]
        for record in records
        if record["accounting_status"] == "merged"
        and record["canonical_observation_id"] is not None
    }
    records_by_id = {record["observation_id"]: record for record in records}
    for origin, target in merge_edges.items():
        origin_cluster = records_by_id[origin]["cluster_index"]
        target_record = records_by_id.get(target)
        if (
            origin_cluster is not None
            and target_record is not None
            and target_record["cluster_index"] != origin_cluster
        ):
            errors.append(
                f"merged record {origin} and canonical target {target} differ in cluster"
            )
    for origin in merge_edges:
        seen: set[str] = set()
        cursor: str | None = origin
        while cursor in merge_edges:
            if cursor in seen:
                errors.append(f"merge cycle includes {origin}")
                break
            seen.add(cursor)
            cursor = merge_edges[cursor]
        terminal = records_by_id.get(cursor)
        origin_record = records_by_id[origin]
        if terminal is None:
            continue
        if terminal["accounting_status"] == "merged":
            errors.append(f"merged record {origin} has no terminal canonical record")
        if terminal["cluster_index"] != origin_record["cluster_index"]:
            errors.append(
                f"merged record {origin} and terminal canonical {cursor} differ in cluster"
            )
        if terminal["role"] != "canonical":
            errors.append(
                f"merged record {origin} terminates at non-canonical role {terminal['role']}"
            )

    queue: list[dict[str, Any]] = []
    if not errors:
        public_clusters = derive_public_clusters(output)
        public_ids = [cluster["cluster_id"] for cluster in public_clusters]
        if len(public_ids) != len(set(public_ids)):
            return ["derived public cluster ids are not unique"], []
        for cluster in sorted(public_clusters, key=lambda item: item["cluster_id"]):
            if cluster["route"] != "source_verification":
                continue
            queue.append(
                {
                    "cluster_id": cluster["cluster_id"],
                    "route": "source_verification",
                    "members": cluster["members"],
                    "required_verification_depth": cluster[
                        "required_verification_depth"
                    ],
                    "requested_source_relationships": sorted(
                        cluster["requested_source_relationships"]
                    ),
                }
            )
    return errors, queue
