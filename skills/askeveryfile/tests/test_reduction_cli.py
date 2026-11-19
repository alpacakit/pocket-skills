from __future__ import annotations
import copy
import fcntl
import hashlib
import json
import os
import signal
import shutil
import time
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
PLANNER = SCRIPTS / "plan_reduction.py"
RUNNER = SCRIPTS / "run_reducers.py"
VALIDATOR = SCRIPTS / "validate_reduction.py"
PROJECTOR = SCRIPTS / "project_verification_input.py"
ACTION_VALIDATOR = SCRIPTS / "validate_verification_actions.py"
sys.path.insert(0, str(SCRIPTS))
from scan_artifacts import artifact_manifest_sha256, sha256_file, render_json
from reduction_jobs import (group_capacity, build_plan, partition_jobs, fit_context, make_job)
from reduction_labels import (position_index, label_reserve, label_closure, label_shape_errors, SubjectPrefix)
from reduction_execution import inspect_execution
from bound_reduction import load_packet, inspect_bound_reduction
from reduction_semantic_state import inspect_job_attempts
from reducer_runtime_policy import reducer_policy_value, reducer_policy_sha256

TEST_MODEL = "test-reducer-model"
TEST_EFFORT = "test-reducer-effort"
TEST_POLICY_ARGS = ("--model", TEST_MODEL, "--effort", TEST_EFFORT)

