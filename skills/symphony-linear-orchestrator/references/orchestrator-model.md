# Orchestrator Model

## Core roles

- **Orchestrator**: the human or interactive coding agent that inspects the repository, creates or updates guidance, shapes the plan, monitors progress, and reviews worker output.
- **Symphony**: the scheduler and runtime that dispatches workers from a tracker-backed queue.
- **Worker**: the isolated execution agent that works a single issue at a time.
- **Linear**: the source of truth for issue planning, dependencies, status, and handoff state.

## Why the orchestrator layer is separate from Symphony

Symphony is optimized for dispatch and isolation. It should not be treated as the entire operating model. The orchestrator layer exists because someone still needs to:

- inspect the target repo
- shape issues into bounded units of work
- define validation commands
- watch for failure modes and retries
- review worker output before final completion

Without an explicit orchestrator, the system becomes fragile. Large tickets, weak issue bodies, and silent validation drift all become more likely.

## Self-improvement loop

The orchestrator is also the improvement layer. After each execution wave, it should:

- capture fresh observations in `.orchestration/LEARNINGS.md`
- update `.orchestration/RUNBOOK.md` with repeatable operator steps
- promote stable worker-facing rules into `AGENTS.md`
- tighten the issue template and workflow defaults when the same problems recur

That loop is what makes the system self-improving instead of merely repeatable.

## Default state model

Use this state model in Linear:

- `Backlog`
- `Todo`
- `In Progress`
- `In Review`
- `Done`

`In Review` is the default orchestrator review gate. Workers should move completed work there after validation and a final status comment. The orchestrator reads the output, integrates or rejects it, then decides whether the issue moves to `Done`, back to `Todo`, or to a blocked state.

## First-run posture

- Start with one worker for the first real run.
- Scale out only after the repo baseline, issue boundaries, and review loop are behaving predictably.
- Keep the first wave small enough to preserve operator control, even if that leaves worker slots unused at first.
- Prefer explicit acceptance criteria over vague goals.
- Review everything in `In Review` before trusting the loop.
- Integrate validated output quickly. Throughput depends on the orchestrator moving the dependency chain, not waiting for formal polish.
- Turn repeated lessons into better defaults. The fastest teams do not just review output; they reduce the odds of the same mistake on the next wave.
- Keep secrets, credentials, session cookies, personal data, and raw customer payloads out of issues, workflow files, and learnings docs.
- Prefer fast bootstrap failure and no-progress requeue over hoping a confused worker will self-correct.
