"""Render one human-readable inspection entry point for a scan."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scan_artifacts import (
    read_json,
    write_text_atomic,
)


def _link(label: str, path: Path) -> str:
    return f"[{label}](<{path.expanduser().resolve()}>)"


def _code_fence(value: str) -> str:
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", value)),
        default=0,
    )
    fence = "`" * max(3, longest + 1)
    closing_separator = "" if value.endswith("\n") else "\n"
    return f"{fence}\n{value}{closing_separator}{fence}"


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    try:
        value = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _latest_invocation(
    scan_dir: Path, shard_id: str
) -> tuple[Path, dict[str, Any]] | None:
    candidates: list[tuple[int, Path]] = []
    pattern = re.compile(rf"^{re.escape(shard_id)}\.attempt-(\d+)\.invocation\.json$")
    for path in (scan_dir / "logs").glob(f"{shard_id}.attempt-*.invocation.json"):
        match = pattern.match(path.name)
        if match:
            candidates.append((int(match.group(1)), path))
    for _, path in sorted(candidates, reverse=True):
        value = _read_optional_json(path)
        if value is not None:
            return path, value
    return None


def _prompt_path(scan_dir: Path, shard_id: str) -> Path | None:
    for path in (
        scan_dir / "prompts" / f"{shard_id}.md.gz",
        scan_dir / "prompts" / f"{shard_id}.md",
    ):
        if path.is_file():
            return path
    return None


def _diagnostic_path(scan_dir: Path, invocation_path: Path) -> Path | None:
    stem = invocation_path.name.removesuffix(".invocation.json")
    for suffix in (".stderr.log", ".jsonl"):
        path = scan_dir / "logs" / f"{stem}{suffix}"
        if path.is_file():
            return path
    return invocation_path if invocation_path.is_file() else None


def _shard_status(
    result_path: Path,
    latest: tuple[Path, dict[str, Any]] | None,
) -> str:
    if latest is not None:
        invocation_path, invocation = latest
        invocation_status = invocation.get("status")
        if invocation_status == "failed" and (
            not result_path.exists()
            or invocation_path.stat().st_mtime >= result_path.stat().st_mtime
        ):
            return "failed"
        if invocation_status in {"running", "process_completed"}:
            return "running"
    if result_path.is_file():
        return "complete"
    return "pending"


def _scope_text(plan: dict[str, Any]) -> str:
    settings = plan["settings"]
    if settings.get("scope_mode") != "only":
        return "all planned text files"
    only_paths = settings.get("only_paths", [])
    return ", ".join(f"`{path}`" for path in only_paths) or "selected paths"


# Machine reason codes carry a ":detail" suffix the reader does not need.
EXCLUSION_LABELS = {
    "binary_nul": "binary or non-UTF-8",
    "default_excluded_directory": "generated or dependency directory",
    "excluded_glob": "excluded by request",
    "generated_dependency_lock": "generated or lock file",
    "gitignored": "ignored by git",
    "non_utf8_or_binary": "binary or non-UTF-8",
    "not_a_regular_file": "not a regular file",
    "outside_only_scope": "outside the selected scope",
    "scan_output_directory": "scan output directory",
    "symlink_not_followed": "symlink not followed",
    "unreadable": "unreadable",
    "user_excluded_directory": "excluded by request",
    "user_excluded_file": "excluded by request",
}


def _exclusion_text(plan: dict[str, Any]) -> str:
    """Summarize exclusions in plain language so scope stays accountable."""
    excluded = plan.get("excluded") or []
    if not excluded:
        return "none"
    counts: Counter[str] = Counter()
    for item in excluded:
        code = str(item.get("reason", "")).split(":", 1)[0]
        counts[EXCLUSION_LABELS.get(code, code)] += 1
    detail = ", ".join(f"{label} {count}" for label, count in sorted(counts.items()))
    return f"{len(excluded)} ({detail})"


def _prompt_summary(
    plan: dict[str, Any], scan_dir: Path, run_state: dict[str, Any] | None
) -> str:
    shard_ids = [item["shard_id"] for item in plan["shards"]]
    retained = sum(
        _prompt_path(scan_dir, shard_id) is not None for shard_id in shard_ids
    )
    if retained == len(shard_ids) and shard_ids:
        return f"exact rendered prompts retained for all {retained} shards"
    policy = (run_state or {}).get("prompt_retention")
    if policy == "all":
        return (
            "exact rendered prompts retained for "
            f"{retained}/{len(shard_ids)} shards"
        )
    if retained:
        return (
            f"{retained}/{len(shard_ids)} exact prompts retained for failed attempts; "
            "successful prompts are reconstructed when validated"
        )
    if run_state:
        return (
            "successful prompts are not retained; failed prompts are retained "
            "automatically"
        )
    return "not selected yet; the default retains failed prompts only"


def _overall_status(
    plan: dict[str, Any],
    validation: dict[str, Any] | None,
    run_state: dict[str, Any] | None,
    review_packet: Path | None,
    complete: int,
    failed: int,
) -> str:
    if review_packet is not None and review_packet.is_file():
        return "validated; scout observations are ready for synthesis"
    if validation is not None:
        return "validated" if validation.get("valid") else "validation failed"
    if run_state and run_state.get("status") == "running":
        return "scouts running"
    if failed or (run_state and run_state.get("status") == "failed"):
        return "scout failures"
    if complete == len(plan["shards"]) and plan["shards"]:
        return "scouts complete; validation pending"
    return "planned"


@dataclass(frozen=True)
class ReportArtifacts:
    run_state: dict[str, Any] | None
    validation_path: Path
    validation: dict[str, Any] | None
    review_packet: Path | None


def _current_artifacts(
    scan_dir: Path,
    review_packet_path: Path | None,
) -> ReportArtifacts:
    run_state_path = scan_dir / "run.json"
    run_state = _read_optional_json(run_state_path)
    run_mtime = run_state_path.stat().st_mtime if run_state_path.is_file() else None

    resolved_validation = scan_dir / "validation.json"
    validation = _read_optional_json(resolved_validation)
    if (
        validation is not None
        and run_mtime is not None
        and resolved_validation.stat().st_mtime < run_mtime
    ):
        validation = None

    resolved_packet = (
        review_packet_path.expanduser().resolve()
        if review_packet_path is not None
        else scan_dir / "review-packet.json"
    )
    packet_is_current = (
        resolved_packet.is_file()
        and validation is not None
        and bool(validation.get("valid"))
        and resolved_packet.stat().st_mtime >= resolved_validation.stat().st_mtime
        and (run_mtime is None or resolved_packet.stat().st_mtime >= run_mtime)
    )
    return ReportArtifacts(
        run_state=run_state,
        validation_path=resolved_validation,
        validation=validation,
        review_packet=resolved_packet if packet_is_current else None,
    )


def _shard_rows(
    plan: dict[str, Any], scan_dir: Path
) -> tuple[list[str], int, int]:
    rows: list[str] = []
    complete = 0
    failed = 0
    for shard in plan["shards"]:
        shard_id = shard["shard_id"]
        shard_path = scan_dir / "shards" / f"{shard_id}.json"
        result_path = scan_dir / "results" / f"{shard_id}.json"
        latest = _latest_invocation(scan_dir, shard_id)
        status = _shard_status(result_path, latest)
        complete += int(status == "complete")
        failed += int(status == "failed")

        result = _link("result", result_path) if result_path.is_file() else "—"
        prompt_path = _prompt_path(scan_dir, shard_id)
        prompt = (
            _link("exact prompt", prompt_path)
            if prompt_path is not None
            else "not retained"
        )
        diagnostic = "—"
        if latest is not None and status in {"failed", "running"}:
            diagnostic_path = _diagnostic_path(scan_dir, latest[0])
            if diagnostic_path is not None:
                diagnostic = _link("log", diagnostic_path)
        cells = (
            _link(shard_id, shard_path),
            str(shard.get("scope", ".")).replace("|", "\\|"),
            str(shard.get("unit_count", "—")),
            status,
            result,
            prompt,
            diagnostic,
        )
        rows.append("| " + " | ".join(cells) + " |")
    return rows, complete, failed


def _inspection_lines(
    plan_path: Path,
    validation_path: Path,
    validation: dict[str, Any] | None,
    review_packet: Path | None,
) -> list[str]:
    lines = [
        f"- {_link('plan.json', plan_path)} — scope and source snapshot",
        "- "
        + _link(
            "scout-prompt.md",
            Path(__file__).resolve().parents[1] / "templates" / "scout-prompt.md",
        )
        + " — shared prompt template",
    ]
    if validation is not None and validation_path.is_file():
        lines.append(
            f"- {_link('validation.json', validation_path)} — "
            "coverage and citation validation"
        )
    else:
        lines.append("- `validation.json` — not created yet")
    if review_packet is not None:
        lines.append(
            f"- {_link(review_packet.name, review_packet)} — scout observation "
            "aggregate used before synthesis; not the final answer"
        )
    else:
        lines.append("- `review-packet.json` — not created yet")
    return lines


def _report_content(
    plan_path: Path,
    plan: dict[str, Any],
    artifacts: ReportArtifacts,
    rows: list[str],
    complete: int,
    failed: int,
) -> str:
    scan_dir = plan_path.parent
    validation_text = "not run"
    observations_text = "—"
    if artifacts.validation is not None:
        validation_text = "passed" if artifacts.validation.get("valid") else "failed"
        observations_text = str(artifacts.validation.get("observations", "—"))
    status = _overall_status(
        plan,
        artifacts.validation,
        artifacts.run_state,
        artifacts.review_packet,
        complete,
        failed,
    )
    shard_summary = f"{len(plan['shards'])} ({complete} complete, {failed} failed)"
    inspection = _inspection_lines(
        plan_path,
        artifacts.validation_path,
        artifacts.validation,
        artifacts.review_packet,
    )
    return "\n".join(
        [
            "# Ask Every File scan",
            "",
            f"**Status:** {status}",
            "",
            "## Summary",
            "",
            f"- Target: `{plan['root']}`",
            f"- Scope: {_scope_text(plan)}",
            f"- Text files: {plan['coverage']['included_text_files']}",
            f"- Excluded: {_exclusion_text(plan)}",
            f"- Shards: {shard_summary}",
            "- Access: read-only",
            f"- Prompt retention: {_prompt_summary(plan, scan_dir, artifacts.run_state)}",
            f"- Validation: {validation_text}",
            f"- Observations: {observations_text}",
            "",
            "## Inspection paths",
            "",
            "The Question below is rendered into a prompt, one scout analyzes one",
            "shard and returns one result, a result holds observations, and the",
            "review packet holds every validated observation, unresolved question,",
            "and successful shard receipt.",
            "",
            *inspection,
            "",
            "Successful rendered prompts are normally not stored. The validator",
            "reconstructs each one from the shared template, the Question below, and",
            "the shard definition while the source snapshot is unchanged. Use",
            "`--keep-prompts` when exact rendered prompts must remain inspectable.",
            "",
            "## Shards",
            "",
            "| Shard | Scope | Units | Status | Result | Prompt | Diagnostic |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
            "## Question sent to every shard (verbatim)",
            "",
            _code_fence(plan["question"]),
            "",
        ]
    )


def render_scan_md(
    plan_path: Path,
    *,
    review_packet_path: Path | None = None,
) -> Path:
    """Regenerate scan.md from current artifacts without exposing expert metadata."""
    plan_path = plan_path.expanduser().resolve()
    scan_dir = plan_path.parent
    plan = read_json(plan_path)
    if not isinstance(plan, dict):
        raise TypeError(f"Plan is not an object: {plan_path}")
    artifacts = _current_artifacts(scan_dir, review_packet_path)
    rows, complete, failed = _shard_rows(plan, scan_dir)
    report_path = scan_dir / "scan.md"
    write_text_atomic(
        report_path,
        _report_content(plan_path, plan, artifacts, rows, complete, failed),
    )
    return report_path
