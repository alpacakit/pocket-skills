"""Own CLI-specific isolation, commands, and result parsing for scout backends."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from scan_artifacts import read_json, utc_now, write_json
from scan_contract import (
    CODEX_NON_TOOL_ITEM_TYPES,
    SCOUT_AGENT_POLICIES,
    SCOUT_SANDBOX,
    resolved_models_match_policy,
)

CODEX_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "code_mode_host",
    "skill_search",
    "multi_agent",
    "browser_use",
    "plugins",
    "remote_plugin",
    "apps",
    "plugin_sharing",
    "shell_snapshot",
    "in_app_updates",
)


def iter_json_lines(log_text: str) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from a CLI log while ignoring diagnostic text."""
    for line in log_text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def codex_client_version(cli_version: str) -> str:
    match = re.search(r"(\d+\.\d+\.\d+)", cli_version)
    if not match:
        raise RuntimeError(
            f"Cannot determine Codex client version from {cli_version!r}"
        )
    return match.group(1)


def prepare_isolated_codex_home(
    executable: str, cli_version: str, destination: Path
) -> Path:
    """Create a scan-local Codex home without user configuration or catalogs."""
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    source_home = Path(
        os.environ.get("CODEX_HOME", Path.home() / ".codex")
    ).expanduser()
    source_auth = source_home / "auth.json"
    if not source_auth.is_file():
        raise RuntimeError(f"Codex authentication not found: {source_auth}")
    destination_auth = destination / "auth.json"
    shutil.copyfile(source_auth, destination_auth)
    destination_auth.chmod(0o600)

    bundled = subprocess.run(
        [executable, "debug", "models", "--bundled"],
        capture_output=True,
        text=True,
        check=True,
    )
    catalog = json.loads(bundled.stdout)
    models = catalog.get("models")
    if not isinstance(models, list) or not models:
        raise RuntimeError("Codex bundled model catalog is empty or invalid")
    write_json(
        destination / "models_cache.json",
        {
            "fetched_at": utc_now(),
            "etag": None,
            "client_version": codex_client_version(cli_version),
            "models": models,
        },
    )
    return destination


def extract_codex_thread_id(log_text: str) -> str | None:
    for event in iter_json_lines(log_text):
        if event.get("type") == "thread.started":
            return event.get("thread_id")
    return None


