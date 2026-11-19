"""One accounting wire schema and pure decoder; no universe or truth decisions."""
from __future__ import annotations

from typing import Any

from reduction_contract import (
    ACCOUNTING_STATUSES, CLUSTER_ROLES, CLUSTER_ROUTES, MAX_REDUCTION_ITEMS,
    MAX_RECORD_REASON_CHARS, MAX_CLUSTER_REASON_CHARS, MAX_FALSIFIER_CHARS,
    MAX_UNRESOLVED_REASON_CHARS, MAX_RECEIPT_REASON_CHARS,
    REDUCER_NORMAL_FORM_SCHEMA_VERSION,
)
from verification_contract import SOURCE_RELATIONSHIPS, VERIFICATION_DEPTHS

ACCOUNTING_WIRE_VERSION = 3
ACCOUNTING_DECODER_VERSION = 1
IDENTIFIER_MAX_CHARS = 512
NONMERGED_STATUSES = tuple(s for s in ACCOUNTING_STATUSES if s != "merged")


def accounting_wire_schema() -> dict[str, Any]:
    """Syntax bounds only. Cross-array coverage belongs to semantic closure."""
    def string(maximum: int, nullable: bool = False) -> dict[str, Any]:
        return {"type": ["string", "null"] if nullable else "string",
                "minLength": 1, "maxLength": maximum}

    def enum(values: Any, nullable: bool = False) -> dict[str, Any]:
        return {"type": ["string", "null"] if nullable else "string",
                "enum": list(values) + ([None] if nullable else [])}

    def obj(properties: dict[str, Any]) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": False,
                "required": list(properties), "properties": properties}

    def array(items: dict[str, Any], maximum: int = MAX_REDUCTION_ITEMS) -> dict[str, Any]:
        return {"type": "array", "maxItems": maximum, "items": items}

    record = {"observation_id": string(IDENTIFIER_MAX_CHARS),
              "accounting_status": enum(NONMERGED_STATUSES),
              "reason": string(MAX_RECORD_REASON_CHARS),
              "falsifier": string(MAX_FALSIFIER_CHARS, True)}
    ordinary = obj({**record, "role": enum(r for r in CLUSTER_ROLES if r != "canonical")})
    merged = obj({k: v for k, v in record.items() if k != "accounting_status"} |
                 {"role": enum(("candidate", "supporting"))})
    cluster = obj({
        "route": enum(CLUSTER_ROUTES),
        "required_verification_depth": enum(VERIFICATION_DEPTHS, True),
        "requested_source_relationships": array(enum(SOURCE_RELATIONSHIPS), len(SOURCE_RELATIONSHIPS)),
        "reason": string(MAX_CLUSTER_REASON_CHARS),
        "ordinary": array(ordinary),
        "merge_sets": array(obj({"canonical": obj(record), "merged": array(merged)})),
    })
    return {"$schema": "http://json-schema.org/draft-07/schema#", **obj({
        "schema_version": {"type": "integer", "const": ACCOUNTING_WIRE_VERSION},
        "clusters": array(cluster),
        # verify here is syntactically legal but semantically unclustered: the
        # existing closure must reject it and charge the experiment's red cap.
        "unclustered_records": array(obj(record)),
        "unresolved_records": array(obj({
            "unresolved_id": string(IDENTIFIER_MAX_CHARS),
            "disposition": enum(("preserved", "resolved", "blocked")),
            "reason": string(MAX_UNRESOLVED_REASON_CHARS),
        })),
        "shard_records": array(obj({
            "receipt_id": string(IDENTIFIER_MAX_CHARS),
            "disposition": enum(("accounted", "blocked")),
            "reason": string(MAX_RECEIPT_REASON_CHARS),
        })),
    })}


def accounting_wire_shape_errors(value: Any) -> list[str]:
    """Interpret the one schema, never inspect packet IDs or relationships."""
    errors: list[str] = []

    def visit(item: Any, schema: dict[str, Any], path: str) -> None:
        kind = schema["type"]
        kinds = kind if isinstance(kind, list) else [kind]
        types = {"object": isinstance(item, dict), "array": isinstance(item, list),
                 "string": isinstance(item, str), "integer": type(item) is int,
                 "null": item is None}
        if not any(types[k] for k in kinds):
            errors.append(f"{path} has invalid type")
            return
        if "const" in schema and item != schema["const"]:
            errors.append(f"{path} has invalid constant")
        if "enum" in schema and item not in schema["enum"]:
            errors.append(f"{path} has invalid enum")
        if isinstance(item, dict):
            properties = schema["properties"]
            if set(item) != set(properties):
                errors.append(f"{path} fields mismatch")
            for key in properties:
                if key in item:
                    visit(item[key], properties[key], f"{path}.{key}")
        elif isinstance(item, list):
            if len(item) > schema["maxItems"]:
                errors.append(f"{path} exceeds array bound")
            for index, child in enumerate(item):
                visit(child, schema["items"], f"{path}[{index}]")
        elif isinstance(item, str):
            if "minLength" in schema and not schema["minLength"] <= len(item) <= schema["maxLength"]:
                errors.append(f"{path} exceeds string bound or is empty")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                errors.append(f"{path} is not encodable UTF-8")
    visit(value, accounting_wire_schema(), "wire")
    return errors


def decode_accounting_output(value: dict[str, Any]) -> dict[str, Any]:
    """Total on shape-valid wire. Preserve all occurrences, including bad IDs.

    Placement is the model's explicit semantic choice. Derive only containment
    and direct references; never infer a representative or repair a status.
    """
    records: list[dict[str, Any]] = []
    clusters: list[dict[str, Any]] = []

    def record(item: dict[str, Any], cluster: int | None, role: str | None,
               target: str | None = None, *, merged: bool = False) -> None:
        records.append({"observation_id": item["observation_id"],
                        "accounting_status": "merged" if merged else item["accounting_status"],
                        "cluster_index": cluster, "role": role,
                        "canonical_observation_id": target,
                        "reason": item["reason"], "falsifier": item["falsifier"]})

    for index, cluster in enumerate(value["clusters"]):
        clusters.append({k: (list(v) if isinstance(v, list) else v)
                         for k, v in cluster.items() if k not in ("ordinary", "merge_sets")})
        for member in cluster["ordinary"]:
            record(member, index, member["role"])
        for group in cluster["merge_sets"]:
            canonical = group["canonical"]
            record(canonical, index, "canonical")
            for member in group["merged"]:
                record(member, index, member["role"], canonical["observation_id"], merged=True)
    for member in value["unclustered_records"]:
        record(member, None, None)
    return {"schema_version": REDUCER_NORMAL_FORM_SCHEMA_VERSION,
            "records": records, "clusters": clusters,
            "unresolved_records": [dict(r) for r in value["unresolved_records"]],
            "shard_records": [dict(r) for r in value["shard_records"]]}


def accounting_signals(normal_form: dict[str, Any]) -> dict[str, int]:
    """Derived diagnostics from closed normal form, never acceptance quotas."""
    records = normal_form["records"]
    targets = {r["canonical_observation_id"] for r in records if r["accounting_status"] == "merged"}
    canonical = [r for r in records if r["role"] == "canonical"]
    return {"canonical_records": len(canonical),
            "merged_records": sum(r["accounting_status"] == "merged" for r in records),
            "counterevidence_records": sum(r["role"] == "counterevidence" for r in records),
            "minority_records": sum(r["role"] == "minority" for r in records),
            "merge_sets": len(canonical),
            "empty_merge_sets": sum(r["observation_id"] not in targets for r in canonical)}
