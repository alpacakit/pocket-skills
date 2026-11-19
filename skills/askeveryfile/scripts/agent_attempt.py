"""Append-only Codex CLI execution primitive with no semantic judgment."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reducer_runtime_policy import (
    ReducerPolicy,
    reducer_policy_sha256,
    command_policy_errors,
    validate_usage,
)
from scan_artifacts import render_json, sha256_bytes, sha256_file
from scan_resources import MachineScoutSlots
from scout_backends import (
    CODEX_DISABLED_FEATURES,
    iter_json_lines,
    prepare_isolated_codex_home,
)


def _utc_precise() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_exclusive(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)


def _write_json_exclusive(path: Path, value: Any) -> None:
    _write_exclusive(path, render_json(value).encode("utf-8"))


@dataclass(frozen=True)
class PersistedAttemptInspection:
    """Canonical transport facts for one immutable reducer attempt."""

    errors: tuple[str, ...]
    attempt_dir: Path | None
    receipt_path: Path | None
    receipt_sha256: str | None
    receipt: dict[str, Any] | None
    request_sha256: str | None
    request: dict[str, Any] | None
    input_manifest: Any | None
    raw_result_sha256: str | None
    raw_result: Any | None
    lifecycle: str = "terminal"


@dataclass(frozen=True)
class AttemptExpectation:
    """Bound inputs shared by terminal and interrupted attempt inspection."""

    attempt_id: str
    number: int
    workdir: Path
    prompt: str
    input_manifest: dict[str, Any]
    schema: dict[str, Any]


def _expected_input_errors(directory: Path, expected: AttemptExpectation, policy: dict[str, Any], *, interrupted: bool) -> list[str]:
    runtime_policy = ReducerPolicy(**policy)
    errors: list[str] = []
    inputs = {
        "prompt.md": expected.prompt.encode("utf-8"),
        "input-manifest.json": render_json(expected.input_manifest).encode("utf-8"),
        "schema.json": render_json(expected.schema).encode("utf-8"),
    }
    request_path = directory / "request.json"
    # A request is written after all preflight inputs and before the subprocess.
    require_inputs = not interrupted or request_path.exists()
    for name, data in inputs.items():
        path = directory / name
        if not path.exists() and not path.is_symlink() and not require_inputs:
            continue
        try:
            if path.is_symlink() or path.read_bytes() != data:
                errors.append(f"attempt bound input differs: {name}")
        except OSError as exc:
            errors.append(f"cannot inspect bound attempt input {name}: {exc}")
    if not request_path.exists() and not request_path.is_symlink() and interrupted:
        if any((directory / name).exists() for name in ("stdout.jsonl", "stderr.log")):
            errors.append("attempt output exists without a request")
        return errors
    try:
        request_bytes = request_path.read_bytes()
        request = json.loads(request_bytes)
        if request_bytes != render_json(request).encode("utf-8"):
            errors.append("attempt request is not the canonical transport serialization")
        expected_fields = {
            "schema_version": 1, "phase": "reducer", "attempt_id": expected.attempt_id,
            "attempt": expected.number, "workdir": str(expected.workdir),
            "requested_model": policy["model"], "requested_effort": policy["effort"],
            "sandbox": policy["sandbox"], "tools": policy["tools"],
            "policy_sha256": reducer_policy_sha256(policy),
            "prompt_sha256": sha256_bytes(inputs["prompt.md"]),
            "input_manifest_sha256": sha256_bytes(inputs["input-manifest.json"]),
            "schema_sha256": sha256_bytes(inputs["schema.json"]),
        }
        dynamic = {"command", "cli_version", "started_at_utc", "started_monotonic_ns"}
        if request_path.is_symlink() or not isinstance(request, dict) or set(request) != set(expected_fields) | dynamic:
            errors.append("attempt request fields differ from the transport contract")
            return errors
        for key, value in expected_fields.items():
            if type(request.get(key)) is not type(value) or request[key] != value:
                errors.append(f"attempt request bound {key} differs")
        for key in ("cli_version", "started_at_utc"):
            if not isinstance(request[key], str) or not request[key].strip():
                errors.append(f"attempt request {key} is invalid")
        if type(request["started_monotonic_ns"]) is not int or request["started_monotonic_ns"] < 0:
            errors.append("attempt request monotonic time is invalid")
        command = request["command"]
        if not isinstance(command, list) or not command or not all(isinstance(p, str) for p in command):
            errors.append("attempt request command is invalid")
        elif command != _command(command[0], runtime_policy, expected.workdir,
                                  directory / "schema.json", directory / "raw-result.json"):
            errors.append("attempt request command differs from bound execution")
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"cannot inspect bound attempt request: {exc}")
    return errors


def inspect_persisted_attempt(
    receipt_path_value: Any,
    *,
    attempt_root: Path,
    policy: dict[str, Any],
    allow_failed: bool = False,
    expected: AttemptExpectation | None = None,
) -> PersistedAttemptInspection:
    """Inspect one persisted reducer attempt without domain or closure judgment."""
    empty = {
        "attempt_dir": None,
        "receipt_path": None,
        "receipt_sha256": None,
        "receipt": None,
        "request_sha256": None,
        "request": None,
        "input_manifest": None,
        "raw_result_sha256": None,
        "raw_result": None,
    }
    if not isinstance(receipt_path_value, str) or not receipt_path_value:
        return PersistedAttemptInspection(
            errors=("attempt receipt path is invalid",), **empty
        )
    try:
        receipt_path = Path(receipt_path_value).expanduser().resolve()
        resolved_attempt_root = attempt_root.expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return PersistedAttemptInspection(
            errors=(f"cannot resolve attempt receipt path: {exc}",), **empty
        )
    attempt_dir = receipt_path.parent
    errors: list[str] = []
    if receipt_path.name != "receipt.json":
        errors.append("attempt receipt file name is invalid")
    if resolved_attempt_root not in attempt_dir.parents:
        errors.append("attempt receipt is outside the attempt root")
        return PersistedAttemptInspection(
            errors=tuple(errors),
            attempt_dir=attempt_dir,
            receipt_path=receipt_path,
            receipt_sha256=None,
            receipt=None,
            request_sha256=None,
            request=None,
            input_manifest=None,
            raw_result_sha256=None,
            raw_result=None,
        )

    if expected is not None:
        interrupted = not receipt_path.exists() and not receipt_path.is_symlink()
        errors.extend(_expected_input_errors(attempt_dir, expected, policy, interrupted=interrupted))
        if interrupted:
            # Only absence of BOTH terminal outputs is resumable. A raw result,
            # including an empty one, may carry an uncounted semantic outcome.
            forbidden = {"raw-result.json", "closure.json", "result.json"}
            allowed = {"request.json", "prompt.md", "input-manifest.json", "schema.json", "stdout.jsonl", "stderr.log"}
            entries = list(attempt_dir.iterdir())
            if any(p.name in forbidden for p in entries):
                errors.append("attempt has raw/closure/result without a receipt; result is unknown")
            if any(p.name not in allowed or p.is_symlink() or not p.is_file() for p in entries):
                errors.append("interrupted attempt has unexpected or unsafe artifacts")
            return PersistedAttemptInspection(
                errors=tuple(errors), **{**empty, "attempt_dir": attempt_dir, "receipt_path": receipt_path},
                lifecycle="interrupted" if not errors else "invalid",
            )

    receipt_sha256: str | None = None
    receipt: dict[str, Any] | None = None
    try:
        raw_receipt = receipt_path.read_bytes()
        receipt_sha256 = sha256_bytes(raw_receipt)
        parsed_receipt = json.loads(raw_receipt.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read attempt receipt {receipt_path}: {exc}")
    else:
        if isinstance(parsed_receipt, dict):
            receipt = parsed_receipt
        else:
            errors.append("attempt receipt is not an object")

    request_path = attempt_dir / "request.json"
    request_sha256: str | None = None
    request: dict[str, Any] | None = None
    try:
        raw_request = request_path.read_bytes()
        request_sha256 = sha256_bytes(raw_request)
        parsed_request = json.loads(raw_request.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read attempt request {request_path}: {exc}")
    else:
        if isinstance(parsed_request, dict):
            request = parsed_request
        else:
            errors.append("attempt request is not an object")

    if receipt is not None and request_sha256 is not None:
        if receipt.get("request_sha256") != request_sha256:
            errors.append("attempt request hash mismatch")
    if receipt is not None and request is not None:
        if request.get("schema_version") != 1:
            errors.append("attempt request schema mismatch")
        if receipt.get("schema_version") != 1:
            errors.append("attempt receipt schema mismatch")
        if request.get("phase") != "reducer" or receipt.get("phase") != "reducer":
            errors.append("attempt phase is not reducer")
        if not isinstance(receipt.get("attempt_id"), str) or not receipt.get(
            "attempt_id"
        ):
            errors.append("attempt id is invalid")
        if type(receipt.get("attempt")) is not int or receipt["attempt"] < 1:
            errors.append("attempt number is invalid")
        errors.extend(command_policy_errors(
            request.get("command"), receipt, policy,
            require_success=not (allow_failed and receipt.get("status") == "failed"),
        ))
        for field in (
            "schema_version",
            "attempt_id",
            "attempt",
            "phase",
            "cli_version",
            "requested_model",
            "requested_effort",
            "sandbox",
            "tools",
            "policy_sha256",
            "prompt_sha256",
            "input_manifest_sha256",
            "schema_sha256",
            "started_at_utc",
            "started_monotonic_ns",
        ):
            if request.get(field) != receipt.get(field):
                errors.append(f"attempt request/receipt {field} mismatch")
        if receipt.get("status") not in ("complete", "failed"):
            errors.append("attempt receipt has no terminal status")
        if receipt.get("status") == "failed" and not receipt.get("failure_reasons"):
            errors.append("failed attempt has no failure reasons")
        if receipt.get("status") == "complete" and receipt.get("failure_reasons") != []:
            errors.append("attempt receipt has failure reasons")

    for name, field in (
        ("prompt.md", "prompt_sha256"),
        ("schema.json", "schema_sha256"),
        ("stdout.jsonl", "stdout_sha256"),
        ("stderr.log", "stderr_sha256"),
    ):
        try:
            actual = sha256_file(attempt_dir / name)
        except OSError as exc:
            errors.append(f"cannot hash attempt artifact {name}: {exc}")
            continue
        if receipt is not None and receipt.get(field) != actual:
            errors.append(f"attempt artifact hash mismatch: {name}")

    input_manifest: Any | None = None
    manifest_path = attempt_dir / "input-manifest.json"
    try:
        raw_manifest = manifest_path.read_bytes()
        manifest_sha256 = sha256_bytes(raw_manifest)
        input_manifest = json.loads(raw_manifest.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read attempt input manifest {manifest_path}: {exc}")
    else:
        if receipt is not None and receipt.get(
            "input_manifest_sha256"
        ) != manifest_sha256:
            errors.append("attempt artifact hash mismatch: input-manifest.json")

    raw_result_sha256: str | None = None
    raw_result: Any | None = None
    raw_result_path = attempt_dir / "raw-result.json"
    try:
        raw_result_bytes = raw_result_path.read_bytes()
        raw_result_sha256 = sha256_bytes(raw_result_bytes)
    except OSError as exc:
        errors.append(f"cannot read attempt raw result {raw_result_path}: {exc}")
    else:
        if receipt is not None:
            if receipt.get("result_sha256") != raw_result_sha256:
                errors.append("attempt raw result hash differs from receipt")
        try:
            raw_result = json.loads(raw_result_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            if not (allow_failed and receipt is not None and receipt.get("status") == "failed"):
                errors.append(f"cannot parse attempt raw result: {exc}")

    if receipt is not None:
        try:
            facts = _transport_facts((attempt_dir / "stdout.jsonl").read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot reconstruct terminal transport events: {exc}")
        else:
            for field in ("thread_id", "resolved_model", "model_resolution_status", "resolved_effort",
                          "effort_resolution_status", "observed_tool_items", "usage"):
                if receipt.get(field) != facts.get(field):
                    errors.append(f"attempt receipt {field} differs from terminal events")
            if receipt.get("exit_status") == 0 and receipt.get("raw_result_status") == "valid":
                expected_complete = not _policy_errors(facts, ReducerPolicy(**policy))
                if expected_complete != (receipt.get("status") == "complete"):
                    errors.append("attempt terminal status hides or invents transport completion")
        if raw_result is not None and receipt.get("raw_result_status") not in ("valid", "schema_invalid"):
            errors.append("attempt raw result parse status differs from persisted bytes")

    return PersistedAttemptInspection(
        errors=tuple(errors),
        attempt_dir=attempt_dir,
        receipt_path=receipt_path,
        receipt_sha256=receipt_sha256,
        receipt=receipt,
        request_sha256=request_sha256,
        request=request,
        input_manifest=input_manifest,
        raw_result_sha256=raw_result_sha256,
        raw_result=raw_result,
    )


def _command(
    executable: str,
    policy: ReducerPolicy,
    workdir: Path,
    schema_path: Path,
    result_path: Path,
) -> list[str]:
    command = [
        executable,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
    ]
    for feature in CODEX_DISABLED_FEATURES:
        command.extend(("--disable", feature))
    command.extend(
        (
            "-C",
            str(workdir),
            "-m",
            policy.model,
            "-c",
            f"model_reasoning_effort={policy.effort}",
            "-c",
            'approval_policy="never"',
            "--sandbox",
            policy.sandbox,
            "--output-schema",
            str(schema_path),
            "-o",
            str(result_path),
            "-",
        )
    )
    return command


def _usage_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    usage: Any = None
    for event in events:
        if event.get("type") == "turn.completed" and isinstance(
            event.get("usage"), dict
        ):
            usage = event["usage"]
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if type(input_tokens) is not int or type(output_tokens) is not int:
        return None
    cached = usage.get("cached_input_tokens")
    reasoning = usage.get("reasoning_output_tokens", usage.get("reasoning_tokens"))
    if cached is not None and type(cached) is not int:
        return None
    if reasoning is not None and type(reasoning) is not int:
        return None
    normalized: dict[str, Any] = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning,
    }
    normalized["total_tokens"] = (
        normalized["input_tokens"] + normalized["output_tokens"]
    )
    return normalized


def _transport_facts(stdout: str) -> dict[str, Any]:
    events = list(iter_json_lines(stdout))
    thread_ids = [
        event.get("thread_id")
        for event in events
        if event.get("type") == "thread.started"
        and isinstance(event.get("thread_id"), str)
    ]
    models: list[str] = []
    efforts: list[str] = []
    tool_items: list[str] = []
    for event in events:
        model = event.get("model")
        effort = event.get("effort")
        if isinstance(model, str):
            models.append(model)
        if isinstance(effort, str):
            efforts.append(effort)
        item = event.get("item")
        if isinstance(item, dict):
            item_type = item.get("type")
            if isinstance(item_type, str) and item_type not in (
                "agent_message",
                "reasoning",
                "error",
            ):
                tool_items.append(item_type)
    unique_models = sorted(set(models))
    unique_efforts = sorted(set(efforts))
    return {
        "thread_id": thread_ids[-1] if thread_ids else None,
        "resolved_model": unique_models[0] if len(unique_models) == 1 else None,
        "model_resolution_status": (
            "reported" if len(unique_models) == 1 else "not_reported_by_cli"
            if not unique_models
            else "conflicting_reports"
        ),
        "resolved_effort": unique_efforts[0] if len(unique_efforts) == 1 else None,
        "effort_resolution_status": (
            "reported" if len(unique_efforts) == 1 else "not_reported_by_cli"
            if not unique_efforts
            else "conflicting_reports"
        ),
        "observed_tool_items": sorted(set(tool_items)),
        "usage": _usage_from_events(events),
    }


def _policy_errors(facts: dict[str, Any], policy: ReducerPolicy) -> list[str]:
    errors: list[str] = []
    if facts["thread_id"] is None:
        errors.append("Codex returned no thread.started event")
    if facts["model_resolution_status"] == "reported" and facts[
        "resolved_model"
    ] != policy.model:
        errors.append("resolved model does not match requested model")
    if facts["model_resolution_status"] == "conflicting_reports":
        errors.append("Codex reported conflicting resolved models")
    if facts["effort_resolution_status"] == "reported" and facts[
        "resolved_effort"
    ] != policy.effort:
        errors.append("resolved effort does not match requested effort")
    if facts["effort_resolution_status"] == "conflicting_reports":
        errors.append("Codex reported conflicting resolved efforts")
    if facts["observed_tool_items"]:
        errors.append(f"Codex used tools: {facts['observed_tool_items']}")
    errors.extend(validate_usage(facts["usage"]))
    return errors


def execute_codex_attempt(
    *,
    attempt_dir: Path,
    attempt_id: str,
    attempt_number: int,
    executable: str,
    cli_version: str,
    policy: ReducerPolicy,
    policy_sha256: str,
    workdir: Path,
    prompt: str,
    input_manifest: dict[str, Any],
    schema: dict[str, Any],
    output_errors: Callable[[Any], list[str]],
    machine_slots: MachineScoutSlots,
) -> tuple[dict[str, Any], Any | None]:
    """Execute one attempt and write every byte exactly once."""
    attempt_dir.mkdir(parents=True, exist_ok=False)
    prompt_path = attempt_dir / "prompt.md"
    manifest_path = attempt_dir / "input-manifest.json"
    schema_path = attempt_dir / "schema.json"
    stdout_path = attempt_dir / "stdout.jsonl"
    stderr_path = attempt_dir / "stderr.log"
    result_path = attempt_dir / "raw-result.json"
    request_path = attempt_dir / "request.json"
    receipt_path = attempt_dir / "receipt.json"
    prompt_bytes = prompt.encode("utf-8")
    manifest_bytes = render_json(input_manifest).encode("utf-8")
    schema_bytes = render_json(schema).encode("utf-8")
    _write_exclusive(prompt_path, prompt_bytes)
    _write_exclusive(manifest_path, manifest_bytes)
    _write_exclusive(schema_path, schema_bytes)
    command = _command(executable, policy, workdir, schema_path, result_path)
    started_at = _utc_precise()
    started_ns = time.monotonic_ns()
    request = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "attempt": attempt_number,
        "phase": "reducer",
        "command": command,
        "cli_version": cli_version,
        "workdir": str(workdir),
        "requested_model": policy.model,
        "requested_effort": policy.effort,
        "sandbox": policy.sandbox,
        "tools": list(policy.tools),
        "policy_sha256": policy_sha256,
        "prompt_sha256": sha256_bytes(prompt_bytes),
        "input_manifest_sha256": sha256_bytes(manifest_bytes),
        "schema_sha256": sha256_bytes(schema_bytes),
        "started_at_utc": started_at,
        "started_monotonic_ns": started_ns,
    }
    _write_json_exclusive(request_path, request)

    stdout = ""
    stderr = ""
    exit_status: int | None = None
    launch_error: str | None = None
    with tempfile.TemporaryDirectory(prefix=f"askeveryfile-{attempt_id}-") as temp:
        isolated_home = Path(temp) / "codex-home"
        try:
            prepare_isolated_codex_home(executable, cli_version, isolated_home)
            process_env = os.environ.copy()
            process_env["CODEX_HOME"] = str(isolated_home)
            with machine_slots.hold(attempt_number) as coordinated:
                process = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    cwd=workdir,
                    env=process_env,
                    check=False,
                )
            stdout = process.stdout
            stderr = process.stderr
            exit_status = process.returncode
        except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as exc:
            coordinated = False
            launch_error = f"{type(exc).__name__}: {exc}"
    _write_exclusive(stdout_path, stdout.encode("utf-8"))
    _write_exclusive(stderr_path, stderr.encode("utf-8"))

    facts = _transport_facts(stdout)
    errors: list[str] = []
    parsed: Any | None = None
    raw_result_status = "missing"
    if launch_error:
        errors.append(launch_error)
    if exit_status != 0:
        errors.append(f"Codex exit status is {exit_status}")
    produced_result = result_path.exists()
    if not produced_result:
        errors.append("Codex produced no raw result")
        _write_exclusive(result_path, b"")
    raw = result_path.read_bytes()
    raw_result_sha256 = sha256_bytes(raw)
    if result_path.exists():
        if not raw:
            if produced_result:
                raw_result_status = "empty"
            errors.append("Codex raw result is empty")
        else:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raw_result_status = "invalid_json"
                errors.append(f"Codex raw result is invalid JSON: {exc}")
            else:
                shape_errors = output_errors(parsed)
                if shape_errors:
                    raw_result_status = "schema_invalid"
                    errors.extend(shape_errors)
                else:
                    raw_result_status = "valid"
    if exit_status == 0:
        errors.extend(_policy_errors(facts, policy))

    ended_ns = time.monotonic_ns()
    receipt = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "attempt": attempt_number,
        "phase": "reducer",
        "status": "complete" if not errors else "failed",
        "failure_reasons": errors,
        "command": command,
        "cli_version": cli_version,
        "requested_model": policy.model,
        "requested_effort": policy.effort,
        "sandbox": policy.sandbox,
        "tools": list(policy.tools),
        "policy_sha256": policy_sha256,
        "thread_id": facts["thread_id"],
        "resolved_model": facts["resolved_model"],
        "model_resolution_status": facts["model_resolution_status"],
        "resolved_effort": facts["resolved_effort"],
        "effort_resolution_status": facts["effort_resolution_status"],
        "observed_tool_items": facts["observed_tool_items"],
        "started_at_utc": started_at,
        "ended_at_utc": _utc_precise(),
        "started_monotonic_ns": started_ns,
        "ended_monotonic_ns": ended_ns,
        "elapsed_monotonic_ms": (ended_ns - started_ns) / 1_000_000,
        "exit_status": exit_status,
        "machine_slot_coordinated": coordinated,
        "request_sha256": sha256_bytes(request_path.read_bytes()),
        "prompt_sha256": sha256_bytes(prompt_bytes),
        "input_manifest_sha256": sha256_bytes(manifest_bytes),
        "schema_sha256": sha256_bytes(schema_bytes),
        "stdout_sha256": sha256_bytes(stdout.encode("utf-8")),
        "stderr_sha256": sha256_bytes(stderr.encode("utf-8")),
        "result_sha256": raw_result_sha256,
        "raw_result_status": raw_result_status,
        "usage": facts["usage"],
        "usage_status": "reported" if facts["usage"] is not None else "missing",
    }
    _write_json_exclusive(receipt_path, receipt)
    return receipt, parsed
