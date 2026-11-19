"""Inventory a folder and split it into coverage-checked scout shards."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from scan_artifacts import (
    sha256_bytes,
    sha256_text,
    utc_now,
    write_json,
)
from scan_contract import PLAN_SCHEMA_VERSION
from scan_inventory import (
    DEFAULT_EXCLUDED_DIR_NAMES,
    DEFAULT_EXCLUDED_FILE_NAMES,
    DEFAULT_EXCLUDED_GLOBS,
    flatten_option_groups,
    inventory,
    normalize_exclusion_specs,
    normalize_root_relative_specs,
    validate_policy_overlap,
)
from scan_report import render_scan_md
from scan_resources import (
    MAX_CONCURRENT_SCOUTS,
    SCOUTS_PER_GIB_MEMORY,
    SCOUTS_PER_LOGICAL_CPU,
    hardware_parallel_cap,
)
from scan_sharding import ESTIMATED_BYTES_PER_TOKEN, build_shards
from scan_workspace import (
    WorkspaceError,
    WorkspaceResult,
    validate_question_file,
    validate_scan_parent,
)

DEFAULT_TARGET_TOKENS = 75_000
ESTIMATED_FIXED_ENVELOPE_TOKENS = 15_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a complete text inventory and size-based scout plan.",
        allow_abbrev=False,
    )
    parser.add_argument("root", help="Folder whose text should be analyzed")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="scan_parent returned by prepare_workspace.py",
    )
    question_group = parser.add_mutually_exclusive_group(required=True)
    question_group.add_argument(
        "--question", help="Read-only question asked of every shard"
    )
    question_group.add_argument(
        "--question-file",
        help="UTF-8 question_file returned by prepare_workspace.py",
    )
    parser.add_argument(
        "--target-tokens",
        type=int,
        default=DEFAULT_TARGET_TOKENS,
        help=(
            "Soft estimated input-token target per scout; coherent units may exceed it "
            f"(default: {DEFAULT_TARGET_TOKENS})"
        ),
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Additional relative-path glob to exclude; repeatable",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        nargs="+",
        default=[],
        metavar="PATH",
        help=(
            "File or directory names and relative paths to exclude; accepts multiple "
            "values and may be repeated"
        ),
    )
    parser.add_argument(
        "--force-include",
        action="append",
        nargs="+",
        default=[],
        metavar="PATH",
        help=(
            "Target-relative file or directory paths to scan regardless of --exclude, "
            ".gitignore, and the built-in exclusion lists; does not restrict scope; "
            "accepts multiple values and may be repeated"
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        nargs="+",
        default=[],
        metavar="PATH",
        help=(
            "Restrict the scan to these target-relative file or directory paths; "
            "normal exclusion rules still apply; accepts multiple values and may be "
            "repeated"
        ),
    )
    return parser.parse_args()


def load_question(args: argparse.Namespace, workspace: WorkspaceResult) -> str:
    if args.question is not None:
        question = args.question
    else:
        try:
            question_file = validate_question_file(args.question_file, workspace)
            question = question_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError, WorkspaceError) as exc:
            raise SystemExit(f"Cannot read --question-file as UTF-8: {exc}") from exc
    if not question.strip():
        raise SystemExit("Question must not be empty")
    return question


def git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


@dataclass(frozen=True)
class TokenBudget:
    """How one scout's token target is split between envelope and folder text."""

    question: int
    prompt: int
    content: int


@dataclass(frozen=True)
class ScanRequest:
    """Every resolved input that decides what the scan delivers to scouts."""

    args: argparse.Namespace
    root: Path
    output_parent: Path
    question: str
    excluded_specs: set[str]
    only_specs: set[str]
    force_include_specs: set[str]


@dataclass(frozen=True)
class PlannedCorpus:
    """The snapshot, shards, and token budget derived from one request."""

    files: list[dict[str, Any]]
    excluded: list[dict[str, str]]
    shards: list[dict[str, Any]]
    sharding: dict[str, Any]
    budget: TokenBudget


def resolve_paths(args: argparse.Namespace) -> tuple[Path, WorkspaceResult]:
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Target does not exist: {root}")
    if not root.is_dir():
        raise SystemExit(
            "Target must be a directory. Use referenced files to prepare the question, "
            "then analyze the narrowest folder containing the required text population."
        )
    try:
        workspace = validate_scan_parent(root, args.output_dir)
    except WorkspaceError as exc:
        raise SystemExit(str(exc)) from exc
    return root, workspace


