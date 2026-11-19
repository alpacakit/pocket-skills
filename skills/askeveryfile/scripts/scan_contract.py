"""Persisted schemas, identity checks, and scout policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scan_artifacts import sha256_text

PLAN_SCHEMA_VERSION = 4
RESULT_SCHEMA_VERSION = 6
VALIDATION_SCHEMA_VERSION = 4
REVIEW_PACKET_SCHEMA_VERSION = 4

CONFIDENCES = ("low", "medium", "high")
ANALYSIS_FIELDS = (
    "summary",
    "observations",
    "unresolved_questions",
    "overflowed",
)
OBSERVATION_FIELDS = (
    "title",
    "category",
    "claim",
    "why_it_matters",
    "evidence",
    "confidence",
)
RAW_EVIDENCE_FIELDS = ("path", "line")
MATERIALIZED_EVIDENCE_FIELDS = ("path", "line", "locator", "quote")
SCOUT_RUN_FIELDS = (
    "cli",
    "agent",
    "model",
    "effort",
    "thread_id",
    "attempt",
    "sandbox",
    "resolved_models",
    "initialized_tools",
    "total_cost_usd",
)

MAX_OBSERVATIONS = 50
MAX_EVIDENCE_ITEMS = 3


def unresolved_id(shard_id: str, ordinal: int) -> str:
    if not shard_id or ordinal < 1:
        raise ValueError("unresolved ids require a shard id and 1-based ordinal")
    return f"{shard_id}:unresolved:{ordinal}"


def receipt_id(shard_id: str) -> str:
    if not shard_id:
        raise ValueError("receipt ids require a shard id")
    return f"{shard_id}:receipt"


@dataclass(frozen=True)
class ScoutAgentPolicy:
    """Fixed transport constraints for one selectable scout agent."""

    initialized_tools: tuple[str, ...]
    resolved_model_may_be_qualified: bool
    reports_cost: bool


SCOUT_AGENT_POLICIES: dict[str, ScoutAgentPolicy] = {
    "codex": ScoutAgentPolicy(
        initialized_tools=(),
        resolved_model_may_be_qualified=False,
        reports_cost=False,
    ),
    "claude": ScoutAgentPolicy(
        initialized_tools=("StructuredOutput",),
        resolved_model_may_be_qualified=True,
        reports_cost=True,
    ),
}
# Codex reports work as typed items rather than an init tool list, so tool use
# is observed by item type. "error" carries transport diagnostics such as the
# WebSocket-to-HTTPS fallback and is not scout action. Anything outside this set
# is treated as tool use rather than assumed benign.
CODEX_NON_TOOL_ITEM_TYPES = ("agent_message", "reasoning", "error")
DELIVERY_MODE = "full_text_in_prompt"
SCOUT_SANDBOX = "read-only"

MODEL_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": list(ANALYSIS_FIELDS),
    "properties": {
        "summary": {"type": "string"},
        "observations": {
            "type": "array",
            "maxItems": MAX_OBSERVATIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(OBSERVATION_FIELDS),
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "category": {"type": "string", "minLength": 1},
                    "claim": {"type": "string", "minLength": 1},
                    "why_it_matters": {"type": "string", "minLength": 1},
                    "evidence": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_EVIDENCE_ITEMS,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(RAW_EVIDENCE_FIELDS),
                            "properties": {
                                "path": {"type": "string", "minLength": 1},
                                "line": {"type": "integer", "minimum": 1},
                            },
                        },
                    },
                    "confidence": {"type": "string", "enum": list(CONFIDENCES)},
                },
            },
        },
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
        "overflowed": {"type": "boolean"},
    },
}


def question_identity_errors(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["record is not an object"]
    errors: list[str] = []
    question = value.get("question")
    if not isinstance(question, str) or not question.strip():
        errors.append("question is missing or empty")
    elif value.get("question_sha256") != sha256_text(question):
        errors.append("question_sha256 does not match question")
    return errors


def plan_contract_errors(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["plan is not an object"]
    errors = question_identity_errors(value)
    if value.get("schema_version") != PLAN_SCHEMA_VERSION:
        errors.append("plan schema version mismatch")
    for field in ("scan_id", "snapshot_id", "root"):
        if not isinstance(value.get(field), str) or not value[field]:
            errors.append(f"plan {field} is missing")
    for field in ("files", "shards"):
        if not isinstance(value.get(field), list):
            errors.append(f"plan {field} is not an array")
    for field in ("coverage", "execution"):
        if not isinstance(value.get(field), dict):
            errors.append(f"plan {field} is not an object")
    return errors


def shard_identity_errors(
    plan: Any, shard: Any, expected_shard_id: str | None = None
) -> list[str]:
    if not isinstance(plan, dict) or not isinstance(shard, dict):
        return ["plan or shard is not an object"]
    errors = [f"shard: {error}" for error in question_identity_errors(shard)]
    if shard.get("schema_version") != PLAN_SCHEMA_VERSION:
        errors.append("shard schema version mismatch")
    for key in ("scan_id", "snapshot_id", "question", "question_sha256", "root"):
        if shard.get(key) != plan.get(key):
            errors.append(f"shard {key} mismatch")
    if expected_shard_id is not None and shard.get("shard_id") != expected_shard_id:
        errors.append("shard id does not match its filename")
    return errors


def result_identity_errors(plan: Any, result: Any, expected_shard_id: str) -> list[str]:
    if not isinstance(plan, dict) or not isinstance(result, dict):
        return ["plan or result is not an object"]
    errors: list[str] = []
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        errors.append("result schema version mismatch")
    for key in ("scan_id", "snapshot_id", "question_sha256"):
        if result.get(key) != plan.get(key):
            errors.append(f"result {key} mismatch")
    if result.get("shard_id") != expected_shard_id:
        errors.append("result shard_id mismatch")
    if result.get("status") != "complete":
        errors.append("result is not complete")
    return errors


def scout_policy_errors(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["scan scout policy is missing"]
    errors = []
    if set(value) != {"agent", "model", "effort"}:
        errors.append("scan scout policy fields mismatch")
    if not isinstance(value.get("agent"), str) or value["agent"] not in SCOUT_AGENT_POLICIES:
        errors.append("scan scout policy agent is unsupported")
    for field in ("model", "effort"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"scan scout policy {field} is missing or empty")
    return errors


def resolved_models_match_policy(agent: str, expected: str, models: Any) -> bool:
    if (
        agent not in SCOUT_AGENT_POLICIES
        or not isinstance(models, list)
        or not models
        or not all(isinstance(model, str) for model in models)
    ):
        return False
    policy = SCOUT_AGENT_POLICIES[agent]
    if policy.resolved_model_may_be_qualified:
        return all(expected.lower() in model.lower() for model in models)
    return models == [expected]


def scout_run_policy_errors(value: Any, requested: Any) -> list[str]:
    policy_errors = scout_policy_errors(requested)
    if policy_errors:
        return policy_errors
    if not isinstance(value, dict):
        return ["scout_run metadata missing"]
    errors: list[str] = []
    if set(value) != set(SCOUT_RUN_FIELDS):
        errors.append("scout_run fields do not match the contract")
    agent = value.get("agent")
    if not isinstance(agent, str) or agent not in SCOUT_AGENT_POLICIES:
        errors.append("scout_run agent is unsupported")
        return errors
    policy = SCOUT_AGENT_POLICIES[agent]
    for field in ("agent", "model", "effort"):
        if value.get(field) != requested[field]:
            errors.append(f"scout_run {field} does not match policy")
    if value.get("sandbox") != SCOUT_SANDBOX:
        errors.append("scout_run sandbox does not match policy")
    if not resolved_models_match_policy(agent, requested["model"], value.get("resolved_models")):
        errors.append("resolved model does not match policy")
    if value.get("initialized_tools") != list(policy.initialized_tools):
        errors.append("initialized tools do not match policy")
    if not isinstance(value.get("cli"), str) or not value["cli"]:
        errors.append("scout_run cli is missing")
    if not isinstance(value.get("thread_id"), str) or not value["thread_id"]:
        errors.append("scout_run thread_id is missing")
    attempt = value.get("attempt")
    if type(attempt) is not int or attempt < 1:
        errors.append("scout_run attempt is invalid")
    # Cost is an observed transport fact, not a runner budget.
    cost = value.get("total_cost_usd")
    if policy.reports_cost and (type(cost) not in (int, float) or cost < 0):
        errors.append("scout_run cost is missing or invalid")
    elif not policy.reports_cost and cost is not None:
        errors.append("scout_run total cost must be unknown")
    return errors


def analysis_shape_errors(value: Any, *, materialized: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["analysis is not an object"]
    if set(value) != set(ANALYSIS_FIELDS):
        errors.append("analysis fields do not match the contract")
    if not isinstance(value.get("summary"), str):
        errors.append("summary is missing")
    unresolved = value.get("unresolved_questions")
    if not isinstance(unresolved, list) or not all(
        isinstance(item, str) for item in unresolved
    ):
        errors.append("unresolved_questions is not a string array")
    if type(value.get("overflowed")) is not bool:
        errors.append("overflowed is not a boolean")
    observations = value.get("observations")
    if not isinstance(observations, list):
        errors.append("observations is not an array")
        return errors
    if len(observations) > MAX_OBSERVATIONS:
        errors.append(f"observations exceeds the maximum of {MAX_OBSERVATIONS}")
    evidence_fields = (
        MATERIALIZED_EVIDENCE_FIELDS if materialized else RAW_EVIDENCE_FIELDS
    )
    for observation_index, observation in enumerate(observations):
        label = f"observation[{observation_index}]"
        if not isinstance(observation, dict):
            errors.append(f"{label} is not an object")
            continue
        if set(observation) != set(OBSERVATION_FIELDS):
            errors.append(f"{label} fields do not match the contract")
        for field in ("title", "category", "claim", "why_it_matters"):
            if (
                not isinstance(observation.get(field), str)
                or not observation[field].strip()
            ):
                errors.append(f"{label} has empty {field}")
        if observation.get("confidence") not in CONFIDENCES:
            errors.append(f"{label} has invalid confidence")
        evidence = observation.get("evidence")
        if (
            not isinstance(evidence, list)
            or not 1 <= len(evidence) <= MAX_EVIDENCE_ITEMS
        ):
            errors.append(
                f"{label} must have one to {MAX_EVIDENCE_ITEMS} evidence items"
            )
            continue
        for evidence_index, item in enumerate(evidence):
            evidence_label = f"{label}.evidence[{evidence_index}]"
            if not isinstance(item, dict):
                errors.append(f"{evidence_label} is not an object")
                continue
            if set(item) != set(evidence_fields):
                errors.append(f"{evidence_label} fields do not match the contract")
            if not isinstance(item.get("path"), str) or not item["path"]:
                errors.append(f"{evidence_label} has an invalid path")
            line = item.get("line")
            if type(line) is not int or line < 1:
                errors.append(f"{evidence_label} has an invalid line")
            if materialized and not isinstance(item.get("locator"), str):
                errors.append(f"{evidence_label} has an invalid locator")
            if materialized and not isinstance(item.get("quote"), str):
                errors.append(f"{evidence_label} has an invalid quote")
    return errors
