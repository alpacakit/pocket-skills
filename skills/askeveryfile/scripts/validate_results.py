"""Prove that every planned shard was delivered and returned."""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scan_artifacts import (
    artifact_manifest_sha256,
    load_json_artifact,
    read_json,
    sha256_bytes,
    sha256_text,
    snapshot_file_errors,
    write_text_atomic,
)
from scan_contract import (
    DELIVERY_MODE,
    VALIDATION_SCHEMA_VERSION,
    analysis_shape_errors,
    plan_contract_errors,
    result_identity_errors,
    scout_run_policy_errors,
    shard_identity_errors,
)
from scan_report import render_scan_md
from scout_delivery import build_scout_prompt, extract_line_quote
from scout_policy import load_scout_policy


@dataclass
class ValidationState:
    scout_policy: dict[str, str] | None = None
    errors: list[str] = field(default_factory=list)
    delivered_units: int = 0
    observations: int = 0
    categories: Counter[str] = field(default_factory=Counter)
    scout_run_models: Counter[str] = field(default_factory=Counter)
    scout_run_threads: set[str] = field(default_factory=set)
    result_artifacts: dict[str, str] = field(default_factory=dict)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate scan snapshot, delivery, and result coverage.",
        allow_abbrev=False,
    )
    parser.add_argument("plan", help="plan.json created by plan_scan.py")
    return parser.parse_args()


def validate_analysis(
    analysis: Any,
    delivered_units: dict[str, list[dict[str, Any]]],
    label: str,
    state: ValidationState,
) -> None:
    """Prove every citation was program-generated from the delivered text."""
    state.errors.extend(
        f"{label}: {error}"
        for error in analysis_shape_errors(analysis, materialized=True)
    )
    if not isinstance(analysis, dict):
        return
    observations = analysis.get("observations")
    if not isinstance(observations, list):
        return
    state.observations += len(observations)
    for index, observation in enumerate(observations):
        observation_label = f"{label}: observation[{index}]"
        if not isinstance(observation, dict):
            continue
        category = observation.get("category")
        if isinstance(category, str) and category:
            state.categories[category] += 1
        evidence = observation.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            continue
        for evidence_index, item in enumerate(evidence):
            evidence_label = f"{observation_label}.evidence[{evidence_index}]"
            if not isinstance(item, dict):
                state.errors.append(f"{evidence_label} is not an object")
                continue
            try:
                expected_quote = extract_line_quote(item, delivered_units)
            except ValueError as exc:
                state.errors.append(f"{evidence_label} {exc}")
                continue
            if item.get("locator") != f"L{item['line']}":
                state.errors.append(
                    f"{evidence_label} locator was not program-generated"
                )
            if item.get("quote") != expected_quote:
                state.errors.append(f"{evidence_label} quote was not program-extracted")


def validate_snapshot(plan: dict[str, Any], root: Path, state: ValidationState) -> None:
    state.errors.extend(snapshot_file_errors(plan, root))


def match_result_paths(
    plan: dict[str, Any], scan_dir: Path, state: ValidationState
) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    expected_shards = {item["shard_id"]: item for item in plan["shards"]}
    actual_result_paths = {
        path.stem: path for path in (scan_dir / "results").glob("shard-*.json")
    }
    missing = sorted(set(expected_shards) - set(actual_result_paths))
    unexpected = sorted(set(actual_result_paths) - set(expected_shards))
    if missing:
        state.errors.append(f"Missing result shards: {', '.join(missing)}")
    if unexpected:
        state.errors.append(f"Unexpected result shards: {', '.join(unexpected)}")
    return expected_shards, actual_result_paths


def validate_result_identity(
    plan: dict[str, Any], shard_id: str, result: dict[str, Any], state: ValidationState
) -> None:
    state.errors.extend(
        f"{shard_id}: {error}"
        for error in result_identity_errors(plan, result, shard_id)
    )


