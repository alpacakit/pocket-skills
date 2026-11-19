# Reduction

Reduction is the accounting stage between a green `review-packet.json` and
source verification. No single model call can carry a large packet: the output
must account for every item, so it grows with the item count. Reduction
therefore splits the work by responsibility, not by arbitrary slices:

- **label** jobs read the packet in fixed blocks of positions and give every
  observation and unresolved item one neutral subject key, reusing the keys
  already adopted by earlier blocks; every position with the same key forms
  one group;
- **accounting** jobs read complete cited evidence for whole groups, select
  each canonical record and the records merged into it, and make every final
  accounting decision;
- the **validator** decodes and assembles all jobs, closes the complete packet
  exactly, and alone publishes the ledger and verification queue.

Every job runs in an isolated Codex CLI process, read-only and with no tools,
using the model and effort chosen for the reducer role in
[SKILL.md](../SKILL.md), so the main thread never ingests the packet or any job
input. All label and accounting jobs use the same plan policy.

## Supported flow

```text
green review packet
  → plan_reduction.py       label blocks, worst-case label fit, group cap,
                            immutable bindings
  → run_reducers.py         label jobs in order → key groups → frozen
                            partition → accounting jobs
  → validate_reduction.py   assembly, exact full closure, derived queue,
                            reduction inspection
  → project_verification_input.py   one narrow bundle per queued cluster
  → main agent              current-source verification and conclusion
  → validate_verification_actions.py   action gate over the whole queue
```

Small and large packets use the same flow; there is no separate single-call
mode, no single global grouping call, and no runner or fallback for older
reduction plans. Automatic source-verifier
execution and semantic verifier-result merge are not implemented. A capacity
failure is closed: stop instead of reading a partial packet, splitting a group,
lowering a reserve, or inventing a fallback.

The main thread may read the reduction's `inspection.md`, `validation.json`,
`summary.json`, `verification-queue.json`, and the narrow bundles named by
queued cluster IDs. If the summary's unresolved count is nonzero, it may
project only `unresolved_records` from the ledger for final reporting. It must
not open, paste, or shell-dump the review packet, any label or accounting job
input, the partition, any raw result, or the full ledger into its context.

## Plan the reduction

Materialize the current runtime catalog, then plan once into a new reduction
directory directly inside the scan directory:

```bash
codex debug models --bundled > "<scan-dir>/reduction-context-catalog.json"
python3 "<skill-dir>/scripts/plan_reduction.py" \
  "<scan-dir>/review-packet.json" \
  --context-catalog "<scan-dir>/reduction-context-catalog.json" \
  --model <model> --effort <effort> \
  [--output-dir "<scan-dir>/reduction-<name>"]
```

