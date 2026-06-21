# Symphony + Linear Starter

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent_Skill-v1.1.0-8A2BE2.svg)](#install)

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
               └──────┬──────┘
                      │
                      └─► next wave loops back to the Orchestrator
                          (operator-driven, or the optional goal loop)
```

Two optional lanes layer on top: a single-writer Release Manager that merges `release:ready` PRs, and an [autonomous goal loop](#autonomous-goal-loop-optional) (`goal_state.py`) that picks each next wave and stops on a budget cap or a stuck verdict.

## Prerequisites

Before installing, make sure you have:

- **[Codex](https://openai.com/index/codex/)** or **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** installed (your orchestrator agent)
- **[OpenAI Symphony](https://github.com/openai/symphony)** built locally (the dispatch runtime)
- **[Linear](https://linear.app)** account with an API key (`LINEAR_API_KEY` in your environment)
- **[GitHub CLI](https://cli.github.com/)** (`gh`) installed and authenticated (`gh auth login`)
- **Python 3.10+** and **git**
- A target git repo you want to automate

## Install

### Skills CLI (recommended)

```bash
npx skills add jvogan/symphony-linear-starter
```

This clones the skill into your local skills directory (e.g. `~/.codex/skills/`) so your agent can discover it.

### Codex (manual)

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/symphony-linear-orchestrator "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Restart Codex after installing so the skill is discoverable.

### Claude Code (manual)

Add the skill as a context reference in your project's `CLAUDE.md`:

```markdown
<!-- In your project's CLAUDE.md -->
See @skills/symphony-linear-orchestrator/SKILL.md for Symphony + Linear orchestration.
```

Or copy the skill folder into your project and reference `skills/symphony-linear-orchestrator/SKILL.md` directly from your agent instructions.

## Getting started

The skill includes six scripts to get a repo ready for Symphony:

1. **`doctor.py`** checks that your local toolchain is ready: `git`, `gh` (installed + authenticated), `bash`, `python3`, `codex`, Symphony, and `LINEAR_API_KEY`.
2. **`bootstrap.py`** renders a lane-aware workflow, runbook, learnings log, issue template, and guidance additions into the target repo.
3. **`issue_schema.py`** renders or normalizes canonical Linear issue bodies so the human markdown and `<!-- symphony:schema -->` block stay aligned.
4. **`release_manager.py`** runs the optional single-writer Release Manager lane that queues ready PRs through GitHub Merge Queue / `gh pr merge --auto`. It verifies the merge queue is enabled (`--check-merge-queue`), is safe to re-run (idempotent, finalizes merged issues), and never lets workers race to update `main`.
5. **`preflight.py`** validates the rendered workflow, routing labels, environment policy, closeout contract, snapshot-promotion safety, guardrails, runbook, learnings scaffold, and repo state before you start a run.
6. **`goal_state.py`** is the convergence + budget spine of the optional [autonomous goal loop](skills/symphony-linear-orchestrator/references/autonomous-goal-loop.md). It reads real Linear state plus a budget ledger and returns one verdict — `continue` (here's the next wave), `done`, or `stuck` (a budget cap hit or work stalled → stop and escalate) — so unattended, goal-directed running has a hard brake instead of a hope.

> Run these from a clone of this repo — they use repo-relative script paths:
> `git clone https://github.com/jvogan/symphony-linear-starter && cd symphony-linear-starter`
> (Installing the skill with `npx skills add` above is separate — that's so your agent can discover it.)

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
  --with-release-manager \
  --required-path README.md \
  --required-path package.json \
  --write

# After bootstrap: merge .orchestration/AGENTS_ADDITIONS.md into your target
# repo's AGENTS.md by hand (the script never edits AGENTS.md for you).

# 3. Render or normalize a Linear issue body
echo '{"summary":"Add login","acceptance_criteria":["tests pass"],"validation_commands":["npm test"],"touched_areas":["src/auth"],"complexity":"small"}' \
  | python3 skills/symphony-linear-orchestrator/scripts/issue_schema.py render

# 4. Validate before starting
python3 skills/symphony-linear-orchestrator/scripts/preflight.py \
  --target-repo /path/to/repo \
  --workflow /path/to/repo/.orchestration/wave1.WORKFLOW.md \
  --json

# 5. Dispatch the wave — run your built Symphony binary against the rendered
#    workflow (see https://github.com/openai/symphony for build + exact flags).
#    Symphony reads the workflow frontmatter and spawns one worker per active
#    Linear issue; workers open PRs and mark issues release:ready.
symphony /path/to/repo/.orchestration/wave1.WORKFLOW.md \
  --logs-root /path/to/repo/.orchestration/logs/wave1

# 6. Dry-run the Release Manager lane before enabling mutations
python3 skills/symphony-linear-orchestrator/scripts/release_manager.py \
  --workflow /path/to/repo/.orchestration/release-manager.WORKFLOW.md \
  --json
```

The skill's [SKILL.md](skills/symphony-linear-orchestrator/SKILL.md) and [reference docs](skills/symphony-linear-orchestrator/references/) walk through the full workflow.

## How the workflow runs

1. The orchestrator inspects a repository, updates guidance, and plans issue work in Linear.
2. Symphony dispatches workers from active Linear states.
3. Workers complete bounded changes, validate them, and move issues to `In Review`.
4. The orchestrator reviews worker output, integrates it, and moves issues to `Done`.
5. The orchestrator updates the repo runbook and learnings log, then promotes stable lessons into durable guidance.

When the optional Release Manager lane is enabled, workers still do not deploy. They attach PR URLs and add a `release:ready` label. A single Release Manager pass owns `main`, queues PRs with `gh pr merge --auto`, closes merged issues, and returns conflicted PRs to the worker queue. It first checks that a GitHub merge queue is enabled — so a burst of PRs batches instead of serializing — and is safe to re-run until the burst drains.

### What's automated vs. operator-driven

"Autonomous looping" applies to the **merge** half. Be clear on which hops run themselves:

| Hop | Who |
|---|---|
| Create Linear issues | **Operator** |
| Dispatch a worker wave | **Operator** (run Symphony; or your own scheduler — not built in) |
| Worker → branch + PR + `release:ready` | Worker (automated) |
| Enqueue + merge ready PRs | Release Manager lane / scheduled Action (automated) |
| Close merged issues → `Done` | Lane (automated) |
| Repair a blocked/conflicted PR | **Operator** — re-dispatch a worker; the implementation workflow auto-stops when idle and is not running between waves |

So the merge/land/close cycle is hands-off (via the [scheduled trigger](skills/symphony-linear-orchestrator/references/release-manager-lane.md)); **dispatching each new wave is operator- or scheduler-driven**, not built in by default. To close that gap, enable the optional autonomous goal loop below.

Default first-run concurrency is one worker. Scale out only after preflight, issue shaping, and review loops are working cleanly.

### Autonomous goal loop (optional)

The base skill executes one wave; the **goal loop** decides the *next* wave from a goal and keeps going, so the system can pursue a goal for hours instead of stopping after one wave. It is opt-in and capped.

The one judgment that keeps unattended autonomy from running away — *more, done, or stuck?* — is auditable code (`goal_state.py`), not an improvised vibe. Each lap the orchestrator reads the verdict and takes one action: dispatch the next wave, activate backlog, wait, or **stop** (on `done` or `stuck`). Hard budget caps (`max_laps`, `max_dispatched`, `max_planner_depth`, `max_wall_clock_minutes`) bind even when the goal is unfinished, and a `stuck` verdict always escalates to a human.

It ships in three layers:

| Layer | What it is |
|---|---|
| **Brain** | An orchestrator agent (Claude Code `/loop` or a Codex cron) following the rendered `goal-loop.PROMPT.md` — plan → dispatch → review → re-plan, one lap per heartbeat. |
| **Merge-trigger** | A `push: main` GitHub Action that runs the convergence check when work lands and reports where the goal stands (the clock, not the brain). |
| **Planner-lane** | A dispatchable `sym:planner` role that emits the next wave's issues when planning itself needs fan-out — fenced hard against recursion. |

Default posture is **gated** (the orchestrator reviews `In Review` and owns each merge). Flip it to **auto** (wire the Release Manager lane so `release:ready` PRs merge unattended — only with real validation gates) or a **per-label mix**. The convergence verdict and budget caps are identical across postures.

```bash
# Render the loop artifacts, then init the budget ledger:
python3 skills/symphony-linear-orchestrator/scripts/bootstrap.py \
  --target-repo /path/to/repo --workflow-name wave1 \
  --clone-url git@github.com:owner/repo.git --linear-project-slug proj \
  --with-goal-loop --goal "Ship the X milestone" --write

python3 skills/symphony-linear-orchestrator/scripts/goal_state.py \
  --ledger /path/to/repo/.orchestration/goal-state.json --init \
  --goal "Ship the X milestone" --project-slug proj
# Then hand .orchestration/goal-loop.PROMPT.md to your orchestrator agent.
```

See the [autonomous goal loop guide](skills/symphony-linear-orchestrator/references/autonomous-goal-loop.md) for the full model and safety requirements.

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
| `skills/.../references/` | Operating model, Linear issue contract, workflow spec, onboarding guide, recovery playbook, self-improvement loop, autonomous goal loop, planner lane, example prompts |
| `skills/.../scripts/` | `doctor.py`, `bootstrap.py`, `issue_schema.py`, `release_manager.py`, `preflight.py`, `goal_state.py` |
| `skills/.../assets/templates/` | Workflow, runbook, learnings, issue, guidance, brief, release-manager, goal-loop, and planner templates |
| `skills/.../agents/openai.yaml` | Codex agent configuration |

## Design defaults

- **One worker for the first run**, then scale out when the repo and issue graph are proven
- **Explicit routing lanes** via `sym:small`, `sym:medium`, `sym:large`, and `sym:content`
- **Explicit campaign metadata** so routing, trust boundary, and closeout ownership are visible in the workflow
- **`In Review` as the operator gate** — no auto-merge, no auto-Done
- **Workspace bootstrap assertions** for branch and repo-anchor paths
- **No-progress guardrails** so stuck runs get requeued instead of burning tokens
- **Narrow worker environment allowlist** by default: workers receive `LINEAR_API_KEY`, not the entire shell environment
- **Canonical issue rendering** so the human-readable body and machine-readable schema stay in sync
- **Self-improving loop** via RUNBOOK.md + LEARNINGS.md with promotion into durable guidance
- **Bounded issue contract** with acceptance criteria, validation commands, and touched areas
- **Security/privacy hygiene**: secrets, credentials, and personal data stay out of issue bodies and workflow files; routed Linear issue authors are part of the trusted execution boundary
- **No auto-merge, no snapshot promotion, no background services** in the default workflow
- **Optional single-writer Release Manager lane** for teams that want autonomous merge/deploy flow without parallel agents racing to update `main`
- **Merge-queue readiness check** (`--check-merge-queue`, also run in preflight) so a burst of ready PRs batches through GitHub Merge Queue instead of silently degrading to serial auto-merge
- **Hands-off scheduled trigger** — `bootstrap.py` renders a GitHub Action sample that drains the lane on a cron, concurrency-guarded so ephemeral runners stay single-writer
- **No Linear? GitHub-native path** — a standalone auto-merge-on-label Action sample (`assets/examples/auto-merge-on-label.yml`) for hands-off batched merging without the orchestration lane ([guide](skills/symphony-linear-orchestrator/references/github-native-merge.md))
- **Optional autonomous goal loop** — `bootstrap.py --with-goal-loop` renders a brain prompt, a merge-trigger Action, and a planner workflow that pursue a goal across many waves, gated by `goal_state.py`'s convergence + hard budget caps so unattended running has a real brake ([guide](skills/symphony-linear-orchestrator/references/autonomous-goal-loop.md))

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
