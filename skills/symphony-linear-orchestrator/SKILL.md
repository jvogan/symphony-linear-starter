---
name: symphony-linear-orchestrator
description: Bootstrap strict Symphony + Linear orchestration for a software repository. Use when Codex needs to inspect a repo, review or generate repo-local guidance, shape Linear issues, render a Symphony workflow, run conservative first-wave startup, or handle orchestrator-led review and recovery for worker output.
---

# Symphony + Linear Orchestrator

Use this skill to onboard a repository for Symphony workers coordinated by an orchestrator and tracked in Linear.

## Roles

- The orchestrator inspects the repo, plans work, runs setup, and reviews results.
- Symphony dispatches workers.
- Workers execute bounded issues.
- Linear stores issue planning, dependencies, and review state.

## Required workflow

1. Inspect the target repo and current `AGENTS.md`.
2. Run `scripts/doctor.py` to confirm the local toolchain and auth state.
3. Run `scripts/bootstrap.py` to render onboarding artifacts into the target repo's `.orchestration/` directory.
4. Merge the generated `AGENTS_ADDITIONS.md` content into the target repo manually. Do not let the script edit `AGENTS.md` for you.
5. Create Linear issues using the contract in `references/linear-contract.md`.
6. Run `scripts/preflight.py` before starting any real run.
7. Start Symphony with `max_concurrent_agents: 1` for the first run.
8. Treat `In Review` as the orchestrator gate. Review worker output, integrate the result, then move the issue to `Done`.

## Safety defaults

- Keep most work in `Backlog`.
- Activate only the first execution wave.
- Do not auto-merge.
- Do not default to snapshot promotion or automatic PR creation.
- Do not introduce machine-specific background services into the target repo.

## Reference map

- Read `references/orchestrator-model.md` when you need the operating model and role boundaries.
- Read `references/linear-contract.md` before writing issue bodies or dependency chains.
- Read `references/symphony-workflow.md` before rendering or editing a workflow.
- Read `references/repo-onboarding.md` when reviewing the target repo's `AGENTS.md` and local guidance.
- Read `references/recovery-playbook.md` when a worker stalls, clones the wrong branch, or drifts from validation.
- Read `references/example-prompts.md` when you want prompt patterns for Codex or Claude Code.

