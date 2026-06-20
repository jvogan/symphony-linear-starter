import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "symphony-linear-orchestrator"
    / "scripts"
    / "release_manager.py"
)
spec = importlib.util.spec_from_file_location("release_manager", SCRIPT)
release_manager = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["release_manager"] = release_manager
spec.loader.exec_module(release_manager)

PREFLIGHT = SCRIPT.parent / "preflight.py"
spec_pf = importlib.util.spec_from_file_location("preflight", PREFLIGHT)
preflight = importlib.util.module_from_spec(spec_pf)
assert spec_pf.loader is not None
sys.modules["preflight"] = preflight
spec_pf.loader.exec_module(preflight)


class ReleaseManagerTests(unittest.TestCase):
    def make_issue(self):
        return release_manager.Issue(
            id="issue-id",
            identifier="TST-1",
            title="Test",
            url="",
            description="PR https://github.com/acme/repo/pull/2",
            state="Ready to Merge",
            labels=["release:ready"],
            comments=[],
            team_states={"Done": "done-id", "Todo": "todo-id", "Merging": "merging-id"},
        )

    def make_args(self, apply=False):
        return SimpleNamespace(
            repo="acme/repo",
            base_branch="main",
            ready_states=["Ready to Merge", "In Review"],
            done_state="Done",
            blocked_state="Todo",
            queued_state="Merging",
            apply=apply,
            delete_branch=True,
            merge_method=None,
            comment_mode="minimal",
        )

    def test_outcome_pr_url_takes_precedence(self):
        issue = release_manager.Issue(
            id="id",
            identifier="TST-1",
            title="Test",
            url="",
            description="Older PR https://github.com/acme/repo/pull/1",
            state="Ready to Merge",
            labels=["release:ready"],
            comments=[
                {
                    "body": "<!-- symphony-outcome\nstatus: success\npr_url: https://github.com/acme/repo/pull/2\n-->",
                    "createdAt": "2026-01-01T00:00:00Z",
                }
            ],
            team_states={"Done": "done"},
        )
        self.assertEqual(release_manager.choose_pr_url(issue), "https://github.com/acme/repo/pull/2")

    def test_label_filter_requires_all_configured_labels(self):
        self.assertTrue(release_manager.labels_match(["sym:app", "release:ready"], ["sym:app", "release:ready"]))
        self.assertFalse(release_manager.labels_match(["sym:app"], ["sym:app", "release:ready"]))

    def test_dry_run_would_queue_clean_pr(self):
        original_view = release_manager.gh_pr_view
        try:
            release_manager.gh_pr_view = lambda _url, _repo: {
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "headRefOid": "abc",
                "baseRefName": "main",
            }
            action = release_manager.process_issue(self.make_args(), "linear-key", self.make_issue())
            self.assertEqual(action.status, "would_queue")
            self.assertIn("gh pr merge --auto", action.message)
        finally:
            release_manager.gh_pr_view = original_view

    def test_wrong_base_branch_is_skipped(self):
        original_view = release_manager.gh_pr_view
        try:
            release_manager.gh_pr_view = lambda _url, _repo: {
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "headRefOid": "abc",
                "baseRefName": "develop",
            }
            action = release_manager.process_issue(self.make_args(), "linear-key", self.make_issue())
            self.assertEqual(action.status, "skipped")
            self.assertIn("expected main", action.message)
        finally:
            release_manager.gh_pr_view = original_view

    def test_merged_pr_would_finalize(self):
        original_view = release_manager.gh_pr_view
        try:
            release_manager.gh_pr_view = lambda _url, _repo: {
                "state": "MERGED",
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "baseRefName": "main",
            }
            action = release_manager.process_issue(self.make_args(), "linear-key", self.make_issue())
            self.assertEqual(action.status, "would_finalize")
        finally:
            release_manager.gh_pr_view = original_view

    def test_conflicted_pr_would_block(self):
        original_view = release_manager.gh_pr_view
        try:
            release_manager.gh_pr_view = lambda _url, _repo: {
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "DIRTY",
                "baseRefName": "main",
            }
            action = release_manager.process_issue(self.make_args(), "linear-key", self.make_issue())
            self.assertEqual(action.status, "would_block")
        finally:
            release_manager.gh_pr_view = original_view

    def test_apply_queues_with_head_guard_and_merge_method(self):
        original_view = release_manager.gh_pr_view
        original_enqueue = release_manager.gh_enqueue
        original_comment = release_manager.create_comment
        original_update = release_manager.update_issue_state
        calls = {}
        try:
            release_manager.gh_pr_view = lambda _url, _repo: {
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "headRefOid": "abc",
                "baseRefName": "main",
            }
            release_manager.gh_enqueue = lambda pr_url, repo, head_oid, delete_branch, merge_method, merge_queue=False: calls.update(
                pr_url=pr_url,
                repo=repo,
                head_oid=head_oid,
                delete_branch=delete_branch,
                merge_method=merge_method,
                merge_queue=merge_queue,
            )
            release_manager.create_comment = lambda *_args, **_kwargs: None
            release_manager.update_issue_state = lambda *_args, **_kwargs: None

            args = self.make_args(apply=True)
            args.merge_method = "squash"
            action = release_manager.process_issue(args, "linear-key", self.make_issue())
            self.assertEqual(action.status, "queued")
            self.assertEqual(calls["head_oid"], "abc")
            self.assertTrue(calls["delete_branch"])
            self.assertEqual(calls["merge_method"], "squash")
        finally:
            release_manager.gh_pr_view = original_view
            release_manager.gh_enqueue = original_enqueue
            release_manager.create_comment = original_comment
            release_manager.update_issue_state = original_update

    def test_enqueue_drops_delete_branch_in_queue_mode(self):
        # gh rejects --delete-branch when a merge queue is enabled; the lane must
        # omit it in queue mode and keep it for the no-queue (merge_method) path.
        original_run = release_manager.run
        captured = {}

        def fake_run(cmd, timeout=90):
            captured["cmd"] = list(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        try:
            release_manager.run = fake_run
            release_manager.gh_enqueue(
                "https://github.com/acme/repo/pull/1", "acme/repo", "abc", True, None, merge_queue=True
            )
            self.assertNotIn("--delete-branch", captured["cmd"])
            release_manager.gh_enqueue(
                "https://github.com/acme/repo/pull/1", "acme/repo", "abc", True, "squash", merge_queue=False
            )
            self.assertIn("--delete-branch", captured["cmd"])
        finally:
            release_manager.run = original_run

    def test_comment_mode_none_suppresses_linear_comment(self):
        original_view = release_manager.gh_pr_view
        original_enqueue = release_manager.gh_enqueue
        original_comment = release_manager.create_comment
        original_update = release_manager.update_issue_state
        calls = {"comments": 0}
        try:
            release_manager.gh_pr_view = lambda _url, _repo: {
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "headRefOid": "abc",
                "baseRefName": "main",
            }
            release_manager.gh_enqueue = lambda *_args, **_kwargs: None
            release_manager.create_comment = lambda *_args, **_kwargs: calls.update(comments=calls["comments"] + 1)
            release_manager.update_issue_state = lambda *_args, **_kwargs: None

            args = self.make_args(apply=True)
            args.comment_mode = "none"
            action = release_manager.process_issue(args, "linear-key", self.make_issue())
            self.assertEqual(action.status, "queued")
            self.assertEqual(calls["comments"], 0)
        finally:
            release_manager.gh_pr_view = original_view
            release_manager.gh_enqueue = original_enqueue
            release_manager.create_comment = original_comment
            release_manager.update_issue_state = original_update

    def test_already_queued_pr_is_not_reenqueued(self):
        # Re-running the lane must be a no-op for a PR that already has
        # auto-merge / merge-queue enabled, not a second enqueue.
        enqueue_calls = {"n": 0}
        with mock.patch.object(
            release_manager,
            "gh_pr_view",
            lambda _u, _r: {
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "BLOCKED",
                "headRefOid": "abc",
                "baseRefName": "main",
                "autoMergeRequest": {"enabledAt": "2026-01-01T00:00:00Z"},
            },
        ), mock.patch.object(
            release_manager, "gh_enqueue", lambda *a, **k: enqueue_calls.update(n=enqueue_calls["n"] + 1)
        ), mock.patch.object(
            release_manager, "update_issue_state", lambda *a, **k: None
        ), mock.patch.object(
            release_manager, "create_comment", lambda *a, **k: None
        ):
            action = release_manager.process_issue(self.make_args(apply=True), "key", self.make_issue())
        self.assertEqual(action.status, "queued")
        self.assertIn("already enabled", action.message)
        self.assertEqual(enqueue_calls["n"], 0)

    def test_enqueue_failure_returns_retry_not_error(self):
        # A single failed enqueue (e.g. the head moved) must not crash the
        # batch or count as a hard failure; it is retried next pass.
        def boom(*_a, **_k):
            raise RuntimeError("head moved under us")

        with mock.patch.object(
            release_manager,
            "gh_pr_view",
            lambda _u, _r: {
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "CLEAN",
                "headRefOid": "abc",
                "baseRefName": "main",
                "autoMergeRequest": None,
            },
        ), mock.patch.object(release_manager, "gh_enqueue", boom), mock.patch.object(
            release_manager, "update_issue_state", lambda *a, **k: None
        ), mock.patch.object(
            release_manager, "create_comment", lambda *a, **k: None
        ):
            action = release_manager.process_issue(self.make_args(apply=True), "key", self.make_issue())
        self.assertEqual(action.status, "retry")
        self.assertNotIn(action.status, release_manager.FAILURE_STATUSES)

    def test_queued_state_issue_is_in_flight_not_reenqueued(self):
        # An issue already in queued_state with an open PR must never be
        # re-enqueued (kills the evicted-PR infinite loop and double-enqueue).
        enqueue_calls = {"n": 0}
        issue = self.make_issue()
        issue.state = "Merging"  # already moved to queued_state on a prior pass
        with mock.patch.object(
            release_manager,
            "gh_pr_view",
            lambda _u, _r: {
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "BLOCKED",
                "headRefOid": "abc",
                "baseRefName": "main",
                "autoMergeRequest": None,
            },
        ), mock.patch.object(
            release_manager, "gh_enqueue", lambda *a, **k: enqueue_calls.update(n=enqueue_calls["n"] + 1)
        ):
            action = release_manager.process_issue(self.make_args(apply=True), "key", issue)
        self.assertEqual(action.status, "in_flight")
        self.assertEqual(enqueue_calls["n"], 0)

    def test_missing_done_state_is_misconfigured_and_does_not_comment(self):
        # A renamed/missing closeout state must fail loudly, not silently no-op
        # and re-post the merged comment every pass.
        comment_calls = {"n": 0}
        issue = self.make_issue()
        issue.team_states = {"Todo": "t", "Merging": "m"}  # 'Done' absent
        with mock.patch.object(
            release_manager,
            "gh_pr_view",
            lambda _u, _r: {"state": "MERGED", "isDraft": False, "mergeStateStatus": "CLEAN", "baseRefName": "main", "autoMergeRequest": None},
        ), mock.patch.object(
            release_manager, "update_issue_state", lambda *a, **k: None
        ), mock.patch.object(
            release_manager, "create_comment", lambda *a, **k: comment_calls.update(n=comment_calls["n"] + 1)
        ):
            action = release_manager.process_issue(self.make_args(apply=True), "key", issue)
        self.assertEqual(action.status, "misconfigured")
        self.assertIn(action.status, release_manager.FAILURE_STATUSES)
        self.assertEqual(comment_calls["n"], 0)

    def test_queued_issue_view_failure_is_retry_not_error(self):
        # A transient gh failure on an already-queued issue must not orphan it
        # (retry is counted as pending, so the drain loop keeps reconciling).
        issue = self.make_issue()
        issue.state = "Merging"

        def boom(*_a, **_k):
            raise RuntimeError("gh timeout")

        with mock.patch.object(release_manager, "gh_pr_view", boom):
            action = release_manager.process_issue(self.make_args(apply=True), "key", issue)
        self.assertEqual(action.status, "retry")
        self.assertNotIn(action.status, release_manager.FAILURE_STATUSES)

    def test_fresh_issue_view_failure_is_error(self):
        def boom(*_a, **_k):
            raise RuntimeError("gh 404")

        with mock.patch.object(release_manager, "gh_pr_view", boom):
            action = release_manager.process_issue(self.make_args(apply=True), "key", self.make_issue())
        self.assertEqual(action.status, "error")

    def test_behind_pr_is_still_queued(self):
        # A merge queue rebases BEHIND PRs; the lane must enqueue, not block.
        enqueue_calls = {"n": 0}
        with mock.patch.object(
            release_manager,
            "gh_pr_view",
            lambda _u, _r: {
                "state": "OPEN",
                "isDraft": False,
                "mergeStateStatus": "BEHIND",
                "headRefOid": "abc",
                "baseRefName": "main",
                "autoMergeRequest": None,
            },
        ), mock.patch.object(
            release_manager, "gh_enqueue", lambda *a, **k: enqueue_calls.update(n=enqueue_calls["n"] + 1)
        ), mock.patch.object(
            release_manager, "update_issue_state", lambda *a, **k: None
        ), mock.patch.object(
            release_manager, "create_comment", lambda *a, **k: None
        ):
            action = release_manager.process_issue(self.make_args(apply=True), "key", self.make_issue())
        self.assertEqual(action.status, "queued")
        self.assertEqual(enqueue_calls["n"], 1)


class BatchSelectionTests(unittest.TestCase):
    @staticmethod
    def _issue(labels):
        return release_manager.Issue(
            id="", identifier="x", title="", url="", description="", state="", labels=labels, comments=[], team_states={}
        )

    def test_scan_states_appends_queued_state(self):
        self.assertEqual(release_manager.scan_states(["A", "B"], "C"), ["A", "B", "C"])

    def test_scan_states_dedupes(self):
        self.assertEqual(release_manager.scan_states(["A", "B"], "B"), ["A", "B"])
        self.assertEqual(release_manager.scan_states(["A", "A"], None), ["A"])

    def test_select_candidates_filters_and_bounds(self):
        issues = [self._issue(["release:ready"]), self._issue(["release:ready"]), self._issue(["other"]), self._issue(["release:ready"])]
        selected, deferred = release_manager.select_candidates(issues, ["release:ready"], 2)
        self.assertEqual(len(selected), 2)
        self.assertEqual(deferred, 1)

    def test_select_candidates_no_max_takes_all(self):
        issues = [self._issue(["release:ready"]) for _ in range(3)]
        selected, deferred = release_manager.select_candidates(issues, ["release:ready"], 0)
        self.assertEqual(len(selected), 3)
        self.assertEqual(deferred, 0)


class OwnerRepoParsingTests(unittest.TestCase):
    def test_valid_forms(self):
        self.assertEqual(release_manager.parse_owner_repo("acme/repo"), ("acme", "repo"))
        self.assertEqual(release_manager.parse_owner_repo("acme/repo.git"), ("acme", "repo"))
        self.assertEqual(release_manager.parse_owner_repo(" acme/repo "), ("acme", "repo"))

    def test_rejects_malformed(self):
        for bad in [None, "", "noslash", "git@github.com:acme/repo.git", "https://github.com/acme/repo", "a/b/c", "/repo", "owner/"]:
            self.assertIsNone(release_manager.parse_owner_repo(bad), bad)


class MergeQueueStatusTests(unittest.TestCase):
    @staticmethod
    def _proc(returncode, stdout="", stderr=""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_gh_missing_is_unknown(self):
        with mock.patch.object(release_manager.shutil, "which", return_value=None):
            status = release_manager.merge_queue_status("acme/repo", "main")
        self.assertIsNone(status["enabled"])

    def test_enabled_when_queue_id_present(self):
        proc = self._proc(0, json.dumps({"data": {"repository": {"mergeQueue": {"id": "MQ_1"}}}}))
        with mock.patch.object(release_manager.shutil, "which", return_value="/usr/bin/gh"), mock.patch.object(
            release_manager, "run", return_value=proc
        ):
            status = release_manager.merge_queue_status("acme/repo", "main")
        self.assertTrue(status["enabled"])

    def test_disabled_when_queue_null(self):
        proc = self._proc(0, json.dumps({"data": {"repository": {"mergeQueue": None}}}))
        with mock.patch.object(release_manager.shutil, "which", return_value="/usr/bin/gh"), mock.patch.object(
            release_manager, "run", return_value=proc
        ):
            status = release_manager.merge_queue_status("acme/repo", "main")
        self.assertFalse(status["enabled"])

    def test_api_error_is_unknown(self):
        proc = self._proc(1, "", "gh: Could not resolve to a Repository")
        with mock.patch.object(release_manager.shutil, "which", return_value="/usr/bin/gh"), mock.patch.object(
            release_manager, "run", return_value=proc
        ):
            status = release_manager.merge_queue_status("acme/repo", "main")
        self.assertIsNone(status["enabled"])


class MergeQueueGateTests(unittest.TestCase):
    @staticmethod
    def _args(mode="github-merge-queue", require=False):
        return SimpleNamespace(mode=mode, require_merge_queue=require, repo="acme/repo", base_branch="main")

    @staticmethod
    def _disabled(owner_type="Organization", strict=False):
        """Patch the disabled-queue path's three probes together (hermetic)."""
        return (
            mock.patch.object(release_manager, "merge_queue_status", return_value={"enabled": False, "detail": "off"}),
            mock.patch.object(release_manager, "repo_owner_type", return_value={"type": owner_type}),
            mock.patch.object(release_manager, "base_branch_strict", return_value={"strict": strict}),
        )

    def test_no_gate_when_not_merge_queue_mode(self):
        self.assertIsNone(release_manager.merge_queue_gate(self._args(mode="auto-merge")))

    def test_ok_when_enabled_skips_extra_probes(self):
        # Healthy path must NOT fire the owner/strict probes -- one API call only.
        with mock.patch.object(release_manager, "merge_queue_status", return_value={"enabled": True, "detail": "on"}), \
             mock.patch.object(release_manager, "repo_owner_type", side_effect=AssertionError("should not probe owner")), \
             mock.patch.object(release_manager, "base_branch_strict", side_effect=AssertionError("should not probe strict")):
            self.assertEqual(release_manager.merge_queue_gate(self._args()).status, "ok")

    def test_warn_when_disabled_and_not_required(self):
        m1, m2, m3 = self._disabled()
        with m1, m2, m3:
            self.assertEqual(release_manager.merge_queue_gate(self._args(require=False)).status, "warn")

    def test_error_when_disabled_and_required(self):
        m1, m2, m3 = self._disabled()
        with m1, m2, m3:
            action = release_manager.merge_queue_gate(self._args(require=True))
        self.assertEqual(action.status, "error")
        self.assertIn(action.status, release_manager.FAILURE_STATUSES)

    def test_unknown_does_not_hard_block_even_when_required(self):
        # enabled is None returns before the extra probes, so they need no mock.
        with mock.patch.object(release_manager, "merge_queue_status", return_value={"enabled": None, "detail": "?"}):
            self.assertEqual(release_manager.merge_queue_gate(self._args(require=True)).status, "warn")

    def test_personal_repo_diagnosis_surfaced(self):
        m1, m2, m3 = self._disabled(owner_type="User", strict=False)
        with m1, m2, m3:
            action = release_manager.merge_queue_gate(self._args(require=True))
        self.assertEqual(action.status, "error")
        self.assertIn("personal-account", action.message)

    def test_strict_no_queue_diagnosis_surfaced(self):
        m1, m2, m3 = self._disabled(owner_type="Organization", strict=True)
        with m1, m2, m3:
            action = release_manager.merge_queue_gate(self._args(require=False))
        self.assertEqual(action.status, "warn")
        self.assertIn("rebase storm", action.message)


class SummarizeTests(unittest.TestCase):
    @staticmethod
    def _a(issue, status):
        return release_manager.Action(issue, status, "")

    def test_total_failure_no_progress_is_not_ok(self):
        s = release_manager.summarize([self._a("T-1", "retry"), self._a("T-2", "retry")])
        self.assertFalse(s["ok"])
        self.assertFalse(s["drained"])

    def test_partial_progress_one_error_is_ok(self):
        # One PR error must not tank a pass that queued others.
        s = release_manager.summarize([self._a("T-1", "queued"), self._a("T-2", "error")])
        self.assertTrue(s["ok"])

    def test_misconfigured_is_not_ok(self):
        s = release_manager.summarize([self._a("T-1", "finalized"), self._a("T-2", "misconfigured")])
        self.assertFalse(s["ok"])

    def test_gate_failed_is_not_ok(self):
        s = release_manager.summarize([release_manager.Action("merge-queue", "error", "")], gate_failed=True)
        self.assertFalse(s["ok"])

    def test_skipped_only_pass_is_ok(self):
        # Nothing actionable, nothing failed.
        self.assertTrue(release_manager.summarize([self._a("T-1", "skipped")])["ok"])

    def test_drained_only_when_nothing_pending(self):
        self.assertTrue(release_manager.summarize([self._a("T-1", "finalized")])["drained"])
        self.assertFalse(release_manager.summarize([self._a("T-1", "in_flight")])["drained"])
        self.assertFalse(release_manager.summarize([self._a("T-1", "queued")])["drained"])
        self.assertFalse(release_manager.summarize([release_manager.Action("release-manager", "deferred", "")])["drained"])

    def test_error_keeps_pass_undrained(self):
        # An unresolved error must not let the drain loop declare victory.
        s = release_manager.summarize([self._a("T-1", "error")])
        self.assertFalse(s["drained"])
        self.assertFalse(s["ok"])

    def test_error_with_progress_is_ok_but_not_drained(self):
        s = release_manager.summarize([self._a("T-1", "finalized"), self._a("T-2", "error")])
        self.assertTrue(s["ok"])
        self.assertFalse(s["drained"])

    def test_dry_run_would_queue_counts_as_progress(self):
        # A healthy dry-run with one transient error must not be flagged a failure.
        s = release_manager.summarize([self._a("T-1", "would_queue"), self._a("T-2", "error")])
        self.assertTrue(s["ok"])
        self.assertFalse(s["drained"])


class PreflightHelperTests(unittest.TestCase):
    WORKFLOW = (
        "campaign:\n  mode: release-manager\n  routing_label: sym:x\n"
        "release_manager:\n  mode: github-merge-queue\n  repo: org/app\n  base_branch: main\n"
    )

    def test_named_block_scopes_release_manager_mode_not_campaign_mode(self):
        block = preflight.extract_named_block(self.WORKFLOW, "release_manager")
        self.assertEqual(preflight.extract_scalar(block, "mode"), "github-merge-queue")
        campaign = preflight.extract_named_block(self.WORKFLOW, "campaign")
        self.assertEqual(preflight.extract_scalar(campaign, "mode"), "release-manager")

    def test_check_merge_queue_ready_requires_repo(self):
        status = preflight.check_merge_queue_ready(None, "main")
        self.assertIsNone(status["enabled"])

    def test_check_merge_queue_ready_parses_subprocess_json(self):
        proc = SimpleNamespace(returncode=0, stdout=json.dumps({"merge_queue": {"enabled": True, "detail": "on"}}), stderr="")
        with mock.patch.object(preflight.shutil, "which", return_value="/usr/bin/gh"), mock.patch.object(
            preflight.subprocess, "run", return_value=proc
        ):
            status = preflight.check_merge_queue_ready("org/app", "main")
        self.assertTrue(status["enabled"])

    def test_check_merge_queue_ready_handles_garbage_output(self):
        proc = SimpleNamespace(returncode=1, stdout="not json", stderr="boom")
        with mock.patch.object(preflight.shutil, "which", return_value="/usr/bin/gh"), mock.patch.object(
            preflight.subprocess, "run", return_value=proc
        ):
            status = preflight.check_merge_queue_ready("org/app", "main")
        self.assertIsNone(status["enabled"])

    def test_check_merge_queue_ready_surfaces_diagnosis(self):
        # The enriched diagnosis (owner/strict) must reach preflight, not be dropped
        # so personal/strict repos no longer get the generic "serial auto-merge" line.
        payload = {
            "merge_queue": {"enabled": False, "detail": "off"},
            "owner_type": "User",
            "diagnosis": "...personal-account-owned, where GitHub does not offer a merge queue...",
        }
        proc = SimpleNamespace(returncode=1, stdout=json.dumps(payload), stderr="")
        with mock.patch.object(preflight.shutil, "which", return_value="/usr/bin/gh"), mock.patch.object(
            preflight.subprocess, "run", return_value=proc
        ):
            status = preflight.check_merge_queue_ready("org/app", "main")
        self.assertFalse(status["enabled"])
        self.assertIn("personal-account", status.get("diagnosis", ""))


class RepoOwnerTypeTests(unittest.TestCase):
    @staticmethod
    def _proc(returncode, stdout="", stderr=""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def _info(self, stdout, rc=0):
        with mock.patch.object(release_manager.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(release_manager, "run", return_value=self._proc(rc, stdout)):
            return release_manager.repo_owner_type("acme/repo")

    def test_gh_missing_is_unknown(self):
        with mock.patch.object(release_manager.shutil, "which", return_value=None):
            self.assertIsNone(release_manager.repo_owner_type("acme/repo")["type"])

    def test_bad_repo_is_unknown(self):
        with mock.patch.object(release_manager.shutil, "which", return_value="/usr/bin/gh"):
            self.assertIsNone(release_manager.repo_owner_type("noslash")["type"])

    def test_user_owner(self):
        self.assertEqual(self._info(json.dumps({"type": "User", "private": False}))["type"], "User")

    def test_org_owner(self):
        info = self._info(json.dumps({"type": "Organization", "private": False}))
        self.assertEqual(info["type"], "Organization")
        self.assertFalse(info["private"])

    def test_private_flag_detected(self):
        self.assertTrue(self._info(json.dumps({"type": "Organization", "private": True}))["private"])

    def test_unexpected_value_is_unknown(self):
        # A surprise owner kind (e.g. "Mannequin") must not be reported as User/Org.
        self.assertIsNone(self._info(json.dumps({"type": "Mannequin", "private": False}))["type"])

    def test_bad_json_is_unknown(self):
        self.assertIsNone(self._info("not json")["type"])

    def test_api_error_is_unknown(self):
        self.assertIsNone(self._info("", rc=1)["type"])


class BaseBranchStrictTests(unittest.TestCase):
    @staticmethod
    def _proc(returncode, stdout="", stderr=""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def _strict(self, rules=("[]", 0), classic=("", 1, "Branch not protected")):
        # Dispatch the two gh calls base_branch_strict makes (rulesets endpoint,
        # then classic-protection endpoint) per-URL. Default classic = 404
        # "Branch not protected" -- the common "no classic protection" case.
        r_out, r_rc = rules
        c_out, c_rc, c_err = classic

        def fake_run(cmd, *a, **k):
            url = cmd[2]
            if "/rules/branches/" in url:
                return self._proc(r_rc, r_out)
            if "/protection/" in url:
                return self._proc(c_rc, c_out, c_err)
            return self._proc(0, "")

        with mock.patch.object(release_manager.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(release_manager, "run", side_effect=fake_run):
            return release_manager.base_branch_strict("acme/repo", "main")["strict"]

    def test_empty_rules_and_no_classic_is_not_strict(self):
        self.assertFalse(self._strict())

    def test_strict_ruleset_detected(self):
        self.assertTrue(self._strict(rules=(json.dumps(
            [{"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": True}}]), 0)))

    def test_non_strict_ruleset(self):
        self.assertFalse(self._strict(rules=(json.dumps(
            [{"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": False}}]), 0)))

    def test_mixed_rulesets_any_strict_wins(self):
        self.assertTrue(self._strict(rules=(json.dumps([
            {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": False}},
            {"type": "merge_queue", "parameters": {}},
            {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": True}},
        ]), 0)))

    def test_missing_parameters_is_not_strict(self):
        self.assertFalse(self._strict(rules=(json.dumps([{"type": "required_status_checks"}]), 0)))

    def test_non_dict_parameters_does_not_raise(self):
        # Defense-in-depth: a truthy non-dict "parameters" must degrade, not crash.
        self.assertIn(self._strict(rules=(json.dumps(
            [{"type": "required_status_checks", "parameters": [1, 2, 3]}]), 0)), (False, None))

    def test_classic_branch_protection_strict_detected(self):
        # /rules/branches returns [] for classic protection; the classic endpoint
        # reports strict=true. A strict CLASSIC repo must NOT read as non-strict.
        self.assertTrue(self._strict(rules=("[]", 0), classic=("true", 0, "")))

    def test_classic_checks_not_enabled_is_not_strict(self):
        # Branch protected but with no required_status_checks block: the endpoint
        # 404s "Required status checks not enabled" -- definitively not strict.
        self.assertFalse(self._strict(rules=("[]", 0), classic=("", 1, "gh: Required status checks not enabled (HTTP 404)")))

    def test_non_admin_404_is_unknown_not_false(self):
        # GitHub returns 404 "Not Found" (NOT 403) to non-admins to hide that
        # protection exists. This must degrade to unknown, never a confident False
        # (which would suppress the rebase-storm warning for a strict-classic repo).
        self.assertIsNone(self._strict(rules=("[]", 0), classic=("", 1, "gh: Not Found (HTTP 404)")))

    def test_nonexistent_branch_is_unknown(self):
        # A mistyped/not-yet-created base branch 404s "Branch not found"; rulesets
        # return [] (HTTP 200). Must be unknown, not a confident "not strict".
        self.assertIsNone(self._strict(rules=("[]", 0), classic=("", 1, "gh: Branch not found (HTTP 404)")))

    def test_rules_error_with_no_classic_is_unknown(self):
        # Rules endpoint errored (rulesets unknown); classic confirms no protection.
        # Must stay conservative (None), NOT claim a confident False.
        self.assertIsNone(self._strict(rules=("", 1), classic=("", 1, "Branch not protected")))

    def test_non_list_rules_is_unknown(self):
        self.assertIsNone(self._strict(rules=(json.dumps({"message": "Not Found"}), 0)))

    def test_bad_json_rules_is_unknown(self):
        self.assertIsNone(self._strict(rules=("not json", 0)))

    def test_branch_with_slash_is_percent_encoded(self):
        captured = []

        def fake_run(cmd, *a, **k):
            captured.append(cmd)
            return self._proc(0, "[]")

        with mock.patch.object(release_manager.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(release_manager, "run", side_effect=fake_run):
            release_manager.base_branch_strict("acme/repo", "release/v1")
        joined = " ".join(" ".join(c) for c in captured)
        self.assertIn("release%2Fv1", joined)
        self.assertNotIn("branches/release/v1", joined)


class MergeQueueGapMessageTests(unittest.TestCase):
    def test_personal_account_clause(self):
        msg = release_manager.merge_queue_gap_message("main", "User", False)
        self.assertIn("personal-account", msg)
        self.assertIn("organization-owned", msg)
        self.assertNotIn("rebase storm", msg)

    def test_strict_org_is_rebase_storm(self):
        msg = release_manager.merge_queue_gap_message("main", "Organization", True)
        self.assertIn("rebase storm", msg)
        self.assertNotIn("personal-account", msg)

    def test_plain_org_is_serial(self):
        msg = release_manager.merge_queue_gap_message("main", "Organization", False)
        self.assertIn("serial auto-merge", msg)
        self.assertNotIn("rebase storm", msg)

    def test_unknown_stays_conservative(self):
        # Undetermined strict must assert NEITHER the benign serial outcome nor a
        # definite storm -- only that it is unknown and must be verified.
        msg = release_manager.merge_queue_gap_message("main", None, None)
        self.assertIn("could not be read", msg)
        self.assertNotIn("roughly one CI cycle", msg)
        self.assertNotIn("personal-account", msg)

    def test_personal_and_strict_no_impossible_remedy(self):
        # A personal repo with strict checks gets both clauses, but must NOT be told
        # to "enable a merge queue" (impossible there) -- only move-org / drop-strict.
        msg = release_manager.merge_queue_gap_message("main", "User", True)
        self.assertIn("personal-account", msg)
        self.assertIn("rebase storm", msg)
        self.assertNotIn("enable a merge queue", msg.lower())

    def test_private_org_mentions_enterprise_cloud(self):
        # A private org repo (queue needs Enterprise Cloud) must be told so, not
        # given the bare "just enable one" / benign-serial story of a public org.
        msg = release_manager.merge_queue_gap_message("main", "Organization", False, True)
        self.assertIn("Enterprise Cloud", msg)
        self.assertNotIn("personal-account", msg)


if __name__ == "__main__":
    unittest.main()