def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ReductionCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="askeveryfile-test-")
        self.base = Path(self.temporary.name)
        self.codex_home = self.base / "codex-home"
        self.codex_home.mkdir()
        (self.codex_home / "auth.json").write_text("{}\n")
        self.env = os.environ.copy()
        self.env.update({"CODEX_HOME": str(self.codex_home), "PYTHONDONTWRITEBYTECODE": "1",
                         "FAKE_STATE": str(self.base / "calls.json")})
        self.fake = self.base / "fake-codex"
        self.fake.write_text(FAKE_CODEX)
        self.fake.chmod(0o755)

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, script, *args, env=None):
        return subprocess.run([sys.executable, "-B", str(script), *map(str, args)],
                              capture_output=True, text=True, env=env or self.env)

    def make_packet(
        self,
        name: str = "case",
        context_window: int = 400000,
        claim_padding: int = 0,
        model: str = TEST_MODEL,
    ) -> tuple[Path, Path]:
        case = self.base / name
        target = case / "target"
        scan = case / "scan"
        results = scan / "results"
        target.mkdir(parents=True)
        results.mkdir(parents=True)
        source = target / "source.txt"
        source.write_text("first evidence\nsecond evidence\n", encoding="utf-8")
        question = "Which evidence changes the decision?"
        scan_plan = {
            "schema_version": 4,
            "scan_id": name,
            "snapshot_id": f"snapshot-{name}",
            "root": str(target),
            "question": question,
            "question_sha256": sha256_text(question),
            "files": [{"path": "source.txt", "sha256": sha256_file(source)}],
            "shards": [
                {"shard_id": "shard-001"},
                {"shard_id": "shard-002"},
            ],
            "coverage": {
                "included_text_files": 1,
                "excluded_entries": 0,
            },
            "execution": {"shard_count": 2},
            "settings": {"scope_mode": "all"},
            "excluded": [],
        }
        plan_path = scan / "plan.json"
        plan_path.write_text(json.dumps(scan_plan, indent=2) + "\n", encoding="utf-8")
        result_artifacts = {}
        for shard_id in ("shard-001", "shard-002"):
            result_path = results / f"{shard_id}.json"
            result_path.write_text(
                json.dumps({"shard_id": shard_id}) + "\n", encoding="utf-8"
            )
            result_artifacts[f"results/{shard_id}.json"] = sha256_file(result_path)
        results_sha256 = artifact_manifest_sha256(result_artifacts)
        validation_path = scan / "validation.json"
        validation = {
            "schema_version": 4,
            "scan_id": name,
            "plan_sha256": sha256_file(plan_path),
            "valid": True,
            "observations": 2,
            "result_artifacts": result_artifacts,
            "results_sha256": results_sha256,
        }
        validation_path.write_text(
            json.dumps(validation, indent=2) + "\n", encoding="utf-8"
        )
        padding_source = (
            "The replacement owner preserves deterministic behavior across "
            "the persisted boundary and its negative case. "
        )
        padding = (padding_source * (claim_padding // len(padding_source) + 1))[
            :claim_padding
        ]
        observations = []
        for ordinal in (1, 2):
            observations.append(
                {
                    "observation_id": f"shard-001:{ordinal}",
                    "category": "contract",
                    "title": f"Observation {ordinal}",
                    "claim": f"Claim {ordinal}" + padding,
                    "why_it_matters": "It affects the Question.",
                    "scout_confidence": "high",
                    "evidence": [
                        {
                            "path": "source.txt",
                            "absolute_path": str(source),
                            "file_sha256": sha256_file(source),
                            "locator": f"L{ordinal}",
                            "quote": f"{'first' if ordinal == 1 else 'second'} evidence",
                        }
                    ],
                }
            )
        packet = {
            "schema_version": 4,
            "scan_id": name,
            "snapshot_id": f"snapshot-{name}",
            "root": str(target),
            "question": question,
            "question_sha256": sha256_text(question),
            "plan_path": str(plan_path),
            "plan_sha256": sha256_file(plan_path),
            "validation_path": str(validation_path),
            "results_sha256": results_sha256,
            "validated_observation_count": 2,
            "validated_unresolved_count": 2,
            "validated_shard_count": 2,
            "observations": observations,
            "unresolved_questions": [
                {
                    "unresolved_id": "shard-001:unresolved:1",
                    "shard_id": "shard-001",
                    "ordinal": 1,
                    "text": "same unresolved text",
                },
                {
                    "unresolved_id": "shard-001:unresolved:2",
                    "shard_id": "shard-001",
                    "ordinal": 2,
                    "text": "same unresolved text",
                },
            ],
            "shard_receipts": [
                {
                    "receipt_id": "shard-001:receipt",
                    "shard_id": "shard-001",
                    "result_sha256": result_artifacts["results/shard-001.json"],
                    "status": "complete",
                    "observation_ids": ["shard-001:1", "shard-001:2"],
                    "unresolved_ids": [
                        "shard-001:unresolved:1",
                        "shard-001:unresolved:2",
                    ],
                    "overflow_state": "false",
                },
                {
                    "receipt_id": "shard-002:receipt",
                    "shard_id": "shard-002",
                    "result_sha256": result_artifacts["results/shard-002.json"],
                    "status": "complete",
                    "observation_ids": [],
                    "unresolved_ids": [],
                    "overflow_state": "unknown",
                },
            ],
        }
        packet_path = scan / "review-packet.json"
        packet_path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
        catalog_path = scan / "context-catalog.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": model,
                            "context_window": context_window,
                        }
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (scan / "scan.md").write_text("Original scan — immutable\n", encoding="utf-8")
        return packet_path, catalog_path

    def make_reduction(self, name="case", context_window=400000, count=2, only_receipts=False, groups=None, model=TEST_MODEL, effort=TEST_EFFORT):
        packet, catalog = self.make_packet(name, context_window, model=model)
        p = json.loads(packet.read_text())
        seed = p["observations"][0]
        p["observations"] = [{**copy.deepcopy(seed), "observation_id": f"shard-001:{i + 1}",
                              "title": f"Subject {i}", "claim": f"Claim with condition {i}"} for i in range(count)]
        p["validated_observation_count"] = count
        p["shard_receipts"][0]["observation_ids"] = [o["observation_id"] for o in p["observations"]]
        if only_receipts:
            p["observations"] = []
            p["unresolved_questions"] = []
            p["validated_observation_count"] = p["validated_unresolved_count"] = 0
            p["shard_receipts"][0]["observation_ids"] = []
            p["shard_receipts"][0]["unresolved_ids"] = []
        packet.write_text(render_json(p))
        if groups is not None:
            self.env["FAKE_GROUPS"] = json.dumps(groups)
        result = self.run_cli(PLANNER, "--model", model, "--effort", effort, packet, "--context-catalog", catalog)
        if context_window >= 400000:
            self.assertEqual(0, result.returncode, result.stderr)
        return packet.parent / "reduction" / "plan.json"

    def run_reducer(self, plan, modes="green", retries=1):
        self.env["FAKE_MODES"] = modes
        return self.run_cli(RUNNER, plan, "--codex", self.fake, "--retries", retries)

    def calls(self):
        path = self.base / "calls.json"
        return json.loads(path.read_text()) if path.exists() else []

    def green(self, name="case", **kwargs):
        plan = self.make_reduction(name, **kwargs)
        run = self.run_reducer(plan)
        self.assertEqual(0, run.returncode, run.stderr)
        result = self.run_cli(VALIDATOR, plan)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return plan

    def test_green_exact_closure_narrow_usage_and_scan_preservation(self):
        plan = self.green()
        directory = plan.parent
        summary = json.loads((directory / "summary.json").read_text())
        self.assertEqual((2, 2, 2), (summary["observations"], summary["unresolved_questions"], summary["shard_receipts"]))
        self.assertEqual({"input_tokens":200,"cached_input_tokens":80,"output_tokens":40,
                          "reasoning_output_tokens":10,"total_tokens":240}, summary["usage"])
        self.assertIsNone(summary["resolved_model"])
        self.assertEqual("not_reported_by_cli", summary["model_resolution_status"])
        self.assertEqual("Original scan — immutable\n", (directory.parent / "scan.md").read_text())
        self.assertIn("Original scan.md", (directory / "inspection.md").read_text())
        self.assertIn("Accounting jobs: 1/1", (directory / "inspection.md").read_text())
        ledger = json.loads((directory / "accounting-ledger.json").read_text())
        self.assertEqual("unknown", ledger["shard_records"][1]["overflow_state"])
        queue = json.loads((directory / "verification-queue.json").read_text())["queue"]
        for entry in queue:
            cid = entry["cluster_id"]
            bundle = directory / "verification-inputs" / f"{cid}.json"
            result = self.run_cli(PROJECTOR, plan, cid, "--output", bundle)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(len(entry["members"]), len(json.loads(bundle.read_text())["members"]))
        records = directory / "verification-records.json"
        records.write_text("[]")
        action = self.run_cli(ACTION_VALIDATOR, plan, records)
        self.assertNotEqual(0, action.returncode)
        self.assertIn("omit queued clusters", action.stdout)

    def test_requested_policy_reaches_both_jobs_and_preserves_unreported_nulls(self):
        from reducer_runtime_policy import command_policy_errors, reducer_policy_sha256
        plan = self.green(model="alternate-test-reducer", effort="alternate-test-effort")
        value = json.loads(plan.read_text())
        self.assertEqual("alternate-test-reducer", value["policy"]["model"])
        self.assertEqual("alternate-test-effort", value["policy"]["effort"])
        self.assertEqual(reducer_policy_sha256(value["policy"]), value["policy_sha256"])
        for job in ("label-001", "batch-001"):
            directory = plan.parent / "attempts" / job / "attempt-0001"
            request = json.loads((directory / "request.json").read_text())
            receipt = json.loads((directory / "receipt.json").read_text())
            self.assertIn("model_reasoning_effort=alternate-test-effort", request["command"])
            self.assertEqual("alternate-test-effort", request["requested_effort"])
            self.assertEqual("alternate-test-reducer", request["requested_model"])
            self.assertEqual("alternate-test-effort", receipt["requested_effort"])
            self.assertEqual("alternate-test-reducer", receipt["requested_model"])
            self.assertEqual(value["policy_sha256"], receipt["policy_sha256"])
            self.assertEqual([], command_policy_errors(request["command"], receipt, value["policy"]))
            for field in ("model", "effort"):
                self.assertIsNone(receipt[f"resolved_{field}"])
                self.assertEqual("not_reported_by_cli", receipt[f"{field}_resolution_status"])
                forged = {**receipt, f"resolved_{field}": value["policy"][field]}
                self.assertTrue(command_policy_errors(request["command"], forged, value["policy"]))
            different = {**receipt, "requested_effort": "different-test-effort"}
            self.assertTrue(command_policy_errors(request["command"], different, value["policy"]))
        reused = self.run_reducer(plan)
        self.assertEqual(0, reused.returncode, reused.stderr)
        self.assertEqual(["label", "accounting"], self.calls())
        # Rehashing a changed policy cannot authorize reuse of earlier attempts.
        changed = copy.deepcopy(value)
        changed["policy"]["effort"] = "different-test-effort"
        changed["policy_sha256"] = reducer_policy_sha256(changed["policy"])
        plan.write_text(render_json(changed))
        resumed = self.run_reducer(plan)
        self.assertNotEqual(0, resumed.returncode)
        self.assertIn("requested_effort", resumed.stderr)
        validated = self.run_cli(VALIDATOR, plan)
        self.assertNotEqual(0, validated.returncode)
        self.assertIn("requested_effort", validated.stdout)
        self.assertEqual(["label", "accounting"], self.calls())

    def test_changed_policy_is_a_distinct_semantic_experiment(self):
        from reduction_semantic_state import semantic_experiment_sha256
        plan = self.make_reduction()
        current = json.loads(plan.read_text())
        packet_path = Path(current["bindings"]["packet_path"])
        catalog_path = Path(current["bindings"]["context_catalog_path"])
        catalog = json.loads(catalog_path.read_text())
        catalog["models"].append({"slug": "alternate-test-reducer", "context_window": 400000})
        catalog_path.write_text(render_json(catalog))
        packet, bindings, catalog = load_packet(packet_path, catalog_path)
        for field, alternate in (("model", "alternate-test-reducer"), ("effort", "alternate-test-effort")):
            with self.subTest(field=field):
                policy = dict(current["policy"], **{field: alternate})
                changed = build_plan(packet, bindings, catalog, policy)
                self.assertNotEqual(semantic_experiment_sha256(changed), semantic_experiment_sha256(current))
                self.assertEqual(current["contracts"], changed["contracts"])
                self.assertEqual(current["label_blocks"], changed["label_blocks"])
                # The matching bound catalog permits this deliberate new policy.
                self.assertEqual((), inspect_bound_reduction(changed, plan.parent).errors)
                sibling = plan.parent.with_name(f"reduction-{field}")
                result = self.run_cli(PLANNER, packet_path, "--model", policy["model"],
                                      "--effort", policy["effort"], "--context-catalog", catalog_path,
                                      "--output-dir", sibling)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(changed, json.loads((sibling / "plan.json").read_text()))

    def test_rehashed_plan_model_must_match_bound_context_catalog(self):
        plan = self.make_reduction("rehashed-plan-model")
        original = json.loads(plan.read_text())
        catalog_path = Path(original["bindings"]["context_catalog_path"])
        catalog = json.loads(catalog_path.read_text())
        catalog["models"].append({"slug": "small-test-model", "context_window": 1000})
        catalog_path.write_text(render_json(catalog))
        original["bindings"]["context_catalog_sha256"] = sha256_file(catalog_path)
        self.assertEqual((), inspect_bound_reduction(original, plan.parent).errors)
        for model in ("unknown-test-model", "small-test-model"):
            with self.subTest(model=model):
                value = copy.deepcopy(original)
                value["policy"]["model"] = model
                value["policy_sha256"] = reducer_policy_sha256(value["policy"])
                plan.write_text(render_json(value))
                inspection = inspect_bound_reduction(value, plan.parent)
                self.assertIn("context", "; ".join(inspection.errors).lower())
                run = self.run_reducer(plan)
                self.assertNotEqual(0, run.returncode)
                self.assertIn("context", run.stderr.lower())
                validated = self.run_cli(VALIDATOR, plan)
                self.assertNotEqual(0, validated.returncode)
                self.assertIn("context", validated.stdout.lower())
                self.assertEqual([], self.calls())

    def test_green_reuse_does_not_launch_and_restores_wrapper_after_interrupt(self):
        plan = self.green()
        before = {str(p): p.read_bytes() for p in (plan.parent / "attempts").rglob("*") if p.is_file()}
        (plan.parent / "results/batch-001.json").unlink()
        count = len(self.calls())
        result = self.run_reducer(plan)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(count, len(self.calls()))
        self.assertEqual(before, {str(p): p.read_bytes() for p in (plan.parent / "attempts").rglob("*") if p.is_file()})

    def test_partial_results_never_publish_and_projector_refuses(self):
        plan = self.make_reduction()
        run = self.run_reducer(plan, "green,no_result", 0)
        self.assertNotEqual(0, run.returncode)
        validated = self.run_cli(VALIDATOR, plan)
        self.assertNotEqual(0, validated.returncode)
        self.assertFalse((plan.parent / "verification-queue.json").exists())
        projected = self.run_cli(PROJECTOR, plan, "cluster-fake", "--output", plan.parent / "verification-inputs/cluster-fake.json")
        self.assertNotEqual(0, projected.returncode)

    def test_transport_failures_are_bounded_and_do_not_consume_semantic_cap(self):
        for mode in ("no_result", "empty", "invalid", "schema_invalid", "no_usage", "tool", "model_mismatch", "effort_mismatch", "exit7"):
            with self.subTest(mode=mode):
                case = self.make_reduction(mode)
                self.env["FAKE_STATE"] = str(self.base / f"calls-{mode}.json")
                failed = self.run_reducer(case, mode, 0)
                self.assertNotEqual(0, failed.returncode)
                self.assertFalse((case.parent / "blocked.json").exists())
                first = case.parent / "attempts/label-001/attempt-0001/raw-result.json"
                original = first.read_bytes()
                resumed = self.run_reducer(case, "green", 1)
                self.assertEqual(0, resumed.returncode, resumed.stderr)
                self.assertEqual(original, first.read_bytes())
                self.assertTrue((case.parent / "attempts/label-001/attempt-0002").exists())

    def test_semantic_red_across_label_and_accounting_caps_at_two_and_recovers_markers(self):
        plan = self.make_reduction()
        result = self.run_reducer(plan, "semantic_red,green,semantic_red", 1)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(3, len(self.calls()))
        blocked = plan.parent / "blocked.json"
        data = json.loads(blocked.read_text())
        self.assertEqual(2, len(data["semantic_red_attempts"]))
        blocked.unlink()
        for closure in (plan.parent / "attempts").rglob("closure.json"):
            closure.unlink()
        result = self.run_reducer(plan, "green")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("semantic_blocked", result.stderr)
        self.assertEqual(3, len(self.calls()))
        self.assertTrue(blocked.exists())
        self.assertIn("semantic-red attempts: 2/2", (plan.parent / "inspection.md").read_text())
        self.assertEqual("Original scan — immutable\n", (plan.parent.parent / "scan.md").read_text())

    def test_semantic_red_in_two_distinct_accounting_jobs_shares_cap(self):
        plan = self.make_reduction(count=180)
        first = self.run_reducer(plan, "green,green,semantic_red", 0)
        self.assertNotEqual(0, first.returncode)
        second = self.run_reducer(plan, "green,green,green,green,semantic_red", 0)
        self.assertNotEqual(0, second.returncode)
        self.assertEqual(["label","label","accounting","accounting","accounting"], self.calls())
        blocked = json.loads((plan.parent / "blocked.json").read_text())
        self.assertEqual({"batch-001","batch-002"}, {c["job_id"] for c in blocked["semantic_red_attempts"]})
        again = self.run_reducer(plan)
        self.assertNotEqual(0,again.returncode)
        self.assertEqual(5,len(self.calls()))
        validated = self.run_cli(VALIDATOR, plan)
        self.assertNotEqual(0, validated.returncode)
        self.assertIn("Accounting jobs: 1/2", (plan.parent/"inspection.md").read_text())

    def test_accounting_red_retries_only_accounting(self):
        plan = self.make_reduction()
        result = self.run_reducer(plan, "green,semantic_red,green", 1)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["label", "accounting", "accounting"], self.calls())
        validated = self.run_cli(VALIDATOR, plan)
        self.assertEqual(0, validated.returncode, validated.stdout)

    def test_same_sibling_rejected_old_overflow_preserved(self):
        plan = self.make_reduction()
        packet = plan.parent.parent / "review-packet.json"
        catalog = plan.parent.parent / "context-catalog.json"
        duplicate = self.run_cli(PLANNER, *TEST_POLICY_ARGS, packet, "--context-catalog", catalog,
                                 "--output-dir", plan.parent.parent / "reduction-again")
        self.assertNotEqual(0, duplicate.returncode)
        self.assertIn("identical semantic", duplicate.stderr)
        old = plan.parent.parent / "reduction-old"
        old.mkdir()
        p = json.loads(plan.read_text())
        # Frozen output from e1781f5's actual planner on a synthetic packet;
        # migrate the old contract, rather than relabeling a new plan as v2.
        legacy = json.loads((SKILL / "tests/fixtures/reduction-v2-overflow.json").read_text())
        legacy["bindings"] = {**legacy["bindings"], **p["bindings"]}
        (old / "plan.json").write_text(render_json(legacy))
        before = (old / "plan.json").read_bytes()
        # Remove only the never-executed NEW fixture plan. The migration input
        # is the distinct frozen legacy schema, retained byte-for-byte below.
        import shutil
        shutil.rmtree(plan.parent)
        created = self.run_cli(PLANNER, *TEST_POLICY_ARGS, packet, "--context-catalog", catalog,
                               "--output-dir", plan.parent.parent / "reduction-capacity-v3")
        self.assertEqual(0, created.returncode, created.stderr)
        self.assertEqual(before, (old / "plan.json").read_bytes())

    def test_lock_rejects_second_runner_and_validator(self):
        plan = self.make_reduction()
        with (plan.parent / "runner.lock").open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for script in (RUNNER, VALIDATOR):
                result = self.run_cli(script, plan)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("already active", result.stderr)
        self.assertEqual([], self.calls())

    def test_projection_input_source_catalog_and_plan_tamper_rejected(self):
        policy_changes = {"model": "different-test-model", "effort": "different-test-effort",
                          "agent": "claude", "sandbox": "unsafe-test-sandbox", "tools": ["test-tool"]}
        for field in ("projection", "source", "catalog", *policy_changes, "schema"):
            with self.subTest(field=field):
                plan = self.make_reduction(field)
                value = json.loads(plan.read_text())
                if field == "projection":
                    inspect_execution(plan, value, publish=True)
                    path = plan.parent / "jobs/label-001/input.json"
                    path.write_bytes(path.read_bytes() + b" ")
                elif field == "source":
                    path = Path(json.loads(Path(value["bindings"]["scan_plan_path"]).read_text())["root"]) / "source.txt"
                    path.write_text("changed source")
                elif field == "catalog":
                    path = Path(value["bindings"]["context_catalog_path"])
                    path.write_text(path.read_text() + " ")
                else:
                    if field in policy_changes:
                        value["policy"][field] = policy_changes[field]
                        if field in ("agent", "sandbox", "tools"):
                            value["policy_sha256"] = reducer_policy_sha256(value["policy"])
                    else:
                        value["label_blocks"][0]["schema_sha256"] = "0" * 64
                    plan.write_text(render_json(value))
                result = self.run_reducer(plan)
                self.assertNotEqual(0, result.returncode)
                if field in policy_changes:
                    validated = self.run_cli(VALIDATOR, plan)
                    self.assertNotEqual(0, validated.returncode)
                    self.assertIn("reducer policy", validated.stdout)
        self.assertEqual([], self.calls())

    def test_partition_and_attempt_artifact_tamper_are_red(self):
        for target in ("partition", "input", "prompt", "schema", "raw", "receipt", "wrapper", "closure"):
            with self.subTest(target=target):
                plan = self.green(target)
                directory = plan.parent
                paths = {"partition":directory/"partition/manifest.json", "input":directory/"partition/inputs/batch-001.json",
                         "prompt":directory/"attempts/batch-001/attempt-0001/prompt.md",
                         "schema":directory/"attempts/batch-001/attempt-0001/schema.json",
                         "raw":directory/"attempts/batch-001/attempt-0001/raw-result.json",
                         "receipt":directory/"attempts/batch-001/attempt-0001/receipt.json",
                         "wrapper":directory/"results/batch-001.json", "closure":directory/"attempts/batch-001/attempt-0001/closure.json"}
                path = paths[target]
                if target in ("input", "prompt", "schema", "raw"):
                    path.write_bytes(path.read_bytes()+b" ")
                else:
                    value = json.loads(path.read_text()); value["tamper"] = True;path.write_text(render_json(value))
                result = self.run_cli(VALIDATOR, plan)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse((directory / "summary.json").exists())
                calls = len(self.calls())
                result = self.run_reducer(plan)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(calls, len(self.calls()))

    def test_multijob_packing_quotes_atomic_receipts_and_full_closure(self):
        plan = self.green(count=180)
        partition = json.loads((plan.parent / "partition/manifest.json").read_text())
        self.assertGreater(len(partition["jobs"]), 1)
        receipts_jobs = [j for j in partition["jobs"] if j["receipt_indices"]]
        self.assertEqual(1, len(receipts_jobs))
        self.assertEqual([0,1], receipts_jobs[0]["receipt_indices"])
        self.assertEqual(182, sum(len(j["positions"]) for j in partition["jobs"]))
        for j in partition["jobs"]:
            self.assertTrue(j["context"]["fit"])
            data = json.loads((plan.parent / "partition/inputs" / f"{j['job_id']}.json").read_text())
            self.assertTrue(all(row[6][0][2] == "first evidence" for row in data["o"]))
        summary = json.loads((plan.parent / "summary.json").read_text())
        self.assertEqual(len(partition["jobs"]) + len(json.loads(plan.read_text())["label_blocks"]), summary["execution_jobs"])

    def test_receipts_only_and_zero_universe(self):
        plan = self.green(only_receipts=True)
        partition = json.loads((plan.parent / "partition/manifest.json").read_text())
        self.assertEqual([], partition["groups"])
        self.assertEqual(1, len(partition["jobs"]))
        self.assertEqual([], partition["jobs"][0]["positions"])
        self.assertEqual([0,1], partition["jobs"][0]["receipt_indices"])
        summary = json.loads((plan.parent / "summary.json").read_text())
        self.assertEqual(0, summary["observations"])
        self.assertEqual(2, summary["shard_receipts"])

    def test_oversized_label_group_semantic_red_not_shape_retry_or_split(self):
        plan = self.make_reduction(count=180)
        result = self.run_reducer(plan, "oversized", 1)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(["label", "label"], self.calls())
        self.assertTrue((plan.parent / "blocked.json").exists())
        self.assertFalse((plan.parent / "partition").exists())
        receipt = json.loads((plan.parent / "attempts/label-001/attempt-0001/receipt.json").read_text())
        self.assertEqual("valid", receipt["raw_result_status"])

    def test_invalid_partial_request_is_fail_closed(self):
        plan = self.make_reduction()
        directory = plan.parent / "attempts/label-001/attempt-0001"
        directory.mkdir(parents=True)
        (directory / "request.json").write_text("{}")
        result = self.run_reducer(plan)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], self.calls())

    def test_schema_admits_true_oversized_group_and_semantic_cap_stops_it(self):
        plan = self.make_reduction(count=180)
        result = self.run_reducer(plan, "schema_oversized", 1)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(["label", "label"], self.calls())
        value = json.loads(plan.read_text())
        cap = value["group_capacity"]["max_group_positions"]
        for directory in (plan.parent / "attempts/label-001").iterdir():
            schema = json.loads((directory / "schema.json").read_text())
            self.assertEqual(128, schema["properties"]["assignments"]["items"]["properties"]["positions"]["maxItems"])
            raw = json.loads((directory / "raw-result.json").read_text())
            self.assertGreater(len(raw["assignments"][0]["positions"]), cap)
            receipt = json.loads((directory / "receipt.json").read_text())
            self.assertEqual("valid", receipt["raw_result_status"])
            self.assertEqual("semantic_red", json.loads((directory / "closure.json").read_text())["status"])
        self.assertTrue((plan.parent / "blocked.json").is_file())
        self.assertFalse((plan.parent / "partition").exists())
        self.assertFalse((plan.parent / "verification-queue.json").exists())

    def test_kill_model_call_resumes_same_cli_in_next_immutable_directory(self):
        for kind, sig in (("label", signal.SIGTERM), ("accounting", signal.SIGKILL)):
            with self.subTest(kind=kind):
                plan = self.make_reduction(kind)
                self.env["FAKE_STATE"] = str(self.base / f"{kind}-calls.json")
                marker = self.base / f"{kind}-started.json"
                env = {**self.env, "FAKE_PAUSE_KIND":kind, "FAKE_STARTED":str(marker)}
                command = [sys.executable,"-B",str(RUNNER),str(plan),"--codex",str(self.fake),"--retries","0"]
                proc = subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                started = None
                try:
                    deadline = time.monotonic()+10
                    while not marker.exists() and proc.poll() is None and time.monotonic()<deadline:
                        time.sleep(0.01)
                    self.assertTrue(marker.exists(), "fake model did not enter the call")
                    started = json.loads(marker.read_text())
                    os.killpg(proc.pid,sig)
                    proc.communicate(timeout=5)
                    self.assertEqual(-sig,proc.returncode)
                    job = "label-001" if kind=="label" else "batch-001"
                    first = plan.parent / "attempts" / job / "attempt-0001"
                    self.assertFalse((first / "receipt.json").exists())
                    self.assertFalse((first / "raw-result.json").exists())
                    before = {p.name:p.read_bytes() for p in first.iterdir()}
                    resumed = subprocess.run(command,env=self.env,capture_output=True,text=True,timeout=15)
                    self.assertEqual(0,resumed.returncode,resumed.stderr)
                    self.assertTrue((first.parent / "attempt-0002/receipt.json").is_file())
                    self.assertEqual(before,{p.name:p.read_bytes() for p in first.iterdir()})
                    self.assertFalse((first / "closure.json").exists())
                    result = self.run_cli(VALIDATOR,plan)
                    self.assertEqual(0,result.returncode,result.stdout+result.stderr)
                    self.assertEqual("Original scan — immutable\n",(plan.parent.parent / "scan.md").read_text())
                    self.assertIn("semantic-red attempts: 0/2",(plan.parent / "inspection.md").read_text())
                    calls = list(self.calls())
                    reuse = subprocess.run(command,env=self.env,capture_output=True,text=True,timeout=15)
                    self.assertEqual(0,reuse.returncode,reuse.stderr)
                    self.assertEqual(calls,self.calls())
                finally:
                    if proc.poll() is None:
                        os.killpg(proc.pid,signal.SIGKILL)
                        proc.communicate(timeout=5)
                    if started is not None:
                        isolated = Path(started["codex_home"]).parent
                        self.assertTrue(isolated.name.startswith("askeveryfile-"))
                        shutil.rmtree(isolated,ignore_errors=True)

    def interrupted_inputs(self, plan, number=1, job_id="label-001"):
        from agent_attempt import _command
        from reducer_runtime_policy import ReducerPolicy
        from reduction_semantic_state import input_manifest
        value = json.loads(plan.read_text())
        policy = ReducerPolicy(**value["policy"])
        view = inspect_execution(plan, value, publish=True)
        job = next(j for j in view.jobs if j.job_id == job_id)
        directory = plan.parent / "attempts" / job_id / f"attempt-{number:04d}"
        manifest = input_manifest(plan,value,job)
        inputs = {"prompt.md":job.prompt.encode(), "input-manifest.json":render_json(manifest).encode(),
                  "schema.json":render_json(job.schema).encode()}
        request = {"schema_version":1,"attempt_id":f"{job_id}-attempt-{number:04d}","attempt":number,
                   "phase":"reducer","cli_version":"codex-cli 9.8.7","workdir":str(plan.parent),
                   "command":_command(str(self.fake),policy,plan.parent,directory/"schema.json",directory/"raw-result.json"),
                   "requested_model":policy.model,"requested_effort":policy.effort,
                   "sandbox":policy.sandbox,"tools":[],"policy_sha256":value["policy_sha256"],
                   "prompt_sha256":hashlib.sha256(inputs["prompt.md"]).hexdigest(),
                   "input_manifest_sha256":hashlib.sha256(inputs["input-manifest.json"]).hexdigest(),
                   "schema_sha256":hashlib.sha256(inputs["schema.json"]).hexdigest(),
                   "started_at_utc":"2026-11-01T00:00:00+00:00","started_monotonic_ns":1}
        inputs["request.json"] = render_json(request).encode()
        return directory,inputs

    def test_each_preflight_interruption_preserves_bytes_and_consumes_directory_number(self):
        for stage in range(5):
            with self.subTest(stage=stage):
                plan = self.make_reduction(f"stage-{stage}")
                self.env["FAKE_STATE"] = str(self.base / f"stage-{stage}-calls.json")
                directory,inputs = self.interrupted_inputs(plan,number=7)
                directory.mkdir(parents=True)
                for name,data in list(inputs.items())[:stage]:
                    (directory/name).write_bytes(data)
                before = {p.name:p.read_bytes() for p in directory.iterdir()}
                run = self.run_reducer(plan)
                self.assertEqual(0,run.returncode,run.stderr)
                self.assertTrue((directory.parent/"attempt-0008/receipt.json").is_file())
                self.assertEqual(before,{p.name:p.read_bytes() for p in directory.iterdir()})
                self.assertFalse((directory/"closure.json").exists())

    def test_rawless_interruption_rejects_modified_inputs_unknown_results_and_closure(self):
        for defect in ("prompt.md","input-manifest.json","schema.json","request.json","command","request_binding",
                       "raw", "empty_raw", "closure", "local_result", "wrapper", "unknown", "missing_input", "unknown_directory"):
            with self.subTest(defect=defect):
                plan = self.make_reduction(defect.replace(".","-"))
                directory,inputs = self.interrupted_inputs(plan)
                directory.mkdir(parents=True)
                for name,data in inputs.items():
                    (directory/name).write_bytes(data)
                if defect in inputs:
                    (directory/defect).write_bytes(inputs[defect]+b" ")
                elif defect in ("command","request_binding"):
                    value=json.loads((directory/"request.json").read_text())
                    if defect=="command":value["command"][value["command"].index("-o")+1]="/tmp/unbound-result.json"
                    else:value["attempt"]=2
                    (directory/"request.json").write_text(render_json(value))
                elif defect in ("raw","empty_raw"):
                    (directory/"raw-result.json").write_bytes(b"{}" if defect=="raw" else b"")
                elif defect=="closure":(directory/"closure.json").write_text("{}")
                elif defect=="local_result":(directory/"result.json").write_text("{}")
                elif defect=="wrapper":
                    (plan.parent/"results").mkdir()
                    (plan.parent/"results/label-001.json").write_text("{}")
                elif defect=="unknown":(directory/"unknown.json").write_text("{}")
                elif defect=="unknown_directory":
                    renamed=directory.parent/"hidden-attempt"
                    directory.rename(renamed)
                    directory=renamed
                else:(directory/"schema.json").unlink()
                before={p.name:p.read_bytes() for p in directory.iterdir()}
                run=self.run_reducer(plan)
                self.assertNotEqual(0,run.returncode)
                self.assertEqual(before,{p.name:p.read_bytes() for p in directory.iterdir()})
                self.assertFalse((directory.parent/"attempt-0002").exists())
        self.assertEqual([],self.calls())

    def test_interrupted_attempt_does_not_reset_prior_semantic_red(self):
        plan=self.make_reduction()
        first=self.run_reducer(plan,"semantic_red",0)
        self.assertNotEqual(0,first.returncode)
        directory,inputs=self.interrupted_inputs(plan,number=2)
        directory.mkdir(parents=True)
        for name,data in inputs.items():(directory/name).write_bytes(data)
        before={p.name:p.read_bytes() for p in directory.iterdir()}
        second=self.run_reducer(plan,"semantic_red",0)
        self.assertNotEqual(0,second.returncode)
        blocked=json.loads((plan.parent/"blocked.json").read_text())
        self.assertEqual(2,len(blocked["semantic_red_attempts"]))
        self.assertEqual(before,{p.name:p.read_bytes() for p in directory.iterdir()})
        self.assertTrue((directory.parent/"attempt-0003/receipt.json").is_file())
        third=self.run_reducer(plan)
        self.assertNotEqual(0,third.returncode)
        self.assertEqual(2,len(self.calls()))
        self.assertFalse((directory.parent/"attempt-0004").exists())

    def test_failed_inspection_preserves_verified_label_partition_progress_and_reason(self):
        plan = self.make_reduction(count=180)
        # Both labels and first accounting complete; the second accounting fails.
        run = self.run_reducer(plan,"green,green,green,exit7",0)
        self.assertNotEqual(0,run.returncode)
        text = (plan.parent/"inspection.md").read_text()
        self.assertIn("Labels: 2/2",text)
        self.assertIn("Partition: fixed",text)
        self.assertIn("Accounting jobs: 1/2",text)
        self.assertIn("Current job: batch-002",text)
        self.assertIn("semantic-red attempts: 0/2",text)
        self.assertIn("transport/shape failed; exit=7, raw=missing",text)
        self.assertNotIn("pending label",text)
        self.assertNotIn("first evidence",text)
        self.assertNotIn("Claim with condition",text)
        validated=self.run_cli(VALIDATOR,plan)
        self.assertNotEqual(0,validated.returncode)
        text=(plan.parent/"inspection.md").read_text()
        self.assertIn("Labels: 2/2",text)
        self.assertIn("Accounting jobs: 1/2",text)
        self.assertIn("Missing authenticated result: batch-002",text)

    def test_overflow_plan_creates_no_dynamic_jobs_or_attempts(self):
        packet, catalog = self.make_packet(context_window=1000)
        result = self.run_cli(PLANNER, *TEST_POLICY_ARGS, packet, "--context-catalog", catalog)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("atomic_unit_overflow", json.loads((packet.parent / "reduction/plan.json").read_text())["status"])
        self.assertFalse((packet.parent / "reduction/jobs").exists())
        self.assertFalse((packet.parent / "reduction/attempts").exists())
        run = self.run_reducer(packet.parent / "reduction/plan.json")
        self.assertNotEqual(0, run.returncode)
        self.assertEqual([], self.calls())

    def test_label_overflow_stops_even_when_atomic_units_fit(self):
        packet,catalog=self.make_packet(context_window=50000,claim_padding=30000)
        created=self.run_cli(PLANNER, *TEST_POLICY_ARGS,packet,"--context-catalog",catalog)
        self.assertNotEqual(0,created.returncode)
        plan=packet.parent/"reduction/plan.json"
        value=json.loads(plan.read_text())
        self.assertEqual("label_input_overflow",value["status"])
        self.assertGreater(value["group_capacity"]["max_group_positions"],0)
        self.assertNotEqual(0,self.run_reducer(plan).returncode)
        self.assertFalse((plan.parent/"jobs").exists())
        self.assertFalse((plan.parent/"attempts").exists())
        self.assertEqual([],self.calls())

    def test_all_three_accounting_universes_fail_closed_through_cli(self):
        for universe in ("records", "unresolved_records", "shard_records"):
            for defect in ("missing", "duplicate", "foreign"):
                with self.subTest(universe=universe, defect=defect):
                    plan = self.make_reduction(f"{universe}-{defect}")
                    self.env["FAKE_STATE"] = str(self.base / f"calls-{universe}-{defect}.json")
                    self.env["FAKE_DEFECT"] = f"{universe}:{defect}"
                    result = self.run_reducer(plan, "green", 1)
                    self.assertNotEqual(0, result.returncode)
                    self.assertTrue((plan.parent / "blocked.json").exists())
                    receipt = json.loads((plan.parent / "attempts/batch-001/attempt-0001/receipt.json").read_text())
                    self.assertEqual("valid", receipt["raw_result_status"])
                    self.assertFalse((plan.parent / "verification-queue.json").exists())
        self.env.pop("FAKE_DEFECT", None)

    def test_noncontiguous_relation_group_stays_atomic_and_preserves_minority(self):
        groups = [[0,179]] + [[i] for i in range(182) if i not in (0,179)]
        plan = self.green(count=180, groups=groups)
        partition = json.loads((plan.parent / "partition/manifest.json").read_text())
        owners = [j for j in partition["jobs"] if 0 in j["positions"]]
        self.assertEqual(1, len(owners))
        self.assertIn(179, owners[0]["positions"])
        ledger = json.loads((plan.parent / "accounting-ledger.json").read_text())
        records = {r["observation_id"]:r for r in ledger["records"]}
        self.assertEqual("minority", records["shard-001:180"]["reduction"]["role"])
        self.assertEqual(records["shard-001:1"]["reduction"]["cluster_id"],
                         records["shard-001:180"]["reduction"]["cluster_id"])

    def test_action_records_close_against_new_queue_and_reject_stale_bindings(self):
        from test_reduction_contract import action_record
        plan = self.green()
        queue = json.loads((plan.parent / "verification-queue.json").read_text())["queue"]
        entry = queue[0]
        bundle = plan.parent / "verification-inputs" / f"{entry['cluster_id']}.json"
        projected = self.run_cli(PROJECTOR, plan, entry["cluster_id"], "--output", bundle)
        self.assertEqual(0, projected.returncode, projected.stderr)
        record = action_record()
        record.update({"cluster_id":entry["cluster_id"], "verification_queue_sha256":sha256_file(plan.parent / "verification-queue.json"),
                       "verification_bundle_sha256":sha256_file(bundle), "source_snapshot_id":"snapshot-case",
                       "required_depth":entry["required_verification_depth"]})
        path = plan.parent / "records.json"
        path.write_text(render_json([record]))
        validated = self.run_cli(ACTION_VALIDATOR, plan, path)
        self.assertEqual(0, validated.returncode, validated.stdout + validated.stderr)
        for field in ("verification_queue_sha256", "verification_bundle_sha256", "source_snapshot_id"):
            wrong = {**record, field:"stale"}
            path.write_text(render_json([wrong]))
            rejected = self.run_cli(ACTION_VALIDATOR, plan, path)
            self.assertNotEqual(0, rejected.returncode)
        path.write_text(render_json([record]))
        bundle.write_text(bundle.read_text() + " ")
        rejected = self.run_cli(ACTION_VALIDATOR, plan, path)
        self.assertNotEqual(0, rejected.returncode)

    def test_worst_x_group_plus_receipts_fits_and_empty_universe_packs(self):
        from reduction_jobs import reducer_input_value, compact, subset, assemble_outputs
        plan_path = self.make_reduction(count=180)
        plan = json.loads(plan_path.read_text())
        packet = json.loads(Path(plan["bindings"]["packet_path"]).read_text())
        projection = reducer_input_value(packet)
        costs = [len(compact(r)) for r in projection["o"] + projection["u"]]
        limit = plan["group_capacity"]["max_group_positions"]
        worst = sorted(range(len(costs)), key=lambda i:costs[i], reverse=True)[:limit]
        job = make_job(packet,"accounting","worst",plan["capacity"],limit,worst,[0,1],"0"*64)
        self.assertTrue(job.descriptor["context"]["fit"])
        empty = {**packet, "observations":[], "unresolved_questions":[], "shard_receipts":[]}
        manifest, jobs = partition_jobs(empty, plan, [], "0"*64)
        self.assertEqual([],jobs)
        self.assertEqual([],assemble_outputs(empty,jobs,{},limit)["records"])

    def test_receipt_status_cannot_hide_complete_semantic_red(self):
        plan = self.make_reduction()
        run = self.run_reducer(plan,"semantic_red",0)
        self.assertNotEqual(0,run.returncode)
        path = plan.parent / "attempts/label-001/attempt-0001/receipt.json"
        value = json.loads(path.read_text())
        value["status"]="failed"
        value["failure_reasons"]=["invented failure"]
        path.write_text(render_json(value))
        result=self.run_reducer(plan)
        self.assertNotEqual(0,result.returncode)
        self.assertEqual(1,len(self.calls()))

    def test_label_projection_preserves_original_semantic_fields_and_positions(self):
        packet, catalog = self.make_packet()
        p, b, c = load_packet(packet, catalog)
        projection = position_index(p)
        self.assertEqual([0,1], [r[0] for r in projection["o"]])
        self.assertEqual([2,3], [r[0] for r in projection["u"]])
        self.assertEqual(p["question"], projection["q"])
        for original, row in zip(p["observations"], projection["o"]):
            self.assertEqual([original["title"],original["claim"],original["why_it_matters"]], row[4:7])
            self.assertEqual([[0,"L1"]] if row[0]==0 else [[0,"L2"]], row[7])
        self.assertNotIn("first evidence", json.dumps(projection))
        def output(groups):
            return {"schema_version":1,"new_subjects":[{"key":f"s-{i}","definition":"subject"} for i in range(len(groups))],
                    "assignments":[{"key":f"s-{i}","positions":g} for i,g in enumerate(groups)]}
        self.assertEqual([], label_closure(output([[0,2],[1,3]]),list(range(4)),4,SubjectPrefix(),4))
        for groups in ([[0,1],[2,2]],[[0,1],[2]],[[1,2],[3,4]],[[0,1,2,3,4]], [[],[0,1,2,3]]):
            self.assertTrue(label_closure(output(groups),list(range(4)),4,SubjectPrefix(),4))
        self.assertTrue(label_shape_errors(output([[True]]),4,4))

    def test_reserve_bounds_serialization_and_group_worst_case_fit(self):
        packet, catalog = self.make_packet()
        p,b,c = load_packet(packet,catalog)
        plan = build_plan(p,b,c,reducer_policy_value(TEST_MODEL, TEST_EFFORT))
        bound = plan["group_capacity"]
        self.assertTrue(bound["worst_case"]["fit"])
        for groups in ([[0],[1],[2],[3]],[[0,1,2,3]],[[0,2],[1,3]]):
            value={"schema_version":1,"new_subjects":[{"key":f"s-{i}","definition":"subject"} for i in range(len(groups))],
                   "assignments":[{"key":f"s-{i}","positions":g} for i,g in enumerate(groups)]}
            size=len(render_json(value).encode())
            self.assertLessEqual(size, label_reserve(4,4)["max_serialized_utf8_bytes"])
        context = {"context_window_tokens":20000,"effective_context_window_percent":100,"effective_context_window_tokens":20000}
        f=fit_context(0,0,{"computed_tokens":3616},context,"label")
        self.assertTrue(f["fit"])
        context["effective_context_window_tokens"]-=1
        self.assertFalse(fit_context(0,0,{"computed_tokens":3616},context,"label")["fit"])


