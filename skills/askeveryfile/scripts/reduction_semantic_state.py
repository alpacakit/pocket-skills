"""One experiment-wide Owner for immutable job reuse, semantic cap and locking."""
from __future__ import annotations

import fcntl
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_attempt import AttemptExpectation, PersistedAttemptInspection, inspect_persisted_attempt
from reduction_contract import canonical_sha256
from reduction_jobs import ReductionJob, job_shape_errors, job_closure, REDUCTION_RESULT_SCHEMA_VERSION
from scan_artifacts import load_json_artifact, render_json, sha256_bytes, write_json_exclusive_atomic

SEMANTIC_RED_ATTEMPT_LIMIT = 2


@contextmanager
def reduction_lock(reduction_dir: Path):
    path = reduction_dir / "runner.lock"
    if path.is_symlink():
        raise ValueError("Reduction lock must not be a symlink")
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Reduction runner/validator is already active") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def semantic_experiment_bindings(plan: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": plan["schema_version"], "packet_sha256": plan["bindings"]["packet_sha256"],
            "contracts": plan["contracts"], "policy_sha256": plan["policy_sha256"],
            "label_blocks_sha256": canonical_sha256(plan["label_blocks"])}



def semantic_experiment_sha256(plan: dict[str, Any]) -> str:
    return canonical_sha256(semantic_experiment_bindings(plan))


def input_manifest(plan_path: Path, plan: dict[str, Any], job: ReductionJob) -> dict[str, Any]:
    return {"schema_version": 5, "reduction_plan": {"path": str(plan_path), "sha256": load_json_artifact(plan_path).sha256},
            "bindings": plan["bindings"], "policy_sha256": plan["policy_sha256"], "job": job.descriptor}


def inspect_job_attempts(plan_path: Path, plan: dict[str, Any], job: ReductionJob, *,
                         persist: bool = False) -> tuple[list[PersistedAttemptInspection], list[dict[str, Any]]]:
    """Authenticate transport, re-evaluate closure, and recover missing markers."""
    reduction_dir = plan_path.parent
    root = reduction_dir / "attempts" / job.job_id
    expected_manifest = input_manifest(plan_path, plan, job)
    attempts: list[PersistedAttemptInspection] = []
    closures: list[dict[str, Any]] = []
    if root.is_symlink():
        raise ValueError("Attempt job root must not be a symlink")
    for directory in sorted(root.iterdir()) if root.exists() else []:
        match = re.fullmatch(r"attempt-(\d{4,})", directory.name)
        if directory.is_symlink() or not directory.is_dir() or match is None:
            raise ValueError(f"Invalid immutable attempt directory: {directory}")
        number = int(match[1])
        if number < 1 or directory.name != f"attempt-{number:04d}":
            raise ValueError("Noncanonical immutable attempt number")
        expected_id = f"{job.job_id}-attempt-{number:04d}"
        expected = AttemptExpectation(expected_id, number, reduction_dir, job.prompt, expected_manifest, job.schema)
        attempt = inspect_persisted_attempt(str(directory / "receipt.json"), attempt_root=root, policy=plan["policy"],
                                             allow_failed=True, expected=expected)
        if attempt.errors:
            raise ValueError("Invalid/incomplete immutable attempt: " + "; ".join(attempt.errors))
        attempts.append(attempt)
        if attempt.lifecycle == "interrupted":
            continue
        if attempt.receipt is None:
            raise ValueError("Attempt has no authenticated terminal receipt")
        receipt = attempt.receipt
        closure_path = directory / "closure.json"
        if receipt["status"] != "complete":
            if receipt.get("raw_result_status") == "schema_invalid" and not job_shape_errors(job, attempt.raw_result):
                raise ValueError("Failed attempt hides a shape-valid result")
            if closure_path.exists():
                raise ValueError("Failed transport attempt carries a semantic closure")
            continue
        shape_errors = job_shape_errors(job, attempt.raw_result)
        if shape_errors:
            raise ValueError("Complete attempt is not shape valid: " + "; ".join(shape_errors))
        errors = job_closure(job, attempt.raw_result, plan["group_capacity"]["max_group_positions"])
        value = {"schema_version": 2, "status": "semantic_red" if errors else "green", "job_id": job.job_id,
                 "reduction_plan_sha256": expected_manifest["reduction_plan"]["sha256"],
                 "semantic_experiment_sha256": semantic_experiment_sha256(plan),
                 "receipt_sha256": attempt.receipt_sha256, "result_sha256": attempt.raw_result_sha256,
                 "errors": errors}
        if closure_path.exists():
            if load_json_artifact(closure_path).value != value:
                raise ValueError("Semantic closure artifact is not canonical")
        elif persist:
            write_json_exclusive_atomic(closure_path, value)
        closures.append({"path": str(closure_path), "sha256": sha256_bytes(render_json(value).encode("utf-8")), **value})
    return attempts, closures


