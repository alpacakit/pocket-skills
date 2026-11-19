---
name: dontspaghettimycode
description: Design, assess, refactor, and verify load-bearing software structure by giving each fact, rule, state transition, and failure policy one authoritative Owner. Use when durable or external data, domain invariants, schemas, public contracts, authorization, payments, migrations, or recovery change; when adding or modifying behavior in existing code would otherwise stack a special-case branch, flag, parameter, or near-copy; or when scattered decisions, drifting state, leaking boundary meanings, or unowned failures reveal structural distortion. Skip work that neither changes nor assesses system structure.
---

# DON'T SPAGHETTI MY CODE

Design for maintainability around stable data structures and their relationships so one conceptual change needs the fewest necessary coordinated edits. Put each mutable fact, rule, and transition behind one authoritative Owner.

Treat recurring bugs, change amplification or collision, invalid or drifting data, operational workarounds, and green-but-irrelevant tests as symptoms of structural distortion. Trace each symptom to the wrong requirement, model, Owner, boundary, lifecycle, failure policy, or verification path that forces surrounding code to compensate. Correct that source and remove the compensations.

Separate the correct target, compatibility obligations, and transition path. Existing code, stored shape, and business workflow are evidence to inspect, not authority to preserve. Define the target from required behavior and invariants first; then decide what must survive the transition from actual reliance and obligations.

Preserve project conventions unless they conflict with a higher priority below.

## Vocabulary

- **Canonical** — the sole writable source of a fact.
- **Derived** — data rebuildable from Canonical data.
- **Owner** — the one concrete place every write or decision of that kind passes through, such as a module, function, class, or database constraint; not a layer or team.
- **Expected failure** — an anticipated non-success outcome represented in the contract and handled deliberately by the caller.
- **Fault** — a dependency, resource, or invariant failure outside normal domain outcomes.

Translate the labels in user-facing output when helpful.

## Decision priority

Resolve conflicts from left to right:

`Safety, law, security, privacy > data integrity > required target behavior and explicit compatibility obligations > ownership and consistency > measured reliability and performance > simplicity and convenience`

At the same level, protect the choice with the greater reversal cost and blast radius. If a higher priority forces a deviation, record the evidence, guard, and revisit condition.

## Rules

1. **One decision, one Owner.** Give each fact one writable source and each invariant, policy, and transition one implementation. Use the smallest natural data structure, constraint, or function that can enforce it; an adapter owns only boundary translation, not the underlying decision. The rule runs both ways: do not gather facts that change for different reasons into one Owner. Let one process, service, or database host multiple logical Owners only when their colocation does not create shared decision authority, couple otherwise independent mutable state or domain lifecycles, or force unrelated changes to be understood or modified together.

2. **Separate boundary meanings.** Treat external input, domain, persistence, API, and UI as distinct contracts. Reuse a representation only when vocabulary, validation, lifecycle, and compatibility are genuinely identical; otherwise validate and translate.

3. **Model the target before logic.** Define identity, ownership, relationships, states, lifecycle, and invariants before implementation. Prefer direct code and explicit structures; do not add indirection or configuration for imagined needs.

4. **Keep Canonical data coherent.** Classify every intentional copy and define its source, consistency, and lifecycle.

5. **Make failure explicit.** Represent Expected failures in the contract. Propagate Faults with cause and operation context; never swallow them, retry without a bound, or disguise failure as ambiguous success.

6. **Change deliberately.** Correct existing architecture and workflows when the target requires it. When adding or modifying behavior, restructure the existing branches, data, and functions that answer the same question so the new case becomes an ordinary case of one structure, not a special-case branch, flag parameter, or near-copy beside the old code; prefer the shape in which the special case disappears. Reshape only for cases that exist now (Rule 3) and stop at code that answers a different question (Rule 1). If the authorized scope excludes the reshaping, report the special case added and the reshaping left undone. For each durable boundary choose `preserve`, `migrate`, `break`, or `not applicable` from actual reliance and obligations; a regression is an unintended break of something chosen for preservation.

## Workflow

For an assessment only, read `reference/warnings.md`, load other references only as evidence requires, and do not edit.

