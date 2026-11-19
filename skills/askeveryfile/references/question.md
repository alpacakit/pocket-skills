# Write the Question

This document owns Question construction and the shardable search model. Every
shard receives the same Question but only its own whole files or structural
fragments. A scout cannot inspect the working tree, commands, history, other
shards, linked material, or prior conversation.

State the local predicates each shard can apply independently and the
cross-shard judgment synthesis must make.

## Write six sections

Headings are free text. Keep each section's meaning, not its wording, and hold
each to one to three sentences or bullets.

- **Goal** — the decision the full scan must support.
- **What to inspect** — the local evidence and search model each scout applies.
- **When to report** — the evidence threshold, observation identity, and useful
  categories.
- **What not to report** — false positives and unsupported inferences.
- **Priority** — the recall-versus-precision tradeoff.
- **What synthesis decides** — the aggregation and judgment reserved for the
  main agent.

## Build a self-contained search model

Inspect every premise and comparison target that will not be delivered to every
shard: uncommitted or staged diffs, referenced files, issues, runtime failures
and logs, baseline commits, branches, screenshots, prior analysis, and
historical implementations. A file inside the target is still unavailable to a
scout that does not receive it.

Describe only the characteristics needed to recognize and judge local evidence:

- purpose, user-visible behavior, triggers, and inputs;
- state read or written, outputs, and side effects;
- failure, retry, idempotency, and lifecycle behavior;
- ownership and architectural boundaries;
- authoritative source, applicable version, and baseline; and
- exclusions, likely false positives, and classification criteria.

Use names, literals, and implementation details as search hints, not the
definition of equivalence. Never depend on “this diff”, “the code above”, or
“the referenced design”. Treat an unverified caller conclusion as a hypothesis
and request supporting and disconfirming evidence.

## Separate local collection from global judgment

| User intent | Ask each scout to report | Reserve for synthesis |
| --- | --- | --- |
| Count or find all | One evidenced candidate per observation, with a stable identity and role | Deduplicate, verify, and count |
| Find equivalent work | Candidate purpose, triggers, state, effects, failures, and ownership boundary | Classify complete equivalence, partial reuse, same-owner duplication, intentional translation, or separation |
| Audit a rule or convention | Applicable violations, governing examples, explicit exceptions, and ambiguity | Verify applicability and separate violations from exceptions or preferences |
| Find conflicting rules | Comparable statements with local scope, authority, and currentness | Decide conflict, compatible scoped variants, or ambiguity |
| Prove unused or obsolete | Positive use or retention evidence, including runtime, tooling, dynamic entry, public contract, fixtures, and governing design | Qualify absence and decide retain, connect, move, or delete |
| Audit a claim or defect | Supporting and counterevidence, reachable triggers, consequences, and falsifiers | Confirm, correct, reject, or leave inconclusive |
| Build a semantic inventory | Definitions, state, handlers, configuration, documentation, and tests that add distinct meaning | Merge related representations and identify ownership gaps or conflicts |
| Change or migrate | Read-only candidates, dependencies, and consequences | Decide and verify changes in a separate implementation step |

## Apply these rules

1. Define one observation and its stable deduplication identity. For counts,
   distinguish implementations from callers, wrappers, configuration, tests,
   and documentation.
2. When the Question requires a decision about every deterministically
   enumerable subject, build that subject manifest before scouting and define
   the stable subject identity here. Observation coverage does not substitute
   for subject coverage.
3. Choose categories from one useful axis, preferably finding kind or evidence
   role. Do not mix action, confidence, verification state, and finding kind
   into one category list.
4. Request direct line evidence and the local context needed to interpret it.
   Split multi-file reasoning into roles such as definition, trigger, caller,
   state, side effect, failure handling, rule, and counterevidence; connect
   those roles during synthesis.
5. Preserve authority and currentness. Examples, generated indexes, backups,
   tests, and caller analysis are not governing evidence by default.
6. Keep exact strings, filenames, imports, declarations, and AST relationships
   in deterministic search or parsing; give scouts only the remaining semantic
   judgment.
7. Narrow or split the population when a shard could exceed 50 observations.
   Never ask an exhaustive scan to sample silently.
