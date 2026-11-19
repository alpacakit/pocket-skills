"""Bind verification records to one current green queue before any action."""

from __future__ import annotations

import argparse

from reduction_jobs import REDUCTION_PLAN_SCHEMA_VERSION
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from scan_artifacts import (
    load_json_artifact,
    path_is_within,
    render_json,
    snapshot_file_errors,
    write_json_atomic,
)
from verification_contract import ACTION_VALIDATION_SCHEMA_VERSION, action_gate_errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("plan", help="current green reduction/plan.json")
    parser.add_argument("records", help="verification-records JSON array or object")
    parser.add_argument("--output", help="Optional bound action-validation JSON path")
    return parser.parse_args()


def _id_counts(items: Any, key: str) -> Counter[str]:
    if not isinstance(items, list):
        return Counter()
    return Counter(
        item.get(key)
        for item in items
        if isinstance(item, dict) and isinstance(item.get(key), str)
    )


def _binding_errors(
    records: Any,
    queue_entries: Any,
    *,
    plan_sha256: str,
    queue_sha256: str,
    source_snapshot_id: Any,
    reduction_dir: Path,
) -> list[str]:
    if not isinstance(records, list) or not isinstance(queue_entries, list):
        return ["verification records or queue are not arrays"]
    errors: list[str] = []
    queue_counts = _id_counts(queue_entries, "cluster_id")
    record_counts = _id_counts(records, "cluster_id")
    if any(count != 1 for count in queue_counts.values()):
        errors.append("green queue cluster ids are not unique")
    if any(count != 1 for count in record_counts.values()):
        errors.append("verification record cluster ids are not unique")
    missing = sorted((queue_counts - record_counts).elements())
    unexpected = sorted((record_counts - queue_counts).elements())
    if missing:
        errors.append(f"verification records omit queued clusters: {missing}")
    if unexpected:
        errors.append(f"verification records name unqueued clusters: {unexpected}")

    verification_ids = _id_counts(records, "verification_id")
    if sum(verification_ids.values()) != len(records) or any(
        count != 1 for count in verification_ids.values()
    ):
        errors.append("verification ids must be present and unique")
    queue_by_id = {
        item["cluster_id"]: item
        for item in queue_entries
        if isinstance(item, dict) and isinstance(item.get("cluster_id"), str)
    }
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        label = f"verification_record[{index}]"
        cluster_id = record.get("cluster_id")
        entry = queue_by_id.get(cluster_id)
        if entry is None:
            continue
        if record.get("verification_queue_sha256") != queue_sha256:
            errors.append(f"{label} verification queue binding mismatch")
        if record.get("source_snapshot_id") != source_snapshot_id:
            errors.append(f"{label} source snapshot binding mismatch")
        if record.get("required_depth") != entry.get("required_verification_depth"):
            errors.append(f"{label} required depth differs from green queue")
        dimensions = record.get("checked_dimensions")
        if isinstance(dimensions, dict):
            for relationship in entry.get("requested_source_relationships", []):
                if dimensions.get(relationship) != "checked":
                    errors.append(
                        f"{label} queued relationship is not checked: {relationship}"
                    )

        bundle_path = reduction_dir / "verification-inputs" / f"{cluster_id}.json"
        try:
            bundle_artifact = load_json_artifact(bundle_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{label} cannot read bound verification bundle: {exc}")
            continue
        if record.get("verification_bundle_sha256") != bundle_artifact.sha256:
            errors.append(f"{label} verification bundle binding mismatch")
        bundle = bundle_artifact.value
        expected_bundle = {
            "cluster_id": cluster_id,
            "reduction_plan_sha256": plan_sha256,
            "verification_queue_sha256": queue_sha256,
            "source_snapshot_id": source_snapshot_id,
            "required_verification_depth": entry.get("required_verification_depth"),
            "requested_source_relationships": entry.get(
                "requested_source_relationships"
            ),
        }
        if not isinstance(bundle, dict):
            errors.append(f"{label} verification bundle is not an object")
            continue
        for field, expected in expected_bundle.items():
            if bundle.get(field) != expected:
                errors.append(f"{label} verification bundle {field} mismatch")
    return errors


def main() -> int:
    args = parse_args()
    plan_path = Path(args.plan).expanduser().resolve()
    plan_artifact = load_json_artifact(plan_path)
    plan = plan_artifact.value
    if not isinstance(plan, dict) or plan.get("schema_version") != REDUCTION_PLAN_SCHEMA_VERSION or plan.get("status") != "ready":
        raise SystemExit("Reduction plan is not ready")
    reduction_dir = plan_path.parent
    validation_artifact = load_json_artifact(reduction_dir / "validation.json")
    validation = validation_artifact.value
    if (
        not isinstance(validation, dict)
        or validation.get("valid") is not True
        or validation.get("status") != "green"
        or validation.get("reduction_plan_sha256") != plan_artifact.sha256
    ):
        raise SystemExit("Reduction validation is not current and green")
    queue_artifact = load_json_artifact(reduction_dir / "verification-queue.json")
    if queue_artifact.sha256 != validation.get("derived_artifacts", {}).get(
        "verification-queue.json"
    ):
        raise SystemExit("Verification queue hash does not match green validation")
    queue = queue_artifact.value
    queue_entries = queue.get("queue") if isinstance(queue, dict) else None
    if (
        not isinstance(queue, dict)
        or queue.get("reduction_plan_sha256") != plan_artifact.sha256
    ):
        raise SystemExit("Verification queue does not bind the current plan")

    scan_plan_artifact = load_json_artifact(Path(plan["bindings"]["scan_plan_path"]))
    if scan_plan_artifact.sha256 != plan["bindings"]["scan_plan_sha256"]:
        raise SystemExit("Bound scan plan is stale")
    scan_plan = scan_plan_artifact.value
    if not isinstance(scan_plan, dict):
        raise SystemExit("Bound scan plan is invalid")
    target_root = Path(scan_plan["root"]).expanduser().resolve()
    snapshot_errors = snapshot_file_errors(scan_plan, target_root)
    if snapshot_errors:
        raise SystemExit("Source snapshot changed: " + "; ".join(snapshot_errors[:3]))

    records_path = Path(args.records).expanduser().resolve()
    records_artifact = load_json_artifact(records_path)
    value = records_artifact.value
    records = value.get("records") if isinstance(value, dict) else value
    packet_artifact = load_json_artifact(Path(plan["bindings"]["packet_path"]))
    if packet_artifact.sha256 != plan["bindings"]["packet_sha256"]:
        raise SystemExit("Bound review packet is stale")
    packet = packet_artifact.value
    source_snapshot_id = packet.get("snapshot_id") if isinstance(packet, dict) else None

    errors = action_gate_errors(records)
    errors.extend(
        _binding_errors(
            records,
            queue_entries,
            plan_sha256=plan_artifact.sha256,
            queue_sha256=queue_artifact.sha256,
            source_snapshot_id=source_snapshot_id,
            reduction_dir=reduction_dir,
        )
    )
    report = {
        "schema_version": ACTION_VALIDATION_SCHEMA_VERSION,
        "valid": not errors,
        "reduction_plan_sha256": plan_artifact.sha256,
        "verification_queue_sha256": queue_artifact.sha256,
        "source_snapshot_id": source_snapshot_id,
        "records_path": str(records_path),
        "records_sha256": records_artifact.sha256,
        "record_count": len(records) if isinstance(records, list) else 0,
        "errors": errors,
    }
    if args.output:
        output = Path(args.output).expanduser().resolve()
        allowed = (reduction_dir / "verification-results").resolve()
        if not path_is_within(output, allowed) or path_is_within(output, target_root):
            raise SystemExit(
                f"Action validation output must be inside the bound directory: {allowed}"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(output, report)
    print(render_json(report), end="")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
