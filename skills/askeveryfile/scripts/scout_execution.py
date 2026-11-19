"""Prepare, execute, validate, and persist one scout shard at a time."""

from __future__ import annotations

import gzip
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scan_artifacts import (
    read_json,
    render_json,
    sha256_text,
    utc_now,
    write_json,
    write_json_atomic,
)
from scan_contract import (
    DELIVERY_MODE,
    RESULT_SCHEMA_VERSION,
    SCOUT_SANDBOX,
    analysis_shape_errors,
    result_identity_errors,
    scout_run_policy_errors,
    shard_identity_errors,
)
from scan_resources import MachineScoutSlots
from scout_backends import ScoutBackend
from scout_delivery import (
    build_scout_prompt,
    materialize_evidence,
    output_schema_for_units,
)


@dataclass(frozen=True)
class RunContext:
    plan: dict[str, Any]
    scan_dir: Path
    backend: ScoutBackend
    retries: int
    runtime_workdir: Path
    home_template: Path | None
    homes_root: Path | None
    machine_slots: MachineScoutSlots
    keep_prompts: bool


@dataclass(frozen=True)
class PreparedShard:
    shard: dict[str, Any]
    shard_id: str
    result_path: Path
    prompt: str
    delivered_units: dict[str, list[dict[str, Any]]]
    prompt_path: Path
    prompt_sha256: str
    schema_path: Path
    schema_sha256: str
    output_schema: dict[str, Any]
    slot_offset: int


@dataclass(frozen=True)
class AttemptFiles:
    last_message: Path
    log: Path
    stderr: Path
    invocation: Path


class AttemptFailed(Exception):
    """One scout attempt that did not reach a stored, evidence-backed result."""


def _existing_result(
    context: RunContext,
    prepared: PreparedShard,
) -> tuple[dict[str, Any] | None, int]:
    if not prepared.result_path.exists():
        return None, 0
    try:
        value = read_json(prepared.result_path)
    except (OSError, json.JSONDecodeError):
        return None, 0
    if not isinstance(value, dict):
        return None, 0
    scout_run = value.get("scout_run")
    if not isinstance(scout_run, dict):
        return None, 0
    identity_matches = (
        not result_identity_errors(context.plan, value, prepared.shard_id)
        and value.get("prompt_sha256") == prepared.prompt_sha256
        and value.get("schema_sha256") == prepared.schema_sha256
        and scout_run.get("agent") == context.backend.name
        and scout_run.get("model") == context.backend.model
        and scout_run.get("effort") == context.backend.effort
        and not scout_run_policy_errors(scout_run, context.backend.requested)
    )
    if not identity_matches or analysis_shape_errors(
        value.get("analysis"), materialized=True
    ):
        return None, 0
    materialized, errors, migrations = materialize_evidence(
        value.get("analysis"), prepared.delivered_units
    )
    if errors:
        return None, 0
    value["analysis"] = materialized
    return value, migrations


def _next_attempt_number(log_dir: Path, shard_id: str) -> int:
    attempts: list[int] = []
    pattern = re.compile(rf"^{re.escape(shard_id)}\.attempt-(\d+)\.invocation\.json$")
    for path in log_dir.glob(f"{shard_id}.attempt-*.invocation.json"):
        match = pattern.match(path.name)
        if match:
            attempts.append(int(match.group(1)))
    return max(attempts, default=0) + 1


def _prepare_shard(shard_path: Path, context: RunContext) -> PreparedShard:
    shard = read_json(shard_path)
    identity_errors = shard_identity_errors(context.plan, shard, shard_path.stem)
    if identity_errors:
        raise RuntimeError("; ".join(identity_errors))
    shard_id = shard["shard_id"]
    prompt, delivered_units = build_scout_prompt(shard)
    output_schema = output_schema_for_units(delivered_units)
    return PreparedShard(
        shard=shard,
        shard_id=shard_id,
        result_path=context.scan_dir / "results" / f"{shard_id}.json",
        prompt=prompt,
        delivered_units=delivered_units,
        prompt_path=context.scan_dir / "prompts" / f"{shard_id}.md.gz",
        prompt_sha256=sha256_text(prompt),
        schema_path=context.scan_dir / "schemas" / f"{shard_id}.schema.json",
        schema_sha256=sha256_text(render_json(output_schema)),
        output_schema=output_schema,
        slot_offset=int(re.sub(r"\D", "", shard_id) or 0),
    )


