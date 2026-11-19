"""One persisted reconstruction path for labeled runner, reuse and validator."""
from __future__ import annotations
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from bound_reduction import inspect_bound_reduction
from reduction_jobs import ReductionJob, make_job, partition_jobs
from reduction_labels import SubjectPrefix, advance_prefix, subject_groups
from reduction_semantic_state import (SemanticStateInspection, inspect_job_attempts,
                                      selected_job_result, ensure_semantic_state)
from reduction_inspection import ReductionProgress
from scan_artifacts import render_json, sha256_file


def materialize(directory: Path, files: dict[str, bytes], *, publish: bool, staging_root: Path) -> bool:
    if directory.is_symlink():
        raise ValueError('Canonical job/partition directory must not be a symlink')
    if not directory.exists():
        if not publish:
            return False
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f'.{directory.name}-', dir=staging_root))
        try:
            for name, data in files.items():
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            staging.rename(directory)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    if not directory.is_dir():
        raise ValueError('Canonical materialization is not a directory')
    paths = list(directory.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise ValueError('Canonical materialization contains a symlink')
    if {str(p.relative_to(directory)) for p in paths if p.is_file()} != set(files):
        raise ValueError('Canonical materialization files mismatch')
    if any((directory / name).read_bytes() != data for name, data in files.items()):
        raise ValueError('Canonical materialization bytes mismatch')
    return True


@dataclass(frozen=True)
class ExecutionInspection:
    packet: dict[str, Any]
    prefix: SubjectPrefix
    jobs: list[ReductionJob]
    accounting: list[ReductionJob]
    state: SemanticStateInspection
    results: dict[str, dict[str, Any]]
    next_job: ReductionJob | None


def inspect_execution(plan_path: Path, plan: dict[str, Any], *, publish: bool = False,
                      progress: ReductionProgress | None = None) -> ExecutionInspection:
    progress = progress or ReductionProgress()
    inspection = inspect_bound_reduction(plan, plan_path.parent)
    if inspection.errors or inspection.packet is None:
        raise ValueError('Invalid reduction: ' + '; '.join(inspection.errors))
    packet = inspection.packet
    directory = plan_path.parent
    prefix = SubjectPrefix()
    jobs: list[ReductionJob] = []
    results: dict[str, dict[str, Any]] = {}
    progress.labels_total = len(plan['label_blocks'])
    progress.labels_completed = 0
    progress.partition_status = 'pending labels'
    limit = plan['group_capacity']['max_group_positions']
    total = len(packet['observations']) + len(packet['unresolved_questions'])
    pending = None
    for block in plan['label_blocks']:
        job = make_job(packet, 'label', block['job_id'], plan['capacity'], limit,
                       block['positions'], [], prefix=prefix)
        if not job.descriptor['context']['fit']:
            raise ValueError('Canonical label actual input overflow')
        jobdir = directory / 'jobs' / job.job_id
        ready = materialize(jobdir, {'input.json': job.input_bytes,
                                     'manifest.json': render_json(job.descriptor).encode('utf-8')}, publish=publish, staging_root=directory)
        if not ready and ((directory / 'attempts' / job.job_id).exists()
                          or (directory / 'results' / f'{job.job_id}.json').exists()):
            raise ValueError('Label artifacts have no canonical job materialization')
        jobs.append(job)
        attempts, _ = inspect_job_attempts(plan_path, plan, job, persist=publish)
        result = selected_job_result(plan_path, plan, job, attempts, publish=publish)
        if result is None:
            pending = job
            break
        results[job.job_id] = result
        prefix = advance_prefix(prefix, result['output'], block['positions'], total, limit,
                                job.job_id, sha256_file(directory / 'results' / f'{job.job_id}.json'))
        progress.labels_completed += 1
        progress.label_statistics = list(prefix.statistics)
    root = directory / 'jobs'
    known_labels = {j.job_id for j in jobs}
    if root.is_symlink() or (root.exists() and any(p.name not in known_labels or p.is_symlink() or not p.is_dir()
                                                  for p in root.iterdir())):
        raise ValueError('Unexpected future label materialization outside authenticated prefix')
    accounting: list[ReductionJob] = []
    if pending is None:
        progress.partition_status = 'pending'
        manifest, accounting = partition_jobs(packet, plan, subject_groups(prefix, total), prefix.chain_sha256)
        files = {'manifest.json': render_json(manifest).encode('utf-8'),
                 **{f'inputs/{j.job_id}.json': j.input_bytes for j in accounting}}
        if not materialize(directory / 'partition', files, publish=publish, staging_root=directory):
            raise ValueError('Missing canonical accounting partition')
        progress.partition_status = 'fixed'
        progress.total = len(accounting)
        jobs += accounting
    elif (directory / 'partition').exists() or (directory / 'partition').is_symlink():
        raise ValueError('Partition exists before a complete authenticated label prefix')
    # Only now is the complete reachable prefix/partition known. Count ALL its
    # terminal red attempts, including events whose marker was never written.
    state = ensure_semantic_state(plan_path, plan, jobs, persist=publish)
    progress.observe(state, jobs)
    for job in accounting:
        result = selected_job_result(plan_path, plan, job, state.attempts[job.job_id], publish=publish)
        if result is not None:
            results[job.job_id] = result
        elif pending is None:
            pending = job
    return ExecutionInspection(packet, prefix, jobs, accounting, state, results, pending)
