---
name: pickthemodel
description: Choose the CLI, model, and effort for a role when a skill or task launches another AI, from a choice saved in the user's memory or, when none is saved, from a small trial whose result is saved for next time. Use when a skill names a model-choice key such as `pocket-skills/<skill>/<role>`, or when you are about to pick a model for a delegated run and nothing names one. Skip it when the user or the project has already named the model and effort for this work.
---

# Pick the Model

A skill that launches another AI names a role and what that role needs, not a
model. This skill is the one rule that turns a role into a CLI, a model, and an
effort, so a new model release changes a saved choice and not the skills.

## The key

Each role has a fixed key, `pocket-skills/<skill>/<role>`, given by the calling
skill (for example `pocket-skills/askeveryfile/scout`). Search and save with the
key exactly as written.

## Choose

Take the first that applies:

1. **Named for this work.** The user, the request, or the project's
   instructions name the CLI, model, or effort: use that, and settle any part
   they leave unnamed by the next steps. If what they named cannot run, stop
   and tell whoever named it. Do not substitute another.
2. **Saved.** Search the user's memory kept in this environment for the key;
   if it is not there, or there is no memory, read
   `~/.config/pocket-skills/model-choices.md`. When an entry exists and agrees
   with whatever was named in step 1, check that the CLI still offers its
   model, then use it.
3. **Trial.** No usable entry: run the trial below within whatever was named,
   use its result, and save it.

Say in one line which model and effort you used and where the choice came from
(named, saved, or trial).

## Trial

The calling skill states what the role needs, the small task that shows it, and
whether it wants the cheapest candidate that passes or the strongest that runs.

1. Take at most three candidates that could plausibly do the role, from the
   current generation the CLI offers now: its model list, its help, or the
   provider's current documentation, not a model name you remember. Order them
   by cost from the provider's published pricing. If you cannot tell the order,
   ask the user.
2. Cheapest wanted: run the small task with the cheapest candidate at its
   lowest sensible effort, and move up only while the result misses what the
   role needs. Keep the first that passes.
3. Strongest wanted: keep the strongest candidate that starts and completes the
   task.

A trial is small on purpose: one sample, a few candidates. It is not a
benchmark.

## Save

One entry per key:

```text
pocket-skills/askeveryfile/scout
choice: <cli> <model>, effort <effort>
source: trial YYYY-MM-DD
trial: <candidates tried, the sample, what passing meant, in one line>
```

- The choice applies across projects. Save it where the memory keeps
  user-level facts, under that memory's own rules, including any confirmation
  it requires before writing. When more than one memory is available, save
  where every CLI and project can read it. When there is no memory, or it has
  no user-level place, write `~/.config/pocket-skills/model-choices.md`
  instead.
- If saving is refused, needs an approval you do not get, or fails, use the
  result for this conversation and continue. Never work around an approval to
  save.
- When the user tells you which model or effort to use for a role from now on,
  save it with `source: user YYYY-MM-DD` and no `trial` line. Only the user
  changes a `user` entry; never replace it with a trial result. If it cannot
  run, tell the user, run a trial for this run only, and leave the entry as it
  is.

## Run a trial again only when

- the saved model is no longer offered, or
- results with the saved choice stop meeting what the role needs, as the
  calling skill defines it.

A model that is still offered but fails for now (a rate limit, an overload) is
not a reason: leave the entry, pick another candidate as in step 1 of the
trial, and use it for this run only without saving it.
Not on every use, not on a schedule, and not because a newer model exists; the
user can ask for a new trial at any time. A new trial replaces a `trial` entry
in the place where that entry is saved, under the same save rules.

## What a saved choice is not

A saved choice only selects a model. It grants no permission: what may be
launched, spending limits, approvals, and sandboxing still come from the user,
the project, and the calling skill.
