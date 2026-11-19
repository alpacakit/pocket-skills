"""Deliver every planned text unit to parallel read-only scouts."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from scan_artifacts import read_json, utc_now, write_json_atomic
from scan_contract import SCOUT_AGENT_POLICIES, plan_contract_errors
from scan_report import render_scan_md
from scan_resources import (
    MachineScoutSlots,
    available_memory_ratio,
    hardware_parallel_cap,
)
from scout_backends import resolve_backend
from scout_execution import RunContext, run_one
from scout_policy import bind_scout_policy

LAUNCH_BURST = 10
LAUNCH_INTERVAL_SECONDS = 1.1
RATE_LIMITED_INTERVAL_SECONDS = 2.2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one read-only scout for every plan shard.",
        allow_abbrev=False,
    )
    parser.add_argument("plan", help="plan.json created by plan_scan.py")
    parser.add_argument(
        "--agent",
        choices=sorted(SCOUT_AGENT_POLICIES),
        required=True,
        help="Agent CLI used for every shard",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument(
        "--parallel", type=int, help="Override plan concurrency downward"
    )
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument(
        "--only-shard",
        action="append",
        default=[],
        help="Run only the named shard id; repeatable and intended for targeted recovery or canaries",
    )
    parser.add_argument(
        "--keep-prompts",
        action="store_true",
        help=(
            "Retain each rendered scout prompt as deterministic gzip; "
            "failed attempts are retained regardless"
        ),
    )
    parser.add_argument("--codex", default="codex", help="Codex CLI executable")
    parser.add_argument("--claude", default="claude", help="Claude CLI executable")
    return parser.parse_args()


def recent_rate_limit_detected(log_dir: Path, started_epoch: float) -> bool:
    pattern = re.compile(
        r"(?:\b429\b|rate.?limit|too many requests|overloaded)", re.IGNORECASE
    )
    for path in log_dir.glob("*.stderr.log"):
        try:
            if path.stat().st_mtime < started_epoch:
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def emit_event(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


def validate_runtime_options(args: argparse.Namespace, plan: dict[str, Any]) -> int:
    if args.retries < 0:
        raise SystemExit("--retries must not be negative")
    planned = plan["execution"]["recommended_max_concurrent_scouts"]
    parallel = planned if args.parallel is None else min(planned, args.parallel)
    if parallel < 1 and plan["execution"]["shard_count"]:
        raise SystemExit("Parallelism must be positive")
    return parallel


def selected_shards(
    scan_dir: Path, plan: dict[str, Any], only_shards: list[str]
) -> list[Path]:
    shard_paths = sorted((scan_dir / "shards").glob("shard-*.json"))
    if len(shard_paths) != plan["execution"]["shard_count"]:
        raise SystemExit("Shard files do not match plan")
    if only_shards:
        requested = set(only_shards)
        available = {path.stem for path in shard_paths}
        unknown = sorted(requested - available)
        if unknown:
            raise SystemExit(f"Unknown shard ids: {', '.join(unknown)}")
        shard_paths = [path for path in shard_paths if path.stem in requested]
    return shard_paths


def dispatch_scouts(
    shard_paths: list[Path], context: RunContext, parallel: int
) -> list[str]:
    failures: list[str] = []
    run_started_epoch = time.time()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, parallel)
    ) as executor:
        futures: dict[concurrent.futures.Future[tuple[str, bool, str]], Path] = {}
        offset = 0
        current_burst = min(LAUNCH_BURST, max(1, parallel))
        current_interval = LAUNCH_INTERVAL_SECONDS
        rate_adjusted = False
        next_launch_at = time.monotonic()
        while offset < len(shard_paths) or futures:
            capacity = parallel - len(futures)
            now = time.monotonic()
            if offset < len(shard_paths) and capacity > 0 and now >= next_launch_at:
                if not rate_adjusted and recent_rate_limit_detected(
                    context.scan_dir / "logs", run_started_epoch
                ):
                    current_burst = max(1, current_burst // 2)
                    current_interval = max(
                        RATE_LIMITED_INTERVAL_SECONDS, current_interval * 2
                    )
                    rate_adjusted = True
                    emit_event(
                        {
                            "event": "launch_rate_adjusted",
                            "reason": "provider_rate_limit",
                            "launch_burst": current_burst,
                            "launch_interval_seconds": current_interval,
                        }
                    )
                memory_ratio = available_memory_ratio()
                if memory_ratio is not None and memory_ratio < 0.15:
                    emit_event(
                        {
                            "event": "launch_paused",
                            "reason": "memory_pressure",
                            "available_memory_ratio": memory_ratio,
                            "launched": offset,
                            "active": len(futures),
                        }
                    )
                    next_launch_at = now + 5
                else:
                    launch_count = min(
                        current_burst, capacity, len(shard_paths) - offset
                    )
                    for shard_path in shard_paths[offset : offset + launch_count]:
                        future = executor.submit(run_one, shard_path, context)
                        futures[future] = shard_path
                    offset += launch_count
                    next_launch_at = now + current_interval
                    emit_event(
                        {
                            "event": "scouts_dispatched",
                            "count": launch_count,
                            "launched": offset,
                            "active": len(futures),
                        }
                    )

            if not futures:
                if offset < len(shard_paths):
                    time.sleep(max(0.01, next_launch_at - time.monotonic()))
                continue
            has_open_slot = offset < len(shard_paths) and len(futures) < parallel
            wait_timeout = (
                max(0.0, next_launch_at - time.monotonic()) if has_open_slot else None
            )
            completed, _ = concurrent.futures.wait(
                futures,
                timeout=wait_timeout,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in completed:
                futures.pop(future, None)
                shard_id, success, detail = future.result()
                if not success:
                    failures.append(shard_id)
                emit_event(
                    {
                        "event": "scout_finished",
                        "shard_id": shard_id,
                        "success": success,
                        "detail": detail,
                        "active": len(futures),
                        "remaining": len(shard_paths) - offset,
                    }
                )
            if completed:
                render_scan_md(context.scan_dir / "plan.json")
    return failures


def write_run_state(
    scan_dir: Path,
    *,
    status: str,
    selected_shards: list[Path],
    keep_prompts: bool,
    started_at: str,
    failures: list[str] | None = None,
) -> None:
    state: dict[str, Any] = {
        "status": status,
        "started_at": started_at,
        "selected_shards": [path.stem for path in selected_shards],
        "prompt_retention": "all" if keep_prompts else "failures_only",
        "failures": failures or [],
    }
    if status != "running":
        state["ended_at"] = utc_now()
    write_json_atomic(scan_dir / "run.json", state)


def main() -> int:
    args = parse_args()
    plan_path = Path(args.plan).expanduser().resolve()
    plan = read_json(plan_path)
    plan_errors = plan_contract_errors(plan)
    if plan_errors:
        raise SystemExit("Invalid plan: " + "; ".join(plan_errors))
    scan_dir = plan_path.parent
    # Sized from the pressure-free hardware ceiling so that every concurrent
    # scan, whatever the memory happened to be when each was planned, agrees on
    # the same pool.
    _, hardware = hardware_parallel_cap()
    machine_slots = MachineScoutSlots(hardware["machine_parallel_cap"])
    parallel = validate_runtime_options(args, plan)
    shard_paths = selected_shards(scan_dir, plan, args.only_shard)
    requested = {"agent": args.agent, "model": args.model, "effort": args.effort}
    backend = resolve_backend(requested, args.codex, args.claude)
    try:
        bind_scout_policy(scan_dir, requested)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    for name in ("results", "logs", "schemas"):
        (scan_dir / name).mkdir(exist_ok=True)
    runtime_workdir = Path(tempfile.gettempdir()) / "askeveryfile-cli" / plan["scan_id"]
    runtime_workdir.mkdir(parents=True, exist_ok=True)
    backend.prepare_scan(scan_dir)
    started_at = utc_now()
    write_run_state(
        scan_dir,
        status="running",
        selected_shards=shard_paths,
        keep_prompts=args.keep_prompts,
        started_at=started_at,
    )
    scan_md = render_scan_md(plan_path)

    with backend.home_workspace(plan["scan_id"]) as (home_template, homes_root):
        context = RunContext(
            plan=plan,
            scan_dir=scan_dir,
            backend=backend,
            retries=args.retries,
            runtime_workdir=runtime_workdir,
            home_template=home_template,
            homes_root=homes_root,
            machine_slots=machine_slots,
            keep_prompts=args.keep_prompts,
        )
        emit_event(
            {
                "event": "scouts_started",
                "agent": backend.name,
                "model": backend.model,
                "effort": backend.effort,
                "cli": backend.cli_version,
                "shards": len(shard_paths),
                "parallel": parallel,
                "launch_burst": LAUNCH_BURST,
                "launch_interval_seconds": LAUNCH_INTERVAL_SECONDS,
                "retries": args.retries,
                "available_memory_ratio": available_memory_ratio(),
                "machine_slot_capacity": machine_slots.capacity,
                "machine_slot_coordination": (
                    "flock" if machine_slots.available else "unavailable"
                ),
                "home_mode": backend.home_mode,
                "prompt_retention": (
                    "all" if args.keep_prompts else "failures_only"
                ),
                "scan": str(scan_md),
            }
        )
        failures = dispatch_scouts(shard_paths, context, parallel)
    write_run_state(
        scan_dir,
        status="failed" if failures else "complete",
        selected_shards=shard_paths,
        keep_prompts=args.keep_prompts,
        started_at=started_at,
        failures=failures,
    )
    scan_md = render_scan_md(plan_path)
    emit_event(
        {
            "event": "scouts_complete",
            "failures": len(failures),
            "failed_shards": failures,
            "scan": str(scan_md),
        }
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