def _persist_prompt(prepared: PreparedShard) -> Path:
    prepared.prompt_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{prepared.prompt_path}.tmp")
    temporary.write_bytes(gzip.compress(prepared.prompt.encode("utf-8"), mtime=0))
    temporary.replace(prepared.prompt_path)
    return prepared.prompt_path


def _attempt_files(scan_dir: Path, stem: str) -> AttemptFiles:
    logs = scan_dir / "logs"
    return AttemptFiles(
        last_message=logs / f"{stem}.last.json",
        log=logs / f"{stem}.jsonl",
        stderr=logs / f"{stem}.stderr.log",
        invocation=logs / f"{stem}.invocation.json",
    )


def _result_wrapper(
    context: RunContext,
    prepared: PreparedShard,
    attempt: int,
    thread_id: str,
    transport_facts: dict[str, Any],
    model_output: dict[str, Any],
) -> dict[str, Any]:
    coverage = [
        {
            "unit_id": unit["unit_id"],
            "path": unit["path"],
            "start_line": unit["start_line"],
            "end_line": unit["end_line"],
            "file_sha256": unit["file_sha256"],
            "delivery": DELIVERY_MODE,
        }
        for unit in prepared.shard["units"]
    ]
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "scan_id": context.plan["scan_id"],
        "snapshot_id": context.plan["snapshot_id"],
        "question_sha256": context.plan["question_sha256"],
        "shard_id": prepared.shard_id,
        "status": "complete",
        "prompt_sha256": prepared.prompt_sha256,
        "schema_sha256": prepared.schema_sha256,
        "coverage": coverage,
        "scout_run": {
            "cli": context.backend.cli_version,
            "agent": context.backend.name,
            "model": context.backend.model,
            "effort": context.backend.effort,
            "thread_id": thread_id,
            "attempt": attempt,
            "sandbox": SCOUT_SANDBOX,
            "resolved_models": transport_facts["resolved_models"],
            "initialized_tools": transport_facts["initialized_tools"],
            "total_cost_usd": transport_facts["total_cost_usd"],
        },
        "analysis": model_output,
    }


def _invocation_record(
    context: RunContext,
    prepared: PreparedShard,
    stem: str,
    command: list[str],
    requested_thread_id: str | None,
) -> dict[str, Any]:
    """Record the launch facts required to reconstruct one attempt."""
    return {
        "attempt_id": stem,
        "agent": context.backend.name,
        "cli": context.backend.cli_version,
        "model": context.backend.model,
        "effort": context.backend.effort,
        "sandbox": SCOUT_SANDBOX,
        "workdir": str(context.runtime_workdir),
        "home_mode": context.backend.home_mode,
        "prompt_path": (
            str(prepared.prompt_path) if prepared.prompt_path.exists() else None
        ),
        "prompt_sha256": prepared.prompt_sha256,
        "schema_path": str(prepared.schema_path),
        "schema_sha256": prepared.schema_sha256,
        "command": command,
        "requested_thread_id": requested_thread_id,
        "started_at": utc_now(),
        "status": "running",
    }


