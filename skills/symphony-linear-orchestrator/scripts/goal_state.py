#!/usr/bin/env python3
"""Goal-state convergence + budget check for autonomous goal loops.

This is the safety spine of the autonomous goal loop. A goal loop runs many
laps -- plan, dispatch, wait for merges, re-plan -- and the one judgment that
separates safe autonomy from a 3am runaway is the per-lap question: *more,
done, or stuck?* This script answers it from real Linear state plus a small
budget ledger, so the decision is auditable code instead of vibes.

It is deliberately read-only about the world: it never dispatches, merges, or
edits issues. It reports a verdict and (optionally) advances its own ledger.
The caller -- a brain loop (references/autonomous-goal-loop.md), a planner
(references/planner-lane.md), or a merge-trigger Action -- acts on the verdict.

Verdicts:

* ``continue`` with an action of ``dispatch`` (here is the next wave of ready
  issues, within budget), ``activate_backlog`` (planned work exists but nothing
  is ready -- promote some), or ``wait`` (work is in flight and progressing --
  heartbeat).
* ``done`` -- nothing ready, in flight, or in the backlog remains (or the goal
  was explicitly marked done). The loop stops.
* ``stuck`` -- a budget cap was hit, work stalled with nothing dispatchable, or
  everything left is blocked. The loop stops and escalates to a human. This is
  the guardrail that makes unattended running safe; ``stuck`` exits non-zero so
  a scheduler/Action surfaces it loudly instead of spinning.

The decision logic (``decide``) and the ledger update (``advance_ledger``) are
pure functions exercised by ``--self-test`` and tests/test_goal_state.py, so the
convergence behavior is verifiable without a network or a live tracker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


GRAPHQL_URL = "https://api.linear.app/graphql"

# Default mapping of Linear state names to convergence buckets. Aligns with the
# contract states (references/linear-contract.md) plus the release lane's
# "Merging" queued state. Operators override these at --init for custom flows.
DEFAULT_STATES: dict[str, list[str]] = {
    "backlog": ["Backlog"],
    "ready": ["Todo"],
    "in_flight": ["In Progress", "In Review", "Merging"],
    "done": ["Done"],
    "blocked": [],
    # Dropped work is neither pending nor progress -- it just leaves the board.
    "dropped": ["Canceled", "Cancelled", "Closed", "Duplicate"],
}

# Conservative defaults sized for a multi-hour unattended run. Every one is a
# hard ceiling: hitting it yields a ``stuck`` verdict that stops the loop.
DEFAULT_BUDGET = {
    "max_laps": 50,
    "max_dispatched": 100,
    "max_planner_depth": 2,
    "max_wall_clock_minutes": 480,
    "wave_size": 5,
    "stall_threshold": 3,
}

# Pending buckets -- work that still owes the goal something. Done and dropped
# (Canceled/Closed/...) are excluded, so "nothing pending" is the real done
# signal. `other` (an issue in a state mapped to no bucket) IS counted as pending:
# an unmapped state must never be silently treated as finished -- the loop forces
# the operator to map or resolve it instead of declaring the goal done.
PENDING_BUCKETS = ("ready", "in_flight", "backlog", "blocked", "other")

# Exit codes: continue/done are healthy (0); stuck must be loud (2) so a CI/cron
# caller stops and pages a human instead of treating it as a normal pass.
EXIT_OK = 0
EXIT_STUCK = 2


@dataclass
class Verdict:
    verdict: str  # continue | done | stuck
    action: str  # dispatch | activate_backlog | wait | escalate | stop
    reason: str
    counts: dict[str, int]
    budget: dict[str, Any]
    goal: str
    next_wave: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "action": self.action,
            "reason": self.reason,
            "next_wave": self.next_wave,
            "counts": self.counts,
            "budget": self.budget,
            "goal": self.goal,
        }

    def exit_code(self) -> int:
        return EXIT_STUCK if self.verdict == "stuck" else EXIT_OK


def _as_int(value: Any, default: int) -> int:
    """Coerce a (possibly hand-corrupted) ledger field to int, else fall back.

    A garbled numeric field in the ledger must not crash the safety check with a
    raw ValueError; it degrades to the default so the loop keeps a working bound.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def linear_graphql(api_key: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Linear GraphQL HTTP {exc.code}: {detail[:400]}") from exc
    if payload.get("errors"):
        raise RuntimeError(f"Linear GraphQL errors: {payload['errors']}")
    return payload


def fetch_issue_states(api_key: str, project_slug: str) -> tuple[list[tuple[str, str]], bool]:
    """Return ``[(identifier, state_name), ...]`` for every issue in the project.

    Paginates fully so counts are never silently truncated -- a wrong count is
    how a loop declares ``done`` while work remains. A runaway guard caps the
    page walk and flags ``truncated`` rather than looping forever on a pathologic
    project; the caller surfaces that instead of trusting a partial count.
    """
    query = """
query GoalState($slug: String!, $after: String) {
  issues(filter: {project: {slugId: {eq: $slug}}}, first: 250, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { identifier state { name } }
  }
}
"""
    results: list[tuple[str, str]] = []
    after: str | None = None
    truncated = False
    for _ in range(20):  # 20 * 250 = 5000 issues before we refuse to keep paging
        payload = linear_graphql(api_key, query, {"slug": project_slug, "after": after})
        block = payload.get("data", {}).get("issues", {})
        for node in block.get("nodes", []):
            results.append((node.get("identifier") or "", (node.get("state") or {}).get("name") or ""))
        page = block.get("pageInfo", {})
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
    else:
        truncated = True
    return results, truncated


def bucket_counts(
    issue_states: list[tuple[str, str]], states_cfg: dict[str, list[str]]
) -> tuple[dict[str, int], list[str]]:
    """Count issues per bucket and return the ready issues' identifiers (sorted).

    State matching is case-insensitive. A state listed in no bucket is counted as
    ``other`` (reported, never pending) so an unmapped Linear state can never be
    silently treated as done.
    """
    lookup: dict[str, str] = {}
    for bucket, names in states_cfg.items():
        for name in names:
            lookup[name.strip().lower()] = bucket
    counts = {bucket: 0 for bucket in states_cfg}
    counts["other"] = 0
    ready_ids: list[str] = []
    for identifier, state in issue_states:
        bucket = lookup.get((state or "").strip().lower(), "other")
        counts[bucket] = counts.get(bucket, 0) + 1
        if bucket == "ready" and identifier:
            ready_ids.append(identifier)
    return counts, sorted(ready_ids)


def default_ledger(goal: str, project_slug: str, started_at: float, overrides: dict[str, Any]) -> dict[str, Any]:
    ledger: dict[str, Any] = {
        "goal": goal,
        "project_slug": project_slug,
        "started_at": started_at,
        "laps": 0,
        "dispatched": 0,
        "planner_depth": 0,
        "goal_done": False,
        "stall_laps": 0,
        "last_done": -1,
        "last_dispatched": 0,
        "states": {bucket: list(names) for bucket, names in DEFAULT_STATES.items()},
    }
    ledger.update(DEFAULT_BUDGET)
    for key, value in overrides.items():
        if value is not None:
            ledger[key] = value
    return ledger


def validate_budget(ledger: dict[str, Any]) -> list[str]:
    """Reject degenerate caps so a misconfigured ledger fails loudly at --init.

    A cap of 0 (or negative) on the positive ceilings produces confusing
    behavior (e.g. wave_size=0 -> an empty dispatch wave that makes no progress),
    so refuse it up front rather than discover it mid-run.
    """
    problems = []
    for key in ("max_laps", "max_dispatched", "wave_size", "max_wall_clock_minutes", "stall_threshold"):
        if _as_int(ledger.get(key), 0) < 1:
            problems.append(f"{key} must be >= 1")
    if _as_int(ledger.get("max_planner_depth"), 0) < 0:
        problems.append("max_planner_depth must be >= 0")
    return problems


def load_ledger(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise RuntimeError(f"ledger at {path} is not a JSON object")
    return data


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2) + "\n")


