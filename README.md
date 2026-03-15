# Symphony + Linear Starter

![Symphony + Linear Orchestration Starter](assets/github/social-preview.png)

## What this repo is

`symphony-linear-starter` is a starter skill and operator toolkit for running Codex or Claude Code as the orchestrator over Symphony workers with Linear-managed execution. It is designed for practical Symphony + Linear workflows where a human or interactive agent does the planning, review, and recovery work while Symphony dispatches bounded worker tasks.

This repo focuses on practical multi-agent orchestration for software delivery. It gives an operator a reusable skill, issue planning contract, workflow templates, and lightweight validation scripts without copying a private local setup. The goal is not just safe setup; the goal is to get a real AI team moving quickly with a clear operator loop.

## Who it is for

This repo is for developers, technical founders, engineering leads, and AI-native operators who want:

- Symphony to run workers against a real codebase
- Linear to hold the execution plan, issue planning, and review state
- Codex or Claude Code to act as the orchestrator
- a fast first run with clear review gates and real parallel execution

It is not for teams looking for a generic agent framework with many trackers or many worker runtimes. The v1 scope is Symphony, Linear, Codex, and Claude Code.

## How the orchestration model works

The operating model is intentionally simple:

1. The orchestrator inspects a repository, updates repo-local guidance, and plans issue work.
2. Linear stores the issue planning contract, dependencies, and state transitions.
3. Symphony dispatches workers from active Linear states.
4. Workers complete bounded changes, validate them, and move issues to `In Review`.
5. The orchestrator reviews the worker output, integrates the result, and moves the issue to `Done`.

This separation matters. Symphony is the scheduler. Linear is the source of work state. The orchestrator owns issue planning, workflow setup, quality control, and recovery when a worker gets stuck.

## What is included

- `skills/symphony-linear-orchestrator/`
  - installable skill for Codex
  - reusable instructions for Claude Code
  - public references for issue planning, workflow setup, onboarding, and recovery
  - scripts for environment checks, scaffolding, and preflight validation
- `.github/`
  - issue templates for feedback during private preview
- `assets/github/social-preview.png`
  - GitHub social preview image for discoverability

The starter is opinionated:

- focused Symphony + Linear workflow
- orchestrator review gate in `In Review`
- default parallelism of `max_concurrent_agents: 3`
- no auto-merge
- no local snapshot mode
- no machine-specific background services

## Install and use in Codex

Install the skill folder into your Codex skills directory, then restart Codex so it is discoverable.

Manual install:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/symphony-linear-orchestrator "${CODEX_HOME:-$HOME/.codex}/skills/"
```

If you already use a GitHub-based skill installer, install the path:

```text
skills/symphony-linear-orchestrator
```

Then use prompts like:

- `Use $symphony-linear-orchestrator to onboard this repo for Symphony + Linear execution.`
- `Use $symphony-linear-orchestrator to create a first-wave workflow and issue plan for this codebase.`

## Use with Claude Code

Claude Code can use the same content even without Codex-specific skill installation. Keep this repo cloned locally and reference:

```text
skills/symphony-linear-orchestrator/SKILL.md
```

Recommended usage patterns:

- point Claude Code at the skill folder as shared instructions
- copy the skill into repo-local guidance if you want a project-specific version
- use the bundled templates and scripts even if you are not installing the skill as a first-class Codex skill

## First-run checklist

1. Confirm `git`, `gh`, `codex`, `python3`, Symphony, and `LINEAR_API_KEY` are available.
2. Run `python3 skills/symphony-linear-orchestrator/scripts/doctor.py --json`.
3. Run `python3 skills/symphony-linear-orchestrator/scripts/bootstrap.py --target-repo /path/to/repo --workflow-name wave1 --clone-url <url> --linear-project-slug <slug>` (dry-run).
4. Review `.orchestration/AGENTS_ADDITIONS.md` and merge the right parts into the target repo's `AGENTS.md`.
5. Re-run the bootstrap command with `--write` to generate files.
6. Create bounded Linear issues using the bundled contract.
7. Run `python3 skills/symphony-linear-orchestrator/scripts/preflight.py --target-repo /path/to/repo --workflow /path/to/workflow --json`.
8. Start Symphony with three workers by default. Drop to one only if the repo is fragile, the test baseline is unclear, or the ticket graph is still rough.
9. Review worker output in `In Review` before marking anything `Done`.

## Example prompts

- `Use $symphony-linear-orchestrator to inspect this repo and generate a Symphony workflow plus Linear issue template.`
- `Use $symphony-linear-orchestrator to onboard this project for multi-agent orchestration with Symphony and Linear.`
- `Use $symphony-linear-orchestrator to turn this feature request into a first execution wave with issue planning and validation commands.`
- `Use $symphony-linear-orchestrator to review this repository's AGENTS.md and tell me what is missing before I hand work to Symphony workers.`
- `Use $symphony-linear-orchestrator to generate a three-worker Symphony workflow with an In Review gate.`
- `Use $symphony-linear-orchestrator to draft the Linear issue bodies and dependencies for this milestone.`
- `Use $symphony-linear-orchestrator to run preflight checks for this repository and explain any blockers.`
- `Use $symphony-linear-orchestrator to recover a stalled Symphony run and recommend the next operator action.`

## Safety defaults

- Keep most issues in `Backlog`.
- Activate only the first wave, but size it to fill three worker slots.
- Require explicit validation commands in each issue.
- Use `In Review` as the orchestrator review gate.
- Keep workers bounded to the issue instead of allowing broad cleanup.
- Integrate validated work quickly and move issues forward so the dependency chain stays hot.
- Reduce concurrency to one only for unproven repos or unstable baselines.

## FAQ

### Why use `In Review` instead of auto-completing to `Done`?

Because the orchestrator is responsible for software delivery quality. `In Review` keeps the review gate explicit and portable across repositories.

### Why is the orchestrator layer separate from Symphony?

Because Symphony schedules workers. It does not replace issue planning, recovery, quality control, or operator judgment.

### Does this repo create Linear issues automatically?

No. It gives you the contract and templates for issue planning. The operator still owns the actual plan and the resulting Linear state.

### Does this repo support trackers other than Linear?

No. The v1 contract is focused Symphony + Linear.

### Does this repo support snapshot promotion or automatic PR handoff?

Not by default. This starter is optimized for fast operator-led throughput with an explicit review gate.

## Current limitations

- `preflight.py` validates local readiness and workflow shape, but it does not query the Linear API directly.
- The starter assumes you already have a working Symphony runtime.
- Claude Code can use the materials, but Codex has the better first-class skill installation model.
- The social preview image is included locally, but GitHub social preview upload is still a manual settings step.
- Parallel runs are most effective when issue bodies are specific and the orchestrator reviews workspaces aggressively.

## Roadmap

- optional Linear API verification for live queue checks
- richer bootstrap templates for language-specific repos
- example repos showing issue planning and worker review
- public release with `MIT` after the private preview is reviewed
- possible future `llms.txt` and public docs refinements for broader discoverability
