# Symphony + Linear Starter

![Symphony + Linear Orchestration Starter](assets/github/social-preview.png)

A skill and toolkit for running Codex or Claude Code as the orchestrator over Symphony workers with Linear-managed execution. Inspect a repo, plan bounded issues in Linear, dispatch parallel workers through Symphony, and review the output through an explicit operator gate.

## How it works

1. The orchestrator inspects a repository, updates guidance, and plans issue work in Linear.
2. Symphony dispatches workers from active Linear states.
3. Workers complete bounded changes, validate them, and move issues to `In Review`.
4. The orchestrator reviews worker output, integrates it, and moves issues to `Done`.

Default concurrency is three workers. The review gate is `In Review`. No auto-merge.

## Install

### Codex

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/symphony-linear-orchestrator "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Restart Codex after installing so the skill is discoverable.

### Claude Code

Point Claude Code at the skill folder as shared instructions, or copy the skill into your project:

```text
skills/symphony-linear-orchestrator/SKILL.md
```

## Getting started

The skill includes three scripts to get a repo ready for Symphony:

1. **`doctor.py`** checks that your local toolchain (`git`, `gh`, `bash`, `python3`, Symphony, `LINEAR_API_KEY`) is ready.
2. **`bootstrap.py`** renders a workflow, issue template, and guidance additions into the target repo.
3. **`preflight.py`** validates the rendered workflow and repo state before you start a run.

Run all three from `skills/symphony-linear-orchestrator/scripts/`. The skill's [SKILL.md](skills/symphony-linear-orchestrator/SKILL.md) and [reference docs](skills/symphony-linear-orchestrator/references/) walk through the full workflow.

## Example prompts

- `Use $symphony-linear-orchestrator to onboard this repo for Symphony + Linear execution.`
- `Use $symphony-linear-orchestrator to turn this feature request into a first execution wave with bounded Linear tickets.`
- `Use $symphony-linear-orchestrator to generate a three-worker Symphony workflow with an In Review gate.`
- `Use $symphony-linear-orchestrator to run preflight checks and explain any blockers.`
- `Use $symphony-linear-orchestrator to recover a stalled Symphony run and recommend the next operator action.`

## What's inside

| Directory | Contents |
|---|---|
| `skills/symphony-linear-orchestrator/` | Installable skill, reference docs, scripts, templates, agent config |
| `skills/.../references/` | Operating model, Linear issue contract, workflow spec, onboarding guide, recovery playbook, example prompts |
| `skills/.../scripts/` | `doctor.py`, `bootstrap.py`, `preflight.py` |
| `skills/.../assets/templates/` | Workflow, issue, guidance, and brief templates |

The defaults are a starting point. Adjust `max_concurrent_agents`, extend the issue contract, add recovery steps, or swap worker models to fit your team and repos. Contributions and feedback are welcome via [GitHub issues](https://github.com/jvogan/symphony-linear-starter/issues).

## License

[MIT](LICENSE)
