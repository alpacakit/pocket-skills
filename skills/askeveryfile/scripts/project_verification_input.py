"""Project one closed queue cluster into a narrow source-verification bundle."""

from __future__ import annotations

import argparse

from reduction_jobs import REDUCTION_PLAN_SCHEMA_VERSION
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from verification_contract import VERIFICATION_BUNDLE_SCHEMA_VERSION
from scan_artifacts import (
    load_json_artifact,
    path_is_within,
    snapshot_file_errors,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("plan", help="green reduction/plan.json")
    parser.add_argument("cluster_id", help="one cluster id from verification queue")
    parser.add_argument("--output", required=True, help="new bundle JSON path")
    return parser.parse_args()


def _unique_map(items: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise SystemExit(f"{label} is not an array")
    values: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(key), str):
            raise SystemExit(f"{label} contains an invalid {key}")
        counts[item[key]] += 1
        values[item[key]] = item
    duplicates = sorted(value for value, count in counts.items() if count != 1)
    if duplicates:
        raise SystemExit(f"{label} contains duplicate ids: {duplicates}")
    return values


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
    scan_plan_path = Path(plan["bindings"]["scan_plan_path"]).expanduser().resolve()
    scan_plan_artifact = load_json_artifact(scan_plan_path)
    if scan_plan_artifact.sha256 != plan["bindings"]["scan_plan_sha256"]:
        raise SystemExit("Bound scan plan is stale")
    scan_plan = scan_plan_artifact.value
    if not isinstance(scan_plan, dict):
        raise SystemExit("Bound scan plan is invalid")
    target_root = Path(scan_plan["root"]).expanduser().resolve()
    snapshot_errors = snapshot_file_errors(scan_plan, target_root)
    if snapshot_errors:
        raise SystemExit("Source snapshot changed: " + "; ".join(snapshot_errors[:3]))
    queue_path = reduction_dir / "verification-queue.json"
    queue_artifact = load_json_artifact(queue_path)
    expected_queue_hash = validation.get("derived_artifacts", {}).get(
        "verification-queue.json"
    )
    if queue_artifact.sha256 != expected_queue_hash:
        raise SystemExit("Verification queue hash does not match green validation")
    queue_value = queue_artifact.value
    queue_entries = queue_value.get("queue") if isinstance(queue_value, dict) else None
    if not isinstance(queue_entries, list):
        raise SystemExit("Verification queue is invalid")
    matches = [
        item
        for item in queue_entries
        if isinstance(item, dict) and item.get("cluster_id") == args.cluster_id
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Queue must contain cluster exactly once: {args.cluster_id}"
        )
    entry = matches[0]
    if entry.get("route") != "source_verification":
        raise SystemExit("Queue cluster is not routed to source verification")

    packet_path = Path(plan["bindings"]["packet_path"])
    packet_artifact = load_json_artifact(packet_path)
    if packet_artifact.sha256 != plan["bindings"]["packet_sha256"]:
        raise SystemExit("Review packet is stale")
    packet = packet_artifact.value
    packet_records = _unique_map(
        packet.get("observations") if isinstance(packet, dict) else None,
        "observation_id",
        "packet observations",
    )
    members = entry.get("members")
    if not isinstance(members, list):
        raise SystemExit("Queue members are invalid")
    member_ids = [
        item.get("observation_id") if isinstance(item, dict) else None
        for item in members
    ]
    if any(not isinstance(value, str) for value in member_ids):
        raise SystemExit("Queue contains an invalid member id")
    duplicates = sorted(
        key for key, count in Counter(member_ids).items() if count != 1
    )
    if duplicates:
        raise SystemExit(f"Queue contains duplicate member ids: {duplicates}")
    missing_packet = sorted(set(member_ids) - set(packet_records))
    if missing_packet:
        raise SystemExit(
            f"Queue projection is not exact: missing_packet={missing_packet}"
        )
    projected = []
    for member in members:
        observation_id = member["observation_id"]
        record = packet_records[observation_id]
        projected.append(
            {
                "observation_id": observation_id,
                "role": member["role"],
                "record": record,
                "evidence_locators": [
                    {
                        "path": evidence["path"],
                        "file_sha256": evidence["file_sha256"],
                        "locator": evidence["locator"],
                    }
                    for evidence in record["evidence"]
                ],
            }
        )
    if Counter(item["observation_id"] for item in projected) != Counter(member_ids):
        raise SystemExit("Projected bundle does not exactly cover queue members")
    bundle = {
        "schema_version": VERIFICATION_BUNDLE_SCHEMA_VERSION,
        "cluster_id": args.cluster_id,
        "route": "source_verification",
        "required_verification_depth": entry["required_verification_depth"],
        "requested_source_relationships": entry[
            "requested_source_relationships"
        ],
        "reduction_plan_sha256": plan_artifact.sha256,
        "verification_queue_sha256": queue_artifact.sha256,
        "source_snapshot_id": packet["snapshot_id"],
        "question_sha256": packet["question_sha256"],
        "members": projected,
    }
    output = Path(args.output).expanduser().resolve()
    expected_output = (reduction_dir / "verification-inputs" / f"{args.cluster_id}.json").resolve()
    if output != expected_output:
        raise SystemExit(f"Verification bundle must use the bound path: {expected_output}")
    if path_is_within(output, target_root):
        raise SystemExit("Verification bundle output must be outside the read-only target")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as handle:
            json.dump(bundle, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise SystemExit(f"Refusing to overwrite verification bundle: {output}") from exc
    print(
        json.dumps(
            {
                "status": "complete",
                "cluster_id": args.cluster_id,
                "members": len(projected),
                "output": str(output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
