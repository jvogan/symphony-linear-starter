# Planner Lane

The planner lane makes **planning itself dispatchable**. Normally the
orchestrator (the brain loop) shapes the next wave. When that shaping is too big
for one pass -- a broad goal, many independent areas -- you can dispatch
`sym:planner` workers that emit the next wave's issues in parallel, the same way
implementation workers emit code.

This is the most powerful and the most dangerous layer of the autonomous goal
loop (`references/autonomous-goal-loop.md`). A planner that creates more planners
is unbounded recursion. Use it only when planning genuinely needs fan-out, and
keep the fences on.

## The role

A planner worker:

- reads the goal, the repo, and the current Linear issues,
- decomposes the next increment into bounded, validatable issues following
  `references/linear-contract.md`,
- creates them in `Backlog` with routing labels (`sym:small` / `sym:medium` /
  `sym:large`),
- and stops. It does not write product code and does not open PRs.

It is rendered by `bootstrap.py --with-goal-loop` to
`.orchestration/planner.WORKFLOW.md` and dispatched like any other Symphony
workflow, routed by the `sym:planner` label.

## The fences (do not remove)

Three independent limits keep the lane from running away. All three must hold:

1. **`max_planner_depth`** (ledger + workflow). A planner refuses to create new
   `sym:planner` issues once `goal_state.py` reports `budget.planner_depth` at the
   cap. Planners can shape implementation issues at any depth, but the chain of
   planners-shaping-planners is bounded.
2. **`max_issues_per_plan`** (workflow). One planner run emits at most this many
   issues. A plan that wants more is a signal the increment is too large -- split
   it across future laps.
3. **The convergence gate** (`goal_state.py`). Before shaping anything a planner
   reads the verdict. On `done` or `stuck` it creates nothing, comments why, and
   moves to `In Review`. This is what stops a planner from piling work onto a loop
   that the budget says should stop.

The dispatch budget (`max_dispatched`) bounds the issues those plans ultimately
turn into, so even a misfiring planner cannot generate unbounded execution.

## Tracking planner depth

The brain sets depth when it dispatches a planner that was itself spawned by a
planner:

```bash
# A planner dispatched by the orchestrator is depth 1:
python3 skills/symphony-linear-orchestrator/scripts/goal_state.py \
  --ledger .orchestration/goal-state.json --planner-depth 1

# If that planner's issues include another planner, the next is depth 2, etc.
```

When `planner_depth` exceeds `max_planner_depth`, `goal_state.py` returns `stuck`
and the loop escalates. This code check is a **backstop**, and it only fires if
the brain actually tracks depth with `--planner-depth`. The always-on fence is the
worker instruction the planner reads every run -- it refuses to create
`sym:planner` issues at or above the cap -- backed by `max_issues_per_plan` and
the dispatch budget. Keep the worker instruction; the ledger counter is the extra
belt if your brain maintains it.

## Waking the planner from CI

The merge-trigger Action (`goal-loop.gha.yml`) can optionally *wake* a planner
when work lands: if the convergence verdict wants more work shaped
(`dispatch`/`activate_backlog`) and no `sym:planner` issue is already open, create
one. The open-issue check (`should_wake_planner` in `goal_state.py`) is the
idempotency guard -- it is what stops a push from spawning a planner every time.

This is left **opt-in** and off by default in the rendered Action: a reporter
that also mutates Linear on every push is a footgun for a public starter. Turn it
on deliberately, and only with the depth and convergence fences in place.

## When NOT to use it

- For a small or well-understood goal, the brain can shape each wave directly --
  skip the planner lane entirely.
- If you find yourself raising `max_planner_depth` past 2, stop and reconsider the
  goal decomposition instead. Deep planner recursion is almost always a sign the
  goal was not decomposable enough to automate safely.
