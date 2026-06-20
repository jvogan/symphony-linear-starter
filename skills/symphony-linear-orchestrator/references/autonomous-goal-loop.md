# Autonomous Goal Loop

The base skill executes one wave well: plan issues, dispatch workers, review,
promote learnings. This reference adds the layer above that -- the loop that
decides the *next* wave from a goal and keeps going, so the system can pursue a
goal for hours instead of stopping after one wave.

It is optional and opt-in. The default skill stays operator-driven; turn this on
only when you want unattended, goal-directed running and have accepted the
safety requirements below.

## The one judgment that matters

A goal loop is a cheap idea with one expensive failure mode: running away. The
thing that prevents it is a single per-lap judgment -- *more, done, or stuck?* --
and this skill makes that judgment **auditable code**, not a vibe the agent
improvises. That code is `scripts/goal_state.py`. Everything else here is
plumbing around it.

`goal_state.py` reads the real Linear project state plus a small budget ledger
(`.orchestration/goal-state.json`) and returns one verdict:

| Verdict | Action | Meaning |
|---|---|---|
| `continue` | `dispatch` | ready issues exist; here is the next wave (bounded by `wave_size` and the dispatch budget) |
| `continue` | `activate_backlog` | planned work exists but nothing is ready; promote some |
| `continue` | `wait` | work is in flight and progressing; heartbeat |
| `done` | `stop` | nothing ready, in flight, or in the backlog remains (or the goal was marked done) |
| `stuck` | `escalate` | a budget cap was hit, work stalled, or all that remains is blocked -- **stop and get a human** |

`stuck` exits non-zero so a scheduler or CI surfaces it loudly instead of
spinning. The decision logic and ledger math are pure functions covered by
`--self-test` and `tests/test_goal_state.py`.

## Three layers

The loop is built in three layers. The first is enough on its own; the other two
make it faster and let planning itself scale.

### 1. The brain (the loop)

A goal-holding orchestrator runs the lap: read `goal_state.py`, take the single
action it returns, integrate finished work, heartbeat, repeat. Two delivery
forms, same logic:

- **Live** -- an orchestrator agent (Claude Code via `/loop`, or a Codex
  orchestrator) following `.orchestration/goal-loop.PROMPT.md` (rendered by
  `bootstrap.py --with-goal-loop`). Easiest to start, easiest to watch.
- **Durable** -- the same prompt driven on a cron/launchd cadence so it survives
  a closed session.

The brain is an agent following a documented loop -- this skill does **not** ship
a daemon that autonomously dispatches and merges on its own. The only code in the
loop is the safety spine. For a robust setup, split the brain into a small team
(planner / reviewer / integrator) so one bad plan cannot burn the whole budget.

### 2. The merge-trigger (the clock)

A `push: __REQUIRED_BRANCH__` GitHub Action (`goal-loop.gha.yml`, rendered
sample) that runs `goal_state.py` whenever work lands and reports the verdict to
the run summary -- a faster, event-driven heartbeat than a fixed sleep, with a
slow `schedule:` cron as the stall fallback. It is the clock, not the brain: it
reports (and can optionally wake a planner), but it does not decide content and
it does not merge. An Action cannot spawn an orchestrator session, so in a setup
with no live brain it wakes the *planner queue*, and the actual planning still
happens in a dispatched agent. It runs read-only by default (it does not commit
the ledger back), so durable lap/stall accounting stays owned by the brain.

### 3. The planner-lane (planning that scales)

A dispatchable `sym:planner` role (`planner.WORKFLOW.md`, rendered sample) whose
workers emit the *next wave's issues* instead of writing code. Use it when
shaping the next wave is itself too big for one orchestrator pass. This is the
sharpest tool here: planners that spawn planners are unbounded recursion, so it
is fenced hard -- a `max_planner_depth` cap in the ledger, a `max_issues_per_plan`
cap in the workflow, and the convergence gate (a planner consults `goal_state.py`
and refuses to pile on more work when the verdict is not "shape more"). See
`references/planner-lane.md`.

## Safety requirements

Autonomy is opt-in and capped. Do not skip these:

- **A decomposable goal.** The loop only works if the goal can be split into
  bounded, independently-validatable issues. A vague goal produces vague issues
  and the loop amplifies the mistake.
- **Real validation gates on every PR.** Auto-merge without required status
  checks (or a merge queue) accumulates subtly-broken code fast. In the default
  gated posture you are the gate; in auto mode the gates must be wired.
- **The budget is a hard stop.** `max_laps`, `max_dispatched`, `max_planner_depth`,
  and `max_wall_clock_minutes` all bind even when the goal is unfinished. When one
  trips, the loop stops and escalates -- by design.
- **Honor `stuck`.** Stalled, blocked, or over-budget all mean a human decides
  the next move. Never code around the brake. The stall guard counts *brain laps*
  with no completion and no new dispatch, not wall-clock -- a wave whose first
  merge legitimately spans several quiet heartbeats can trip it. That is
  fail-safe (it escalates, never runs away). After you investigate and confirm
  the wave is just slow, resume with `goal_state.py --reset-stall` (and raise
  `--stall-threshold` if your waves are routinely quiet for a while), so tune the
  threshold to your heartbeat rather than disabling the guard.
- **Checkpoints.** The ledger plus `LEARNINGS.md` are the resumable trail; keep
  them in the repo so a restarted loop knows where it was.
- **Same data hygiene as the rest of the skill.** No secrets, tokens, or customer
  data in issues, commits, the ledger, or comments.

## Posture

The default shipped posture is **gated**: the orchestrator watches and owns each
merge (`In Review` is the gate). It flips trivially to **auto** (wire the Release
Manager lane and let `release:ready` PRs merge unattended) or a **mix** (auto for
low-risk lanes, gated for risky ones, routed by label). The convergence verdict
and budget caps are identical across postures -- only *who clears `In Review`*
changes. Configure whichever fits the operator's risk tolerance.

## Quick start

```bash
# 1. Render the loop artifacts into the target repo.
python3 skills/symphony-linear-orchestrator/scripts/bootstrap.py \
  --target-repo /path/to/repo --workflow-name wave1 \
  --clone-url git@github.com:owner/repo.git --linear-project-slug proj \
  --with-goal-loop --goal "Ship the X milestone" --write

# 2. Initialize the ledger (one time).
python3 skills/symphony-linear-orchestrator/scripts/goal_state.py \
  --ledger /path/to/repo/.orchestration/goal-state.json --init \
  --goal "Ship the X milestone" --project-slug proj

# 3. Hand .orchestration/goal-loop.PROMPT.md to your orchestrator agent and start
#    the loop. It will dispatch, wait, review, and re-plan until done or stuck.
```

## How this composes with the rest of the skill

- The implementation wave is the same `workflow.WORKFLOW.md` the base skill
  dispatches; the loop just decides *when* to dispatch it again.
- Auto mode rides on the Release Manager lane (`references/release-manager-lane.md`).
- The planner lane uses the same Linear contract (`references/linear-contract.md`)
  and issue rendering (`scripts/issue_schema.py`) as hand-planned waves.
- Convergence is consulted by all three layers -- the brain each lap, the
  merge-trigger each push, the planner before it shapes work -- so they cannot
  disagree about whether to keep going.
