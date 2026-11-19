"""Pure labeled reduction contracts, capacity, atomic packing and assembly."""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reducer_runtime_policy import render_reducer_prompt, reducer_policy_errors, reducer_policy_sha256
from reduction_contract import (
    reducer_input_value, project_reducer_input,
    reduction_closure, canonical_sha256, REDUCER_NORMAL_FORM_SCHEMA_VERSION,
)
from accounting_wire import (accounting_wire_schema, accounting_wire_shape_errors, decode_accounting_output,
                             ACCOUNTING_WIRE_VERSION, ACCOUNTING_DECODER_VERSION)
from scan_artifacts import render_json, sha256_bytes, sha256_file
from reduction_labels import (LABEL_BLOCK_SIZE, LABEL_PROJECTION_VERSION, KEY_ASCII_LIMIT, DEFINITION_CHAR_LIMIT, DEFINITION_BYTE_LIMIT,
                              SubjectPrefix, label_input_value,
                              label_output_schema, label_shape_errors, label_closure, label_reserve, worst_vocabulary)

REDUCTION_PLAN_SCHEMA_VERSION = 5
REDUCTION_RESULT_SCHEMA_VERSION = 5
BYTES_PER_ESTIMATED_TOKEN = 3.2
SYSTEM_PROMPT_RESERVE_TOKENS = 8192
RECOVERY_HEADROOM_TOKENS = 8192
# Operational planning budget, NOT a catalog/backend limit. With the unchanged
# worst-case reserve plus a nesting envelope it bounds job response size. Past
# receipts used ~100-150 output tokens/observation; this avoids huge responses
# without assuming sparse clusters or reducing the accounting reserve.
ACCOUNTING_OUTPUT_BUDGET_TOKENS = 65536
NESTING_BASE_BYTES = 64
NESTING_BYTES_PER_OBSERVATION = 128
OUTPUT_RESERVE_BASE_TOKENS = 4096 + NESTING_BASE_BYTES
OUTPUT_RESERVE_PER_RECORD_TOKENS = 192 + NESTING_BYTES_PER_OBSERVATION
OUTPUT_RESERVE_PER_CLUSTER_TOKENS = 256
OUTPUT_RESERVE_PER_UNRESOLVED_TOKENS = 96
OUTPUT_RESERVE_PER_RECEIPT_TOKENS = 96
OUTPUT_RESERVE_FORMULA = "4160 + records*320 + max_clusters*256 + unresolved*96 + receipts*96"
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def output_reserve_estimate(observations: int, unresolved: int, receipts: int) -> int:
    return (OUTPUT_RESERVE_BASE_TOKENS + observations * OUTPUT_RESERVE_PER_RECORD_TOKENS
            + observations * OUTPUT_RESERVE_PER_CLUSTER_TOKENS
            + unresolved * OUTPUT_RESERVE_PER_UNRESOLVED_TOKENS
            + receipts * OUTPUT_RESERVE_PER_RECEIPT_TOKENS)


