"""Derived reduction metadata only; originating scan.md is never rewritten."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from scan_artifacts import write_text_atomic

@dataclass
class ReductionProgress:
    labels_completed: int | None = None
    labels_total: int | None = None
    label_statistics: list[dict[str, Any]] = field(default_factory=list)
    partition_status: str = 'unverified'
    completed: int | None = None
    total: int | None = None
    semantic_red: int | None = None
    current_job: str | None = None

    def observe(self, state: Any, jobs: list[Any]) -> None:
        self.semantic_red = sum(c['status'] == 'semantic_red' for c in state.closures)
        if self.total is not None:
            green = {c['job_id'] for c in state.closures if c['status'] == 'green'}
            self.completed = sum(j.descriptor['kind'] == 'accounting' and j.job_id in green for j in jobs)


def render_reduction_inspection(directory: Path, plan: dict[str, Any], status: str,
                                *, progress: ReductionProgress | None = None,
                                failure_reason: str | None = None) -> Path:
    progress = progress or ReductionProgress()
    scan = Path(plan['bindings']['scan_plan_path']).parent
    def link(name: str, path: Path) -> str:
        return f'[{name}](<{path.resolve()}>)'
    total = progress.total if progress.total is not None else progress.partition_status
    lines = ['# Ask Every File reduction', '', f'**Status:** {status}', '',
             f'- Labels: {progress.labels_completed if progress.labels_completed is not None else "unverified"}/{progress.labels_total if progress.labels_total is not None else len(plan["label_blocks"])}',
             f'- Partition: {progress.partition_status}',
             f'- Accounting jobs: {progress.completed if progress.completed is not None else "unverified"}/{total}',
             f'- Experiment semantic-red attempts: {progress.semantic_red if progress.semantic_red is not None else "unverified"}/2',
             f'- Current job: {progress.current_job or "none"}',
             f'- Maximum relation group positions: {plan["group_capacity"]["max_group_positions"]}',
             f'- Accounting output budget: {plan["contracts"]["accounting_output_budget_tokens"]} planning tokens; not a backend limit', '']
    if progress.label_statistics:
        lines += ['## Label diagnostics', '', '| Block | Adopted new keys | Reused keys |', '|---|---:|---:|']
        for s in progress.label_statistics:
            lines.append(f'| {s["job_id"]} | {s["new_keys"]} | {s["reused_keys"]} |')
        zero = [s['job_id'] for s in progress.label_statistics[1:] if s['reused_keys'] == 0]
        lines += ['', 'Zero reuse after the first block (diagnostic only): ' + (', '.join(zero) or 'none'), '']
    if failure_reason:
        lines += ['## Failure', '', '```text', failure_reason.replace('```', '` ` `'), '```', '']
    lines += [link('Original scan.md (unchanged)', scan / 'scan.md'), link('Bound reduction plan', directory / 'plan.json'), '']
    for name in ('partition/manifest.json', 'blocked.json', 'validation.json', 'summary.json', 'verification-queue.json'):
        if (directory / name).is_file():
            lines.append(link(name, directory / name))
    lines += ['', 'Only full validation publishes a green ledger and queue. Local completion is not full closure.',
              'Labels compare current rows with all prior key definitions, not prior original rows. Earlier labels are never revised; one position has one key; separate keys do not prove absence of relations.',
              'The main thread must not read the packet, label inputs, accounting inputs, or full ledger.', '']
    path = directory / 'inspection.md'
    write_text_atomic(path, '\n'.join(lines))
    return path
