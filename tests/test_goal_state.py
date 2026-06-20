"""Tests for goal_state.py -- the convergence + budget spine of the goal loop.

The decision logic is the one piece that keeps unattended autonomy from running
away, so it is tested as a matrix: every verdict path, every budget ceiling, the
stall guard, ledger advancement, bucketing, and the CLI round-trip (init ->
record -> verdict) with exit codes.
"""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "skills" / "symphony-linear-orchestrator" / "scripts" / "goal_state.py"


def load_module():
    spec = importlib.util.spec_from_file_location("goal_state_mod", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["goal_state_mod"] = module
    spec.loader.exec_module(module)
    return module


gs = load_module()


def counts(**kw):
    base = {b: 0 for b in gs.DEFAULT_STATES}
    base["other"] = 0
    base.update(kw)
    return base


def ledger(**kw):
    led = gs.default_ledger("goal", "proj", 1000.0, {})
    led["laps"] = 1  # past --init, mid-run
    led.update(kw)
    return led


class SelfTest(unittest.TestCase):
    def test_self_test_passes(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = gs.self_test()
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(buf.getvalue())["ok"])


class DecideMatrix(unittest.TestCase):
    def test_dispatch_when_ready(self):
        v = gs.decide(counts(ready=3), ["A-1", "A-2", "A-3"], ledger(), 0.0)
        self.assertEqual((v.verdict, v.action), ("continue", "dispatch"))
        self.assertEqual(v.next_wave, ["A-1", "A-2", "A-3"])

    def test_dispatch_wave_bounded_by_wave_size(self):
        ready = [f"A-{i}" for i in range(10)]
        v = gs.decide(counts(ready=10), ready, ledger(wave_size=4), 0.0)
        self.assertEqual(len(v.next_wave), 4)

    def test_wait_when_only_in_flight(self):
        v = gs.decide(counts(in_flight=2, done=1), [], ledger(), 5.0)
        self.assertEqual((v.verdict, v.action), ("continue", "wait"))

    def test_activate_backlog(self):
        v = gs.decide(counts(backlog=5, done=1), [], ledger(), 5.0)
        self.assertEqual((v.verdict, v.action), ("continue", "activate_backlog"))

    def test_done_when_nothing_pending(self):
        v = gs.decide(counts(done=9, dropped=3), [], ledger(), 5.0)
        self.assertEqual((v.verdict, v.action), ("done", "stop"))

    def test_done_when_goal_marked(self):
        v = gs.decide(counts(ready=5), ["A-1"], ledger(goal_done=True), 0.0)
        self.assertEqual(v.verdict, "done")

    def test_blocked_only_is_stuck(self):
        v = gs.decide(counts(blocked=2, done=3), [], ledger(), 5.0)
        self.assertEqual((v.verdict, v.action), ("stuck", "escalate"))
        self.assertIn("blocked", v.reason)

    def test_dropped_alone_is_done_not_stuck(self):
        # Dropped issues are gone, not pending -- they must not strand the loop.
        v = gs.decide(counts(done=2, dropped=4), [], ledger(), 5.0)
        self.assertEqual(v.verdict, "done")

    def test_unmapped_state_blocks_done(self):
        # Issues in a state mapped to no bucket (`other`) are pending: the loop
        # must escalate to map/resolve them, never silently report done.
        v = gs.decide(counts(done=1, other=3), [], ledger(), 5.0)
        self.assertEqual(v.verdict, "stuck")
        self.assertIn("unmapped", v.reason)

    def test_unmapped_with_in_flight_keeps_going(self):
        # Work is moving, so an unmapped issue does not escalate yet.
        v = gs.decide(counts(in_flight=2, other=1), [], ledger(), 5.0)
        self.assertEqual(v.verdict, "continue")

    def test_empty_wave_is_stuck(self):
        # wave_size 0 with ready work would dispatch nothing forever -> escalate.
        v = gs.decide(counts(ready=5), ["A-1"], ledger(wave_size=0), 0.0)
        self.assertEqual(v.verdict, "stuck")
        self.assertIn("empty", v.reason)

    def test_ready_with_no_identifiers_is_stuck(self):
        # ready>0 but no identifiers to dispatch is also an empty wave.
        v = gs.decide(counts(ready=3), [], ledger(), 0.0)
        self.assertEqual(v.verdict, "stuck")

    def test_in_flight_outranks_blocked_keeps_going(self):
        # Blocked work plus live work should wait, not escalate.
        v = gs.decide(counts(in_flight=1, blocked=2), [], ledger(), 5.0)
        self.assertEqual(v.verdict, "continue")


class BudgetCeilings(unittest.TestCase):
    def test_lap_budget_stuck(self):
        v = gs.decide(counts(ready=5), ["A-1"], ledger(laps=51, max_laps=50), 1.0)
        self.assertEqual(v.verdict, "stuck")
        self.assertIn("lap budget", v.reason)

    def test_wall_clock_stuck(self):
        v = gs.decide(counts(ready=5), ["A-1"], ledger(max_wall_clock_minutes=60), 60.0)
        self.assertEqual(v.verdict, "stuck")
        self.assertIn("time budget", v.reason)

    def test_planner_depth_stuck(self):
        v = gs.decide(counts(ready=5), ["A-1"], ledger(planner_depth=3, max_planner_depth=2), 1.0)
        self.assertEqual(v.verdict, "stuck")
        self.assertIn("planner depth", v.reason)

    def test_dispatch_budget_waits_when_in_flight(self):
        v = gs.decide(counts(ready=5, in_flight=2), ["A-1"], ledger(dispatched=100, max_dispatched=100), 1.0)
        self.assertEqual((v.verdict, v.action), ("continue", "wait"))

    def test_dispatch_budget_stuck_when_idle(self):
        v = gs.decide(counts(ready=5), ["A-1"], ledger(dispatched=100, max_dispatched=100), 1.0)
        self.assertEqual(v.verdict, "stuck")
        self.assertIn("dispatch budget", v.reason)

    def test_dispatch_wave_clamped_by_remaining_budget(self):
        # Only 2 dispatches left -> wave is 2 even though wave_size is 5.
        ready = [f"A-{i}" for i in range(5)]
        v = gs.decide(counts(ready=5), ready, ledger(dispatched=98, max_dispatched=100, wave_size=5), 1.0)
        self.assertEqual((v.verdict, v.action), ("continue", "dispatch"))
        self.assertEqual(len(v.next_wave), 2)

    def test_budget_check_precedes_done(self):
        # A blown budget should escalate even if the board looks finishable.
        v = gs.decide(counts(ready=1), ["A-1"], ledger(laps=99, max_laps=50), 1.0)
        self.assertEqual(v.verdict, "stuck")


class StallGuard(unittest.TestCase):
    def test_stall_threshold_reached_is_stuck(self):
        v = gs.decide(counts(in_flight=2, done=1), [], ledger(stall_laps=3, stall_threshold=3), 5.0)
        self.assertEqual(v.verdict, "stuck")
        self.assertIn("stalled", v.reason)

    def test_below_stall_threshold_waits(self):
        v = gs.decide(counts(in_flight=2, done=1), [], ledger(stall_laps=2, stall_threshold=3), 5.0)
        self.assertEqual(v.verdict, "continue")

    def test_advance_progress_resets_stall(self):
        led = gs.advance_ledger(ledger(stall_laps=2, last_done=1), {"done": 2, "in_flight": 1, "ready": 0})
        self.assertEqual(led["stall_laps"], 0)
        self.assertEqual(led["last_done"], 2)
        self.assertEqual(led["laps"], 2)

    def test_advance_no_progress_increments_stall(self):
        led = gs.advance_ledger(ledger(stall_laps=1, last_done=5), {"done": 5, "in_flight": 2, "ready": 0})
        self.assertEqual(led["stall_laps"], 2)

    def test_advance_active_dispatch_resets_stall(self):
        # dispatched rose since last lap -> progress, even with no new done.
        led = gs.advance_ledger(ledger(stall_laps=2, last_done=5, dispatched=3, last_dispatched=0),
                                {"done": 5, "in_flight": 2, "ready": 3})
        self.assertEqual(led["stall_laps"], 0)

    def test_advance_ready_but_idle_still_stalls(self):
        # Ready work present but dispatch is flat (over budget / wedged) -> stall
        # must keep climbing; the old ready==0 guard was blind to this.
        led = gs.advance_ledger(ledger(stall_laps=1, last_done=5, dispatched=3, last_dispatched=3),
                                {"done": 5, "in_flight": 2, "ready": 3})
        self.assertEqual(led["stall_laps"], 2)

    def test_advance_first_lap_never_false_trips(self):
        # last_done starts at -1, so lap one always counts as progress.
        led = gs.advance_ledger(ledger(stall_laps=0, last_done=-1), {"done": 0, "in_flight": 2, "ready": 0})
        self.assertEqual(led["stall_laps"], 0)


class Bucketing(unittest.TestCase):
    def test_case_insensitive_and_unknown_is_other(self):
        issues = [("A-2", "Todo"), ("A-1", "todo"), ("A-3", "In Review"), ("A-4", "Done"), ("A-5", "Triaging")]
        c, ready_ids = gs.bucket_counts(issues, gs.DEFAULT_STATES)
        self.assertEqual(c["ready"], 2)
        self.assertEqual(c["in_flight"], 1)
        self.assertEqual(c["done"], 1)
        self.assertEqual(c["other"], 1)
        self.assertEqual(ready_ids, ["A-1", "A-2"])  # sorted

    def test_unknown_state_never_counts_as_pending_done(self):
        c, _ = gs.bucket_counts([("A-1", "Weird")], gs.DEFAULT_STATES)
        self.assertEqual(c["other"], 1)
        self.assertEqual(c["done"], 0)


class WakePlanner(unittest.TestCase):
    def _shape(self):
        return gs.decide(counts(ready=3), ["A-1"], ledger(), 0.0)

    def test_wakes_on_shape_when_none_open(self):
        self.assertTrue(gs.should_wake_planner(self._shape(), has_open_planner=False))

    def test_idempotent_when_planner_open(self):
        self.assertFalse(gs.should_wake_planner(self._shape(), has_open_planner=True))

    def test_no_wake_on_wait(self):
        wait_v = gs.decide(counts(in_flight=2), [], ledger(), 0.0)
        self.assertFalse(gs.should_wake_planner(wait_v, has_open_planner=False))

    def test_no_wake_on_done(self):
        done_v = gs.decide(counts(done=5), [], ledger(), 0.0)
        self.assertFalse(gs.should_wake_planner(done_v, has_open_planner=False))


class CliRoundTrip(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["goal_state.py", *argv]), redirect_stdout(buf):
            try:
                rc = gs.main()
            except SystemExit as exc:  # argparse parser.error
                return None, exc.code
        return buf.getvalue(), rc

    def test_init_then_dispatch_records_lap(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            out, rc = self._run(["--ledger", str(led), "--init", "--goal", "ship", "--project-slug", "p", "--max-laps", "7"])
            self.assertEqual(rc, 0)
            self.assertTrue(led.exists())
            self.assertEqual(json.loads(led.read_text())["max_laps"], 7)

            out, rc = self._run(["--ledger", str(led), "--record", "--dispatched", "3",
                                 "--counts", "ready=4,in_flight=1,done=2", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertEqual(payload["action"], "dispatch")
            data = json.loads(led.read_text())
            self.assertEqual(data["laps"], 1)
            self.assertEqual(data["dispatched"], 3)

    def test_stuck_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p", "--max-laps", "1"])
            # Force the lap count past the cap, then check.
            data = json.loads(led.read_text())
            data["laps"] = 5
            led.write_text(json.dumps(data))
            out, rc = self._run(["--ledger", str(led), "--counts", "ready=2", "--json"])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(out)["verdict"], "stuck")

    def test_done_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p"])
            out, rc = self._run(["--ledger", str(led), "--counts", "done=4", "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["verdict"], "done")

    def test_mark_done_persists_and_forces_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p"])
            out, rc = self._run(["--ledger", str(led), "--mark-done", "--counts", "ready=9", "--json"])
            self.assertEqual(json.loads(out)["verdict"], "done")
            self.assertTrue(json.loads(led.read_text())["goal_done"])

    def test_missing_ledger_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "absent.json"
            _, code = self._run(["--ledger", str(led), "--counts", "ready=1"])
            self.assertEqual(code, 2)  # argparse error exit

    def test_init_requires_goal_and_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            _, code = self._run(["--ledger", str(led), "--init", "--goal", "only-goal"])
            self.assertEqual(code, 2)

    def test_record_without_dispatch_advances_lap_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p"])
            self._run(["--ledger", str(led), "--record", "--counts", "in_flight=2,done=1", "--json"])
            data = json.loads(led.read_text())
            self.assertEqual(data["laps"], 1)
            self.assertEqual(data["dispatched"], 0)

    def test_init_rejects_degenerate_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            _, code = self._run(["--ledger", str(led), "--init", "--goal", "g",
                                 "--project-slug", "p", "--wave-size", "0"])
            self.assertEqual(code, 2)
            self.assertFalse(led.exists())  # not written when invalid

    def test_negative_counts_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p"])
            _, code = self._run(["--ledger", str(led), "--counts", "ready=-1"])
            self.assertEqual(code, 2)

    def test_missing_started_at_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p"])
            data = json.loads(led.read_text())
            del data["started_at"]
            led.write_text(json.dumps(data))
            out, rc = self._run(["--ledger", str(led), "--counts", "ready=2", "--json"])
            self.assertIn("warnings", json.loads(out))

    def test_reset_stall_clears_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p"])
            data = json.loads(led.read_text())
            data["stall_laps"] = 5
            led.write_text(json.dumps(data))
            self._run(["--ledger", str(led), "--reset-stall", "--counts", "in_flight=2", "--json"])
            self.assertEqual(json.loads(led.read_text())["stall_laps"], 0)

    def test_retune_stall_threshold_on_live_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p"])
            self._run(["--ledger", str(led), "--stall-threshold", "9", "--counts", "in_flight=1", "--json"])
            self.assertEqual(json.loads(led.read_text())["stall_threshold"], 9)

    def test_retune_rejects_degenerate_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p"])
            _, code = self._run(["--ledger", str(led), "--wave-size", "0", "--counts", "ready=1"])
            self.assertEqual(code, 2)

    def test_corrupt_numeric_field_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            led = Path(tmp) / "gs.json"
            self._run(["--ledger", str(led), "--init", "--goal", "g", "--project-slug", "p"])
            data = json.loads(led.read_text())
            data["max_laps"] = "fifty"  # corrupt
            led.write_text(json.dumps(data))
            out, rc = self._run(["--ledger", str(led), "--counts", "ready=2", "--json"])
            # Degrades to the default cap instead of throwing ValueError.
            self.assertEqual(json.loads(out)["budget"]["max_laps"], gs.DEFAULT_BUDGET["max_laps"])


if __name__ == "__main__":
    unittest.main()
