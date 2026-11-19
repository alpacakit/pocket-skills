"""Collect every validated scout observation into one complete review packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scan_artifacts import (
    artifact_manifest_sha256,
    load_json_artifact,
    read_json,
    snapshot_file_errors,
    write_json_atomic,
)
from scan_contract import (
    REVIEW_PACKET_SCHEMA_VERSION,
    VALIDATION_SCHEMA_VERSION,
    plan_contract_errors,
    receipt_id,
    unresolved_id,
)
from scan_report import render_scan_md
from scan_workspace import WorkspaceError, review_packet_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("plan", help="plan.json for a green scan")
    parser.add_argument(
        "--output",
        help="Output path (must be <scan-dir>/review-packet.json)",
    )
    return parser.parse_args()


def load_results(scan_dir: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Read each result once so the packet uses the exact bytes that are hashed."""
    artifacts: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    for path in sorted((scan_dir / "results").glob("shard-*.json")):
        artifact = load_json_artifact(path)
        artifacts[path.relative_to(scan_dir).as_posix()] = artifact.sha256
        value = artifact.value
        if not isinstance(value, dict):
            raise TypeError(f"Result is not an object: {path}")
        results.append(value)
    return artifacts, results


def collect_observations(
    results: list[dict[str, Any]], root: Path, source_hashes: dict[str, str]
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for result in results:
        for index, observation in enumerate(
            result["analysis"]["observations"], start=1
        ):
            evidence = []
            for item in observation["evidence"]:
                evidence.append(
                    {
                        "path": item["path"],
                        "absolute_path": str(root / item["path"]),
                        "file_sha256": source_hashes[item["path"]],
                        "locator": item["locator"],
                        "quote": item["quote"],
                    }
                )
            observations.append(
                {
                    "observation_id": f"{result['shard_id']}:{index}",
                    "category": observation["category"],
                    "title": observation["title"],
                    "claim": observation["claim"],
                    "why_it_matters": observation["why_it_matters"],
                    "scout_confidence": observation["confidence"],
                    "evidence": evidence,
                }
            )
    return observations


def collect_unresolved_questions(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve unresolved-question multiplicity with position-based ids."""
    unresolved: list[dict[str, Any]] = []
    for result in results:
        shard_id = result["shard_id"]
        for ordinal, value in enumerate(
            result["analysis"]["unresolved_questions"], start=1
        ):
            unresolved.append(
                {
                    "unresolved_id": unresolved_id(shard_id, ordinal),
                    "shard_id": shard_id,
                    "ordinal": ordinal,
                    "text": value,
                }
            )
    return unresolved


def collect_shard_receipts(
    results: list[dict[str, Any]], artifacts: dict[str, str]
) -> list[dict[str, Any]]:
    """Retain one successful receipt even when a shard found nothing."""
    receipts: list[dict[str, Any]] = []
    for result in results:
        shard_id = result["shard_id"]
        observation_ids = [
            f"{shard_id}:{ordinal}"
            for ordinal, _ in enumerate(
                result["analysis"]["observations"], start=1
            )
        ]
        unresolved_ids = [
            unresolved_id(shard_id, ordinal)
            for ordinal, _ in enumerate(
                result["analysis"]["unresolved_questions"], start=1
            )
        ]
        overflow = result["analysis"].get("overflowed")
        overflow_state = (
            str(overflow).lower()
            if result.get("schema_version") != 5 and type(overflow) is bool
            else "unknown"
        )
        result_name = f"results/{shard_id}.json"
        receipts.append(
            {
                "receipt_id": receipt_id(shard_id),
                "shard_id": shard_id,
                "result_sha256": artifacts[result_name],
                "status": result["status"],
                "observation_ids": observation_ids,
                "unresolved_ids": unresolved_ids,
                "overflow_state": overflow_state,
            }
        )
    return receipts


def validate_output_path(
    output: Path,
    scan_id: str,
) -> None:
    """Allow only a prior packet for this scan to be regenerated."""
    if not output.exists():
        return
    if not output.is_file():
        raise SystemExit(
            f"Review packet output already exists and is not a file: {output}"
        )
    try:
        existing = read_json(output)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"Refusing to overwrite a non-packet output file: {output}: {exc}"
        ) from exc
    replaceable = (
        isinstance(existing, dict)
        and existing.get("schema_version") in (3, REVIEW_PACKET_SCHEMA_VERSION)
        and existing.get("scan_id") == scan_id
        and isinstance(existing.get("plan_sha256"), str)
        and isinstance(existing.get("results_sha256"), str)
        and isinstance(existing.get("observations"), list)
    )
    if not replaceable:
        raise SystemExit(f"Refusing to overwrite a non-packet output file: {output}")


def load_green_validation(
    scan_dir: Path, plan: dict[str, Any], plan_sha256: str
) -> tuple[Path, dict[str, Any]]:
    """Refuse to build unless a current, green validation covers this plan."""
    validation_path = scan_dir / "validation.json"
    validation = read_json(validation_path)
    if (
        not isinstance(validation, dict)
        or validation.get("schema_version") != VALIDATION_SCHEMA_VERSION
    ):
        raise SystemExit(f"Unsupported validation report: {validation_path}")
    if not validation.get("valid"):
        raise SystemExit(f"Validation is not green: {validation_path}")
    if validation.get("scan_id") != plan.get("scan_id"):
        raise SystemExit("Plan and validation scan ids do not match")
    if validation.get("plan_sha256") != plan_sha256:
        raise SystemExit("The plan changed after validation; validate again")
    return validation_path, validation


def load_validated_results(
    scan_dir: Path, validation: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    """Return the results only while their exact bytes are the validated ones."""
    validated_artifacts = validation.get("result_artifacts")
    if not isinstance(validated_artifacts, dict) or not all(
        isinstance(name, str) and isinstance(digest, str)
        for name, digest in validated_artifacts.items()
    ):
        raise SystemExit("Validation result artifact manifest is invalid")
    validated_digest = artifact_manifest_sha256(validated_artifacts)
    if validation.get("results_sha256") != validated_digest:
        raise SystemExit("Validation result artifact digest is invalid")
    try:
        current_artifacts, results = load_results(scan_dir)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise SystemExit(f"Cannot read current result artifacts: {exc}") from exc
    if current_artifacts != validated_artifacts:
        raise SystemExit("Result artifacts changed since validation; validate again")
    return validated_digest, results


def main() -> int:
    args = parse_args()
    plan_path = Path(args.plan).expanduser().resolve()
    scan_dir = plan_path.parent
    plan_artifact = load_json_artifact(plan_path)
    plan_sha256 = plan_artifact.sha256
    plan = plan_artifact.value
    plan_errors = plan_contract_errors(plan)
    if plan_errors:
        raise SystemExit("Invalid plan: " + "; ".join(plan_errors))
    validation_path, validation = load_green_validation(scan_dir, plan, plan_sha256)

    try:
        output = review_packet_output(scan_dir, args.output, Path(plan["root"]))
    except WorkspaceError as exc:
        raise SystemExit(str(exc)) from exc
    validate_output_path(output, plan["scan_id"])
    snapshot_errors = snapshot_file_errors(plan, Path(plan["root"]))
    if snapshot_errors:
        raise SystemExit(
            "Source files changed after planning; replan and validate again: "
            + "; ".join(snapshot_errors[:3])
        )

    validated_digest, results = load_validated_results(scan_dir, validation)
    source_hashes = {item["path"]: item["sha256"] for item in plan["files"]}
    observations = collect_observations(results, Path(plan["root"]), source_hashes)
    unresolved_questions = collect_unresolved_questions(results)
    shard_receipts = collect_shard_receipts(results, validation["result_artifacts"])
    expected = validation.get("observations")
    if len(observations) != expected:
        raise SystemExit(
            "Observation count mismatch: "
            f"validation={expected}, packet={len(observations)}"
        )
    packet = {
        "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "scan_id": plan["scan_id"],
        "snapshot_id": plan["snapshot_id"],
        "root": plan["root"],
        "question": plan["question"],
        "question_sha256": plan["question_sha256"],
        "plan_path": str(plan_path),
        "plan_sha256": plan_sha256,
        "validation_path": str(validation_path),
        "results_sha256": validated_digest,
        "validated_observation_count": expected,
        "validated_unresolved_count": len(unresolved_questions),
        "validated_shard_count": len(shard_receipts),
        "observations": observations,
        "unresolved_questions": unresolved_questions,
        "shard_receipts": shard_receipts,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output, packet)
    scan_md = render_scan_md(plan_path, review_packet_path=output)
    print(
        json.dumps(
            {
                "output": str(output),
                "observations": len(observations),
                "unresolved_questions": len(unresolved_questions),
                "shard_receipts": len(shard_receipts),
                "scan": str(scan_md),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