def context_capacity(catalog: Any, model: str) -> dict[str, int]:
    if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
        raise ValueError("Context catalog has no models array")
    matches = [m for m in catalog["models"] if isinstance(m, dict) and m.get("slug") == model]
    if len(matches) != 1:
        raise ValueError(f"Context catalog must contain model exactly once: {model}")
    m = matches[0]
    window = m.get("context_window", m.get("context_window_tokens"))
    percent = m.get("effective_context_window_percent", 100)
    if type(window) is not int or window <= 0 or type(percent) is not int or not 1 <= percent <= 100:
        raise ValueError("Invalid context window/effective_context_window_percent")
    return {"context_window_tokens": window, "effective_context_window_percent": percent,
            "effective_context_window_tokens": window * percent // 100}


def fit_context(prompt_bytes: int, schema_bytes: int, reserve: dict[str, Any],
                capacity: dict[str, int], kind: str) -> dict[str, Any]:
    estimated = math.ceil((prompt_bytes + schema_bytes) / BYTES_PER_ESTIMATED_TOKEN) + SYSTEM_PROMPT_RESERVE_TOKENS
    output = reserve["computed_tokens"]
    total = estimated + output + RECOVERY_HEADROOM_TOKENS
    return {**capacity, "rendered_prompt_utf8_bytes": prompt_bytes, "schema_utf8_bytes": schema_bytes,
            "estimated_input_tokens": estimated, "output_reserve_tokens": output,
            "output_reserve": reserve, "recovery_headroom_tokens": RECOVERY_HEADROOM_TOKENS,
            "system_prompt_reserve_tokens": SYSTEM_PROMPT_RESERVE_TOKENS,
            "estimated_total_tokens": total, "estimator": "ceil(utf8_bytes/3.2)",
            "operational_output_budget_tokens": ACCOUNTING_OUTPUT_BUDGET_TOKENS if kind == "accounting" else None,
            "fit": total <= capacity["effective_context_window_tokens"] and
                   (kind != "accounting" or output <= ACCOUNTING_OUTPUT_BUDGET_TOKENS)}


@dataclass(frozen=True)
class ReductionJob:
    descriptor: dict[str, Any]
    input_bytes: bytes
    prompt: str
    schema: dict[str, Any]
    universe: dict[str, Any]
    label_prefix: SubjectPrefix | None = None

    @property
    def job_id(self) -> str:
        return self.descriptor["job_id"]


def subset(packet: dict[str, Any], positions: list[int], receipt_indices: list[int]) -> dict[str, Any]:
    # Internal universe only; never published as another review packet.
    n = len(packet["observations"])
    return {"question": packet["question"],
            "observations": [packet["observations"][p] for p in positions if p < n],
            "unresolved_questions": [packet["unresolved_questions"][p - n] for p in positions if p >= n],
            "shard_receipts": [packet["shard_receipts"][r] for r in receipt_indices]}


def make_job(packet: dict[str, Any], kind: str, job_id: str, capacity: dict[str, int],
             group_limit: int, positions: list[int] | None = None,
             receipt_indices: list[int] | None = None, parent_sha256: str | None = None,
             *, prefix: SubjectPrefix | None = None) -> ReductionJob:
    count = len(packet["observations"]) + len(packet["unresolved_questions"])
    positions = list(range(count)) if positions is None else sorted(positions)
    receipts = list(range(len(packet["shard_receipts"]))) if receipt_indices is None else sorted(receipt_indices)
    universe = subset(packet, positions, receipts)
    if kind == "label":
        if prefix is None or receipts:
            raise ValueError("Label job requires its authenticated prefix and no receipts")
        data = compact(label_input_value(packet, positions, prefix.vocabulary()))
        schema = label_output_schema(len(positions), count)
        reserve = label_reserve(len(positions), count)
        template = (TEMPLATES / "label-reducer-prompt.md").read_text(encoding="utf-8").replace("{{GROUP_LIMIT}}", str(group_limit))
        parent_sha256 = prefix.chain_sha256
    elif kind == "accounting":
        data = project_reducer_input(universe)
        schema = accounting_wire_schema()
        o, u, r = (len(universe[k]) for k in ("observations", "unresolved_questions", "shard_receipts"))
        reserve = {"kind": "count_derived_estimate", "formula": OUTPUT_RESERVE_FORMULA,
                   "counts": {"records": o, "max_clusters": o, "unresolved": u, "receipts": r},
                   "constants": {"base_tokens": OUTPUT_RESERVE_BASE_TOKENS, "per_record_tokens": OUTPUT_RESERVE_PER_RECORD_TOKENS,
                                 "per_cluster_tokens": OUTPUT_RESERVE_PER_CLUSTER_TOKENS, "per_unresolved_tokens": OUTPUT_RESERVE_PER_UNRESOLVED_TOKENS, "per_receipt_tokens": OUTPUT_RESERVE_PER_RECEIPT_TOKENS},
                   "nesting_envelope": {"base_bytes": NESTING_BASE_BYTES, "per_observation_bytes": NESTING_BYTES_PER_OBSERVATION, "byte_to_token_upper_bound": 1},
                   "computed_tokens": output_reserve_estimate(o, u, r), "backend_enforced_maximum": False}
        template = (TEMPLATES / "reducer-prompt.md").read_text(encoding="utf-8")
    else:
        raise ValueError("Unknown reduction job kind")
    prompt = render_reducer_prompt(template, data.decode("utf-8"))
    schema_bytes = render_json(schema).encode("utf-8")
    descriptor = {"job_id": job_id, "kind": kind, "positions": positions, "receipt_indices": receipts,
                  "total_positions": count, "parent_result_chain_sha256": parent_sha256,
                  "input_sha256": sha256_bytes(data),
                  "rendered_prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                  "schema_sha256": sha256_bytes(schema_bytes),
                  "context": fit_context(len(prompt.encode("utf-8")), len(schema_bytes), reserve, capacity, kind)}
    if kind == "accounting":
        descriptor.update({"accounting_wire_version": ACCOUNTING_WIRE_VERSION, "accounting_decoder_version": ACCOUNTING_DECODER_VERSION})
    if prefix is not None:
        descriptor["vocabulary_sha256"] = canonical_sha256(prefix.vocabulary())
    return ReductionJob(descriptor, data, prompt, schema, universe, prefix)


def group_capacity(packet: dict[str, Any], capacity: dict[str, int]) -> dict[str, Any]:
    """Prove every group <=X fits, using full dictionaries and worst row costs."""
    projection = reducer_input_value(packet)
    costs = sorted([len(compact(row)) + 1 for row in projection["o"] + projection["u"]], reverse=True)
    base = {**projection, "o": [], "u": []}  # includes ALL receipts and full dictionaries
    base_bytes = len(compact(base))
    template = (TEMPLATES / "reducer-prompt.md").read_text(encoding="utf-8")
    envelope = len(render_reducer_prompt(template, "").encode("utf-8"))
    schema_bytes = len(render_json(accounting_wire_schema()).encode("utf-8"))
    prefixes = [0]
    for cost in costs:
        prefixes.append(prefixes[-1] + cost)
    r = len(packet["shard_receipts"])
    def candidate(x: int) -> dict[str, Any]:
        # Treat every group position as an observation (the larger observation rate rather than the unresolved rate).
        return fit_context(base_bytes + prefixes[x] + envelope, schema_bytes,
                           {"computed_tokens": output_reserve_estimate(x, 0, r)}, capacity, "accounting")
    lo, hi = 0, len(costs)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if candidate(mid)["fit"]:
            lo = mid
        else:
            hi = mid - 1
    return {"max_group_positions": lo, "worst_case": candidate(lo),
            "method": "full dictionaries and receipts + largest X serialized rows; all X count as observations",
            "next_group_fits": candidate(lo + 1)["fit"] if lo < len(costs) else None}


def build_plan(packet: dict[str, Any], bindings: dict[str, Any], catalog: Any, policy: dict[str, Any]) -> dict[str, Any]:
    errors = reducer_policy_errors(policy)
    if errors:
        raise ValueError("Invalid reducer policy: " + "; ".join(errors))
    capacity = context_capacity(catalog, policy["model"])
    bound = group_capacity(packet, capacity)
    limit = bound["max_group_positions"]
    count = len(packet["observations"]) + len(packet["unresolved_questions"])
    blocks = []
    for start in range(0, count, LABEL_BLOCK_SIZE):
        positions = list(range(start, min(start + LABEL_BLOCK_SIZE, count)))
        worst = SubjectPrefix({k: {"definition": d, "positions": [i]}
                               for i, (k, d) in enumerate(worst_vocabulary(start))})
        job_id = f"label-{len(blocks)+1:03d}"
        job = make_job(packet, "label", job_id, capacity, limit, positions, [], prefix=worst)
        blocks.append({"job_id": job_id, "positions": positions,
                       "source_input_sha256": sha256_bytes(compact(label_input_value(packet, positions, []))),
                       "schema_sha256": job.descriptor["schema_sha256"],
                       "worst_context": job.descriptor["context"]})
    receipts_job = make_job(packet, "accounting", "receipts-check", capacity, limit, [],
                            list(range(len(packet["shard_receipts"]))))
    status = ("atomic_unit_overflow" if (count and limit == 0) or not receipts_job.descriptor["context"]["fit"]
              else "label_input_overflow" if any(not b["worst_context"]["fit"] for b in blocks) else "ready")
    templates = {name: sha256_file(TEMPLATES / name) for name in ("label-reducer-prompt.md", "reducer-prompt.md")}
    return {"schema_version": REDUCTION_PLAN_SCHEMA_VERSION, "status": status,
            "failure_reason": None if status == "ready" else status, "mode": "labeled", "scan_id": packet["scan_id"],
            "bindings": bindings, "policy": policy, "policy_sha256": reducer_policy_sha256(policy),
            "contracts": {"label_projection_version": LABEL_PROJECTION_VERSION, "label_output_version": 1,
                          "prefix_fold_version": 1, "block_size": LABEL_BLOCK_SIZE,
                          "key_ascii_max": KEY_ASCII_LIMIT, "definition_codepoints_max": DEFINITION_CHAR_LIMIT, "definition_utf8_bytes_max": DEFINITION_BYTE_LIMIT,
                          "unused_new_subjects": "validate_but_do_not_adopt", "vocabulary_membership_counts": False,
                          "accounting_projection_version": 1, "accounting_output_version": ACCOUNTING_WIRE_VERSION,
                          "accounting_decoder_version": ACCOUNTING_DECODER_VERSION, "accounting_normal_form_version": REDUCER_NORMAL_FORM_SCHEMA_VERSION, "templates": templates,
                          "accounting_output_schema_sha256": canonical_sha256(accounting_wire_schema()),
                          "accounting_output_reserve_formula": OUTPUT_RESERVE_FORMULA,
                          "accounting_nesting_envelope": {"base_bytes": NESTING_BASE_BYTES,
                                                           "per_observation_bytes": NESTING_BYTES_PER_OBSERVATION},
                          "accounting_output_budget_tokens": ACCOUNTING_OUTPUT_BUDGET_TOKENS,
                          "accounting_output_budget_basis": ("Operational planning bound, not a backend limit. "
                              "Old 448/observation reserve plus 128 nesting bytes/observation and 64 base bytes; "
                              "prior reviewed receipts used about 100-150 output tokens/observation. "
                              "This bounds response size without assuming sparse clustering."),
                          "packing": "stable-first-fit-atomic-v1"},
            "capacity": capacity, "group_capacity": bound, "label_blocks": blocks,
            "accounting_status": "pending_labels"}


def job_shape_errors(job: ReductionJob, output: Any) -> list[str]:
    if job.descriptor["kind"] == "label":
        return label_shape_errors(output, len(job.descriptor["positions"]), job.descriptor["total_positions"])
    return accounting_wire_shape_errors(output)


def job_closure(job: ReductionJob, output: Any, group_limit: int) -> list[str]:
    if job.descriptor["kind"] == "label":
        if job.label_prefix is None:
            return ["Label job has no bound prefix"]
        return label_closure(output, job.descriptor["positions"], job.descriptor["total_positions"], job.label_prefix, group_limit)
    shape = accounting_wire_shape_errors(output)
    if shape:
        return shape
    return reduction_closure(job.universe, decode_accounting_output(output))[0]


def partition_jobs(packet: dict[str, Any], plan: dict[str, Any], groups: list[list[int]],
                   parent_sha256: str) -> tuple[dict[str, Any], list[ReductionJob]]:
    limit = plan["group_capacity"]["max_group_positions"]
    count = len(packet["observations"]) + len(packet["unresolved_questions"])
    if Counter(p for g in groups for p in g) != Counter(range(count)) or any(not g for g in groups):
        raise ValueError("Relation groups do not exactly cover the packet")
    groups = sorted([sorted(g) for g in groups], key=lambda g: g[0])
    units = [(g, []) for g in groups]
    if packet["shard_receipts"]:
        units.append(([], list(range(len(packet["shard_receipts"])))))
    packed: list[tuple[list[int], list[int]]] = []
    for positions, receipts in units:
        for i, (old_p, old_r) in enumerate(packed):
            trial = make_job(packet, "accounting", "packing", plan["capacity"], limit, old_p + positions, old_r + receipts, parent_sha256)
            if trial.descriptor["context"]["fit"]:
                packed[i] = (old_p + positions, old_r + receipts)
                break
        else:
            trial = make_job(packet, "accounting", "packing", plan["capacity"], limit, positions, receipts, parent_sha256)
            if not trial.descriptor["context"]["fit"]:
                raise ValueError("atomic_unit_overflow; no group/receipt splitting")
            packed.append((positions, receipts))
    jobs = [make_job(packet, "accounting", f"batch-{i:03d}", plan["capacity"], limit, p, r, parent_sha256)
            for i, (p, r) in enumerate(packed, 1)]
    manifest = {"schema_version": 3, "parent_result_chain_sha256": parent_sha256,
                "packet_sha256": plan["bindings"]["packet_sha256"], "groups": groups,
                "jobs": [j.descriptor for j in jobs]}
    return manifest, jobs


def assemble_outputs(packet: dict[str, Any], jobs: list[ReductionJob], outputs: dict[str, Any],
                     group_limit: int) -> dict[str, Any]:
    if set(outputs) != {j.job_id for j in jobs}:
        raise ValueError("accounting job result coverage mismatch")
    combined: dict[str, Any] = {"schema_version": REDUCER_NORMAL_FORM_SCHEMA_VERSION, "records": [], "clusters": [], "unresolved_records": [], "shard_records": []}
    for job in jobs:
        output = outputs[job.job_id]
        errors = job_closure(job, output, group_limit)
        if errors:
            raise ValueError("; ".join(errors))
        output = decode_accounting_output(output)
        offset = len(combined["clusters"])
        combined["records"].extend({**r, "cluster_index": None if r["cluster_index"] is None else r["cluster_index"] + offset}
                                   for r in output["records"])
        for key in ("clusters", "unresolved_records", "shard_records"):
            combined[key].extend(output[key])
    errors, _ = reduction_closure(packet, combined)
    if errors:
        raise ValueError("full closure: " + "; ".join(errors))
    return combined
