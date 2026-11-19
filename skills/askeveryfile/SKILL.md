---
name: askeveryfile
description: Run one bounded, read-only semantic audit across every eligible text file in a directory to answer a corpus-wide Question with cited, validated evidence. Use when exact search or deterministic parsing cannot settle a semantic inventory, prior-art or duplicate search, rule or contradiction audit, or claim review. Skip small or exact queries, direct editing, and repeat scans for the same unfinished Question.
---

# Ask Every File

Run one bounded, read-only evidence scan. Scripts own inventory, snapshots,
delivery, and deterministic validation; scouts report local cited observations;
isolated reducers give every result a subject key block by block, against the
keys earlier blocks adopted, and account for every item from its cited
evidence; the validator closes the complete set;
the main thread verifies decision-driving source evidence and owns the
conclusion. It never ingests the review packet or any reducer input.

```text
Question → plan + snapshot → scouts → validation → review packet
         → subject labeling → grouped accounting → exact full closure
         → closed verification queue → narrow source verification
         → conclusion
```

Use `shard`, `scout`, `Question`, `observation`, `validation`, and
`review packet` verbatim in every language. Write the Question, progress
updates, and final answer in the user's language; preserve source text, code,
identifiers, paths, artifact names, and schema keys exactly.

## When to use it

Use this skill when exact search or deterministic parsing cannot answer a
semantic Question across a directory: inventories, prior art or duplication,
rules and contradictions, obsolete-work claims, or claim audits.

Skip it for exact lookups, structural queries, small corpora, or Questions that
need one whole-corpus reading pass. In a mixed change request, use this skill
only to gather evidence; implementation is a separate step. For the same
unfinished Question and unchanged snapshot, reuse green artifacts rather than
scanning again.

## Reference ownership

- [Write the Question](references/question.md) owns Question construction and
  the shardable search model.
- [Reduction](references/reduction.md) owns reducer invocation, artifacts,
  versions, closure, budgeting, narrow bundles, and action validation.
- [Synthesize and Verify](references/protocol.md) owns post-reduction source
  verification and final reporting.

## 1. Define the Question and scope

Follow [Write the Question](references/question.md). Keep an explicitly
supplied target. Otherwise choose the narrowest directory containing the
intended text population. Do not silently widen to a filesystem root, home
directory, or workspace parent. Scan unrelated roots separately.

Prepare one private OS-temporary workspace before writing scan artifacts:

```bash
python3 "<skill-dir>/scripts/prepare_workspace.py" "<target>" "<topic>"
```

Use the returned absolute `question_file` and `scan_parent` paths. Write the
Question to `question_file`. Never substitute a project-local `.askeveryfile`,
`.tmp`, or other directory; the helper also keeps output outside the containing
Git repository. Reuse the same workspace only for the same target and topic.

## 2. Choose the scout and reducer

Scouts require the agent CLI, model, and effort; reduction planning requires
the model and effort for its Codex CLI jobs. Choose them with the `pickthemodel`
skill. Without that skill, use what the user names, and ask if nothing is named.

| Role key | What the role needs | Trial |
|---|---|---|
| `pocket-skills/askeveryfile/scout` | Reads one shard with no tools, on the `codex` or `claude` CLI, and reports cited local observations as structured output. It runs once per shard, so it wants the cheapest model and effort that still reads accurately. | With the real Question, plan one separate scan per candidate over the same three to five representative files (`--only`), each in its own workspace, run it with that candidate, and read the same files yourself. The candidate passes when validation is green and its observations miss nothing decisive that you found and cite nothing that is not there. |
| `pocket-skills/askeveryfile/reducer` | Labels observations in bounded blocks, then accounts for complete subject groups on the `codex` CLI with no tools. Its context window must fit each job and the worst-case label vocabulary; it wants the cheapest model and effort that reaches green full closure. | The real reduction is the trial; follow [Reduction](references/reduction.md). |

A saved scout choice stops meeting its role when shards stay invalid after
their bounded reruns or source verification keeps contradicting its
observations; a saved reducer choice, when it ends closure-red.

## 3. Plan, run, and validate scouts