@dataclass(frozen=True)
class SemanticStateInspection:
    closures: tuple[dict[str, Any], ...]
    attempts: dict[str, list[PersistedAttemptInspection]]
    blocked: dict[str, Any] | None


def ensure_semantic_state(plan_path: Path, plan: dict[str, Any], jobs: list[ReductionJob], *,
                          persist: bool = True) -> SemanticStateInspection:
    known = {j.job_id for j in jobs}
    attempt_root = plan_path.parent / "attempts"
    if attempt_root.exists() and any(p.name not in known or p.is_symlink() or not p.is_dir() for p in attempt_root.iterdir()):
        raise ValueError("Unknown job attempts outside canonical partition")
    result_root = plan_path.parent / "results"
    if result_root.is_symlink() or attempt_root.is_symlink():
        raise ValueError("Job artifact root must not be a symlink")
    if result_root.exists() and any(p.name not in {f"{k}.json" for k in known} or p.is_symlink() or not p.is_file()
                                    for p in result_root.iterdir()):
        raise ValueError("Unknown results outside canonical prefix/partition")
    attempts: dict[str, list[PersistedAttemptInspection]] = {}
    closures: list[dict[str, Any]] = []
    for job in jobs:
        a, c = inspect_job_attempts(plan_path, plan, job, persist=persist)
        attempts[job.job_id] = a
        closures.extend(c)
    closures.sort(key=lambda c: c["path"])
    red = [c for c in closures if c["status"] == "semantic_red"]
    if len(red) > SEMANTIC_RED_ATTEMPT_LIMIT:
        raise ValueError("Semantic-red count exceeds experiment-wide cap")
    expected_block = None
    if len(red) == SEMANTIC_RED_ATTEMPT_LIMIT:
        expected_block = {"schema_version": 2, "status": "semantic_blocked", "semantic_red_attempt_limit": 2,
                          "reduction_plan_sha256": load_json_artifact(plan_path).sha256,
                          "semantic_experiment_sha256": semantic_experiment_sha256(plan), "semantic_red_attempts": red}
    blocked_path = plan_path.parent / "blocked.json"
    if blocked_path.exists():
        if load_json_artifact(blocked_path).value != expected_block or expected_block is None:
            raise ValueError("Semantic blocked artifact is not canonical")
    elif expected_block is not None and persist:
        write_json_exclusive_atomic(blocked_path, expected_block)
    return SemanticStateInspection(tuple(closures), attempts, expected_block)


def selected_job_result(plan_path: Path, plan: dict[str, Any], job: ReductionJob,
                        attempts: list[PersistedAttemptInspection], *, publish: bool = False) -> dict[str, Any] | None:
    green = [a for a in attempts if a.receipt is not None and a.receipt["status"] == "complete"
             and not job_closure(job, a.raw_result, plan["group_capacity"]["max_group_positions"])]
    result_path = plan_path.parent / "results" / f"{job.job_id}.json"
    if result_path.is_symlink():
        raise ValueError("Result artifact must not be a symlink")
    if not green:
        if result_path.exists():
            raise ValueError("Stored result has no authenticated closure-green attempt")
        return None
    a = green[0]
    expected = {"schema_version": REDUCTION_RESULT_SCHEMA_VERSION, "job_id": job.job_id,
                "reduction_plan_sha256": load_json_artifact(plan_path).sha256, "job": job.descriptor,
                "attempt_id": a.receipt["attempt_id"], "attempt_receipt_path": str(a.receipt_path),
                "attempt_receipt_sha256": a.receipt_sha256, "result_sha256": a.raw_result_sha256,
                "output": a.raw_result}
    if result_path.exists():
        if load_json_artifact(result_path).value != expected:
            raise ValueError("Stored result differs from immutable raw attempt/bindings")
    elif publish:
        write_json_exclusive_atomic(result_path, expected)
    else:
        return None
    return expected
