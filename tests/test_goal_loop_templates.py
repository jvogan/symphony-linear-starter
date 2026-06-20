"""Tests for the rendered autonomous-goal-loop artifacts.

These are templates (no live repo), so the tests pin the invariants that make
them correct and safe: valid YAML, the read-only/least-privilege posture of the
merge-trigger Action, the planner's recursion fences, the loud `stuck` exit, and
that secrets are referenced rather than inlined. Plus the bootstrap integration:
`--with-goal-loop` renders exactly the three artifacts, and nothing without it.
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
SKILL_DIR = REPO_ROOT / "skills" / "symphony-linear-orchestrator"
BOOTSTRAP_PATH = SKILL_DIR / "scripts" / "bootstrap.py"
TEMPLATE_DIR = SKILL_DIR / "assets" / "templates"
GHA_TMPL = TEMPLATE_DIR / "goal-loop.gha.yml.tmpl"
PLANNER_TMPL = TEMPLATE_DIR / "planner.WORKFLOW.md.tmpl"
PROMPT_TMPL = TEMPLATE_DIR / "goal-loop.PROMPT.md.tmpl"


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap_goal_mod", BOOTSTRAP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap_goal_mod"] = module
    spec.loader.exec_module(module)
    return module


bootstrap = load_bootstrap()

VALUES = {
    "WORKFLOW_NAME": "wave1",
    "CLONE_URL": "git@github.com:acme/repo.git",
    "LINEAR_PROJECT_SLUG": "proj",
    "REPO_NAME": "repo",
    "ISSUE_LABEL": "sym:medium",
    "MODEL": "gpt-5.4-mini",
    "REASONING_EFFORT": "medium",
    "MAX_CONCURRENT_AGENTS": "1",
    "REQUIRED_BRANCH": "main",
    "REQUIRED_PATHS_JSON": '["README.md"]',
    "GITHUB_REPO": "acme/repo",
    "RELEASE_CRON": "*/10 * * * *",
    "GOAL": "Ship the X milestone",
    "GOAL_HEARTBEAT_CRON": "0 */6 * * *",
}


def render(path):
    return bootstrap.render(path.read_text(), VALUES)


class MergeTriggerActionTests(unittest.TestCase):
    def setUp(self):
        self.text = render(GHA_TMPL)

    def test_no_unrendered_placeholders(self):
        self.assertNotIn("__", self.text)

    def test_triggers_on_push_schedule_and_dispatch(self):
        self.assertIn("push:", self.text)
        self.assertIn('branches: ["main"]', self.text)
        self.assertIn("schedule:", self.text)
        self.assertIn('cron: "0 */6 * * *"', self.text)
        self.assertIn("workflow_dispatch:", self.text)

    def test_ignores_ledger_path_to_avoid_self_trigger(self):
        # A brain that commits the ledger must not retrigger this report in a loop.
        self.assertIn("paths-ignore:", self.text)
        self.assertIn(".orchestration/goal-state.json", self.text)

    def test_concurrency_guard(self):
        self.assertIn("concurrency:", self.text)
        self.assertIn("group: goal-loop-main", self.text)

    def test_least_privilege_read_only(self):
        # The reporter never writes to the repo; contents: read, no write scopes.
        self.assertIn("contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertNotIn("pull-requests: write", self.text)

    def test_runs_goal_state_read_only(self):
        self.assertIn("scripts/goal_state.py", self.text)
        # Read-only: the actual invocation must not advance/commit the ledger from
        # CI. Check the command line itself, not the prose that explains why.
        self.assertIn('python3 "$script" --ledger "$ledger" --json', self.text)
        invocations = [ln for ln in self.text.splitlines() if 'python3 "$script"' in ln]
        self.assertTrue(invocations)
        for line in invocations:
            self.assertNotIn("--record", line)

    def test_secret_referenced_not_inlined(self):
        self.assertIn("LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}", self.text)
        self.assertNotIn("lin_api_", self.text)

    def test_stuck_fails_the_run(self):
        # rc==2 (stuck) must surface as a failed run, not a silent pass.
        self.assertIn('exit "$rc"', self.text)
        self.assertIn("::error::", self.text)

    def test_guards_missing_files(self):
        self.assertIn("LINEAR_API_KEY secret is not set", self.text)
        self.assertIn('[ -f "$script" ]', self.text)
        self.assertIn('[ -f "$ledger" ]', self.text)

    def test_planner_wake_is_opt_in_only(self):
        # The doc promise: CI does not mutate Linear on every push by default.
        self.assertIn("opt-in", self.text)
        self.assertIn("references/planner-lane.md", self.text)

    def test_is_valid_yaml(self):
        try:
            import yaml  # type: ignore
        except ImportError:
            self.skipTest("pyyaml not available")
        parsed = yaml.safe_load(self.text)
        self.assertIn("jobs", parsed)
        self.assertIn("concurrency", parsed)
        self.assertIs(parsed["concurrency"]["cancel-in-progress"], True)
        self.assertEqual(parsed["permissions"]["contents"], "read")


class PlannerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = render(PLANNER_TMPL)

    def test_no_unrendered_placeholders(self):
        self.assertNotIn("__", self.text)

    def test_routed_by_planner_label(self):
        self.assertIn('labels: ["sym:planner"]', self.text)

    def test_recursion_fences_present(self):
        self.assertIn("max_issues_per_plan: 5", self.text)
        self.assertIn("max_planner_depth: 2", self.text)

    def test_consults_convergence_before_planning(self):
        self.assertIn("scripts/goal_state.py", self.text)
        self.assertIn("do not create issues", self.text.lower())

    def test_does_not_write_code(self):
        self.assertIn("not to write product code", self.text)
        self.assertIn("not PRs", self.text)

    def test_creates_issues_in_backlog_not_self_activated(self):
        self.assertIn("Backlog", self.text)
        self.assertIn("issue_schema.py", self.text)

    def test_strong_planner_model(self):
        self.assertIn("gpt-5.4", self.text)
        self.assertIn("model_reasoning_effort=high", self.text)

    def test_narrow_env_allowlist(self):
        self.assertIn('shell_environment_policy.include_only=["LINEAR_API_KEY"]', self.text)

    def test_frontmatter_is_valid_yaml(self):
        try:
            import yaml  # type: ignore
        except ImportError:
            self.skipTest("pyyaml not available")
        frontmatter = self.text.split("---", 2)[1]
        parsed = yaml.safe_load(frontmatter)
        self.assertEqual(parsed["planner"]["max_planner_depth"], 2)
        self.assertEqual(parsed["tracker"]["issue_filters"]["labels"], ["sym:planner"])


class BrainPromptTests(unittest.TestCase):
    def setUp(self):
        self.text = render(PROMPT_TMPL)

    def test_no_unrendered_placeholders(self):
        self.assertNotIn("__", self.text)

    def test_embeds_goal_and_project(self):
        self.assertIn("Ship the X milestone", self.text)
        self.assertIn("proj", self.text)

    def test_lap_records_and_reads_state(self):
        self.assertIn("goal_state.py", self.text)
        self.assertIn("--record", self.text)
        self.assertIn("--init", self.text)

    def test_obeys_stuck_and_budget(self):
        self.assertIn("stuck", self.text.lower())
        self.assertIn("budget", self.text.lower())
        self.assertIn("stop the loop", self.text.lower())

    def test_gated_default_with_flip_documented(self):
        self.assertIn("gated", self.text.lower())
        self.assertIn("Auto", self.text)
        self.assertIn("release:ready", self.text)

    def test_records_dispatch_for_budget(self):
        self.assertIn("--dispatched", self.text)


class BootstrapIntegrationTests(unittest.TestCase):
    def _run_bootstrap(self, extra_args):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / ".git").mkdir()
            (target / "README.md").write_text("# fixture\n")
            argv = [
                "bootstrap.py",
                "--target-repo", str(target),
                "--workflow-name", "wave1",
                "--clone-url", "git@github.com:acme/repo.git",
                "--linear-project-slug", "proj",
                "--required-branch", "main",
                "--required-path", "README.md",
                *extra_args,
            ]
            buf = io.StringIO()
            with mock.patch.object(sys, "argv", argv), redirect_stdout(buf):
                rc = bootstrap.main()
            self.assertEqual(rc, 0)
            return json.loads(buf.getvalue())

    def _dests(self, manifest):
        return {entry["destination"].split("/.orchestration/")[-1] for entry in manifest["files"]}

    def test_goal_loop_renders_three_artifacts(self):
        manifest = self._run_bootstrap(["--with-goal-loop", "--goal", "Ship X"])
        self.assertTrue(manifest["goal_loop"])
        self.assertEqual(manifest["goal"], "Ship X")
        dests = self._dests(manifest)
        self.assertIn("goal-loop.PROMPT.md", dests)
        self.assertIn("goal-loop.gha.yml", dests)
        self.assertIn("planner.WORKFLOW.md", dests)

    def test_no_goal_loop_artifacts_without_flag(self):
        manifest = self._run_bootstrap([])
        self.assertFalse(manifest["goal_loop"])
        self.assertIsNone(manifest["goal"])
        dests = self._dests(manifest)
        self.assertNotIn("goal-loop.PROMPT.md", dests)
        self.assertNotIn("planner.WORKFLOW.md", dests)

    def test_goal_embedded_in_rendered_prompt(self):
        manifest = self._run_bootstrap(["--with-goal-loop", "--goal", "Ship the Q3 thing"])
        prompt = next(e["rendered"] for e in manifest["files"] if e["destination"].endswith("goal-loop.PROMPT.md"))
        self.assertIn("Ship the Q3 thing", prompt)


if __name__ == "__main__":
    unittest.main()