For an addition or modification to existing code, the symptom is its change cost: in step 1 name the existing branches, data, and functions that answer the question the new case raises, and in step 2 decide whether the new case would land as a special case. If it already fits, implement it directly.

For greenfield work with no existing structure to land in, name required behavior and forbidden states, skip distortion diagnosis, and continue at step 3; do not invent a pathology.

1. **Pin the symptom** — name the observable wrong behavior, change cost, data anomaly, operational workaround, or verification gap. Record the scenario, expected and actual outcomes, affected users or callers, and evidence.
2. **Diagnose the distortion** — read `reference/warnings.md` and support or disprove the deepest shared cause.
3. **Define the target** — define the correct requirement, model, Owner, and invariant independently of the compensations. For persisted or domain state, start with the conceptual model and actual storage. Separate the correct end state, compatibility obligations, and transition path; load every applicable reference below before planning.
4. **Map the change** — for a load-bearing change, write the Change map below. Inspect outward through `data and schema → domain and Owner → application and API → UI`, changing each in-scope layer the target requires.
5. **Implement from the Owner outward** — establish the authority first, then update dependent representations and callers. Remove compensating decisions made unnecessary by the correction. Add adapters or bridges only for the boundary policy chosen in `reference/remediation.md`.
6. **Verify and close** — reproduce the original symptom through the boundary real callers reach, then exercise target behavior, a forbidden case, preserved behavior, and applicable failure, migration, rollback, or removal paths. Confirm that the diagnosed cause and its compensations are gone; fix or revert unintended regressions. Use the Structural result below for load-bearing work or any assessment.

Before accepting a new check, make it fail under deliberate removal or substitution and take the result from the subject, not the harness. Keep any check that must remain true with the project.

## Required records

### Before editing: Change map

A change is load-bearing when it affects persisted or external data, domain state or invariants, a public contract, authorization, payment, failure or retry semantics, or a policy used by multiple modules.

After inspection and before editing, report in the user's language:

```markdown
## Change map

- Problem: observable symptom, scenario, and cost; for greenfield work, the risk or forbidden state instead
- Distortion: deepest supported wrong requirement, model, Owner, boundary, lifecycle, failure policy, or verification path, and the compensations it forces — or not applicable for greenfield work
- Target: smallest correct end state and its Owner
- Compatibility: `preserve`, `migrate`, `break`, or `not applicable` for each durable boundary, with evidence and transition
- Deferred: guarded work left behind and its removal or revisit trigger — or None
```

Keep each field to one line. Omit the map for a non-load-bearing change.

### After work: Structural result

For a load-bearing change or assessment, close once in the user's language with the actual result; when delegated, use the host's return shape instead. Skip this for a non-load-bearing edit.

```markdown
## Structural result

- Result: actual correction or assessment conclusion
- Cause: observed symptom or forbidden state, supported distortion, evidence, and disconfirming evidence
- Ownership: each affected fact, rule, or transition and its one authoritative path and symbol — or unchanged
- Removed: the independent decision or writable source that no longer exists — or None
- Compatibility: durable boundaries preserved, migrated, broken, or not applicable, with evidence and result
- Verified: original symptom or forbidden state, corrected invariant, applicable failures, compatibility, or migration, the real boundary they ran through, and the command or artifact
- Remaining: guarded legacy, deferred or uninspected scope, and the trigger to revisit — or None
```

## Reference

| File | Owns | Read when |
|---|---|---|
| `reference/warnings.md` | Observable symptoms, structural distortion hypotheses, and diagnosis | Assessing spaghetti risk or tracing a symptom to its structural cause |
| `reference/modeling.md` | Data classification, target models, absence, normalization, and Derived data | Changing external or persisted data, schemas, domain state, denormalization, or optionality |
| `reference/failures.md` | Expected failures, Faults, retry, cleanup, fallback, and partial success | Changing failure or recovery behavior |
| `reference/remediation.md` | Compatibility, migration, deferral, and legacy removal | Crossing stored data, external contracts, relied-upon workflows, or overlapping deployments |
| `reference/examples.md` | Ambiguous decision patterns | A rule remains unclear after inspecting the actual artifact |