def resolve_request(args: argparse.Namespace) -> ScanRequest:
    """Normalize every path option once, before anything reads the target."""
    root, workspace = resolve_paths(args)
    only_specs = normalize_root_relative_specs(
        flatten_option_groups(args.only), root, "--only"
    )
    force_include_specs = normalize_root_relative_specs(
        flatten_option_groups(args.force_include), root, "--force-include"
    )
    validate_policy_overlap(only_specs, force_include_specs)
    return ScanRequest(
        args=args,
        root=root,
        output_parent=workspace.scan_parent,
        question=load_question(args, workspace),
        excluded_specs=normalize_exclusion_specs(flatten_option_groups(args.exclude)),
        only_specs=only_specs,
        force_include_specs=force_include_specs,
    )


def plan_corpus(request: ScanRequest) -> PlannedCorpus:
    """Snapshot the selected text and split it into shard-sized bundles."""
    files, excluded = inventory(
        request.root,
        request.output_parent,
        request.args.exclude_glob,
        request.excluded_specs,
        request.only_specs,
        request.force_include_specs,
    )
    budget = scout_token_budget(request.question, request.args.target_tokens)
    shards, sharding = build_shards(request.root, files, budget.content)
    return PlannedCorpus(files, excluded, shards, sharding, budget)


def scout_token_budget(question: str, target_tokens: int) -> TokenBudget:
    question_tokens = max(
        1, math.ceil(len(question.encode("utf-8")) / ESTIMATED_BYTES_PER_TOKEN)
    )
    prompt_tokens = ESTIMATED_FIXED_ENVELOPE_TOKENS + question_tokens
    content_tokens = target_tokens - prompt_tokens
    if content_tokens < 10_000:
        raise SystemExit(
            "The question leaves less than 10000 tokens for folder text. "
            "Shorten it to a compact shard-local brief or increase --target-tokens "
            f"(estimated prompt reserve: {prompt_tokens})."
        )
    return TokenBudget(question_tokens, prompt_tokens, content_tokens)


def plan_settings(request: ScanRequest, corpus: PlannedCorpus) -> dict[str, Any]:
    args = request.args
    budget = corpus.budget
    return {
        "target_tokens_per_scout": args.target_tokens,
        "content_token_budget_per_scout": budget.content,
        "fixed_envelope_token_reserve": ESTIMATED_FIXED_ENVELOPE_TOKENS,
        "estimated_question_tokens_per_scout": budget.question,
        "estimated_prompt_tokens_per_scout": budget.prompt,
        "hard_file_token_budget": corpus.sharding["hard_file_token_budget"],
        "token_estimate": (
            "ceil((utf8_file_bytes + rendered_L_number_pipe_prefix_bytes)/3) "
            "plus estimated question tokens and fixed execution-envelope reserve"
        ),
        "default_excluded_directory_names": sorted(DEFAULT_EXCLUDED_DIR_NAMES),
        "default_excluded_file_names": sorted(DEFAULT_EXCLUDED_FILE_NAMES),
        "excluded_globs": [*DEFAULT_EXCLUDED_GLOBS, *args.exclude_glob],
        "excluded_paths": sorted(request.excluded_specs),
        "gitignore_filter": "git ls-files --cached --others --exclude-standard",
        "scope_mode": "only" if request.only_specs else "all",
        "only_paths": sorted(request.only_specs),
        "force_included_paths": sorted(request.force_include_specs),
        "exclusion_precedence": (
            "only_paths restrict scope before exclusions; force_included_paths override "
            "user exclusions, the default lists, and .gitignore within that scope; "
            "symlinks, the scan output directory, and unreadable or non-text files "
            "stay excluded"
        ),
    }


def plan_coverage(corpus: PlannedCorpus) -> dict[str, Any]:
    files = corpus.files
    excluded = corpus.excluded
    sharding = corpus.sharding
    planned_unit_tokens = sum(shard["estimated_tokens"] for shard in corpus.shards)
    top_level_counts = Counter(
        item["path"].split("/", 1)[0] if "/" in item["path"] else "." for item in files
    )
    return {
        "included_text_files": len(files),
        "included_text_files_by_top_level": dict(sorted(top_level_counts.items())),
        "included_bytes": sum(item["bytes"] for item in files),
        "estimated_tokens": sum(item["estimated_tokens"] for item in files),
        "planned_unit_tokens": planned_unit_tokens,
        "estimated_total_scout_input_tokens": (
            planned_unit_tokens + len(corpus.shards) * corpus.budget.prompt
        ),
        "excluded_entries": len(excluded),
        "excluded_by_reason": dict(
            sorted(Counter(item["reason"] for item in excluded).items())
        ),
        "split_file_count": sharding["split_file_count"],
        "split_files": sharding["split_files"],
        "unit_count": sharding["unit_count"],
        "unit_boundary_counts": sharding["boundary_counts"],
    }