def extract_claude_result(
    log_text: str,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    final_event: dict[str, Any] | None = None
    for event in iter_json_lines(log_text):
        if event.get("type") == "result":
            final_event = event
    if final_event is None:
        return None, None, None
    structured = final_event.get("structured_output")
    if not isinstance(structured, dict):
        raw_result = final_event.get("result")
        if isinstance(raw_result, str):
            try:
                structured = json.loads(raw_result)
            except json.JSONDecodeError:
                structured = None
    return (
        final_event.get("session_id"),
        structured if isinstance(structured, dict) else None,
        final_event,
    )


def codex_transport_facts(log_text: str, model: str) -> dict[str, Any]:
    """Observe from the log that Codex produced no tool or command item."""
    observed_tools: set[str] = set()
    for event in iter_json_lines(log_text):
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type not in CODEX_NON_TOOL_ITEM_TYPES:
            observed_tools.add(item_type)
    return {
        "resolved_models": [model],
        "initialized_tools": sorted(observed_tools),
        "total_cost_usd": None,
    }


def claude_transport_facts(
    log_text: str, final_event: dict[str, Any]
) -> dict[str, Any]:
    models: set[str] = set()
    initialized_tools: list[str] | None = None
    for event in iter_json_lines(log_text):
        if event.get("type") == "system" and event.get("subtype") == "init":
            model = event.get("model")
            if isinstance(model, str):
                models.add(model)
            tools = event.get("tools")
            if isinstance(tools, list) and all(isinstance(tool, str) for tool in tools):
                initialized_tools = tools
        message = event.get("message")
        if isinstance(message, dict) and isinstance(message.get("model"), str):
            models.add(message["model"])
    model_usage = final_event.get("modelUsage")
    if isinstance(model_usage, dict):
        models.update(key for key in model_usage if isinstance(key, str))
    return {
        "resolved_models": sorted(models),
        "initialized_tools": initialized_tools,
        "total_cost_usd": final_event.get("total_cost_usd"),
    }


class ScoutBackend(ABC):
    """One resolved backend and every backend-specific execution decision."""

    name: str

    def __init__(self, executable: str, cli_version: str, requested: dict[str, str]) -> None:
        self.executable = executable
        self.cli_version = cli_version
        self.policy = SCOUT_AGENT_POLICIES[self.name]
        self.requested = requested
        self.model = requested["model"]
        self.effort = requested["effort"]

    @property
    def home_mode(self) -> str | None:
        return None

    def prepare_scan(self, scan_dir: Path) -> None:
        """Create backend-specific immutable scan inputs, if any."""

    @contextlib.contextmanager
    def home_workspace(self, scan_id: str) -> Iterator[tuple[Path | None, Path | None]]:
        yield None, None

    def prepare_env(
        self,
        home_template: Path | None,
        homes_root: Path | None,
        shard_id: str,
    ) -> dict[str, str] | None:
        return None

    def requested_thread_id(self) -> str | None:
        return None

    @abstractmethod
    def command(
        self,
        *,
        runtime_workdir: Path,
        scan_dir: Path,
        schema_path: Path,
        last_message_path: Path,
        output_schema: dict[str, Any],
        requested_thread_id: str | None,
    ) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def _parse_result(
        self, last_message_path: Path, log_text: str
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        raise NotImplementedError

    def parse_result(
        self, last_message_path: Path, log_text: str
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        thread_id, model_output, facts = self._parse_result(last_message_path, log_text)
        if not resolved_models_match_policy(self.name, self.model, facts["resolved_models"]):
            raise ValueError(
                f"{self.name} used an undeclared model: "
                + ", ".join(facts["resolved_models"] or ["<unknown>"])
            )
        expected_tools = list(self.policy.initialized_tools)
        if facts["initialized_tools"] != expected_tools:
            raise ValueError(
                f"{self.name} used unexpected tools: "
                + ", ".join(facts["initialized_tools"] or ["<unknown>"])
            )
        return thread_id, model_output, facts


class CodexBackend(ScoutBackend):
    name = "codex"

    @property
    def home_mode(self) -> str:
        return "isolated_per_scout_home"

    @contextlib.contextmanager
    def home_workspace(self, scan_id: str) -> Iterator[tuple[Path | None, Path | None]]:
        with tempfile.TemporaryDirectory(
            prefix=f"askeveryfile-{scan_id}-"
        ) as container:
            temp_root = Path(container)
            template = temp_root / "template-home"
            homes_root = temp_root / "scout-homes"
            homes_root.mkdir(mode=0o700)
            prepare_isolated_codex_home(self.executable, self.cli_version, template)
            yield template, homes_root

    def prepare_env(
        self,
        home_template: Path | None,
        homes_root: Path | None,
        shard_id: str,
    ) -> dict[str, str]:
        if home_template is None or homes_root is None:
            raise RuntimeError("isolated Codex home was not prepared")
        scout_home = homes_root / shard_id
        shutil.copytree(home_template, scout_home)
        cache_path = scout_home / "models_cache.json"
        cache = read_json(cache_path)
        cache["fetched_at"] = utc_now()
        write_json(cache_path, cache)
        process_env = os.environ.copy()
        process_env["CODEX_HOME"] = str(scout_home)
        return process_env

    def command(
        self,
        *,
        runtime_workdir: Path,
        scan_dir: Path,
        schema_path: Path,
        last_message_path: Path,
        output_schema: dict[str, Any],
        requested_thread_id: str | None,
    ) -> list[str]:
        command = [
            self.executable,
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
                str(runtime_workdir),
                "-m",
                self.model,
                "-c",
                f"model_reasoning_effort={self.effort}",
                "-c",
                'approval_policy="never"',
                "--sandbox",
                SCOUT_SANDBOX,
                "--output-schema",
                str(schema_path),
                "-o",
                str(last_message_path),
                "-",
            )
        )
        return command

    def _parse_result(
        self, last_message_path: Path, log_text: str
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        thread_id = extract_codex_thread_id(log_text)
        if not thread_id:
            raise ValueError("Codex returned no thread.started event")
        try:
            model_output = read_json(last_message_path)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Codex final JSON: {exc}") from exc
        return thread_id, model_output, codex_transport_facts(log_text, self.model)


class ClaudeBackend(ScoutBackend):
    name = "claude"

    def prepare_scan(self, scan_dir: Path) -> None:
        write_json(scan_dir / "empty-mcp.json", {"mcpServers": {}})

    def requested_thread_id(self) -> str:
        return str(uuid.uuid4())

    def command(
        self,
        *,
        runtime_workdir: Path,
        scan_dir: Path,
        schema_path: Path,
        last_message_path: Path,
        output_schema: dict[str, Any],
        requested_thread_id: str | None,
    ) -> list[str]:
        if requested_thread_id is None:
            raise RuntimeError("Claude thread id was not prepared")
        return [
            self.executable,
            "-p",
            "--session-id",
            requested_thread_id,
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--safe-mode",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            str(scan_dir / "empty-mcp.json"),
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
            "--json-schema",
            json.dumps(output_schema, ensure_ascii=False, separators=(",", ":")),
            "--verbose",
        ]

    def _parse_result(
        self, last_message_path: Path, log_text: str
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        thread_id, model_output, final_event = extract_claude_result(log_text)
        if not thread_id or model_output is None or final_event is None:
            raise ValueError("Claude returned no session-backed structured result")
        facts = claude_transport_facts(log_text, final_event)
        if type(facts["total_cost_usd"]) not in (int, float):
            # This is a malformed transport result, not a caller type error.
            raise ValueError("Claude did not report a total cost")
        write_json(last_message_path, model_output)
        return thread_id, model_output, facts


def resolve_backend(
    requested: dict[str, str], codex_executable: str, claude_executable: str
) -> ScoutBackend:
    """Locate one backend CLI and bind every backend-specific decision."""
    name = requested["agent"]
    backend_types = {"codex": CodexBackend, "claude": ClaudeBackend}
    executable_names = {"codex": codex_executable, "claude": claude_executable}
    if name not in backend_types:
        raise SystemExit(f"Unsupported scout backend: {name}")
    backend_type = backend_types[name]
    executable_name = executable_names[name]
    executable_path = shutil.which(executable_name)
    if not executable_path:
        raise SystemExit(f"{name} CLI not found: {executable_name}")
    try:
        version_result = subprocess.run(
            [executable_path, "--version"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"{executable_path} is not a usable CLI: {exc}") from exc
    return backend_type(executable_path, version_result.stdout.strip(), requested)
