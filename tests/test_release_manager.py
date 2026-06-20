import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


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
            release_manager.gh_enqueue = lambda pr_url, repo, head_oid, delete_branch, merge_method: calls.update(
                pr_url=pr_url,
                repo=repo,
                head_oid=head_oid,
                delete_branch=delete_branch,
                merge_method=merge_method,
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


if __name__ == "__main__":
    unittest.main()