def _execute_attempt(
    context: RunContext,
    prepared: PreparedShard,
    process_env: dict[str, str] | None,
    attempt: int,
    files: AttemptFiles,
    invocation: dict[str, Any],
    command: list[str],
) -> str:
    try:
        with context.machine_slots.hold(prepared.slot_offset) as coordinated:
            invocation["machine_slot_coordinated"] = coordinated
            process = subprocess.run(
                command,
                input=prepared.prompt,
                capture_output=True,
                text=True,
                env=process_env,
                cwd=context.runtime_workdir,
                check=False,
            )
    except OSError as exc:
        raise AttemptFailed(f"failed to start {context.backend.name}: {exc}") from exc

    files.log.write_text(process.stdout, encoding="utf-8")
    files.stderr.write_text(process.stderr, encoding="utf-8")
    invocation.update(
        {
            "ended_at": utc_now(),
            "exit_status": process.returncode,
            "status": "process_completed" if process.returncode == 0 else "failed",
        }
    )
    write_json(files.invocation, invocation)
    if process.returncode != 0:
        raise AttemptFailed(
            f"{context.backend.name} exit {process.returncode}; see {files.stderr.name}"
        )

    try:
        thread_id, model_output, transport_facts = context.backend.parse_result(
            files.last_message, process.stdout
        )
    except ValueError as exc:
        raise AttemptFailed(str(exc)) from exc
    invocation["thread_id"] = thread_id
    invocation.update(transport_facts)
    write_json(files.invocation, invocation)

    shape_errors = analysis_shape_errors(model_output, materialized=False)
    if shape_errors:
        raise AttemptFailed(f"invalid result shape: {'; '.join(shape_errors[:3])}")
    materialized, evidence_errors, _ = materialize_evidence(
        model_output, prepared.delivered_units
    )
    if evidence_errors:
        raise AttemptFailed(f"invalid evidence lines: {'; '.join(evidence_errors[:3])}")

    wrapper = _result_wrapper(
        context, prepared, attempt, thread_id, transport_facts, materialized
    )
    write_json_atomic(prepared.result_path, wrapper)
    invocation["status"] = "complete"
    write_json(files.invocation, invocation)
    return f"{len(materialized.get('observations', []))} observations"


def _run_attempt(
    context: RunContext,
    prepared: PreparedShard,
    process_env: dict[str, str] | None,
    attempt: int,
) -> tuple[bool, str]:
    stem = f"{prepared.shard_id}.attempt-{attempt}"
    files = _attempt_files(context.scan_dir, stem)
    requested_thread_id = context.backend.requested_thread_id()
    command = context.backend.command(
        runtime_workdir=context.runtime_workdir,
        scan_dir=context.scan_dir,
        schema_path=prepared.schema_path,
        last_message_path=files.last_message,
        output_schema=prepared.output_schema,
        requested_thread_id=requested_thread_id,
    )
    invocation = _invocation_record(
        context, prepared, stem, command, requested_thread_id
    )
    write_json(files.invocation, invocation)
    try:
        return True, _execute_attempt(
            context, prepared, process_env, attempt, files, invocation, command
        )
    except AttemptFailed as exc:
        try:
            invocation["prompt_path"] = str(_persist_prompt(prepared))
        except OSError as retention_error:
            invocation["prompt_retention_error"] = str(retention_error)
        invocation["status"] = "failed"
        invocation["failure_reason"] = str(exc)
        write_json(files.invocation, invocation)
        return False, str(exc)


def _run_one(shard_path: Path, context: RunContext) -> tuple[str, bool, str]:
    prepared = _prepare_shard(shard_path, context)
    write_json(prepared.schema_path, prepared.output_schema)
    if context.keep_prompts:
        _persist_prompt(prepared)

    stored_result, changes = _existing_result(context, prepared)
    if stored_result is not None:
        if changes:
            write_json_atomic(prepared.result_path, stored_result)
        detail = (
            f"updated {changes} stored evidence fields"
            if changes
            else "already complete with line evidence"
        )
        return prepared.shard_id, True, detail

    first_attempt = _next_attempt_number(context.scan_dir / "logs", prepared.shard_id)
    process_env = context.backend.prepare_env(
        context.home_template, context.homes_root, prepared.shard_id
    )
    last_error = "unknown failure"
    for attempt in range(first_attempt, first_attempt + context.retries + 1):
        success, detail = _run_attempt(context, prepared, process_env, attempt)
        if success:
            return prepared.shard_id, True, detail
        last_error = detail
    return prepared.shard_id, False, last_error


def run_one(shard_path: Path, context: RunContext) -> tuple[str, bool, str]:
    """Isolate one shard's preparation or execution failure from its peers."""
    try:
        return _run_one(shard_path, context)
    except Exception as exc:  # noqa: BLE001 - one shard must not stop its peers
        detail = f"{type(exc).__name__}: {exc}"
        try:
            write_json(
                context.scan_dir / "logs" / f"{shard_path.stem}.internal-error.json",
                {
                    "shard_id": shard_path.stem,
                    "operation": "prepare_or_run_shard",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "recorded_at": utc_now(),
                },
            )
        except OSError:
            pass
        return shard_path.stem, False, detail
