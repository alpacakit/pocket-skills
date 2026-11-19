"""Single inspection boundary for packet provenance and labeled reduction bindings."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scan_artifacts import (load_json_artifact, sha256_file, snapshot_file_errors, artifact_manifest_sha256)
from scan_contract import CONFIDENCES, REVIEW_PACKET_SCHEMA_VERSION, VALIDATION_SCHEMA_VERSION, receipt_id, unresolved_id
from scan_workspace import reduction_output
from reduction_jobs import REDUCTION_PLAN_SCHEMA_VERSION, build_plan
from reducer_runtime_policy import reducer_policy_errors, reducer_policy_sha256

def _ids(items: Any, field: str, label: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    if not isinstance(items, list):
        return [], [f"packet {label} is not an array"]
    values: list[str] = []
    for index, item in enumerate(items):
        value = item.get(field) if isinstance(item, dict) else None
        if not isinstance(value, str) or not value:
            errors.append(f"packet {label}[{index}] has invalid {field}")
        else:
            values.append(value)
    duplicate = sorted(key for key, count in Counter(values).items() if count != 1)
    if duplicate:
        errors.append(f"packet {label} has duplicate ids: {duplicate}")
    return values, errors


def packet_errors(packet: Any, scan_plan: dict[str, Any]) -> list[str]:
    if not isinstance(packet, dict):
        return ["review packet is not an object"]
    errors: list[str] = []
    if packet.get("schema_version") != REVIEW_PACKET_SCHEMA_VERSION:
        errors.append("review packet schema version mismatch")
    for key in (
        "scan_id",
        "snapshot_id",
        "question",
        "question_sha256",
        "plan_path",
        "plan_sha256",
        "validation_path",
        "results_sha256",
    ):
        if packet.get(key) != scan_plan.get(key) and key in (
            "scan_id",
            "snapshot_id",
            "question",
            "question_sha256",
        ):
            errors.append(f"packet {key} mismatch")
        elif key not in ("scan_id", "snapshot_id", "question", "question_sha256") and not isinstance(
            packet.get(key), str
        ):
            errors.append(f"packet {key} is missing")
    observation_ids, observation_errors = _ids(
        packet.get("observations"), "observation_id", "observations"
    )
    unresolved_ids, unresolved_errors = _ids(
        packet.get("unresolved_questions"),
        "unresolved_id",
        "unresolved_questions",
    )
    receipt_ids, receipt_errors = _ids(
        packet.get("shard_receipts"), "receipt_id", "shard_receipts"
    )
    errors.extend(observation_errors + unresolved_errors + receipt_errors)
    if packet.get("validated_observation_count") != len(observation_ids):
        errors.append("packet validated_observation_count mismatch")
    if packet.get("validated_unresolved_count") != len(unresolved_ids):
        errors.append("packet validated_unresolved_count mismatch")
    if packet.get("validated_shard_count") != len(receipt_ids):
        errors.append("packet validated_shard_count mismatch")
    if not isinstance(packet.get("question"), str) or not packet["question"]:
        errors.append("packet question is empty or invalid")
    for index, observation in enumerate(packet.get("observations", [])):
        label = f"packet observations[{index}]"
        if not isinstance(observation, dict):
            continue
        for field in (
            "observation_id",
            "category",
            "scout_confidence",
            "title",
            "claim",
            "why_it_matters",
        ):
            if not isinstance(observation.get(field), str):
                errors.append(f"{label} has invalid {field}")
        if observation.get("scout_confidence") not in CONFIDENCES:
            errors.append(f"{label} has invalid scout_confidence")
        evidence_rows = observation.get("evidence")
        if not isinstance(evidence_rows, list):
            errors.append(f"{label} evidence is not an array")
            continue
        for evidence_index, evidence in enumerate(evidence_rows):
            evidence_label = f"{label}.evidence[{evidence_index}]"
            if not isinstance(evidence, dict):
                errors.append(f"{evidence_label} is not an object")
                continue
            for field in ("path", "locator", "quote"):
                if not isinstance(evidence.get(field), str):
                    errors.append(f"{evidence_label} has invalid {field}")
            path = evidence.get("path")
            if isinstance(path, str) and Path(path).is_absolute():
                errors.append(f"{evidence_label} path is not relative")
    for index, unresolved_item in enumerate(
        packet.get("unresolved_questions", [])
    ):
        if not isinstance(unresolved_item, dict):
            continue
        if not isinstance(unresolved_item.get("text"), str):
            errors.append(
                f"packet unresolved_questions[{index}] has invalid text"
            )
    expected_receipts = [receipt_id(item["shard_id"]) for item in scan_plan["shards"]]
    if Counter(receipt_ids) != Counter(expected_receipts):
        errors.append("packet shard receipts do not exactly cover planned shards")
    for receipt in packet.get("shard_receipts", []):
        if not isinstance(receipt, dict):
            continue
        if receipt.get("status") != "complete":
            errors.append(f"receipt {receipt.get('receipt_id')} is not complete")
        if receipt.get("overflow_state") not in ("true", "false", "unknown"):
            errors.append(
                f"receipt {receipt.get('receipt_id')} has invalid overflow state"
            )
        shard_id = receipt.get("shard_id")
        if not isinstance(shard_id, str) or receipt.get("receipt_id") != receipt_id(
            shard_id
        ):
            errors.append(f"packet receipt has unstable id: {receipt.get('receipt_id')}")
            continue
        receipt_observations = receipt.get("observation_ids")
        receipt_unresolved = receipt.get("unresolved_ids")
        if not isinstance(receipt_observations, list):
            errors.append(f"receipt {receipt['receipt_id']} observation_ids is invalid")
        else:
            expected = [
                value for value in observation_ids if value.startswith(f"{shard_id}:")
            ]
            if Counter(receipt_observations) != Counter(expected):
                errors.append(
                    f"receipt {receipt['receipt_id']} observation ids mismatch"
                )
        if not isinstance(receipt_unresolved, list):
            errors.append(f"receipt {receipt['receipt_id']} unresolved_ids is invalid")
        else:
            expected = [
                value
                for value in unresolved_ids
                if value.startswith(f"{shard_id}:unresolved:")
            ]
            if Counter(receipt_unresolved) != Counter(expected):
                errors.append(
                    f"receipt {receipt['receipt_id']} unresolved ids mismatch"
                )
    for item in packet.get("unresolved_questions", []):
        if not isinstance(item, dict):
            continue
        shard_id = item.get("shard_id")
        ordinal = item.get("ordinal")
        if (
            not isinstance(shard_id, str)
            or type(ordinal) is not int
            or ordinal < 1
            or item.get("unresolved_id") != unresolved_id(shard_id, ordinal)
        ):
            errors.append(
                f"packet unresolved item has unstable id: {item.get('unresolved_id')}"
            )
    return errors


def _current_result_manifest(
    scan_dir: Path, validation: dict[str, Any]
) -> dict[str, str]:
    expected = validation.get("result_artifacts")
    if not isinstance(expected, dict) or not all(
        isinstance(name, str) and isinstance(digest, str)
        for name, digest in expected.items()
    ):
        raise SystemExit("Validation result artifact manifest is invalid")
    actual: dict[str, str] = {}
    for name in sorted(expected):
        path = scan_dir / name
        try:
            actual[name] = sha256_file(path)
        except OSError as exc:
            raise SystemExit(f"Cannot hash validated result {path}: {exc}") from exc
    if actual != expected:
        raise SystemExit("Validated result artifacts changed; validate and rebuild")
    if artifact_manifest_sha256(actual) != validation.get("results_sha256"):
        raise SystemExit("Validation results_sha256 does not match result manifest")
    return actual



def load_packet(packet_path: Path, catalog_path: Path) -> tuple[dict[str, Any], dict[str, Any], Any]:
    artifact = load_json_artifact(packet_path)
    packet = artifact.value
    if not isinstance(packet, dict):
        raise ValueError("Review packet is not an object")
    scan_path = Path(packet["plan_path"]).resolve()
    scan_artifact = load_json_artifact(scan_path)
    scan = scan_artifact.value
    if not isinstance(scan, dict) or packet.get("plan_sha256") != scan_artifact.sha256:
        raise ValueError("Review packet scan plan is stale")
    errors = packet_errors(packet, scan)
    if errors:
        raise ValueError("Invalid review packet: " + "; ".join(errors))
    validation_path = Path(packet["validation_path"]).resolve()
    validation_artifact = load_json_artifact(validation_path)
    validation = validation_artifact.value
    if (not isinstance(validation, dict) or validation.get("schema_version") != VALIDATION_SCHEMA_VERSION
        or validation.get("valid") is not True or validation.get("plan_sha256") != scan_artifact.sha256
        or packet.get("results_sha256") != validation.get("results_sha256")):
        raise ValueError("Review packet lacks current green scout validation")
    snapshot_errors = snapshot_file_errors(scan, Path(scan["root"]))
    if snapshot_errors:
        raise ValueError("Source snapshot changed: " + "; ".join(snapshot_errors[:3]))
    results = _current_result_manifest(scan_path.parent, validation)
    if any(r.get("result_sha256") != results.get(f"results/{r['shard_id']}.json") for r in packet["shard_receipts"]):
        raise ValueError("Receipt result hash mismatch")
    catalog = load_json_artifact(catalog_path)
    bindings = {"packet_path": str(packet_path), "packet_sha256": artifact.sha256,
                "scan_plan_path": str(scan_path), "scan_plan_sha256": scan_artifact.sha256,
                "scout_validation_path": str(validation_path), "scout_validation_sha256": validation_artifact.sha256,
                "results_sha256": validation["results_sha256"], "result_artifacts": dict(sorted(results.items())),
                "context_catalog_path": str(catalog_path), "context_catalog_sha256": catalog.sha256}
    return packet, bindings, catalog.value


@dataclass(frozen=True)
class BoundReductionInspection:
    errors: tuple[str, ...]
    packet: dict[str, Any] | None = None


def require_reduction_location(plan: dict[str, Any], plan_path: Path) -> Path:
    """Authorize a publication location, independently of evidence freshness.

    The original scan plan supplies the read-only root. Its path and the
    packet/validation path bindings anchor the reduction to that scan. Hashes,
    source bytes and packet/catalog contents belong to inspect_bound_reduction;
    they must not prevent the validator from revoking an old green publication.
    """
    path = plan_path.expanduser().absolute()
    scan_path = Path(plan["bindings"]["scan_plan_path"]).expanduser().absolute()
    # Do not resolve away aliases before checking them, including ancestors.
    if path.resolve(strict=True) != path or scan_path.resolve(strict=True) != scan_path:
        raise ValueError("Reduction/scan plan location must not contain symlinks or aliases")
    if path.name != "plan.json" or scan_path.name != "plan.json":
        raise ValueError("Reduction and original scan plans must be named plan.json")
    scan_dir = scan_path.parent
    for key, name in (("packet_path", "review-packet.json"), ("scout_validation_path", "validation.json")):
        if Path(plan["bindings"][key]) != scan_dir / name:
            raise ValueError(f"Reduction {key} location does not belong to the original scan")
    scan = load_json_artifact(scan_path).value
    if not isinstance(scan, dict) or not isinstance(scan.get("root"), str) or not scan["root"]:
        raise ValueError("Original scan lacks its read-only root")
    reduction_output(scan_dir, path.parent, Path(scan["root"]))
    return path


def inspect_bound_reduction(plan: dict[str, Any], reduction_dir: Path) -> BoundReductionInspection:
    try:
        if plan.get("schema_version") != REDUCTION_PLAN_SCHEMA_VERSION:
            raise ValueError("Unsupported reduction plan")
        if plan.get("status") != "ready" or plan.get("mode") != "labeled":
            raise ValueError("Reduction plan is not ready")
        require_reduction_location(plan, reduction_dir / "plan.json")
        policy = plan.get("policy")
        errors = reducer_policy_errors(policy)
        if errors:
            raise ValueError("Invalid reducer policy: " + "; ".join(errors))
        if plan.get("policy_sha256") != reducer_policy_sha256(policy):
            raise ValueError("Reduction reducer policy hash mismatch")
        b = plan["bindings"]
        packet, bindings, catalog = load_packet(Path(b["packet_path"]), Path(b["context_catalog_path"]))
        expected = build_plan(packet, bindings, catalog, policy)
        if expected["capacity"] != plan.get("capacity"):
            raise ValueError("Reduction plan context capacity does not match bound catalog model")
        if expected != plan:
            raise ValueError("Reduction plan is stale: bindings/contracts/capacity mismatch")
        return BoundReductionInspection((), packet)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, SystemExit) as exc:
        return BoundReductionInspection((str(exc),))
