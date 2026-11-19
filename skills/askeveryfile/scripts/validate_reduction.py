"""Publish only complete labeled reduction closure and validator-owned queue."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from bound_reduction import inspect_bound_reduction, require_reduction_location
from accounting_wire import accounting_signals
from reduction_execution import inspect_execution
from reduction_contract import derive_public_clusters, reduction_closure
from reduction_jobs import REDUCTION_PLAN_SCHEMA_VERSION, assemble_outputs
from reduction_semantic_state import (reduction_lock, inspect_job_attempts, selected_job_result, ensure_semantic_state)
from reduction_inspection import ReductionProgress, render_reduction_inspection
from scan_artifacts import (load_json_artifact, render_json, sha256_file, write_json_atomic)

ACTION_ARTIFACTS = ("accounting-ledger.json", "verification-queue.json", "summary.json")

def _publish_green(
    reduction_dir: Path,
    plan: dict[str, Any],
    plan_sha256: str,
    packet: dict[str, Any],
    output: dict[str, Any],
    queue_entries: list[dict[str, Any]],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    input_observations = {
        item["observation_id"]: item for item in packet["observations"]
    }
    public_clusters_in_model_order = derive_public_clusters(output)
    public_cluster_ids = [
        cluster["cluster_id"] for cluster in public_clusters_in_model_order
    ]

    def publish_record(record: dict[str, Any]) -> dict[str, Any]:
        cluster_index = record["cluster_index"]
        return {
            "observation_id": record["observation_id"],
            "accounting_status": record["accounting_status"],
            "cluster_id": (
                public_cluster_ids[cluster_index]
                if cluster_index is not None
                else None
            ),
            "role": record["role"],
            "canonical_observation_id": record["canonical_observation_id"],
            "reason": record["reason"],
            "falsifier": record["falsifier"],
        }

    ledger_records = [
        {
            "observation_id": record["observation_id"],
            "observation": input_observations[record["observation_id"]],
            "reduction": publish_record(record),
        }
        for record in sorted(output["records"], key=lambda item: item["observation_id"])
    ]
    public_clusters = sorted(
        public_clusters_in_model_order, key=lambda item: item["cluster_id"]
    )
    packet_receipts = {
        item["receipt_id"]: item for item in packet["shard_receipts"]
    }
    restored_shard_records = [
        {
            "receipt_id": record["receipt_id"],
            "status": packet_receipts[record["receipt_id"]]["status"],
            "overflow_state": packet_receipts[record["receipt_id"]][
                "overflow_state"
            ],
            "disposition": record["disposition"],
            "reason": record["reason"],
        }
        for record in sorted(
            output["shard_records"], key=lambda item: item["receipt_id"]
        )
    ]
    ledger = {
        "schema_version": 2,
        "reduction_plan_sha256": plan_sha256,
        "packet_sha256": plan["bindings"]["packet_sha256"],
        "records": ledger_records,
        "clusters": public_clusters,
        "unresolved_records": sorted(
            output["unresolved_records"], key=lambda item: item["unresolved_id"]
        ),
        "shard_records": restored_shard_records,
    }
    queue = {
        "schema_version": 1,
        "reduction_plan_sha256": plan_sha256,
        "packet_sha256": plan["bindings"]["packet_sha256"],
        "queue": queue_entries,
    }
    status_counts = Counter(
        record["accounting_status"] for record in output["records"]
    )
    overflow_counts = Counter(
        record["overflow_state"] for record in restored_shard_records
    )
    summary = {
        "schema_version": 1,
        "status": "green",
        "mode": "labeled",
        "reduction_plan_sha256": plan_sha256,
        "observations": len(output["records"]),
        "accounting_statuses": dict(sorted(status_counts.items())),
        "clusters": len(output["clusters"]),
        "source_verification_clusters": len(queue_entries),
        "source_verification_observations": sum(
            len(item["members"]) for item in queue_entries
        ),
        "unresolved_questions": len(output["unresolved_records"]),
        "shard_receipts": len(output["shard_records"]),
        "overflow_states": dict(sorted(overflow_counts.items())),
        "usage": receipt["usage"],
        "resolved_model": receipt["resolved_model"],
        "model_resolution_status": receipt["model_resolution_status"],
        "requested_model": plan["policy"]["model"],
        "requested_effort": plan["policy"]["effort"],
        "resolved_effort": receipt["resolved_effort"],
        "effort_resolution_status": receipt["effort_resolution_status"],
        "execution_jobs": receipt["execution_jobs"],
        "label_jobs": len(plan["label_blocks"]),
        "accounting_structure": accounting_signals(output),
    }
    staged: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="reduction-green-", dir=reduction_dir) as temp:
        temporary = Path(temp)
        for name, value in (
            ("accounting-ledger.json", ledger),
            ("verification-queue.json", queue),
            ("summary.json", summary),
        ):
            path = temporary / name
            path.write_text(render_json(value), encoding="utf-8")
            staged[name] = path
        for name in ACTION_ARTIFACTS:
            os.replace(staged[name], reduction_dir / name)
    return summary



def aggregate_receipts(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    usage = {}
    for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"):
        values = [r["usage"][key] for r in receipts]
        usage[key] = None if any(v is None for v in values) else sum(values)
    result: dict[str, Any] = {"usage": usage, "execution_jobs": len(receipts)}
    for name in ("model", "effort"):
        reported = bool(receipts) and all(r[f"{name}_resolution_status"] == "reported" for r in receipts)
        result[f"{name}_resolution_status"] = "reported" if reported else "not_reported_by_cli"
        result[f"resolved_{name}"] = receipts[0][f"resolved_{name}"] if reported else None
    return result


def validate(plan_path: Path, plan: dict[str, Any]) -> int:
    directory = plan_path.parent
    validation = {"schema_version": 2, "status": "red", "valid": False, "mode": "labeled",
                  "reduction_plan_sha256": load_json_artifact(plan_path).sha256,
                  "packet_sha256": plan.get("bindings", {}).get("packet_sha256")}
    progress = ReductionProgress(completed=0)
    try:
        view = inspect_execution(plan_path, plan, progress=progress)
        packet, accounting, jobs, state = view.packet, view.accounting, view.jobs, view.state
        if state.blocked is not None:
            raise ValueError("semantic_blocked")
        if view.next_job is not None:
            raise ValueError(f"Missing authenticated result: {view.next_job.job_id}")
        outputs, receipts, result_hashes = {}, [], {}
        for job in jobs:
            progress.current_job = job.job_id
            result = selected_job_result(plan_path, plan, job, state.attempts[job.job_id])
            if result is None:
                raise ValueError(f"Missing authenticated result: {job.job_id}")
            result_hashes[job.job_id] = sha256_file(directory / "results" / f"{job.job_id}.json")
            receipt = load_json_artifact(Path(result["attempt_receipt_path"])).value
            receipts.append(receipt)
            if job.descriptor["kind"] == "accounting":
                outputs[job.job_id] = result["output"]
        combined = assemble_outputs(packet, accounting, outputs, plan["group_capacity"]["max_group_positions"])
        errors, queue = reduction_closure(packet, combined)
        if errors:
            raise ValueError("; ".join(errors))
        # Recheck source/packet after potentially lengthy output inspection.
        final_inspection = inspect_bound_reduction(plan, directory)
        if final_inspection.errors:
            raise ValueError("; ".join(final_inspection.errors))
        summary = _publish_green(directory, plan, validation["reduction_plan_sha256"], packet,
                                 combined, queue, aggregate_receipts(receipts))
        progress.current_job = None
        validation.update({"status": "green", "valid": True, "errors": [],
                           "job_results": result_hashes,
                           "partition_sha256": sha256_file(directory / "partition" / "manifest.json"),
                           "derived_artifacts": {name: sha256_file(directory / name) for name in ACTION_ARTIFACTS},
                           "closed_counts": {"observations": summary["observations"], "clusters": summary["clusters"],
                                             "unresolved_questions": summary["unresolved_questions"],
                                             "shard_receipts": summary["shard_receipts"], "queue_clusters": len(queue)}})
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        for name in ACTION_ARTIFACTS:
            (directory / name).unlink(missing_ok=True)
        validation["errors"] = [str(exc)]
    # Commit marker published last; all readers bind the exact derived hashes.
    write_json_atomic(directory / "validation.json", validation)
    render_reduction_inspection(directory, plan, validation["status"], progress=progress,
                                failure_reason="; ".join(validation["errors"]) if validation["errors"] else None)
    print(render_json(validation), end="")
    return 0 if validation["valid"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("plan", help="Labeled reduction plan v5")
    args = parser.parse_args()
    path = Path(args.plan).expanduser().absolute()
    plan = load_json_artifact(path).value
    if not isinstance(plan, dict) or plan.get("schema_version") != REDUCTION_PLAN_SCHEMA_VERSION:
        raise ValueError("Unsupported reduction plan; expected labeled plan v5")
    path = require_reduction_location(plan, path)
    with reduction_lock(path.parent):
        return validate(path, plan)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        sys.exit(str(exc))
