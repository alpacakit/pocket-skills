"""Verification bundle, action shape, literals, and fail-closed action safety."""

from __future__ import annotations

from typing import Any, Iterable

VERIFICATION_BUNDLE_SCHEMA_VERSION = 1
ACTION_VALIDATION_SCHEMA_VERSION = 1

VERIFICATION_DEPTHS = (
    "citation_integrity",
    "source_context",
    "authority_graph",
    "behavioral",
)
SOURCE_RELATIONSHIPS = (
    "test_source",
    "production_source",
    "caller",
    "gate",
    "history",
    "replacement_owner",
    "negative_case",
)
DIMENSION_STATES = ("checked", "not_required", "missing")


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


def action_gate_errors(records: Any) -> list[str]:
    """Pure fail-closed gate for future verification-driven actions."""
    if not isinstance(records, list):
        return ["verification records are not an array"]
    errors: list[str] = []
    required_keys = (
        "verification_id",
        "cluster_id",
        "verification_queue_sha256",
        "verification_bundle_sha256",
        "source_snapshot_id",
        "disposition",
        "required_depth",
        "actual_depth",
        "checked_dimensions",
        "verified_claim",
        "authority",
        "consequence",
        "current_evidence",
        "falsifier",
        "action",
        "lost_scenario",
        "replacement_owner",
        "negative_case",
        "persisted_boundary",
        "security_boundary",
    )
    depth_rank = {name: index for index, name in enumerate(VERIFICATION_DEPTHS)}
    for index, record in enumerate(records):
        label = f"verification_record[{index}]"
        errors.extend(_exact_keys(record, required_keys, label))
        if not isinstance(record, dict):
            continue
        if "source_verified" in record:
            errors.append(f"{label} source_verified boolean is forbidden")
        for field in (
            "verification_id",
            "cluster_id",
            "verification_queue_sha256",
            "verification_bundle_sha256",
            "source_snapshot_id",
            "verified_claim",
            "authority",
            "consequence",
            "falsifier",
        ):
            if not _is_nonempty_string(record.get(field)):
                errors.append(f"{label} {field} is empty")
        if record.get("disposition") not in ("confirmed", "rejected", "inconclusive"):
            errors.append(f"{label} disposition is invalid")
        required_depth = record.get("required_depth")
        actual_depth = record.get("actual_depth")
        if required_depth not in VERIFICATION_DEPTHS or actual_depth not in VERIFICATION_DEPTHS:
            errors.append(f"{label} verification depth is invalid")
        elif depth_rank[actual_depth] < depth_rank[required_depth]:
            errors.append(f"{label} actual depth is weaker than required depth")
        dimensions = record.get("checked_dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != set(
            SOURCE_RELATIONSHIPS
        ):
            errors.append(f"{label} checked_dimensions do not match the contract")
            dimensions = {}
        elif any(value not in DIMENSION_STATES for value in dimensions.values()):
            errors.append(f"{label} checked_dimensions contain an invalid state")
        evidence = record.get("current_evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{label} current_evidence is empty")
        action = record.get("action")
        if action not in ("delete", "retain", "replace", "none"):
            errors.append(f"{label} action is invalid")
        for field in ("persisted_boundary", "security_boundary"):
            if type(record.get(field)) is not bool:
                errors.append(f"{label} {field} is not boolean")
        if action != "delete":
            continue
        if record.get("disposition") != "confirmed":
            errors.append(f"{label} delete requires confirmed disposition")
        for field in ("lost_scenario", "replacement_owner", "negative_case"):
            if not _is_nonempty_string(record.get(field)):
                errors.append(f"{label} delete requires {field}")
        for dimension in ("replacement_owner", "negative_case"):
            if dimensions.get(dimension) != "checked":
                errors.append(f"{label} delete requires checked {dimension}")
        # Boundary booleans are verifier claims, not a trusted way to waive checks.
        # All deletes therefore close the protected production/history dimensions.
        required_dimensions = {"production_source", "caller", "gate", "history"}
        for dimension in sorted(required_dimensions):
            if dimensions.get(dimension) != "checked":
                errors.append(f"{label} delete requires checked {dimension}")
    return errors