def validate_schema(
    scan_dir: Path, shard_id: str, result: dict[str, Any], state: ValidationState
) -> None:
    schema_path = scan_dir / "schemas" / f"{shard_id}.schema.json"
    try:
        schema_sha256 = sha256_bytes(schema_path.read_bytes())
    except OSError as exc:
        state.errors.append(f"{shard_id}: cannot read output schema: {exc}")
        return
    if result.get("schema_sha256") != schema_sha256:
        state.errors.append(f"{shard_id}: output schema hash mismatch")


def validate_coverage(
    shard: dict[str, Any], result: dict[str, Any], state: ValidationState
) -> None:
    label = shard["shard_id"]
    expected_units = {unit["unit_id"]: unit for unit in shard["units"]}
    coverage = result.get("coverage")
    if not isinstance(coverage, list):
        state.errors.append(f"{label}: coverage is not an array")
        coverage = []
    actual_units: dict[str, dict[str, Any]] = {}
    for receipt in coverage:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("unit_id"), str):
            state.errors.append(f"{label}: invalid coverage receipt")
            continue
        unit_id = receipt["unit_id"]
        if unit_id in actual_units:
            state.errors.append(f"{label}: duplicate coverage receipt {unit_id}")
        actual_units[unit_id] = receipt
    shared_ids = set(expected_units) & set(actual_units)
    absent_units = sorted(set(expected_units) - set(actual_units))
    extra_units = sorted(set(actual_units) - set(expected_units))
    if absent_units:
        state.errors.append(
            f"{label}: missing delivered units: {', '.join(absent_units)}"
        )
    if extra_units:
        state.errors.append(
            f"{label}: unexpected delivered units: {', '.join(extra_units)}"
        )
    for unit_id in shared_ids:
        expected = expected_units[unit_id]
        receipt = actual_units[unit_id]
        for key in ("path", "start_line", "end_line", "file_sha256"):
            if receipt.get(key) != expected.get(key):
                state.errors.append(f"{label}: coverage {unit_id} has {key} mismatch")
        if receipt.get("delivery") != DELIVERY_MODE:
            state.errors.append(f"{label}: coverage {unit_id} lacks full-text delivery")
    state.delivered_units += len(shared_ids)


def validate_scout_run(
    result: dict[str, Any], label: str, state: ValidationState
) -> None:
    scout_run = result.get("scout_run")
    state.errors.extend(
        f"{label}: {error}" for error in scout_run_policy_errors(scout_run, state.scout_policy)
    )
    if not isinstance(scout_run, dict):
        return
    model = scout_run.get("model")
    thread_id = scout_run.get("thread_id")
    if isinstance(model, str) and model:
        state.scout_run_models[model] += 1
    if isinstance(thread_id, str) and thread_id in state.scout_run_threads:
        state.errors.append(f"{label}: scout_run thread_id reused")
    elif isinstance(thread_id, str) and thread_id:
        state.scout_run_threads.add(thread_id)


def validate_retained_prompt(
    scan_dir: Path,
    shard_id: str,
    expected_prompt: str,
    state: ValidationState,
) -> None:
    """Check optional new gzip and legacy plain-text prompt artifacts."""
    artifacts = (
        (scan_dir / "prompts" / f"{shard_id}.md.gz", True),
        (scan_dir / "prompts" / f"{shard_id}.md", False),
    )
    for path, compressed in artifacts:
        if not path.exists():
            continue
        try:
            if compressed:
                prompt_text = gzip.decompress(path.read_bytes()).decode("utf-8")
            else:
                prompt_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            state.errors.append(
                f"{shard_id}: cannot read retained prompt {path.name}: {exc}"
            )
            continue
        if prompt_text != expected_prompt:
            state.errors.append(
                f"{shard_id}: retained prompt {path.name} differs from the "
                "question and delivered units"
            )


