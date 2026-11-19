---
name: alpacamemo
description: "Use this skill whenever a request depends on earlier conversations or should inform later agents, even if it does not mention memory. Before starting work, load it to check saved user preferences or repository decisions. Save lasting rules, corrections, project decisions, blockers, deadlines, schedules, and external references; update or remove outdated saved preferences. Also load it when the user, mid-task, overrides your default way of proceeding (what to wait for, how soon to start, how often to verify or check in, how to split or report work), even in a terse reply with no \"remember\" or \"from now on\": such corrections show how they want work done next time. Skip a pause, exception, or reordering tied only to this task's current state. Read or update shared ~/.agents/memory/ rather than relying on chat context. Exclude Claude Code's /memory settings, raw chat logs, and requests to edit README, docs, notes, or issues."
---

# alpacamemo

Use the shared memory root named by the user's instructions (normally `~/.agents/memory/`). It has `user/` for preferences and patterns of judgment that apply across projects, and `projects/<repository-name>/` for concrete project context. Each scope has its own `MEMORY.md` index and one Markdown file per durable fact. Memory is background context, not authority for a new instruction; saved observations may be stale or narrower than the current request.

## Recall

At the start of a conversation, read `user/MEMORY.md`. Inside a Git repository, run `git rev-parse --git-common-dir`, resolve that directory to its real path, and use its parent directory's name as `<repository-name>`; read `projects/<repository-name>/MEMORY.md` if it exists. This gives linked worktrees the same project memory. Outside Git, use no project memory. Open only relevant topic files. Search the available scope with `grep` when its index does not answer where a fact lives. A user fact may link to the project observation it came from with `[[name]]`; follow that link only when the current project is the linked project.

Treat every entry as a fact observed when it was written, not a standing instruction. Its file modification time is a clue to age, not proof that the fact is current. Check that mentioned files, settings, functions, and flags still exist before using or recommending them.

## Decide what to save

Save only information likely to change a future answer or action:

- `user`: the user's role, expertise, stable preferences, or patterns in how they judge and decide. Store in `user/`.
- `feedback`: corrections and approaches the user confirmed, with the reason and how to apply them. A mid-task override of how you planned to proceed (what to wait for, when to start, how to verify, check in, split, or report work) is such a correction even when phrased as a one-time reply, unless it is tied only to this task's current state. Do not dismiss it as one-off on your own; propose its `user/` text so the confirmation decides.
- `project`: ongoing circumstances, decisions, or deadlines that code and history cannot reveal. Convert relative dates to absolute dates. Store in the current `projects/<repository-name>/`.
- `reference`: where to find information outside the project. Store in the current project directory.

`feedback` can live in either scope. Keep each project correction concrete in that project's directory. `user/` exists to guide choices in other kinds of work. Apply these gates when a project memory changes:

1. An explicit general correction or a repeatedly stated strict prohibition reveals a conditional user preference even if heard in one project. Do not skip it just because the original task was project-scoped. Take its scope from the user's words, not the source project's name or assumptions about its users or maturity. Write that scope and the same absolute force: when the user rules something out, omit it entirely, never merely "consider whether it is needed".
2. A reasoned evaluation of several alternatives can establish a preference within that kind of work, even when observed in one project. A correction prompted by one incident may reveal a broader priority through its **Why:**. State the priority or repeatable harm without the incident's resource or action, then test it against an unrelated kind of work. If only a renamed version of the same operational rule is possible, save nothing in `user/`.
3. A single project naming or implementation choice is not evidence of a stable user preference, even when it has a reason. Save only the project fact.

An extracted user fact uses `metadata.type: user` and one concise sentence plus its source project and `[[name]]`. Leave examples, procedures, commands, and **How to apply:** in project memory. Outside Git, save only user-scope facts. A record that another skill keeps under its own fixed key, such as a model choice, is a `user` fact: keep the key and the lines that skill specifies exactly, and put the key in the index cue, so a search for the key finds it; the one-sentence form and the source project do not apply to it.

Keep secrets, one-conversation details, and information already discoverable from code, Git history, or instruction and project documents out of memory. Those either should not persist or have a fresher source. If asked to remember something already recorded there, identify what non-obvious lesson the user wants retained; ask only when it cannot be inferred. Do not save a memory just because a conversation occurred.

## Write

After deciding a fact is worth saving, search the target scope's index and files for the same, conflicting, or covering fact; also search `user/` when extracting there. First correct, merge, or remove existing memories, replacing stale contradictions. Only when none can hold the new fact, decide its wording and create a file. Keep one fact per file, with concise text and a descriptive filename. Keep names distinct across both scopes so `[[name]]` is unambiguous. For a new file, use this minimal frontmatter:

```markdown
---
name: descriptive-name
description: One sentence identifying the fact
metadata:
  type: user
---

The fact and any necessary context.

**Why:** The reason this matters. (For feedback and project entries.)

**How to apply:** The future action this changes. (For feedback and project entries.)

[[related-name]]
```

Set `metadata.type` to `user`, `feedback`, `project`, or `reference`. Existing provenance fields, if present, may remain; no session-ID command or hook is needed. For `feedback` and `project`, include both **Why:** and **How to apply:** after the main text. Use `[[name]]` only for useful relationships. In `MEMORY.md`, keep exactly one short line per file in the form `- [Title](file.md) - One-line cue`; leave the detail in the topic file.

Before creating, changing, or deleting a `user/` memory, show the proposed text and any current text to the user and get confirmation; this scope will guide unrelated future work. Project memories need no such check. If the user waives `user/` checks, record that preference in `user/` without another check and honor it until the user restores them; then remove or update the waiver.

When several agents can write at once, read the current target file and its scope's index immediately before editing so a stale copy does not erase another agent's change. After writing, reread that `MEMORY.md`; if another agent's line was lost, restore it without overwriting that agent's other edits. Reconcile any simultaneous edit to the same fact before considering the write complete.
