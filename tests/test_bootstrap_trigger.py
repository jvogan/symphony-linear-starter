"""Tests for the rendered Release Manager scheduled-trigger GitHub Action.

The trigger is a template (no live repo), so these tests pin the invariants that
make it correct and safe: the single-writer concurrency guard, placeholder
substitution, the secret referenced (never inlined), and valid YAML.
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
TEMPLATE_PATH = SKILL_DIR / "assets" / "templates" / "release-manager.gha.yml.tmpl"
EXAMPLE_PATH = SKILL_DIR / "assets" / "examples" / "auto-merge-on-label.yml"


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap_mod", BOOTSTRAP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap_mod"] = module
    spec.loader.exec_module(module)
    return module


bootstrap = load_bootstrap()


def render_sample(cron="*/5 * * * *", branch="main"):
    values = {
        "RELEASE_CRON": cron,
        "REQUIRED_BRANCH": branch,
        "GITHUB_REPO": "acme/repo",
        "REPO_NAME": "repo",
    }
    return bootstrap.render(TEMPLATE_PATH.read_text(), values)


class TriggerRenderTests(unittest.TestCase):
    def test_no_unrendered_placeholders(self):
        rendered = render_sample()
        self.assertNotIn("__", rendered, "a __PLACEHOLDER__ was left unrendered")

    def test_substitutes_cron_and_branch(self):
        rendered = render_sample(cron="*/7 * * * *", branch="release")
        self.assertIn('cron: "*/7 * * * *"', rendered)
        self.assertIn("group: release-manager-release", rendered)

    def test_single_writer_concurrency_guard(self):
        # The load-bearing invariant: ephemeral runners need a cross-run lock.
        rendered = render_sample()
        self.assertIn("concurrency:", rendered)
        self.assertIn("cancel-in-progress: false", rendered)

    def test_triggers_are_schedule_and_manual(self):
        rendered = render_sample()
        self.assertIn("schedule:", rendered)
        self.assertIn("workflow_dispatch:", rendered)

    def test_secret_referenced_not_inlined(self):
        rendered = render_sample()
        self.assertIn("LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}", rendered)
        # A real key must never be baked into the template.
        self.assertNotIn("lin_api_", rendered)

    def test_least_privilege_permissions(self):
        rendered = render_sample()
        self.assertIn("permissions:", rendered)
        self.assertIn("contents: write", rendered)
        self.assertIn("pull-requests: write", rendered)

    def test_invokes_the_lane_in_apply_json(self):
        rendered = render_sample()
        self.assertIn("skills/symphony-linear-orchestrator/scripts/release_manager.py", rendered)
        self.assertIn("--apply --json", rendered)

    def test_guards_secret_and_files_before_running(self):
        rendered = render_sample()
        self.assertIn("LINEAR_API_KEY secret is not set", rendered)
        self.assertIn('[ -f "$script" ]', rendered)
        self.assertIn('[ -f "$workflow" ]', rendered)

    def test_stall_detection_signal(self):
        # A stuck in_flight burst must fail loudly, not spin silently.
        rendered = render_sample()
        self.assertIn("stuck in_flight", rendered)
        self.assertIn("last_inflight", rendered)

    def test_is_valid_yaml(self):
        try:
            import yaml  # type: ignore
        except ImportError:
            self.skipTest("pyyaml not available")
        parsed = yaml.safe_load(render_sample())
        # NB: YAML 1.1 parses the bare key `on` as boolean True, so assert on the
        # keys that are unaffected by that quirk.
        self.assertIn("jobs", parsed)
        self.assertIn("concurrency", parsed)
        self.assertIs(parsed["concurrency"]["cancel-in-progress"], False)


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

    def _gha_entry(self, manifest):
        for entry in manifest["files"]:
            if entry["destination"].endswith("release-manager.gha.yml"):
                return entry
        return None

    def test_release_manager_renders_trigger_sample(self):
        manifest = self._run_bootstrap(["--with-release-manager", "--release-cron", "*/3 * * * *"])
        entry = self._gha_entry(manifest)
        self.assertIsNotNone(entry, "the gha trigger sample was not rendered")
        self.assertIn("concurrency:", entry["rendered"])
        self.assertIn('cron: "*/3 * * * *"', entry["rendered"])
        self.assertIn("group: release-manager-main", entry["rendered"])
        self.assertEqual(manifest["release_cron"], "*/3 * * * *")

    def test_no_trigger_without_release_manager(self):
        manifest = self._run_bootstrap([])
        self.assertIsNone(self._gha_entry(manifest), "trigger sample rendered without --with-release-manager")
        self.assertIsNone(manifest["release_cron"])


class GithubNativeExampleTests(unittest.TestCase):
    """The standalone, Linear-less auto-merge-on-label sample (assets/examples/)."""

    def setUp(self):
        self.text = EXAMPLE_PATH.read_text()

    def test_triggers_on_pr_labeled(self):
        self.assertIn("pull_request:", self.text)
        self.assertIn("types: [labeled]", self.text)

    def test_guards_label_and_skips_drafts(self):
        # Only the ready label, and never a draft (auto-merge rejects drafts).
        self.assertIn("github.event.label.name == 'release:ready'", self.text)
        self.assertIn("!github.event.pull_request.draft", self.text)

    def test_enables_auto_merge(self):
        self.assertIn("gh pr merge --auto", self.text)

    def test_least_privilege_permissions(self):
        self.assertIn("contents: write", self.text)
        self.assertIn("pull-requests: write", self.text)

    def test_secret_referenced_not_inlined(self):
        self.assertIn("${{ secrets.GITHUB_TOKEN }}", self.text)
        self.assertNotIn("ghp_", self.text)
        self.assertNotIn("github_pat_", self.text)

    def test_no_single_writer_lock_needed(self):
        # Structural claim of the doc: per-PR, idempotent -> no concurrency guard.
        self.assertNotIn("concurrency:", self.text)

    def test_is_valid_yaml(self):
        try:
            import yaml  # type: ignore
        except ImportError:
            self.skipTest("pyyaml not available")
        parsed = yaml.safe_load(self.text)
        self.assertIn("jobs", parsed)
        self.assertIn("permissions", parsed)


if __name__ == "__main__":
    unittest.main()