```bash
python3 "<skill-dir>/scripts/plan_scan.py" "<target>" \
  --output-dir "<scan_parent>" \
  --question-file "<question_file>"
python3 "<skill-dir>/scripts/run_scouts.py" "<plan.json>" \
  --agent <agent> --model <model> --effort <effort> [--keep-prompts]
python3 "<skill-dir>/scripts/validate_results.py" "<plan.json>"
```

Use `--question` only for a short inline Question. Before launch, check the
reported root, selected population, exclusions, split files, and shard count.
Shape the population with `--only`, `--exclude`, `--exclude-glob`, and
`--force-include`; force inclusion overrides exclusions but does not restrict
or widen `--only`. Narrow an unexpectedly large plan before launch. Reduction
capacity is fixed, what it must hold grows with the number of observations and
shards, and scouting cannot be reused across a split. So when the Question will
match widely across a large population, split it into separate scans by subtree
before launch rather than after a capacity failure. A scan is bound to the
agent, model, and effort its scouts first ran with; a different choice needs a
new scan. Retain successful prompts with `--keep-prompts` only
when needed for inspection.
Do not synthesize while validation is red. Rerun only missing or invalid shards
with `--only-shard` and a bounded attempt count; replan if source files change.
Never edit results to force green.

## 4. Build, reduce, verify, and report

After green scout validation, build the packet:

```bash
python3 "<skill-dir>/scripts/build_review_packet.py" "<plan.json>"
```

Then follow [Reduction](references/reduction.md): plan, run the label and
accounting jobs, and validate the complete reduction. A plan that is not `ready`
(`label_input_overflow` or `atomic_unit_overflow`) is a closed capacity failure:
do not read a partial packet, split a group, lower a reserve, or invent a
fallback. When no reducer candidate fits, the way forward is new scans over
smaller populations; tell the user that scouting is repeated. Follow
[Synthesize and Verify](references/protocol.md) only after the
whole reduction closes green; green label or accounting jobs alone are not
success. A green validator result is not semantic truth, and no mutation is
allowed before current-source verification and the action validator. A
shape-valid closure-red attempt is not success: preserve its semantic terminal
artifact. The whole reduction experiment stops permanently after two such
attempts in total across all of its label and accounting jobs. If a run is
interrupted, rerun the same command on the same plan; it re-checks every earlier
label result before starting anything, never re-runs a green job, and an
interrupted model call resumes with the next attempt. An attempt left with a raw
result but no receipt stops the reduction closed: keep every artifact, find that
attempt under `attempts/<job>/`, and report its path to the reduction's owner.
Never delete, edit, or replan reduction artifacts to get past a stop, and do not
replan the same semantic inputs in a sibling directory. Change one of those
semantic inputs deliberately before starting a new experiment. To continue a
scan from an older reduction contract, reuse its green scouts and packet as
described in
[Reduction](references/reduction.md#continue-a-scan-from-an-older-reduction-contract).
Keep every generated packet and reduction artifact inside its scan directory.
Export a completed result elsewhere only when the user asks for that
destination, or to keep the evidence described below.

## Keep the task readable and inspectable

`<scan-dir>/scan.md` is the inspection entry point for the scan stage. It
carries the Question, scope, exclusions, shard state, diagnostics, validation,
and artifact links. Reduction scripts never rewrite it. The reduction's
`inspection.md` inside the reduction directory is the entry point for the
reduction stage; it links back to `scan.md` and shows the reduction status,
known progress, and any failure reason.

Show one short sentence in chat after planning, occasionally during a healthy
long run, on failure, after packet build, and after reduction closure. Include
the absolute `scan.md` link through packet build and the absolute reduction
`inspection.md` link once reduction has started; report only user-relevant
counts and state, never packet, label-input, or job-input contents. Put the
reduction `inspection.md` link beside the Question-level conclusion in the final
answer.

The workspace is OS-temporary and may not survive a reboot. When the conclusion
is recorded where it will be read later, such as a task record or a report
file, keep a copy of the scan directory beside it, or at least `scan.md`,
`validation.json`, and whichever reduction and verification files exist, and
cite the copy instead of the temporary path. Links inside `scan.md` are
absolute and keep pointing at the temporary workspace.
