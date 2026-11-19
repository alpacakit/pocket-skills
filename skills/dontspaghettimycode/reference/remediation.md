# Structural Change and Compatibility

Decide compatibility from evidence; do not assume preservation or breakage.

## Check the applicable constraints

Inspect transition constraints:

- stored data, history, backfill, replay, and rollback;
- public clients, integrations, events, exports, and versioning;
- rolling deployments and overlapping internal callers;
- in-flight jobs, retries, messages, payments, and irreversible effects;
- promised, relied-upon, regulated, or unsafe behavior.

Search schemas, migrations, callers, contracts, deployment configuration, usage, and tests. Classify current behavior as required, deliberately changed, or incidental; observation proves existence, not correctness.

Resolve "current" to the version its users actually run, never to a revision inside the change. Compatibility and rollback rehearsed against your own work can exercise a path the real prior version does not have.

## Choose the boundary policy

Choose separately for each durable boundary:

| Policy | Use when | Required action |
|---|---|---|
| **Preserve** | A relied-upon or promised contract must remain | Keep the boundary representation, translate it into the new Owner, and remove the legacy domain decision |
| **Migrate** | Stored data, clients, deployment, or in-flight work require temporary overlap | Follow the migration path below |
| **Break** | No obligation requires compatibility, or the break is required and authorized | Move controlled data and callers coherently, communicate or version where applicable, and remove the old path by the named release criterion |
| **Not applicable** | The change does not cross the boundary | Add no compatibility work |

Record the usage, obligation, rollout, data, and reversal evidence. The old shape alone does not justify preservation; cleaner code alone does not justify breakage.

If evidence remains unavailable, state the uncertainty. Choose a reversible path proportional to reversal cost and blast radius, or surface a blocker when safety, security, privacy, or data integrity cannot be protected.

If a preserved boundary regresses and no safe correction is immediate, revert and redesign the transition.

## Migrate with temporary compatibility

When `migrate` is chosen:

1. **Expand** — add the target representation, field, version, or adapter without removing the old contract.
2. **Establish authority** — name exactly one active Owner and route new decisions and writes through it. Make any duplicate representation Derived with defined ordering, idempotency, divergence detection, and repair.
3. **Migrate** — backfill, transform, replay, or reconcile with observable, restart-safe progress.
4. **Shift and verify** — move readers in controlled groups; verify parity, invariants, errors, lag, and remaining old-path use.
5. **Remove** — delete the bridge after named exit criteria are met and rollback no longer depends on it.

Keep temporary compatibility inside one named boundary with a reason, test, Owner, and removal condition. Add no new callers. A rollback implementation must be inactive in normal operation and have a bounded removal condition.

## Defer with a guard

Defer only when all four items are explicit:

1. the correct target and concrete constraint;
2. the one current Owner through which the decision or write still passes;
3. the guard and focused test that prevent further divergence;
4. the observable event, evidence, or threshold that triggers reconsideration.

An adapter may own a legacy encoding for the life of the bridge. If these items cannot be named, or the guard cannot preserve safety, law, security, privacy, or data integrity, surface the blocker instead of deferring.

When retiring an authority, enumerate the questions it answered and who still asks them, not the symbols that named it. A caller that outlives its authority names none of the retired symbols and passes every symbol-based census.

Do not call remediation complete until the independent legacy decision or writable source is removed.
