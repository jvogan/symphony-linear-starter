# Goal Loop -- orchestrator lap instructions for `__REPO_NAME__`

This is the per-lap runbook for the **brain** of an autonomous goal loop. Give it
to an orchestrator agent (Claude Code via `/loop`, or a Codex orchestrator on a
cron) as its standing instructions. The agent runs **one lap per heartbeat**:
read the goal state, take the single action the convergence check returns, then
wait. The loop's safety does not come from this prompt -- it comes from
`goal_state.py`, which every lap consults and obeys.

- **Goal:** __GOAL__
- **Linear project:** `__LINEAR_PROJECT_SLUG__`
- **Implementation wave:** `.orchestration/__WORKFLOW_NAME__.WORKFLOW.md`
- **Planner wave (optional):** `.orchestration/planner.WORKFLOW.md`
- **Ledger:** `.orchestration/goal-state.json`

## One-time setup

Create the budget ledger (tune the caps; these are the hard stops for an
unattended run):

```bash
python3 skills/symphony-linear-orchestrator/scripts/goal_state.py \
  --ledger .orchestration/goal-state.json --init \
  --goal "__GOAL__" --project-slug "__LINEAR_PROJECT_SLUG__" \
  --max-laps 50 --max-dispatched 100 --max-planner-depth 2 \
  --max-wall-clock-minutes 480 --wave-size 5 --stall-threshold 3
```

## The lap

**1. Read the goal state (and advance the ledger one lap):**

```bash
python3 skills/symphony-linear-orchestrator/scripts/goal_state.py \
  --ledger .orchestration/goal-state.json --record --json
```

**2. Do exactly what `action` says -- nothing more:**

- **`dispatch`** -- the `next_wave` lists ready Linear issues. Make sure they are
  in an active state, then dispatch a worker wave against the implementation
  workflow (Symphony, or your own native agents). After you actually dispatch,
  record it so the dispatch budget stays honest:
  ```bash
  python3 skills/symphony-linear-orchestrator/scripts/goal_state.py \
    --ledger .orchestration/goal-state.json --dispatched <count>
  ```
- **`activate_backlog`** -- there is planned work but nothing is ready. Promote
  the next few `Backlog` issues to `Todo` (or, if the backlog is thin, shape more
  work -- see the planner below). The next lap will dispatch them.
- **`wait`** -- work is in flight and progressing. Do not dispatch. In the
  default gated posture this is also when you **review** anything sitting in
  `In Review` and integrate it (below). Then heartbeat.
- **`escalate`** (verdict `stuck`) -- **stop the loop.** Post the `reason` and the
  budget snapshot to the operator. Do not dispatch, do not "try once more". A
  stuck verdict means a budget cap was hit, work stalled, or everything left is
  blocked -- all of which need a human. (`goal_state.py` exits non-zero here.) The
  stall guard counts brain laps with no completion and no new dispatch, so a
  slow-but-healthy wave can trip it; if you confirm it is just slow, resume with
  `goal_state.py --reset-stall` (optionally raising `--stall-threshold`).
- **`stop`** (verdict `done`) -- **stop the loop.** The goal's work is consumed.
  Write a short closeout: update `.orchestration/LEARNINGS.md` and the brief's
  decision log, and report what shipped.

**3. Review gate (default posture).** When workers move issues to `In Review`,
you integrate them: review the diff/PR, merge it (or hand it to the Release
Manager lane), and move the issue to `Done`. The loop is not "done" until the
board is -- unreviewed `In Review` work keeps the verdict at `continue`/`wait`,
and if it never clears, the stall guard will eventually escalate to you.

**4. Heartbeat.** Wait for the next tick (a push to your base branch lands work
faster than a fixed sleep -- see the merge-trigger Action below) and repeat from
step 1.

## Posture: who watches the merge

Default is **gated**: you (the orchestrator) review `In Review` and own the merge.
The loop dispatches and waits, but a human-or-orchestrator hand is on every
integration. Two other postures, both valid -- configure whichever the operator
wants:

- **Auto** -- wire the optional Release Manager lane (`--with-release-manager`)
  and let workers mark PRs `release:ready`. Then `In Review` -> merged -> `Done`
  happens without you, and the loop runs unattended for hours up to the budget.
  Only do this with **real validation gates** (required status checks / a merge
  queue) on the base branch -- auto-merging without them accumulates
  subtly-broken code. See `references/release-manager-lane.md`.
- **Mix** -- auto-merge the low-risk lanes (`sym:small`, content) and keep the
  risky ones gated for your review. Route by label.

Whatever the posture, the convergence verdict and the budget caps are unchanged --
they bound the loop regardless of who merges.

## Non-negotiables

- **Obey `stuck`.** It is the brake. Never override it to keep going.
- **Honor the budget.** The caps in the ledger are hard stops, not suggestions.
- **Validation gates are required for auto mode.** No required checks, no auto-merge.
- **One action per lap.** Do not batch "dispatch + activate + plan" in one tick;
  let the next lap re-read the real state.
- **Never paste secrets, tokens, or customer data** into Linear, commits, or the
  ledger.

See `references/autonomous-goal-loop.md` for the full model and
`references/planner-lane.md` for dispatching planning itself.
