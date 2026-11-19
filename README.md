# pocket-skills

Skills for AI coding agents such as Claude Code and Codex. Each skill is a folder with a `SKILL.md` file and works on its own. [alpacawork](https://github.com/alpacakit/alpacawork) offers askmeclearly and doyourjob during setup.

## Install

The skills install with the [skills CLI](https://github.com/vercel-labs/skills) (Node 22.20 or later).

```sh
npx skills add alpacakit/pocket-skills
```

To install one skill for your user and one agent:

```sh
npx skills add alpacakit/pocket-skills -s askmeclearly -g -a claude-code -y
```

Update installed skills with `npx skills update`.

## Skills

| Skill | What it does |
|:---|:---|
| [doyourjob](skills/doyourjob/SKILL.md) | The agent settles what evidence, convention or its own authority can settle, and brings you only the judgment that is yours, as one question with a recommendation. |
| [askmeclearly](skills/askmeclearly/SKILL.md) | Makes the agent ask you questions you can answer quickly, with choices, evidence and a recommendation, and start reports with one line and a picture of where to look. Marking screenshots needs Node 18.3 or later. |
| [dontspaghettimycode](skills/dontspaghettimycode/SKILL.md) | Gives each fact, rule and state change in your code one owner, and reshapes code so a new case fits instead of adding another special case. |
| [askeveryfile](skills/askeveryfile/SKILL.md) | A read-only audit that has a model read every text file in a directory and checks every citation. Needs Python 3.10 or later on Linux or macOS and the `codex` CLI (the `claude` CLI can do the reading step instead). It uses model requests in proportion to the size of the directory. |
| [pickthemodel](skills/pickthemodel/SKILL.md) | Chooses the model and effort for a helper AI from your saved choice, or from a small trial that is saved for next time. |
| [alpacamemo](skills/alpacamemo/SKILL.md) | Plain Markdown memory in `~/.agents/memory/`, shared by your agents across projects and worktrees. |

## License

[MIT License](LICENSE). askmeclearly bundles [@resvg/resvg-wasm](https://www.npmjs.com/package/@resvg/resvg-wasm) under the Mozilla Public License 2.0; its license is in [skills/askmeclearly/scripts/vendor/resvg-wasm/](skills/askmeclearly/scripts/vendor/resvg-wasm/LICENSE).
