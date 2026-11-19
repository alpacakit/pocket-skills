"""Run labeled reduction with one immutable attempt lifecycle and experiment cap."""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from agent_attempt import execute_codex_attempt
from reducer_runtime_policy import ReducerPolicy
from reduction_jobs import REDUCTION_PLAN_SCHEMA_VERSION, job_shape_errors
from reduction_execution import inspect_execution
from bound_reduction import require_reduction_location
from reduction_semantic_state import reduction_lock, input_manifest
from reduction_inspection import ReductionProgress, render_reduction_inspection
from scan_artifacts import load_json_artifact
from scan_resources import MachineScoutSlots


def execute(plan_path: Path, plan: dict[str, Any], codex: str, retries: int, progress: ReductionProgress) -> int:
    used_job = None
    used_attempts = 0
    executable = None
    version = None
    while True:
        view = inspect_execution(plan_path, plan, publish=True, progress=progress)
        if view.state.blocked is not None:
            raise ValueError('semantic_blocked: experiment-wide semantic-red cap 2 reached')
        job = view.next_job
        if job is None:
            progress.current_job = None
            render_reduction_inspection(plan_path.parent, plan, 'awaiting_full_validation', progress=progress)
            print(json.dumps({'status': 'awaiting_full_validation', 'label_jobs': len(plan['label_blocks']),
                              'accounting_jobs': len(view.accounting), 'inspection': str(plan_path.parent / 'inspection.md')}))
            return 0
        progress.current_job = job.job_id
        if used_job != job.job_id:
            used_job, used_attempts = job.job_id, 0
        if used_attempts >= retries + 1:
            last = view.state.attempts[job.job_id][-1].receipt
            reason = ('semantic closure failed' if last['status'] == 'complete' else
                      f"transport/shape failed; exit={last['exit_status']}, raw={last['raw_result_status']}")
            raise ValueError(f'Reducer job failed after bounded attempts: {job.job_id}; {reason}')
        if executable is None:
            executable = shutil.which(codex)
            if executable is None:
                raise ValueError(f'Codex CLI not found: {codex}')
            version = subprocess.run([executable, '--version'], capture_output=True, text=True, check=True).stdout.strip()
        previous = view.state.attempts[job.job_id]
        number = max((int(a.attempt_dir.name.removeprefix('attempt-')) for a in previous), default=0) + 1
        render_reduction_inspection(plan_path.parent, plan, 'running', progress=progress)
        execute_codex_attempt(
            attempt_dir=plan_path.parent / 'attempts' / job.job_id / f'attempt-{number:04d}',
            attempt_id=f'{job.job_id}-attempt-{number:04d}', attempt_number=number,
            executable=executable, cli_version=version, policy=ReducerPolicy(**plan["policy"]),
            policy_sha256=plan["policy_sha256"], workdir=plan_path.parent, prompt=job.prompt,
            input_manifest=input_manifest(plan_path, plan, job), schema=job.schema,
            output_errors=lambda output: job_shape_errors(job, output), machine_slots=MachineScoutSlots(1))
        used_attempts += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('plan', help='Labeled reduction plan v5')
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--retries', type=int, default=1, help='Retries per job in this invocation')
    args = parser.parse_args()
    if args.retries < 0:
        raise ValueError('--retries must be at least zero')
    path = Path(args.plan).expanduser().absolute()
    plan = load_json_artifact(path).value
    if not isinstance(plan, dict) or plan.get('schema_version') != REDUCTION_PLAN_SCHEMA_VERSION:
        raise ValueError('Unsupported reduction plan; expected labeled plan v5')
    path = require_reduction_location(plan, path)
    progress = ReductionProgress()
    with reduction_lock(path.parent):
        try:
            return execute(path, plan, args.codex, args.retries, progress)
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            render_reduction_inspection(path.parent, plan, 'semantic_blocked' if str(exc).startswith('semantic_blocked') else 'failed',
                                        progress=progress, failure_reason=str(exc))
            raise ValueError(str(exc)) from exc

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        sys.exit(str(exc))
