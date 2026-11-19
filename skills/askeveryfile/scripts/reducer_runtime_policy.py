"""Reducer runtime policy, prompt framing, and persisted receipt validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from scan_artifacts import sha256_text

REDUCER_INPUT_START = "<<<ASKEVERYFILE_REDUCTION:INPUT>>>"
REDUCER_INPUT_END = "<<<ASKEVERYFILE_REDUCTION:END_INPUT>>>"


@dataclass(frozen=True)
class ReducerPolicy:
    model: str
    effort: str
    agent: str = "codex"
    sandbox: str = "read-only"
    tools: tuple[str, ...] = ()


def reducer_policy_value(model: str, effort: str) -> dict[str, Any]:
    if not _is_nonempty_string(model) or not _is_nonempty_string(effort):
        raise ValueError("Reducer model and effort must be nonempty strings")
    value = asdict(ReducerPolicy(model=model, effort=effort))
    value["tools"] = list(value["tools"])
    return value


def reducer_policy_sha256(policy: Any) -> str:
    return sha256_text(
        json.dumps(
            policy,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def reducer_policy_errors(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["reducer policy is not an object"]
    try:
        expected = reducer_policy_value(value.get("model"), value.get("effort"))
    except ValueError as exc:
        return [str(exc)]
    return [] if value == expected else ["reducer policy value mismatch"]


def render_reducer_prompt(template: str, batch: str) -> str:
    """Render the one exact reducer prompt used for fit, execution, and validation."""
    return (
        template.rstrip("\n")
        + f"\n\n{REDUCER_INPUT_START}\n"
        + batch.rstrip("\n")
        + f"\n{REDUCER_INPUT_END}\n"
    )


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_usage(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["successful attempt usage is missing"]
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    expected = set(fields)
    actual = set(value)
    errors = [] if actual == expected else [
        f"usage fields mismatch: missing={sorted(expected - actual)}, "
        f"unexpected={sorted(actual - expected)}"
    ]
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        if type(value.get(field)) is not int or value[field] < 0:
            errors.append(f"usage {field} is invalid")
    for field in ("cached_input_tokens", "reasoning_output_tokens"):
        if value.get(field) is not None and (
            type(value[field]) is not int or value[field] < 0
        ):
            errors.append(f"usage {field} is invalid")
    if not errors and value["total_tokens"] != (
        value["input_tokens"] + value["output_tokens"]
    ):
        errors.append("usage total_tokens must equal input_tokens + output_tokens")
    if (
        type(value.get("cached_input_tokens")) is int
        and isinstance(value.get("input_tokens"), int)
        and value["cached_input_tokens"] > value["input_tokens"]
    ):
        errors.append("cached_input_tokens exceeds input_tokens")
    if (
        type(value.get("reasoning_output_tokens")) is int
        and isinstance(value.get("output_tokens"), int)
        and value["reasoning_output_tokens"] > value["output_tokens"]
    ):
        errors.append("reasoning_output_tokens exceeds output_tokens")
    return errors


def command_policy_errors(command: Any, receipt: Any, policy: Any, *, require_success: bool = True) -> list[str]:
    """Validate persisted request/receipt facts against the plan's reducer policy."""
    errors = reducer_policy_errors(policy)
    if errors:
        return errors
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        return ["attempt command is not a string array"]
    errors: list[str] = []

    def option_values(option: str) -> list[str]:
        return [
            command[index + 1]
            for index, item in enumerate(command[:-1])
            if item == option
        ]

    if option_values("-m") != [policy["model"]]:
        errors.append("attempt command model does not match policy")
    if f"model_reasoning_effort={policy['effort']}" not in option_values("-c"):
        errors.append("attempt command effort does not match policy")
    if option_values("--sandbox") != [policy["sandbox"]]:
        errors.append("attempt command sandbox does not match policy")
    for required in ("--json", "--ephemeral", "--ignore-user-config", "-"):
        if required not in command:
            errors.append(f"attempt command lacks {required}")
    if not isinstance(receipt, dict):
        return errors + ["attempt receipt is not an object"]
    if require_success and (receipt.get("status") != "complete" or receipt.get("exit_status") != 0):
        errors.append("attempt receipt is not a successful terminal receipt")
    if require_success and receipt.get("raw_result_status") != "valid":
        errors.append("attempt receipt raw result status is not valid")
    if receipt.get("command") != command:
        errors.append("attempt request and receipt commands differ")
    for field, expected in (
        ("requested_model", policy["model"]),
        ("requested_effort", policy["effort"]),
        ("sandbox", policy["sandbox"]),
        ("tools", policy["tools"]),
        ("policy_sha256", reducer_policy_sha256(policy)),
    ):
        if receipt.get(field) != expected:
            errors.append(f"attempt receipt {field} does not match policy")
    if not require_success:
        return errors
    model_status = receipt.get("model_resolution_status")
    resolved_model = receipt.get("resolved_model")
    if model_status == "reported" and resolved_model != policy["model"]:
        errors.append("attempt resolved model does not match policy")
    elif model_status == "not_reported_by_cli" and resolved_model is not None:
        errors.append("unreported resolved model must be null")
    elif model_status not in ("reported", "not_reported_by_cli"):
        errors.append("attempt model resolution status is invalid")
    effort_status = receipt.get("effort_resolution_status")
    resolved_effort = receipt.get("resolved_effort")
    if effort_status == "reported" and resolved_effort != policy["effort"]:
        errors.append("attempt resolved effort does not match policy")
    elif effort_status == "not_reported_by_cli" and resolved_effort is not None:
        errors.append("unreported resolved effort must be null")
    elif effort_status not in ("reported", "not_reported_by_cli"):
        errors.append("attempt effort resolution status is invalid")
    if receipt.get("observed_tool_items") != []:
        errors.append("attempt receipt reports tool use")
    if not _is_nonempty_string(receipt.get("cli_version")):
        errors.append("attempt receipt CLI version is missing")
    if not _is_nonempty_string(receipt.get("thread_id")):
        errors.append("attempt receipt thread id is missing")
    for field in ("started_at_utc", "ended_at_utc"):
        if not _is_nonempty_string(receipt.get(field)):
            errors.append(f"attempt receipt {field} is missing")
    elapsed = receipt.get("elapsed_monotonic_ms")
    if type(elapsed) not in (int, float) or elapsed < 0:
        errors.append("attempt receipt monotonic elapsed time is invalid")
    errors.extend(validate_usage(receipt.get("usage")))
    return errors
