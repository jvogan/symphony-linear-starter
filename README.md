# Symphony + Linear Starter

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent_Skill-v1.0.0-8A2BE2.svg)](#install)

![Symphony + Linear Orchestration Starter](assets/github/social-preview.png)

**Give your AI coding agent the ability to orchestrate a team of autonomous workers.**

This is an installable [agent skill](https://agentskills.io/specification) for [Codex](https://openai.com/index/codex/) and [Claude Code](https://docs.anthropic.com/en/docs/claude-code). It teaches an orchestrator agent how to plan work in [Linear](https://linear.app), dispatch parallel workers through [OpenAI Symphony](https://github.com/openai/symphony), review their output, and feed learnings back into the next wave.

You install the skill, point it at a repo, and your agent gains a complete multi-agent workflow: issue planning, worker dispatch, validation gates, and a self-improving runbook that gets better with every run.

## What each piece does

| Layer | Role |
|---|---|
| **Your agent** (Codex or Claude Code) | The orchestrator. Plans issues, reviews output, promotes learnings. |
| **Linear** | The mission board. Issues, state transitions, dependencies, and acceptance criteria live here. |
| **Symphony** | The dispatch engine. Schedules workers, manages isolation via git worktrees, enforces concurrency. |
| **Workers** (1-3 per wave) | Autonomous agents that execute one bounded issue each, validate their work, and move to In Review. |
| **Runbook + Learnings** | The self-improving loop. Each wave's lessons get promoted into durable guidance for the next. |

```
               ┌─────────────┐
               │ Orchestrator │  (Codex or Claude Code)
               └──────┬──────┘
                      │ plans issues, reviews output, promotes learnings
                      v
               ┌─────────────┐
               │   Linear    │  issues, state, dependencies
               └──────┬──────┘
                      │ active states feed the queue
                      v
               ┌─────────────┐
               │  Symphony   │  dispatch + isolation
               └──┬───┬───┬──┘
                  │   │   │
                  v   v   v
                 W1  W2  W3    (scale out after the first safe run)
                  │   │   │
                  v   v   v
               ┌─────────────┐
               │  In Review  │  operator gate
               └──────┬──────┘
                      │ orchestrator integrates, then Done
                      v
               ┌─────────────┐
               │  Learnings  │  runbook + AGENTS.md get better
               └─────────────┘
```

## Install

### Skills CLI (recommended)

```bash
npx skills add jvogan/symphony-linear-starter
```

### Codex (manual)

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/symphony-linear-orchestrator "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Restart Codex after installing so the skill is discoverable.

### Claude Code (manual)

Point Claude Code at the skill folder as shared instructions, or copy the skill into your project:

```text
skills/symphony-linear-orchestrator/SKILL.md
```

## Getting started

The skill includes four scripts to get a repo ready for Symphony:

1. **`doctor.py`** checks that your local toolchain (`git`, `gh`, `bash`, `python3`, Symphony, `LINEAR_API_KEY`) is ready.
2. **`bootstrap.py`** renders a lane-aware workflow, runbook, learnings log, issue template, and guidance additions into the target repo.
3. **`issue_schema.py`** renders or normalizes canonical Linear issue bodies so the human markdown and `<!-- symphony:schema -->` block stay aligned.
4. **`preflight.py`** validates the rendered workflow, guardrails, runbook, learnings scaffold, and repo state before you start a run.

```bash
# 1. Check toolchain
python3 skills/symphony-linear-orchestrator/scripts/doctor.py --json

# 2. Bootstrap a target repo
python3 skills/symphony-linear-orchestrator/scripts/bootstrap.py \
  --target-repo /path/to/repo \
  --workflow-name wave1 \
  --clone-url git@github.com:owner/repo.git \
  --linear-project-slug proj \
  --lane medium \
  --required-path README.md \
  --required-path package.json \
  --write

# 3. Validate before starting
python3 skills/symphony-linear-orchestrator/scripts/preflight.py \
  --target-repo /path/to/repo \
  --workflow /path/to/repo/.orchestration/wave1.WORKFLOW.md \
  --json
```

The skill's [SKILL.md](skills/symphony-linear-orchestrator/SKILL.md) and [reference docs](skills/symphony-linear-orchestrator/references/) walk through the full workflow.

## How the workflow runs

1. The orchestrator inspects a repository, updates guidance, and plans issue work in Linear.
2. Symphony dispatches workers from active Linear states.
3. Workers complete bounded changes, validate them, and move issues to `In Review`.
4. The orchestrator reviews worker output, integrates it, and moves issues to `Done`.
5. The orchestrator updates the repo runbook and learnings log, then promotes stable lessons into durable guidance.

Default first-run concurrency is one worker. Scale out only after preflight, issue shaping, and review loops are working cleanly.

## Example prompts

```
Use $symphony-linear-orchestrator to onboard this repo for Symphony + Linear execution.
```
```
Use $symphony-linear-orchestrator to turn this feature request into a first execution wave
with bounded Linear tickets and conservative first-run guardrails.
```
```
Use $symphony-linear-orchestrator to run preflight checks and explain any blockers.
```
```
Use $symphony-linear-orchestrator to recover a stalled Symphony run and recommend
the next operator action.
```
```
Use $symphony-linear-orchestrator to turn the last run into updated runbook steps
and durable learnings for the next wave.
```

## What's inside

| Path | Contents |
|---|---|
| `skills/symphony-linear-orchestrator/SKILL.md` | Main skill definition |
| `skills/.../references/` | Operating model, Linear issue contract, workflow spec, onboarding guide, recovery playbook, self-improvement loop, example prompts |
| `skills/.../scripts/` | `doctor.py`, `bootstrap.py`, `issue_schema.py`, `preflight.py` |
| `skills/.../assets/templates/` | Workflow, runbook, learnings, issue, guidance, and brief templates |
| `skills/.../agents/openai.yaml` | Codex agent configuration |

## Design defaults

- **One worker for the first run**, then scale out when the repo and issue graph are proven
- **Explicit routing lanes** via `sym:small`, `sym:medium`, `sym:large`, and `sym:content`
- **`In Review` as the operator gate** — no auto-merge, no auto-Done
- **Workspace bootstrap assertions** for branch and repo-anchor paths
- **No-progress guardrails** so stuck runs get requeued instead of burning tokens
- **Canonical issue rendering** so the human-readable body and machine-readable schema stay in sync
- **Self-improving loop** via RUNBOOK.md + LEARNINGS.md with promotion into durable guidance
- **Bounded issue contract** with acceptance criteria, validation commands, and touched areas
- **Security/privacy hygiene**: secrets, credentials, and personal data stay out of issue bodies and workflow files
- **No auto-merge, no snapshot promotion, no background services** in the default workflow

## Related

- **[symphony-claude-lane](https://github.com/jvogan/symphony-claude-lane)** — Add a specialized Claude Code lane for UI, design, browser-verified, and review work alongside the Codex lane

## Links

- [OpenAI Symphony](https://github.com/openai/symphony) — the dispatch and isolation runtime
- [Linear](https://linear.app) — issue tracker for planning and state
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — agent runtime (orchestrator or worker)
- [Codex](https://openai.com/index/codex/) — agent runtime (orchestrator or worker)
- [Agent Skills spec](https://agentskills.io/specification) — the open standard this skill follows

Contributions and feedback welcome via [GitHub issues](https://github.com/jvogan/symphony-linear-starter/issues).

## License

[MIT](LICENSE)