def advance_ledger(ledger: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    """Advance the ledger one lap and update stall tracking. Pure.

    Forward progress is either the ``done`` count rising OR new work dispatched
    since the last lap (``dispatched`` rose). With neither, while work is in
    flight, the stall counter climbs; any progress resets it. Keying on dispatch
    progress -- not merely "nothing ready" -- is what lets the stall guard catch a
    loop that is over its dispatch budget with ready work it cannot start (which
    would otherwise wait, blind, until the lap cap). The first lap never
    false-trips because ``last_done`` starts at -1 and ``dispatched`` starts at 0.
    """
    updated = dict(ledger)
    prev_done = _as_int(updated.get("last_done", -1), -1)
    prev_dispatched = _as_int(updated.get("last_dispatched", 0), 0)
    cur_dispatched = _as_int(updated.get("dispatched", 0), 0)
    done = counts.get("done", 0)
    made_progress = done > prev_done
    dispatched_new = cur_dispatched > prev_dispatched
    if made_progress or dispatched_new:
        updated["stall_laps"] = 0
    elif counts.get("in_flight", 0) > 0:
        updated["stall_laps"] = _as_int(updated.get("stall_laps", 0), 0) + 1
    else:
        updated["stall_laps"] = 0
    updated["last_done"] = done
    updated["last_dispatched"] = cur_dispatched
    updated["laps"] = _as_int(updated.get("laps", 0), 0) + 1
    return updated


def budget_snapshot(ledger: dict[str, Any], elapsed_minutes: float | None) -> dict[str, Any]:
    return {
        "laps": _as_int(ledger.get("laps"), 0),
        "max_laps": _as_int(ledger.get("max_laps"), DEFAULT_BUDGET["max_laps"]),
        "dispatched": _as_int(ledger.get("dispatched"), 0),
        "max_dispatched": _as_int(ledger.get("max_dispatched"), DEFAULT_BUDGET["max_dispatched"]),
        "planner_depth": _as_int(ledger.get("planner_depth"), 0),
        "max_planner_depth": _as_int(ledger.get("max_planner_depth"), DEFAULT_BUDGET["max_planner_depth"]),
        "stall_laps": _as_int(ledger.get("stall_laps"), 0),
        "stall_threshold": _as_int(ledger.get("stall_threshold"), DEFAULT_BUDGET["stall_threshold"]),
        "elapsed_minutes": round(elapsed_minutes, 1) if elapsed_minutes is not None else None,
        "max_wall_clock_minutes": _as_int(ledger.get("max_wall_clock_minutes"), DEFAULT_BUDGET["max_wall_clock_minutes"]),
    }


def decide(
    counts: dict[str, int],
    ready_ids: list[str],
    ledger: dict[str, Any],
    elapsed_minutes: float | None,
) -> Verdict:
    """Decide more/done/stuck from counts + the (already-advanced) ledger. Pure.

    Hard budget ceilings and the stall guard are checked first: any one of them
    stops the loop regardless of how much work is left, because the whole point
    of a budget is that it binds even when the goal is not finished. Only then do
    we read the work state. Dispatch is additionally gated by the dispatch
    budget: with the budget spent but work still in flight, the loop waits for
    that work rather than declaring failure; with the budget spent and nothing in
    flight, it cannot make progress and escalates.
    """
    goal = str(ledger.get("goal", ""))
    budget = budget_snapshot(ledger, elapsed_minutes)
    wave_size = _as_int(ledger.get("wave_size"), DEFAULT_BUDGET["wave_size"])
    stall_threshold = _as_int(ledger.get("stall_threshold"), DEFAULT_BUDGET["stall_threshold"])

    def verdict(kind: str, action: str, reason: str, wave: list[str] | None = None) -> Verdict:
        return Verdict(kind, action, reason, counts, budget, goal, wave or [])

    # 1. Hard budget ceilings -- bind even with work remaining.
    if budget["laps"] > budget["max_laps"]:
        return verdict("stuck", "escalate", f"lap budget exhausted ({budget['laps']}/{budget['max_laps']})")
    if elapsed_minutes is not None and elapsed_minutes >= budget["max_wall_clock_minutes"]:
        return verdict("stuck", "escalate", f"time budget exhausted ({budget['elapsed_minutes']}/{budget['max_wall_clock_minutes']} min)")
    if budget["planner_depth"] > budget["max_planner_depth"]:
        return verdict("stuck", "escalate", f"planner depth exceeded ({budget['planner_depth']}/{budget['max_planner_depth']})")
    if budget["stall_laps"] >= stall_threshold:
        return verdict("stuck", "escalate", f"no forward progress for {budget['stall_laps']} lap(s); work is stalled")

    # 2. Explicit done marker set by the operator or a closing planner.
    if ledger.get("goal_done"):
        return verdict("done", "stop", "goal marked done")

    ready = counts.get("ready", 0)
    in_flight = counts.get("in_flight", 0)
    backlog = counts.get("backlog", 0)
    blocked = counts.get("blocked", 0)
    other = counts.get("other", 0)
    pending = sum(max(0, counts.get(b, 0)) for b in PENDING_BUCKETS)

    # 3. Nothing pending anywhere -> the goal's decomposed work is consumed.
    if pending == 0:
        return verdict("done", "stop", "no ready, in-flight, backlog, blocked, or unmapped work remains")

    # 4. Only non-actionable work remains (blocked and/or in states mapped to no
    # bucket) and nothing is moving -> a human must unblock or map the states.
    # This is what stops an unmapped Linear state from masquerading as "done".
    if ready == 0 and in_flight == 0 and backlog == 0 and (blocked > 0 or other > 0):
        bits = []
        if blocked > 0:
            bits.append(f"{blocked} blocked")
        if other > 0:
            bits.append(f"{other} in unmapped state(s)")
        return verdict("stuck", "escalate", f"only non-actionable work remains ({', '.join(bits)}); unblock or map states to proceed")

    # 5. Ready work exists -> dispatch, gated by the dispatch budget.
    if ready > 0:
        dispatch_left = budget["max_dispatched"] - budget["dispatched"]
        if dispatch_left <= 0:
            if in_flight > 0:
                return verdict("continue", "wait", "dispatch budget reached; waiting for in-flight work to finish")
            return verdict("stuck", "escalate", "dispatch budget reached with ready work remaining and nothing in flight")
        wave = ready_ids[: min(wave_size, dispatch_left)]
        if not wave:
            # ready work but an empty wave (wave_size<1, or ready issues with no
            # identifiers) cannot make progress -- escalate instead of spinning.
            return verdict("stuck", "escalate", f"{ready} ready issue(s) but the dispatch wave is empty (wave_size={wave_size} or missing identifiers); fix the config")
        return verdict("continue", "dispatch", f"{ready} ready issue(s); dispatch next {len(wave)}", wave)

    # 6. Nothing ready or in flight, but backlog has work -> activate some.
    if ready == 0 and in_flight == 0 and backlog > 0:
        return verdict("continue", "activate_backlog", f"{backlog} backlog issue(s); activate the next ones into a ready state")

    # 7. Work is in flight and progressing (stall guard already passed) -> wait.
    return verdict("continue", "wait", f"{in_flight} issue(s) in flight; waiting for progress")


def should_wake_planner(verdict: Verdict, has_open_planner: bool) -> bool:
    """Whether a merge-trigger should wake (create) a planner issue this run.

    True only when the loop wants more work shaped (continue -> dispatch or
    activate_backlog) AND no planner issue is already open. The open-planner
    check is what keeps a push-triggered Action from spawning a planner per push
    -- the idempotency guard that prevents a runaway backlog.
    """
    if has_open_planner:
        return False
    return verdict.verdict == "continue" and verdict.action in ("dispatch", "activate_backlog")


def elapsed_minutes_from(ledger: dict[str, Any], now: float | None = None) -> float | None:
    started = ledger.get("started_at")
    if not isinstance(started, (int, float)):
        return None
    now = time.time() if now is None else now
    return max(0.0, (now - float(started)) / 60.0)


def parse_counts_arg(raw: str) -> tuple[dict[str, int], list[str]]:
    """Parse ``ready=2,in_flight=1,...`` into counts + synthetic ready ids.

    Lets the verdict be exercised offline (preflight, demos, tests) without a
    live Linear project. Synthetic ready identifiers (READY-1, READY-2, ...) make
    the dispatch wave inspectable.
    """
    counts = {bucket: 0 for bucket in DEFAULT_STATES}
    counts["other"] = 0
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"bad --counts segment '{pair}' (expected name=number)")
        key, value = pair.split("=", 1)
        key = key.strip()
        if key not in counts:
            raise ValueError(f"unknown bucket '{key}' in --counts")
        number = int(value.strip())
        if number < 0:
            raise ValueError(f"negative count for '{key}' in --counts")
        counts[key] = number
    ready_ids = [f"READY-{i + 1}" for i in range(counts.get("ready", 0))]
    return counts, ready_ids


