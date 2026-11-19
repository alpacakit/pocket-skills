# Synthesize and Verify

This document owns post-reduction source verification and reporting.
[Reduction](reduction.md) owns reducer execution, transport artifacts, closure,
and action validation.

Green reduction validation proves snapshot delivery, citation integrity, and
closed accounting; it does not prove semantic correctness, completeness, or
absence. The Question defines relevance and the final decision. Scout category
and confidence are routing hints, not truth, importance, or majority weight.

Do not perform bulk semantic reduction in the main context. Read only the green
summary and verification queue, then one narrow bundle for each queued cluster;
never open, paste, or shell-dump the review packet or any label or accounting
job input.

## Verify queued clusters

Restate the six Question sections from [Write the Question](question.md) as a
short decision checklist. For every queued cluster, use its narrow bundle to
inspect cited source directly for currentness, context, scope, and authority.
Expand to callers, tests, configuration, history, or documentation only when
the queued verification depth or source relationships require it.

Produce exactly one bound verification record for each queued cluster. Preserve
member-level distinctions, including conflicts, counterevidence, and minority
evidence. Record:

- disposition: `confirmed`, `rejected`, or `inconclusive`;
- the verified claim and relevant scope;
- the authoritative source or owner when authority matters;
- the consequence for the Question-level answer; and
- current evidence and a falsifier.

`confirmed` describes a verified claim; it is not permission to act. Record
`delete`, `retain`, `replace`, or `none` separately. Before any mutation, run
the action validator in [Reduction](reduction.md) against the current source
snapshot and every queued cluster. Never treat `source_verified: true` or a
verifier assertion as evidence.

## Handle unresolved items

Every unresolved ID has a reducer disposition and reason in
`accounting-ledger.json`; `summary.json` reports its count. Unresolved items do
not automatically become source-verification clusters. If that count is
nonzero, the main agent may retrieve only `unresolved_records` from the ledger
for final reporting; it must not load the packet or dump the full ledger.

## Report

Lead with the Question-level answer and exact verified evidence. Then separate
rejected candidates, unresolved questions, and coverage limitations. Include
user-relevant scope and exclusion limits, plus any rejected high-signal
observation a reader would otherwise expect.

State plainly when no verified observation survived. Missing scout observations
do not prove corpus-wide absence, and separate clusters do not prove that their
claims are unrelated: reduction groups by subject labels that are never
revised or merged later. When duplicates or conflicts across clusters matter to
the answer, say so as a coverage limitation instead of implying they were
reconciled. When the Question requires an exhaustive
judgment over deterministically enumerable subjects, report subject-manifest
coverage rather than treating file delivery or observation coverage as subject
coverage. Surface snapshot, scout-run, or per-shard detail only when it changes
how the conclusion should be interpreted.
