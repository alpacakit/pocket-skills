"""Plan immutable labeled blocks before atomic quoted accounting."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from bound_reduction import load_packet
from reducer_runtime_policy import reducer_policy_value
from reduction_jobs import build_plan, REDUCTION_PLAN_SCHEMA_VERSION
from reduction_semantic_state import semantic_experiment_sha256
from reduction_inspection import ReductionProgress, render_reduction_inspection
from scan_artifacts import load_json_artifact, write_json_atomic
from scan_workspace import reduction_output


def check_sibling_experiments(scan: Path, output: Path, plan: dict) -> None:
    fingerprint = semantic_experiment_sha256(plan)
    for sibling in sorted(scan.iterdir()):
        previous = sibling / "plan.json"
        if sibling == output or not sibling.is_dir() or not previous.is_file():
            continue
        value = load_json_artifact(previous).value
        if isinstance(value, dict) and value.get("schema_version") == REDUCTION_PLAN_SCHEMA_VERSION:
            if value.get("mode") != "labeled":
                raise ValueError("Invalid current-contract sibling plan")
            if semantic_experiment_sha256(value) == fingerprint:
                raise ValueError(f"Refusing an identical semantic reduction experiment: {previous}")
        elif isinstance(value, dict) and value.get("mode") == "full" and value.get("bindings", {}).get("packet_sha256") == plan["bindings"]["packet_sha256"]:
            # No old execution migration. This authorized cutover is from an
            # unlaunched overflow plan; do not silently inherit/reset attempts.
            if value.get("status") != "full_input_overflow" or any((sibling / "attempts").rglob("*")) or any((sibling / "results").rglob("*")) or (sibling / "blocked.json").exists():
                raise ValueError("Legacy plan is not an unlaunched overflow cutover")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("packet", help="Green review-packet.json (v4)")
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--context-catalog", required=True, help="Exact runtime model catalog")
    parser.add_argument("--output-dir", help="New reduction[-NAME] directly inside the scan directory")
    args = parser.parse_args()
    policy = reducer_policy_value(args.model, args.effort)
    packet_path = Path(args.packet).expanduser().resolve()
    catalog_path = Path(args.context_catalog).expanduser().resolve()
    packet, bindings, catalog = load_packet(packet_path, catalog_path)
    scan = Path(bindings["scan_plan_path"]).parent
    output = reduction_output(scan, args.output_dir, Path(packet["root"]))
    if output.exists():
        raise ValueError(f"Reduction directory already exists: {output}")
    plan = build_plan(packet, bindings, catalog, policy)
    check_sibling_experiments(scan, output, plan)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=scan))
    try:
        write_json_atomic(staging / "plan.json", plan)
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    render_reduction_inspection(output, plan, plan["status"],
                                progress=ReductionProgress(labels_completed=0, labels_total=len(plan["label_blocks"]), partition_status="pending labels", completed=0, semantic_red=0))
    print(json.dumps({"status": plan["status"], "plan": str(output / "plan.json"),
                      "inspection": str(output / "inspection.md"),
                      "max_group_positions": plan["group_capacity"]["max_group_positions"]}))
    return 0 if plan["status"] == "ready" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        sys.exit(str(exc))