def plan_execution(shard_count: int) -> dict[str, Any]:
    local_parallel_cap, hardware = hardware_parallel_cap()
    return {
        "shard_count": shard_count,
        "recommended_max_concurrent_scouts": (
            min(local_parallel_cap, shard_count) if shard_count else 0
        ),
        "dispatch_policy": "rate_limited_slot_refill",
        "hardware": hardware,
        "formula": (
            "shards=directory-local whole-file/subtree bundles packed to target_tokens; "
            "files may exceed the soft target and split only above the hard file budget, "
            "preferring headings or blank-line structural boundaries; "
            f"local_cap=min({MAX_CONCURRENT_SCOUTS}, logical_cpus*{SCOUTS_PER_LOGICAL_CPU}, "
            f"floor(memory_gib*{SCOUTS_PER_GIB_MEMORY})) adjusted by current memory "
            "pressure; max_concurrent_scouts=min(local_cap, shards); the runner "
            f"additionally holds one of at most {MAX_CONCURRENT_SCOUTS} machine-wide "
            "slots per live scout, shared with any other scan on this machine; "
            "refill each free slot subject to launch-rate and pressure controls"
        ),
    }


def write_scan_files(
    scan_dir: Path, plan: dict[str, Any], shards: list[dict[str, Any]]
) -> None:
    shard_dir = scan_dir / "shards"
    shard_dir.mkdir(parents=True)
    write_json(scan_dir / "plan.json", plan)
    identity = {
        key: plan[key]
        for key in ("scan_id", "snapshot_id", "question", "question_sha256", "root")
    }
    for shard in shards:
        write_json(
            shard_dir / f"{shard['shard_id']}.json",
            {"schema_version": PLAN_SCHEMA_VERSION, **identity, **shard},
        )


def scan_identity(request: ScanRequest, corpus: PlannedCorpus) -> tuple[str, str, str]:
    """Derive the snapshot, question, and scan ids that bind every later stage.

    The scan id folds in the settings that change what scouts receive, so a
    replan under different settings cannot silently reuse an earlier scan.
    """
    snapshot_id = sha256_bytes(
        json.dumps(
            {"files": corpus.files, "excluded": corpus.excluded},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )
    question_sha256 = sha256_text(request.question)
    suffix = sha256_text(
        f"{snapshot_id}:{question_sha256}:{request.args.target_tokens}"
    )[:12]
    created_at = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    return snapshot_id, question_sha256, f"scan-{created_at}-{suffix}"


def build_plan(request: ScanRequest, corpus: PlannedCorpus) -> dict[str, Any]:
    snapshot_id, question_sha256, scan_id = scan_identity(request, corpus)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "scan_id": scan_id,
        "created_at": utc_now(),
        "root": str(request.root),
        "git_head": git_head(request.root),
        "snapshot_id": snapshot_id,
        "question": request.question,
        "question_sha256": question_sha256,
        "settings": plan_settings(request, corpus),
        "coverage": plan_coverage(corpus),
        "execution": plan_execution(len(corpus.shards)),
        "files": corpus.files,
        "excluded": corpus.excluded,
        "shards": [
            {
                key: shard[key]
                for key in ("shard_id", "scope", "estimated_tokens", "unit_count")
            }
            for shard in corpus.shards
        ],
    }


def main() -> int:
    args = parse_args()
    if args.target_tokens < 10_000:
        raise SystemExit("--target-tokens must be at least 10000")

    request = resolve_request(args)
    corpus = plan_corpus(request)
    plan = build_plan(request, corpus)

    scan_dir = request.output_parent / plan["scan_id"]
    write_scan_files(scan_dir, plan, corpus.shards)
    scan_md = render_scan_md(scan_dir / "plan.json")
    print(
        json.dumps(
            {
                "plan": str(scan_dir / "plan.json"),
                "scan": str(scan_md),
                "scope": {
                    "root": plan["root"],
                    "mode": plan["settings"]["scope_mode"],
                    "only_paths": plan["settings"]["only_paths"],
                },
                "force_included_paths": plan["settings"]["force_included_paths"],
                "scope_note": (
                    "--force-include overrides exclusions; it does not restrict scope. "
                    "Use --only to restrict the scan."
                ),
                **plan["coverage"],
                **plan["execution"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