FAKE_CODEX = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
if sys.argv[1:]==["--version"]:
 print("codex-cli 9.8.7");sys.exit()
if sys.argv[1:]==["debug","models","--bundled"]:
 print(json.dumps({"models":[{"slug":"test-reducer-model","context_window":400000}]}));sys.exit()
if sys.argv[1]=="exec":
 raw=sys.stdin.read()
 data=json.loads(raw.split("<<<ASKEVERYFILE_REDUCTION:INPUT>>>\n",1)[1].split("\n<<<ASKEVERYFILE_REDUCTION:END_INPUT>>>",1)[0])
 kind="label" if data["v"]==3 else "accounting"
 state=pathlib.Path(os.environ["FAKE_STATE"])
 calls=json.loads(state.read_text()) if state.exists() else []
 idx=len(calls);calls.append(kind);state.write_text(json.dumps(calls))
 if os.environ.get("FAKE_PAUSE_KIND")==kind:
  import time
  marker=pathlib.Path(os.environ["FAKE_STARTED"])
  temporary=marker.with_suffix(".tmp")
  temporary.write_text(json.dumps({"pid":os.getpid(),"codex_home":os.environ["CODEX_HOME"]}))
  temporary.replace(marker)
  while True:time.sleep(1)
 modes=os.environ.get("FAKE_MODES","green").split(",");mode=modes[min(idx,len(modes)-1)]
 result=pathlib.Path(sys.argv[sys.argv.index("-o")+1])
 if mode=="exit7":sys.exit(7)
 if kind=="label":
  positions=[r[0] for r in data["o"]+data["u"]]
  prior={r[0] for r in data["vocabulary"]}
  groups=json.loads(os.environ["FAKE_GROUPS"]) if "FAKE_GROUPS" in os.environ else [[p] for p in positions]
  if mode in ("oversized","schema_oversized"):
   groups=[positions]
   if mode=="schema_oversized":
    schema=json.loads(pathlib.Path(sys.argv[sys.argv.index("--output-schema")+1]).read_text())
    cap=schema["properties"]["assignments"]["items"]["properties"]["positions"]["maxItems"]
    groups=[positions[i:i+cap] for i in range(0,len(positions),cap)]
  assignments=[]
  for i,g in enumerate(groups):
   selected=[p for p in g if p in positions]
   if selected:
    key=f"subject-{i}" if "FAKE_GROUPS" in os.environ or mode in ("oversized","schema_oversized") else f"solo-{g[0]}"
    assignments.append({"key":key,"positions":selected})
  output={"schema_version":1,"new_subjects":[{"key":r["key"],"definition":"A neutral user-facing subject"} for r in assignments if r["key"] not in prior],"assignments":assignments}
  if mode=="semantic_red" and assignments:assignments[0]["key"]="unregistered-subject"
  if mode=="unused":output["new_subjects"].append({"key":"unused-proposal","definition":"A valid unused neutral subject"})
  if mode=="byte_oversize" and output["new_subjects"]:output["new_subjects"][0]["definition"]="😀"*80
 else:
  records=[]
  for i,row in enumerate(data["o"]):
   records.append({"observation_id":row[0],"accounting_status":"verify",
    "reason":"Original cited evidence can change the Question","falsifier":None})
  ordinary=[{**r,"role":"minority"} for r in records[1:]]
  clusters=[{"route":"source_verification","required_verification_depth":"source_context",
   "requested_source_relationships":["replacement_owner","negative_case"],"reason":"Question-sensitive conditions differ",
   "ordinary":ordinary,"merge_sets":[{"canonical":records[0],"merged":[]}]}] if records else []
  output={"schema_version":3,"clusters":clusters,"unclustered_records":[],
   "unresolved_records":[{"unresolved_id":r[0],"disposition":"preserved","reason":"Local analysis remains unresolved"} for r in data["u"]],
   "shard_records":[{"receipt_id":r[0],"disposition":"accounted","reason":"Explicit overflow preserved"} for r in data["r"]]}
  if mode=="semantic_red" and records:records[0]["accounting_status"]="blocked"
  if "FAKE_DEFECT" in os.environ:
   field,defect=os.environ["FAKE_DEFECT"].split(":")
   if field=="records":
    rows=clusters[0]["ordinary"]
    if defect=="missing":rows.pop() if rows else clusters[0]["merge_sets"].clear()
    elif defect=="duplicate":rows[-1]["observation_id"]=records[0]["observation_id"]
    else:(rows[-1] if rows else records[0])["observation_id"]="foreign"
   else:
    rows=output[field]
    if defect=="missing":rows.pop()
    elif defect=="duplicate":rows[-1]=dict(rows[0])
    else:
     key={"unresolved_records":"unresolved_id","shard_records":"receipt_id"}[field]
     rows[-1][key]="foreign"
  if os.environ.get("FAKE_WIRE_OUTPUT"):
   output=json.loads(pathlib.Path(os.environ["FAKE_WIRE_OUTPUT"]).read_text())
 if mode=="empty":result.write_bytes(b"")
 elif mode=="invalid":result.write_text("{")
 elif mode=="schema_invalid":result.write_text("{}")
 elif mode!="no_result":result.write_text(json.dumps(output))
 started={"type":"thread.started","thread_id":f"thread-{idx}"}
 if mode=="model_mismatch":started["model"]="wrong"
 if mode=="effort_mismatch":started["effort"]="medium"
 print(json.dumps(started))
 if mode=="tool":print(json.dumps({"type":"item.completed","item":{"type":"command_execution"}}))
 if mode!="no_usage":print(json.dumps({"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":20,"reasoning_output_tokens":5}}))
else:sys.exit("unexpected command")
'''

if __name__ == "__main__":
    unittest.main()