def self_test() -> int:
    base = default_ledger("ship the thing", "proj", 1000.0, {})

    # Dispatch when ready work exists, bounded by wave_size.
    counts = {"backlog": 0, "ready": 8, "in_flight": 0, "done": 0, "blocked": 0, "dropped": 0, "other": 0}
    ready_ids = [f"READY-{i+1}" for i in range(8)]
    v = decide(counts, ready_ids, {**base, "laps": 1}, 0.0)
    assert v.verdict == "continue" and v.action == "dispatch", v.to_dict()
    assert v.next_wave == ["READY-1", "READY-2", "READY-3", "READY-4", "READY-5"], v.next_wave

    # Wait when work is only in flight and progressing.
    counts = {"backlog": 0, "ready": 0, "in_flight": 3, "done": 1, "blocked": 0, "dropped": 0, "other": 0}
    v = decide(counts, [], {**base, "laps": 2}, 5.0)
    assert v.verdict == "continue" and v.action == "wait", v.to_dict()

    # Activate backlog when nothing ready or in flight but backlog remains.
    counts = {"backlog": 4, "ready": 0, "in_flight": 0, "done": 2, "blocked": 0, "dropped": 0, "other": 0}
    v = decide(counts, [], {**base, "laps": 3}, 5.0)
    assert v.verdict == "continue" and v.action == "activate_backlog", v.to_dict()

    # Done when nothing pending remains.
    counts = {"backlog": 0, "ready": 0, "in_flight": 0, "done": 9, "blocked": 0, "dropped": 2, "other": 0}
    v = decide(counts, [], {**base, "laps": 4}, 5.0)
    assert v.verdict == "done" and v.action == "stop", v.to_dict()

    # Blocked-only remaining -> stuck.
    counts = {"backlog": 0, "ready": 0, "in_flight": 0, "done": 3, "blocked": 2, "dropped": 0, "other": 0}
    v = decide(counts, [], {**base, "laps": 5}, 5.0)
    assert v.verdict == "stuck" and v.action == "escalate", v.to_dict()

    # Lap budget exhausted -> stuck even with ready work.
    counts = {"backlog": 0, "ready": 5, "in_flight": 0, "done": 0, "blocked": 0, "dropped": 0, "other": 0}
    v = decide(counts, ["READY-1"], {**base, "laps": 51, "max_laps": 50}, 5.0)
    assert v.verdict == "stuck" and "lap budget" in v.reason, v.to_dict()

    # Wall-clock budget exhausted -> stuck.
    v = decide(counts, ["READY-1"], {**base, "laps": 2, "max_wall_clock_minutes": 60}, 60.0)
    assert v.verdict == "stuck" and "time budget" in v.reason, v.to_dict()

    # Planner depth exceeded -> stuck.
    v = decide(counts, ["READY-1"], {**base, "laps": 2, "planner_depth": 3, "max_planner_depth": 2}, 5.0)
    assert v.verdict == "stuck" and "planner depth" in v.reason, v.to_dict()

    # Dispatch budget spent, work in flight -> wait (not failure).
    counts = {"backlog": 0, "ready": 5, "in_flight": 2, "done": 0, "blocked": 0, "dropped": 0, "other": 0}
    v = decide(counts, ["READY-1"], {**base, "laps": 2, "dispatched": 100, "max_dispatched": 100}, 5.0)
    assert v.verdict == "continue" and v.action == "wait", v.to_dict()

    # Dispatch budget spent, nothing in flight -> stuck.
    counts = {"backlog": 0, "ready": 5, "in_flight": 0, "done": 0, "blocked": 0, "dropped": 0, "other": 0}
    v = decide(counts, ["READY-1"], {**base, "laps": 2, "dispatched": 100, "max_dispatched": 100}, 5.0)
    assert v.verdict == "stuck" and "dispatch budget" in v.reason, v.to_dict()

    # Stall guard: stall_laps at threshold -> stuck.
    counts = {"backlog": 0, "ready": 0, "in_flight": 2, "done": 1, "blocked": 0, "dropped": 0, "other": 0}
    v = decide(counts, [], {**base, "laps": 6, "stall_laps": 3, "stall_threshold": 3}, 5.0)
    assert v.verdict == "stuck" and "stalled" in v.reason, v.to_dict()

    # Unmapped Linear states are pending, not done: a board with only `other` work
    # must escalate, never silently report the goal finished.
    counts = {"backlog": 0, "ready": 0, "in_flight": 0, "done": 1, "blocked": 0, "dropped": 0, "other": 3}
    v = decide(counts, [], {**base, "laps": 1}, 5.0)
    assert v.verdict == "stuck" and "unmapped" in v.reason, v.to_dict()

    # An empty dispatch wave (wave_size 0) with ready work escalates, not spins.
    counts = {"backlog": 0, "ready": 5, "in_flight": 0, "done": 0, "blocked": 0, "dropped": 0, "other": 0}
    v = decide(counts, ["READY-1"], {**base, "laps": 1, "wave_size": 0}, 0.0)
    assert v.verdict == "stuck" and "empty" in v.reason, v.to_dict()

    # advance_ledger: done progress resets the stall counter.
    led = advance_ledger({**base, "stall_laps": 2, "last_done": 1}, {"done": 2, "in_flight": 1, "ready": 0})
    assert led["stall_laps"] == 0 and led["laps"] == 1 and led["last_done"] == 2, led

    # advance_ledger: no progress with in-flight increments the stall.
    led = advance_ledger({**base, "stall_laps": 1, "last_done": 5}, {"done": 5, "in_flight": 2, "ready": 0})
    assert led["stall_laps"] == 2, led

    # advance_ledger: active dispatch (dispatched rose) resets the stall, even with
    # no done progress yet.
    led = advance_ledger({**base, "stall_laps": 2, "last_done": 5, "dispatched": 3, "last_dispatched": 0}, {"done": 5, "in_flight": 2, "ready": 3})
    assert led["stall_laps"] == 0, led

    # advance_ledger: ready work NOT being dispatched (dispatch flat) still stalls --
    # this is the over-dispatch-budget wedge the old `ready==0` guard missed.
    led = advance_ledger({**base, "stall_laps": 1, "last_done": 5, "dispatched": 3, "last_dispatched": 3}, {"done": 5, "in_flight": 2, "ready": 3})
    assert led["stall_laps"] == 2, led

    # Negative offline counts are rejected before they can cancel pending work.
    try:
        parse_counts_arg("backlog=5,blocked=-5")
        raise AssertionError("expected ValueError on negative count")
    except ValueError:
        pass

    # bucket_counts: case-insensitive mapping, unknown -> other, ready ids sorted.
    issues = [("A-2", "Todo"), ("A-1", "todo"), ("A-3", "In Review"), ("A-4", "Done"), ("A-5", "Triaging")]
    counts, ready_ids = bucket_counts(issues, DEFAULT_STATES)
    assert counts["ready"] == 2 and counts["in_flight"] == 1 and counts["done"] == 1 and counts["other"] == 1, counts
    assert ready_ids == ["A-1", "A-2"], ready_ids

    # should_wake_planner: only on continue+shape, only when none open.
    shape = decide(
        {"backlog": 0, "ready": 3, "in_flight": 0, "done": 0, "blocked": 0, "dropped": 0, "other": 0},
        ["READY-1"], {**base, "laps": 1}, 0.0,
    )
    assert should_wake_planner(shape, has_open_planner=False) is True
    assert should_wake_planner(shape, has_open_planner=True) is False
    wait_v = decide(
        {"backlog": 0, "ready": 0, "in_flight": 2, "done": 0, "blocked": 0, "dropped": 0, "other": 0},
        [], {**base, "laps": 1}, 0.0,
    )
    assert should_wake_planner(wait_v, has_open_planner=False) is False

    # parse_counts_arg round-trips into the verdict path.
    counts, ready_ids = parse_counts_arg("ready=2,in_flight=1")
    assert counts["ready"] == 2 and ready_ids == ["READY-1", "READY-2"], (counts, ready_ids)

    print(json.dumps({
        "ok": True,
        "checks": [
            "dispatch_wave_bounded", "wait_in_flight", "activate_backlog", "done_empty",
            "blocked_stuck", "lap_budget", "wall_clock_budget", "planner_depth",
            "dispatch_budget_wait", "dispatch_budget_stuck", "stall_guard",
            "unmapped_state_pending", "empty_wave_stuck",
            "advance_done_resets", "advance_stall_increments", "advance_dispatch_resets",
            "advance_idle_ready_stalls", "negative_counts_rejected",
            "bucket_counts", "should_wake_planner", "parse_counts",
        ],
    }, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Goal-state convergence + budget check for autonomous goal loops.")
    parser.add_argument("--ledger", default=".orchestration/goal-state.json", help="Path to the budget ledger JSON.")
    parser.add_argument("--init", action="store_true", help="Create a fresh ledger from --goal/--project-slug and the budget flags, then exit.")
    parser.add_argument("--goal", help="Goal statement (required with --init).")
    parser.add_argument("--project-slug", help="Linear project slug to read state from (required with --init).")
    parser.add_argument("--max-laps", type=int, help="Hard cap on loop laps.")
    parser.add_argument("--max-dispatched", type=int, help="Hard cap on total issues dispatched.")
    parser.add_argument("--max-planner-depth", type=int, help="Hard cap on planner recursion depth.")
    parser.add_argument("--max-wall-clock-minutes", type=int, help="Hard cap on wall-clock minutes.")
    parser.add_argument("--wave-size", type=int, help="Issues to dispatch per lap.")
    parser.add_argument("--stall-threshold", type=int, help="Laps with no progress before declaring stuck.")
    parser.add_argument("--record", action="store_true", help="Advance the ledger one lap (stall + lap accounting) and write it back.")
    parser.add_argument("--dispatched", type=int, default=0, help="Add this many to the dispatched counter (use when you actually dispatch).")
    parser.add_argument("--planner-depth", type=int, help="Set the current planner depth in the ledger.")
    parser.add_argument("--mark-done", action="store_true", help="Mark the goal done in the ledger (forces a done verdict).")
    parser.add_argument("--reset-stall", action="store_true", help="Zero the stall counter to resume after a stall escalation you have investigated (e.g. a slow-but-healthy wave). Consider raising --stall-threshold at the same time.")
    parser.add_argument("--set-goal", help="Update the stored goal statement.")
    parser.add_argument("--counts", help="Offline counts (e.g. 'ready=2,in_flight=1') instead of querying Linear. For demos/tests.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--self-test", action="store_true", help="Run local decision-logic tests and exit.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    ledger_path = Path(args.ledger).expanduser()

    if args.init:
        if not args.goal or not args.project_slug:
            parser.error("--init requires --goal and --project-slug")
        overrides = {
            "max_laps": args.max_laps,
            "max_dispatched": args.max_dispatched,
            "max_planner_depth": args.max_planner_depth,
            "max_wall_clock_minutes": args.max_wall_clock_minutes,
            "wave_size": args.wave_size,
            "stall_threshold": args.stall_threshold,
        }
        ledger = default_ledger(args.goal, args.project_slug, time.time(), overrides)
        problems = validate_budget(ledger)
        if problems:
            parser.error("; ".join(problems))
        save_ledger(ledger_path, ledger)
        print(json.dumps({"initialized": str(ledger_path), "goal": args.goal, "project_slug": args.project_slug,
                          "budget": {k: ledger[k] for k in DEFAULT_BUDGET}}, indent=2))
        return EXIT_OK

    if not ledger_path.exists():
        parser.error(f"ledger not found at {ledger_path}; run with --init first")
    ledger = load_ledger(ledger_path)

    dirty = False
    if args.set_goal:
        ledger["goal"] = args.set_goal
        dirty = True
    if args.mark_done:
        ledger["goal_done"] = True
        dirty = True
    if args.planner_depth is not None:
        ledger["planner_depth"] = args.planner_depth
        dirty = True
    if args.reset_stall:
        ledger["stall_laps"] = 0
        dirty = True

    # Allow retuning caps on a live run (e.g. raise --stall-threshold when resuming
    # after a slow-but-healthy wave escalated). Re-validate so a retune cannot set a
    # degenerate cap mid-run.
    budget_overrides = {
        "max_laps": args.max_laps,
        "max_dispatched": args.max_dispatched,
        "max_planner_depth": args.max_planner_depth,
        "max_wall_clock_minutes": args.max_wall_clock_minutes,
        "wave_size": args.wave_size,
        "stall_threshold": args.stall_threshold,
    }
    if any(v is not None for v in budget_overrides.values()):
        for key, val in budget_overrides.items():
            if val is not None:
                ledger[key] = val
        problems = validate_budget(ledger)
        if problems:
            parser.error("; ".join(problems))
        dirty = True

    # Gather counts: offline (--counts) or from Linear.
    if args.counts:
        try:
            counts, ready_ids = parse_counts_arg(args.counts)
        except ValueError as exc:
            parser.error(str(exc))
        truncated = False
    else:
        api_key = os.environ.get("LINEAR_API_KEY")
        if not api_key:
            parser.error("LINEAR_API_KEY is required (or pass --counts for an offline check)")
        project_slug = ledger.get("project_slug")
        if not project_slug:
            parser.error("ledger has no project_slug; re-init with --project-slug")
        states_cfg = ledger.get("states") or DEFAULT_STATES
        issue_states, truncated = fetch_issue_states(api_key, project_slug)
        counts, ready_ids = bucket_counts(issue_states, states_cfg)

    if args.dispatched:
        ledger["dispatched"] = int(ledger.get("dispatched", 0)) + args.dispatched
        dirty = True
    if args.record:
        ledger = advance_ledger(ledger, counts)
        dirty = True

    elapsed = elapsed_minutes_from(ledger)
    verdict = decide(counts, ready_ids, ledger, elapsed)

    if dirty:
        save_ledger(ledger_path, ledger)

    warnings = []
    if elapsed is None:
        warnings.append("wall-clock budget disabled: ledger has no valid started_at")

    payload = verdict.to_dict()
    if truncated:
        payload["truncated"] = True
    if warnings:
        payload["warnings"] = warnings
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"{verdict.verdict.upper()} ({verdict.action}): {verdict.reason}")
        print(f"  counts: {counts}")
        if verdict.next_wave:
            print(f"  next wave: {', '.join(verdict.next_wave)}")
        b = verdict.budget
        print(f"  budget: lap {b['laps']}/{b['max_laps']}, dispatched {b['dispatched']}/{b['max_dispatched']}, "
              f"depth {b['planner_depth']}/{b['max_planner_depth']}, stall {b['stall_laps']}/{b['stall_threshold']}")
        if truncated:
            print("  WARNING: issue list truncated; counts may be incomplete")
        for warning in warnings:
            print(f"  WARNING: {warning}")
    return verdict.exit_code()


if __name__ == "__main__":
    sys.exit(main())