The default output is `<scan-dir>/reduction`. Never overwrite a catalog that an
existing plan already binds; follow [Continue a scan from an older reduction
contract](#continue-a-scan-from-an-older-reduction-contract) instead.

`plan_reduction.py` requires the model and effort and records them as the
plan's policy with `policy_sha256`. Execution, reuse, resume, and validation
use that policy; they do not choose another model or effort. Each consumer
rebuilds the plan from the digest-bound context catalog for the recorded model,
including its effective context window, and rejects a mismatch. A missing,
duplicate, or invalid model catalog entry is an error.

When the reducer choice comes from a trial, a candidate passes when the complete
`validate_reduction.py` is green. A candidate that overflows or ends closure-red
is not kept. Trying a different model or effort changes the policy and is a new
experiment under the rules below; plan it into a new `reduction-<name>` directory
and keep the green scouts and packet unchanged. A larger context window can
clear `label_input_overflow`, but `atomic_unit_overflow` also depends on an
accounting output budget that no model changes. If no candidate fits, report the
closed capacity failure; do not split a subject group or lower a reserve. The
way forward is then new scans over smaller populations, which repeats scouting.

The planner validates the packet, scan plan, green scout validation, result
manifest, and source snapshot exactly as before; it never rescans or rebuilds
the packet. It then fixes:

- **Context window.** The catalog's `context_window`, scaled by
  `effective_context_window_percent` when the catalog provides it. This only
  ever narrows the window. `max_context_window` and other experimental values
  are not capacity.
- **Label blocks.** Positions cover every observation in packet order,
  followed by every unresolved item. They are cut into consecutive blocks of
  128 positions (the last block may be shorter). A block is a unit of work,
  not a semantic group. Each label row carries its frozen position,
  observation ID, category, scout confidence, title, claim, `why_it_matters`,
  and the relative path and locator of every evidence item; an unresolved row
  carries its position, ID, and text. Quotes stay out of label inputs and
  reach the model only through the accounting inputs, which carry every quote
  and locator. File hashes and absolute paths never enter any model input;
  they remain in the review packet and in the plan's bindings.
- **Label fit.** Every block is fit for its worst case before anything runs:
  its rows plus the largest vocabulary earlier blocks could have adopted (one
  new key for every earlier position, each with the longest allowed
  definition), with exact rendered prompt and schema bytes, the system reserve,
  and the recovery headroom. The output reserve is the largest serialized
  label output (every position a new key with the longest definition and its
  own assignment), counted as tokens because a token is never smaller than one
  byte, plus a 16,384-token reasoning allowance. It is a planning bound for the
  scripts' serialization, not a limit the backend enforces.
- **Group cap.** The largest number of positions in one group that is
  guaranteed to fit one accounting job under the accounting fit below. It is
  computed conservatively: the largest cited rows, the full category and path
  dictionaries, every receipt in the same job, and every position charged as
  an observation. The cap therefore depends on the packet and is recorded in
  the plan. For example, with 99 receipts the accounting output budget alone
  allows at most 90 positions (4,160 + 90×576 + 99×96 = 65,504 ≤ 65,536; 91
  would exceed it). The cap is this worst-case guarantee for one group, not the
  size limit of a job, which can reach 106 observations when it holds few
  receipts. It is checked by validation against each key's running position
  count. Label jobs never see those counts or the cap and are not asked to size
  subjects toward it, and the output schema does not bound subject size, so a
  truly larger subject can still be labeled whole and validation closes it.

Packet schema v4 preserves every observation ID, assigns a position-based ID to
every unresolved question, and retains one receipt for every successfully
validated shard, including empty shards. Scout result v6 records explicit
overflow. A still-green v5 result may retain `overflow_state: unknown`; unknown
is never converted to false.

Every value, formula, constant, and count is recorded in `plan.json` (plan
schema 5, mode `labeled`), which is never rewritten afterwards. Its contracts
bind accounting wire 3, decoder 1, normal form 2, and the schema and template
hashes; job result wrappers and attempt input manifests are also version 5, and
the partition manifest is version 3. The runner and validator refuse any other
plan version. The plan status is one of:

- `ready` — every label block fits its worst case, the group cap admits at
  least one position whenever there are positions, and the receipt unit fits an
  accounting job. A packet with no observations and no unresolved items has no
  label jobs and a group cap of 0; it goes straight to packing the receipt
  unit;
- `label_input_overflow` — some label block does not fit its worst case;
- `atomic_unit_overflow` — the group cap is 0 although there are positions, or
  the receipt unit alone does not fit an accounting job.

Any status other than `ready` is a closed failure: the planner still writes
`plan.json` and `inspection.md`, exits nonzero, and creates no executable job
input. A changed source snapshot or catalog, a broken packet or contract, or an
existing output directory stops the planner with an error before any plan is
written. A `ready` plan says nothing yet about accounting: label inputs depend
on earlier label results and accounting jobs depend on the key groups, so the
runner builds and fits each of them exactly when its inputs are known. Every fit
is a planning estimate; it is not evidence that a model call will succeed.

## Subject labeling

Label jobs run one block at a time, in block order. Each job sees the exact
Question, every row of its block, and every key with its definition that
earlier blocks have adopted (without how many positions use it). It returns:

```json
{"schema_version": 1,
 "new_subjects": [{"key": "subject-key", "definition": "…"}],
 "assignments": [{"key": "subject-key", "positions": [128, 132]}]}
```

A key names one neutral subject: the concrete judgment the Question asks about,
shared by every position that bears on it. Entry points, conditions, scope
differences, counterevidence, tests, and minority views of the same subject
take the same key; truth, stance, evidence role, and differences of condition
or scope never split a key. A definition describes the subject neutrally and
never states a claim as true. Subjects the Question treats as separate objects
or operations get separate keys, even when their names look alike. A scout's
subject ID or title is a hint, not the key.

The output is checked in two layers.

**Shape** (a transport/shape failure: it does not consume the semantic cap and
is retried under the invocation's `--retries` budget):

- exactly the three fields above, with `schema_version` 1;
- at most one `new_subjects` entry and one assignment per block position, and
  each assignment has 1 to block-size positions;
- every key matches `^[a-z0-9][a-z0-9._:-]{0,31}$`;
- every definition has 1–80 Unicode code points and no C0 or C1 control
  characters;
- every position is an integer from 0 to the last position of the packet.

**Closure** (a shape-valid output that breaks one of these is a semantic red
attempt and counts toward the experiment's cap of two):

- every position of the block appears in exactly one assignment, and no
  position outside the block appears;
- every assigned key is either an adopted key or declared in `new_subjects`;
  each key appears in at most one assignment; a declared key is unique and is
  not an adopted key;
- every definition is at most 240 UTF-8 bytes;
- no key's running position count, including this block, exceeds the group
  cap.

A declared key that no assignment uses is kept in the raw result but is not
adopted, so later blocks never see it. Adopted keys and definitions are always
derived again from the green label results; there is no separate, editable
vocabulary file.

When every label job is green, one deterministic function puts all positions
with the same key into one group and closes exact coverage of every position.
Groups are ordered by their smallest position; no meaning is added at this
step.

### What labeling cannot do

- A later block compares its own complete rows with the definitions of earlier
  keys, not with earlier rows themselves.
- An earlier assignment is never revised, each position gets exactly one key,
  and no later step merges two keys, even if later evidence suggests they name
  one subject.
- Positions in different groups are not proven unrelated. Exact coverage proves
  that every position was labeled once; it cannot prove that the labels are
  semantically right, which is why accounting re-reads complete evidence and
  the main thread still verifies every queued cluster against current source.

## Partition and accounting jobs

After every label job is green, one deterministic packing function (stable
first-fit) takes whole key groups in order of their smallest position and puts
each into the first accounting job that still fits, opening a new job only
when none does. The complete set of shard receipts is one more atomic unit,
packed last in the same way, so a packet with no observations still takes the
ordinary path. A unit that fits no job by itself stops the run with
`atomic_unit_overflow`; it is never split.

Each job fits only if both conditions hold. First, its exact rendered prompt and
schema bytes, the system reserve and recovery headroom (8,192 tokens each), and
its output reserve fit the context window. The output reserve is:

```text
4,160 + records*320 + max_clusters*256 + unresolved*96 + receipts*96
```

`max_clusters` equals the job's observation count, so the estimate never assumes
sparse clustering and each observation costs 576 tokens. This is the earlier
reserve (4,096 + 448 per observation) with the nested output's extra structure
added on top, at one token per serialized byte (64 + 128 per observation);
nothing was lowered. Second, that reserve stays within the 65,536-token
accounting output budget. The budget is an operational planning bound, not a
catalog or backend limit: it keeps each response to at most 106 observations
without lowering the per-item reserve. Earlier validated receipts used about
100–150 output tokens per observation, well inside the 576 reserved. Neither the
reserve nor the budget is enforced by the backend; actual usage comes from
validated attempt receipts.

The partition is published once as a directory: `partition/manifest.json`
records the groups and every job's positions, receipts, input, prompt, schema,
fit, and the hash chain of all green label results, and `partition/inputs/`
holds each job's exact input bytes. It is never rewritten. Every later run and
the validator re-derive it and stop on any difference.

Each accounting input uses the compact projection v1 grammar for that job's
observations: the exact Question; every observation ID, category, title, claim,
scout confidence, `why_it_matters`, relative evidence path, locator, and quote;
every unresolved ID and text in the job; and every receipt ID, status, and
overflow state when the job holds the receipt unit. Confidence is a routing
hint only; it never establishes truth, importance, or majority weight.

## Accounting output

An accounting job returns accounting wire v3 for its own items. The model places
every ID explicitly; the placement is the judgment:

```json
{
  "schema_version": 3,
  "clusters": [{
    "route": "source_verification",
    "required_verification_depth": "…",
    "requested_source_relationships": ["…"],
    "reason": "…",
    "ordinary": [{
      "observation_id": "…", "accounting_status": "verify",
      "role": "supporting", "reason": "…", "falsifier": null
    }],
    "merge_sets": [{
      "canonical": {
        "observation_id": "…", "accounting_status": "verify",
        "reason": "…", "falsifier": null
      },
      "merged": [{
        "observation_id": "…", "role": "supporting",
        "reason": "…", "falsifier": null
      }]
    }]
  }],
  "unclustered_records": [{
    "observation_id": "…", "accounting_status": "…",
    "reason": "…", "falsifier": null
  }],
  "unresolved_records": [{
    "unresolved_id": "…", "disposition": "preserved", "reason": "…"
  }],
  "shard_records": [{
    "receipt_id": "…", "disposition": "accounted", "reason": "…"
  }]
}
```

- `ordinary` holds non-merged records with the role `candidate`, `supporting`,
  `counterevidence`, or `minority`.
- A `merge_sets` entry names one `canonical` record, chosen by the model, and
  the records `merged` into it (role `candidate` or `supporting`). A cluster may
  hold several merge sets, and `merged` may be empty for an independent
  canonical.
- `unclustered_records` have no role and no reference.

The job may split a group into several clusters or leave members unclustered,
but it can never cluster, merge with, or name an item outside its job.

One pure decoder (`accounting_wire.py`, decoder 1) expands this into the
internal normal form v2. From containment alone it derives each record's cluster
index, the canonical role of a record placed as a canonical, the merged status,
and the direct reference to the set's canonical. It keeps every occurrence in
order. It never repairs a status, drops a record, or chooses a representative,
and being referred to promotes nothing: a record is canonical only if the model
placed it as one. References between records, chains, and references across
clusters cannot be written at all. Whether a placement is semantically right is
still a judgment no check can settle; for example, the wire cannot tell when
evidence that is really counterevidence has been placed as a canonical.

`raw-result.json` and each result wrapper's `output` stay wire v3 and immutable.
The normal form is Derived: it is rebuilt when needed and never written back.

## Exact closure

The output is checked in two layers. **Shape** covers only the wire's syntax:
types, exact fields, enums, string bounds, and fixed array limits. A shape
failure is `schema_invalid`, a transport failure that does not consume the
semantic cap. The schema cannot express how many records all placements add up
to, so it holds no per-job counts and no ID enums. Everything else belongs to
**closure**, applied to the decoded normal form: a missing, duplicate, or
foreign ID, an empty cluster, a route and status conflict, or an inconclusive
record without a falsifier is a semantic red attempt that counts toward the cap
of two.

For each job and again for the assembled whole, closure requires:

- packet observation IDs equal reducer record IDs, with multiplicity one;
- a record owns membership once through its derived `cluster_index` and `role`;
  every index is in range and every cluster has at least one unique member;
- source-verification cluster members are only `verify` or `merged`; every
  `verify` record has a source-verification cluster whose cluster-owned depth
  and requested source relationships are nonempty;
- every summary-only cluster has null depth and no source relationships, never
  contains `verify`, and may contain non-verify terminal/canonical records plus
  `merged` records;
- non-verify records may be unclustered or belong to summary-only, but never to
  source-verification except for `merged`, which may belong to either route;
- unresolved IDs and shard receipt IDs match the packet exactly;
- shard output contains only receipt ID, semantic disposition, and reason; the
  validator restores authoritative status and `true` / `false` / `unknown`
  overflow state from the full packet before publication;
- each merged record reaches a non-merged `canonical` record in its own cluster;
  in a source-verification cluster that canonical is `verify`, and in a
  summary-only cluster it is non-verify; counterevidence and minority records
  are not merged away;
- attempt, usage, policy, source snapshot, and all artifact hashes are current.

Every accounting job uses the same wire schema. It limits identifiers to 512
characters and every array to 50,000 items, source relationships to the seven
contract values, record, unresolved, and receipt reasons to 192 characters,
cluster reasons to 512, and falsifiers to 256.

The validator then re-derives the partition, checks that every job input is the
exact projection of its items, decodes each job with the same decoder, offsets
its cluster indexes, assembles one normal form for the complete packet, and
applies the same closure to the full packet.
There is no partial success: if any label job, any accounting job, or the
assembled closure is not green, nothing is published.

The runner and validator first check where the plan is, separately from whether
its evidence is current. A valid location is a `plan.json` in a `reduction` or
`reduction-<name>` directory directly inside the original scan directory,
outside the read-only target, reached without symlinks or aliases on the plan,
its directory, or any ancestor, with the packet and scout validation bound from
that same scan directory and the original scan plan readable. If the location is
not valid, the script stops with an error and writes nothing. If the location is
valid but the evidence has gone stale (for example, a changed or missing source
snapshot, catalog, or packet, a changed scan plan, or any other bound hash), the
validator still takes the lock, removes `accounting-ledger.json`,
`verification-queue.json`, and `summary.json`, writes a red `validation.json`
and `inspection.md`, and exits nonzero. It leaves attempts, results, job inputs,
and the partition unchanged. Only the validator revokes a published result: the
runner stops as `failed` on stale evidence without calling a model, so run the
validator afterwards to withdraw an earlier green.

Only a green validator publishes:

- `accounting-ledger.json` — every observation, unresolved item, and receipt,
  each with its reducer disposition and reason where applicable, plus
  validator-derived receipt facts and public cluster records. Record membership
  is published with the derived `cluster_id`; the internal positional
  `cluster_index` is never published;
- `verification-queue.json` — only closed clusters whose source verification can
  change the Question-level answer;
- `summary.json` — counts (including unresolved questions), status,
  model-reporting state, usage, and `accounting_structure` (canonical, merged,
  counterevidence, and minority records, merge sets, and empty merge sets).
  These structure counts are diagnostics, not quotas or pass conditions.

Public cluster IDs are safe deterministic digests of the sorted derived
`(observation_id, role)` member list. The model never names public clusters.
Unresolved items are ledger records, not automatic
source-verification clusters.

## Immutable attempts and the semantic cap

Label jobs are named `label-001`, `label-002`, and so on, and accounting jobs
`batch-001`, `batch-002`, and so on. Each label job's `input.json` and
`manifest.json` are published once, by staging and renaming the whole
directory, under `jobs/label-NNN/` when its earlier labels are known. The
manifest binds the hash chain of every earlier label result
(`parent_result_chain_sha256`) and the adopted vocabulary, so changing any
earlier result makes every later input stale. Every job
attempt owns a new exclusive directory under the reduction directory's
`attempts/<job>/`, and a job result is stored as `results/<job>.json`:

```text
attempt-0001/
  request.json
  prompt.md
  input-manifest.json
  schema.json
  stdout.jsonl
  stderr.log
  raw-result.json
  receipt.json
```

A retry creates the next attempt number; no prior attempt byte is overwritten.
Every terminal attempt retains `raw-result.json`. Its receipt distinguishes
`missing`, `empty`, `invalid_json`, `schema_invalid`, and `valid`, so a missing
CLI output cannot masquerade as an empty valid result. A job result is written
only after its closure is green and must equal its immutable raw result exactly.

Exit zero is insufficient. Success also requires valid structured output, no
tool use, a complete terminal usage event, matching policy and input hashes,
and exact equality between the immutable raw result and its reusable wrapper.
Requested and resolved model/effort are separate facts. If the CLI does not
report a resolved value, store null with `not_reported_by_cli`; never infer it.

A shape-valid attempt whose closure is red gets an immutable attempt-local
closure artifact. **The whole reduction experiment allows two such semantic-red
attempts in total**, summed across every label and accounting job. The
second one atomically creates the reduction directory's `blocked.json`; that
terminal state, or two validated red closure artifacts left by an interrupted
blocked-state write, forbids every further reducer subprocess, including jobs
that never started. Invalid or tampered terminal semantic state fails closed.

A red job is retried only with its own unchanged input. Green label results and
the frozen partition are never re-run or replaced, and an accounting red never
restarts labeling. Transport and shape failures do not consume the semantic
cap; they stay resumable under the invocation-local `--retries` budget.

An interruption can leave an attempt directory unfinished. Its contents
decide what happens next:

- **No `receipt.json` and no `raw-result.json`.** The model call never
  produced a result. The directory is kept unchanged as an interrupted
  transport attempt and does not consume the semantic cap. Running the same
  command again starts that job's next attempt number. Any request, prompt,
  manifest, or schema already written in the directory must still match the
  bound job; otherwise the run stops.
- **`raw-result.json` but no `receipt.json`.** The semantic outcome is
  unknown, so the runner and validator stop closed. Leave every artifact in
  place, read the failure reason, find that attempt among the existing
  `attempts/<job>/` directories, and report its path to whoever owns the
  reduction; the scripts do not necessarily print the path. Do not
  delete, move, or edit attempt files, closure artifacts, or `blocked.json`,
  and do not re-plan, to get past it.

Only one runner or validator may work on a reduction directory at a time
(through the directory's `runner.lock`); a second concurrent process is
refused.

## Run, resume, and inspect

```bash
python3 "<skill-dir>/scripts/run_reducers.py" \
  "<reduction-dir>/plan.json" [--retries 1]
python3 "<skill-dir>/scripts/validate_reduction.py" \
  "<reduction-dir>/plan.json"
```

Jobs run one at a time. While it works the runner's status in `inspection.md` is
`running`. A finished runner reports `awaiting_full_validation`; a stopped
runner reports `failed`, or `semantic_blocked` once the cap is spent, and writes
the reason to `inspection.md`. The validator's status is `green` or `red`.
`awaiting_full_validation` is not success until `validate_reduction.py` closes
the whole reduction green. After an interruption or a transport failure, run the
same `run_reducers.py` command against the same plan. It re-checks every
binding, authenticates every existing label result from the first block on,
counts semantic-red attempts across all known jobs, and only then starts a
subprocess: it reuses each green job without starting one and continues with the
first unfinished job from its next attempt number. Any attempt, result, or
partition beyond the first unfinished label that the earlier results cannot
explain stops the run closed. An attempt left with a raw result but no receipt
stops the run instead, as described above. Never delete or re-plan a reduction
directory to restart it.

The planner, runner, and validator each rewrite `inspection.md` inside the
reduction directory. It is the inspection entry point for the reduction stage:
it shows the reduction status and, on failure, the reason; the progress known so
far (finished label jobs out of all blocks, the current job, whether the
partition is published, and finished accounting jobs); the experiment's
semantic-red count; the group cap; and the output budget. For each label block
it lists how many adopted new keys and how many reused earlier keys it produced,
and it flags any block after the first that reused none. These counts are
diagnostics, not quotas or pass conditions. It restates what labeling cannot do
(see above). The document never shows keys, definitions, packet text, or quotes.
It links to the scan's `scan.md`, the plan, the partition manifest, and, once
they exist, `blocked.json`, `validation.json`, `summary.json`, and
`verification-queue.json`. Reduction scripts never rewrite `scan.md`, which
remains the record of the scan stage.

Usage in `summary.json` sums the adopted attempt of every job once. A resolved
model or effort counts as reported only when every job's CLI reported it:

```text
total_tokens = input_tokens + output_tokens
```

`cached_input_tokens` is part of input and `reasoning_output_tokens` is part of
output. Do not add either again.

## New experiments and sibling directories

Planning another sibling reduction directory does not reset the cap.
`plan_reduction.py` rejects a sibling plan of this contract with the same bound
packet, projections, rendered prompts and templates, schemas, and policy, and it
rejects a malformed sibling plan of this contract. Directories of older
contracts (an unlaunched `full_input_overflow` plan, earlier grouped or labeled
experiments, whether blocked or not) may stay beside the new one; nothing in
them is read as a result or reused. A deliberate change to one of those semantic
inputs is a new experiment and may use a `reduction-<name>` directory directly
inside the same scan directory. Reduction output anywhere else is rejected;
existing scan inputs remain readable from their original locations.

## Continue a scan from an older reduction contract

A scan planned by an earlier version may hold a reduction plan that stopped at
`full_input_overflow` before any reducer started, or reduction directories from
earlier grouped or labeled experiments, including blocked ones. This version
does not run, convert, inherit, resume, or reuse any of them, and it never falls
back to them. A blocked experiment stays blocked. A new experiment reuses the
green scouts and the packet, and runs every label and accounting job again. To
continue from the same green scouts:

1. Confirm that a `full_input_overflow` directory has no `attempts/`,
   `results/`, or `blocked.json`. If reducer attempts exist there, they belong
   to that experiment; report them instead of continuing silently. Earlier
   grouped or labeled experiment directories may hold attempts and results; they
   stay as they are and nothing in them is reused.
2. Leave every earlier reduction directory, the source snapshot, the scan
   plan, scout results, validation, review packet, and `scan.md` unchanged. Do
   not rescan or rebuild the packet.
3. Dump the current runtime catalog to a temporary file outside the scan
   directory and compare its SHA-256 with the catalog already in the scan
   directory. If they are equal, bind the existing file. If they differ, write
   the new dump to a new name directly inside the scan directory and bind that;
   never overwrite the old catalog.
4. Plan into a new `reduction-<name>` directory, then run and validate it as
   above.

## Narrow source verification

Project one queued cluster to its deterministic path:

```bash
python3 "<skill-dir>/scripts/project_verification_input.py" \
  "<reduction-dir>/plan.json" "<cluster-id>" \
  --output "<reduction-dir>/verification-inputs/<cluster-id>.json"
```

The projector requires a current green reduction validation. It rechecks the
source snapshot and exact-covers only that cluster's members, records, and
evidence locators. It does not include the full packet, write inside the
read-only target, inspect source, decide authority, or merge semantic results.

The main agent reads the named source context required by the bundle and
follows [Synthesize and Verify](protocol.md). Produce one verification record
for every queued cluster, including rejected and inconclusive outcomes. Each
record separates disposition, required and actual depth, checked dimensions,
verified claim, authority, consequence, current evidence, falsifier, and
proposed action.

## Validate proposed actions

Validate all queue dispositions together:

```bash
python3 "<skill-dir>/scripts/validate_verification_actions.py" \
  "<reduction-dir>/plan.json" "<verification-records.json>" \
  [--output "<reduction-dir>/verification-results/action-validation.json"]
```

Records must cover every green queued cluster exactly once and bind the current
queue hash, narrow bundle hash, and source snapshot. Required depth and source
relationships come from the trusted queue, not from the proposed record.
`source_verified: true` is not evidence.

`action=delete` additionally requires:

- `disposition=confirmed`;
- a concrete lost scenario;
- an equal-or-stronger replacement owner;
- a negative case;
- checked `replacement_owner`, `negative_case`, `production_source`, `caller`,
  `gate`, and `history` dimensions.

Proposal-supplied boundary flags cannot waive those checks. The validator
reports whether the proposal is safe to act on; it does not launch a verifier,
edit source, or perform the action.