def validate_prompt_delivery(
    scan_dir: Path,
    shard: dict[str, Any],
    result: dict[str, Any],
    state: ValidationState,
) -> dict[str, list[dict[str, Any]]]:
    try:
        expected_prompt, delivered = build_scout_prompt(shard)
    except (OSError, RuntimeError) as exc:
        state.errors.append(
            f"{shard['shard_id']}: cannot rebuild delivered prompt: {exc}"
        )
        return {}
    if result.get("prompt_sha256") != sha256_text(expected_prompt):
        state.errors.append(f"{shard['shard_id']}: reconstructed prompt hash mismatch")
    validate_retained_prompt(scan_dir, shard["shard_id"], expected_prompt, state)
    return delivered


def validate_shard(
    plan: dict[str, Any],
    scan_dir: Path,
    shard_id: str,
    result_path: Path,
    state: ValidationState,
) -> None:
    try:
        shard = read_json(scan_dir / "shards" / f"{shard_id}.json")
        result_artifact = load_json_artifact(result_path)
        state.result_artifacts[result_path.relative_to(scan_dir).as_posix()] = (
            result_artifact.sha256
        )
        result = result_artifact.value
    except (OSError, json.JSONDecodeError) as exc:
        state.errors.append(f"{shard_id}: cannot read shard or result JSON: {exc}")
        return
    if not isinstance(shard, dict) or not isinstance(result, dict):
        state.errors.append(f"{shard_id}: shard or result JSON is not an object")
        return
    state.errors.extend(
        f"{shard_id}: {error}" for error in shard_identity_errors(plan, shard, shard_id)
    )
    validate_result_identity(plan, shard_id, result, state)
    validate_schema(scan_dir, shard_id, result, state)
    validate_coverage(shard, result, state)
    validate_scout_run(result, shard_id, state)
    delivered = validate_prompt_delivery(scan_dir, shard, result, state)
    validate_analysis(result.get("analysis"), delivered, shard_id, state)


def main() -> int:
    args = parse_args()
    plan_path = Path(args.plan).expanduser().resolve()
    plan_artifact = load_json_artifact(plan_path)
    plan = plan_artifact.value
    plan_errors = plan_contract_errors(plan)
    if plan_errors:
        raise SystemExit("Invalid plan: " + "; ".join(plan_errors))
    scan_dir = plan_path.parent
    root = Path(plan["root"])
    state = ValidationState()
    try:
        state.scout_policy = load_scout_policy(scan_dir)
    except ValueError as exc:
        state.errors.append(str(exc))
    validate_snapshot(plan, root, state)
    expected_shards, result_paths = match_result_paths(plan, scan_dir, state)
    for shard_id in sorted(set(expected_shards) & set(result_paths)):
        validate_shard(plan, scan_dir, shard_id, result_paths[shard_id], state)
    expected_units_total = sum(item["unit_count"] for item in plan["shards"])
    if state.delivered_units != expected_units_total:
        state.errors.append(
            f"Delivered {state.delivered_units}/{expected_units_total} planned units"
        )

    report = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "scan_id": plan["scan_id"],
        "plan_sha256": plan_artifact.sha256,
        "valid": not state.errors,
        "included_text_files": plan["coverage"]["included_text_files"],
        "excluded_entries": plan["coverage"]["excluded_entries"],
        "shards": len(expected_shards),
        "delivered_units": state.delivered_units,
        "expected_units": expected_units_total,
        "observations": state.observations,
        "categories": dict(sorted(state.categories.items())),
        "scout_run_models": dict(sorted(state.scout_run_models.items())),
        "result_artifacts": dict(sorted(state.result_artifacts.items())),
        "results_sha256": artifact_manifest_sha256(state.result_artifacts),
        "errors": state.errors,
        "warnings": [],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    write_text_atomic(scan_dir / "validation.json", rendered)
    render_scan_md(plan_path)
    print(rendered, end="")
    return 0 if not state.errors else 1


if __name__ == "__main__":
    sys.exit(main())
