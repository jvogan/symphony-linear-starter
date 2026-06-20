#!/usr/bin/env python3
"""Release Manager lane for Symphony + Linear.

The lane is intentionally single-writer: workers prepare PRs and mark Linear
issues ready; this script owns queueing/merging and Linear closeout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GRAPHQL_URL = "https://api.linear.app/graphql"
PR_URL_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[0-9]+")
OUTCOME_RE = re.compile(r"<!--\s*symphony-outcome(?P<body>.*?)-->", re.DOTALL | re.IGNORECASE)


@dataclass
class Issue:
    id: str
    identifier: str
    title: str
    url: str
    description: str
    state: str
    labels: list[str]
    comments: list[dict[str, str]]
    team_states: dict[str, str]


@dataclass
class Action:
    issue: str
    status: str
    message: str
    pr_url: str | None = None
    github_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "issue": self.issue,
            "status": self.status,
            "message": self.message,
        }
        if self.pr_url:
            payload["pr_url"] = self.pr_url
        if self.github_state:
            payload["github_state"] = self.github_state
        return payload


class ReleaseLock:
    def __init__(self, path: Path, timeout: int = 120) -> None:
        self.path = path
        self.timeout = timeout
        self.pid_file = path / "pid"
        self.acquired = False

    def acquire(self) -> None:
        deadline = time.time() + self.timeout
        while True:
            try:
                self.path.mkdir(parents=True)
                self.pid_file.write_text(str(os.getpid()))
                self.acquired = True
                return
            except FileExistsError:
                holder = self.pid_file.read_text().strip() if self.pid_file.exists() else ""
                if holder and not process_alive(holder):
                    shutil.rmtree(self.path, ignore_errors=True)
                    continue
                if time.time() >= deadline:
                    raise RuntimeError(f"release lock still held at {self.path} by PID {holder or '?'}")
                time.sleep(2)

    def release(self) -> None:
        if self.acquired:
            shutil.rmtree(self.path, ignore_errors=True)
            self.acquired = False

    def __enter__(self) -> "ReleaseLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def process_alive(pid_text: str) -> bool:
    try:
        os.kill(int(pid_text), 0)
        return True
    except (OSError, ValueError):
        return False


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def workflow_defaults(path: Path) -> dict[str, Any]:
    text = path.read_text()
    defaults: dict[str, Any] = {}
    project = re.search(r"^\s*project_slug:\s*[\"']?([^\"'\n#]+)", text, re.MULTILINE)
    if project:
        defaults["project_slug"] = project.group(1).strip()
    labels = extract_yaml_list(text, "labels")
    if labels:
        defaults["labels"] = labels
    release_block = re.search(r"^release_manager:\s*\n(?P<body>(?:[ \t]+[^\n]*\n?)+)", text, re.MULTILINE)
    if release_block:
        block = release_block.group("body")
        for key in [
            "repo",
            "base_branch",
            "mode",
            "done_state",
            "blocked_state",
            "queued_state",
            "lock_dir",
            "merge_method",
            "comment_mode",
        ]:
            value = extract_scalar(block, key)
            if value:
                defaults[key] = value
        for key in ["ready_states", "ready_labels"]:
            value = extract_yaml_list(block, key)
            if value:
                defaults[key] = value
        max_per_run = extract_scalar(block, "max_per_run")
        if max_per_run and max_per_run.isdigit():
            defaults["max_per_run"] = int(max_per_run)
        delete_branch = extract_scalar(block, "delete_branch")
        if delete_branch:
            defaults["delete_branch"] = delete_branch.lower() == "true"
    return defaults


def extract_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(key)}:\s*[\"']?([^\"'\n#]+)", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def extract_yaml_list(text: str, key: str) -> list[str]:
    inline = re.search(rf"^\s*{re.escape(key)}:\s*\[(?P<body>[^\]]*)\]", text, re.MULTILINE)
    if inline:
        return [item.strip().strip("\"'") for item in inline.group("body").split(",") if item.strip()]
    block = re.search(rf"^\s*{re.escape(key)}:\s*\n(?P<body>(?:\s+-\s+[^\n]+\n?)+)", text, re.MULTILINE)
    if not block:
        return []
    values = []
    for line in block.group("body").splitlines():
        item = re.sub(r"^\s+-\s+", "", line).strip().strip("\"'")
        if item:
            values.append(item)
    return values


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


def fetch_issues(api_key: str, project_slug: str, state_names: list[str]) -> list[Issue]:
    query = """
query ReleaseManagerIssues($projectSlug: String!, $stateNames: [String!]!) {
  issues(filter: {project: {slugId: {eq: $projectSlug}}, state: {name: {in: $stateNames}}}, first: 100) {
    nodes {
      id
      identifier
      title
      url
      description
      state { name }
      labels { nodes { name } }
      comments(first: 50) { nodes { body createdAt } }
      team { states(first: 100) { nodes { id name } } }
    }
  }
}
"""
    payload = linear_graphql(api_key, query, {"projectSlug": project_slug, "stateNames": state_names})
    issues = []
    for node in payload.get("data", {}).get("issues", {}).get("nodes", []):
        issues.append(
            Issue(
                id=node["id"],
                identifier=node["identifier"],
                title=node.get("title") or "",
                url=node.get("url") or "",
                description=node.get("description") or "",
                state=node.get("state", {}).get("name") or "",
                labels=[label["name"] for label in node.get("labels", {}).get("nodes", [])],
                comments=node.get("comments", {}).get("nodes", []),
                team_states={state["name"]: state["id"] for state in node.get("team", {}).get("states", {}).get("nodes", [])},
            )
        )
    return issues


def create_comment(api_key: str, issue_id: str, body: str) -> None:
    mutation = """
mutation ReleaseManagerComment($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) { success }
}
"""
    linear_graphql(api_key, mutation, {"issueId": issue_id, "body": body})


def update_issue_state(api_key: str, issue_id: str, state_id: str) -> None:
    mutation = """
mutation ReleaseManagerState($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: {stateId: $stateId}) { success }
}
"""
    linear_graphql(api_key, mutation, {"issueId": issue_id, "stateId": state_id})


def extract_pr_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls = []
    for match in PR_URL_RE.finditer(text):
        url = match.group(0)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def parse_outcome_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in OUTCOME_RE.finditer(text):
        for raw_line in match.group("body").splitlines():
            line = raw_line.strip()
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def issue_text(issue: Issue) -> str:
    comments = "\n".join(comment.get("body") or "" for comment in sorted(issue.comments, key=lambda c: c.get("createdAt") or ""))
    return "\n".join([issue.description, comments])


def choose_pr_url(issue: Issue) -> str | None:
    text = issue_text(issue)
    outcome = parse_outcome_values(text)
    for key in ["pr_url", "pull_request", "github_pr", "pr"]:
        value = outcome.get(key)
        if value:
            urls = extract_pr_urls(value)
            if urls:
                return urls[-1]
    urls = extract_pr_urls(text)
    return urls[-1] if urls else None


def labels_match(issue_labels: list[str], required: list[str]) -> bool:
    if not required:
        return True
    normalized = {label.lower() for label in issue_labels}
    return all(label.lower() in normalized for label in required)


def gh_pr_view(pr_url: str, repo: str | None) -> dict[str, Any]:
    fields = "number,url,state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefOid,baseRefName,title"
    cmd = ["gh", "pr", "view", pr_url, "--json", fields]
    if repo:
        cmd.extend(["--repo", repo])
    result = run(cmd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "gh pr view failed")
    return json.loads(result.stdout)


def gh_enqueue(
    pr_url: str,
    repo: str | None,
    head_oid: str | None,
    delete_branch: bool,
    merge_method: str | None,
) -> None:
    cmd = ["gh", "pr", "merge", pr_url, "--auto"]
    if head_oid:
        cmd.extend(["--match-head-commit", head_oid])
    if merge_method:
        cmd.append(f"--{merge_method}")
    if delete_branch:
        cmd.append("--delete-branch")
    if repo:
        cmd.extend(["--repo", repo])
    result = run(cmd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "gh pr merge --auto failed")


def maybe_move(api_key: str, issue: Issue, state_name: str, apply: bool) -> bool:
    state_id = issue.team_states.get(state_name)
    if not state_id:
        return False
    if apply:
        update_issue_state(api_key, issue.id, state_id)
    return True


def release_comment_body(mode: str, event: str, pr_url: str) -> str | None:
    if mode == "none":
        return None
    if mode == "verbose":
        messages = {
            "merged": "Release Manager: PR is merged, closing issue.",
            "closed": "Release Manager: PR is closed without merge; returning for repair.",
            "dirty": "Release Manager: PR has merge conflicts and needs repair.",
            "queued": "Release Manager: queued PR for merge/deploy.",
        }
    else:
        messages = {
            "merged": "release-manager: merged",
            "closed": "release-manager: blocked",
            "dirty": "release-manager: blocked",
            "queued": "release-manager: queued",
        }
    return f"{messages[event]}\n\n{pr_url}"


def maybe_comment(args: argparse.Namespace, api_key: str, issue: Issue, event: str, pr_url: str) -> None:
    body = release_comment_body(args.comment_mode, event, pr_url)
    if body:
        create_comment(api_key, issue.id, body)


def process_issue(args: argparse.Namespace, api_key: str, issue: Issue) -> Action:
    pr_url = choose_pr_url(issue)
    if not pr_url:
        return Action(issue.identifier, "skipped", "no GitHub PR URL found in issue body or comments")
    try:
        pr = gh_pr_view(pr_url, args.repo)
    except Exception as exc:
        return Action(issue.identifier, "error", f"could not inspect PR: {exc}", pr_url=pr_url)

    state = pr.get("state")
    merge_state = pr.get("mergeStateStatus")
    base_branch = pr.get("baseRefName")
    if args.base_branch and base_branch and base_branch != args.base_branch:
        return Action(
            issue.identifier,
            "skipped",
            f"PR targets {base_branch}, expected {args.base_branch}",
            pr_url,
            state,
        )
    if state == "MERGED":
        moved = maybe_move(api_key, issue, args.done_state, args.apply)
        message = f"PR already merged; {'moved' if moved else 'would move'} issue to {args.done_state}"
        if args.apply:
            maybe_comment(args, api_key, issue, "merged", pr_url)
        return Action(issue.identifier, "finalized" if args.apply else "would_finalize", message, pr_url, state)
    if state == "CLOSED":
        moved = maybe_move(api_key, issue, args.blocked_state, args.apply)
        message = f"PR is closed without merge; {'moved' if moved else 'would move'} issue to {args.blocked_state}"
        if args.apply:
            maybe_comment(args, api_key, issue, "closed", pr_url)
        return Action(issue.identifier, "blocked" if args.apply else "would_block", message, pr_url, state)
    if pr.get("isDraft"):
        return Action(issue.identifier, "skipped", "PR is still draft", pr_url, state)
    if merge_state == "DIRTY":
        moved = maybe_move(api_key, issue, args.blocked_state, args.apply)
        message = f"PR has merge conflicts; {'moved' if moved else 'would move'} issue to {args.blocked_state}"
        if args.apply:
            maybe_comment(args, api_key, issue, "dirty", pr_url)
        return Action(issue.identifier, "blocked" if args.apply else "would_block", message, pr_url, state)

    if args.apply:
        gh_enqueue(pr_url, args.repo, pr.get("headRefOid"), args.delete_branch, args.merge_method)
        queued = maybe_move(api_key, issue, args.queued_state, args.apply)
        maybe_comment(args, api_key, issue, "queued", pr_url)
        return Action(issue.identifier, "queued", f"queued with gh pr merge --auto; queued_state_set={queued}", pr_url, state)
    return Action(issue.identifier, "would_queue", f"would run gh pr merge --auto; mergeStateStatus={merge_state}", pr_url, state)


def self_test() -> int:
    issue = Issue(
        id="id",
        identifier="TST-1",
        title="Release thing",
        url="https://linear.app/x/issue/TST-1",
        description="Body with https://github.com/acme/private/pull/7",
        state="Ready to Merge",
        labels=["release:ready", "sym:test"],
        comments=[
            {"body": "<!-- symphony-outcome\nstatus: success\npr_url: https://github.com/acme/private/pull/8\n-->", "createdAt": "2026-01-01T00:00:00Z"}
        ],
        team_states={"Done": "done-id", "Todo": "todo-id", "Merging": "merging-id"},
    )
    assert extract_pr_urls(issue_text(issue)) == [
        "https://github.com/acme/private/pull/7",
        "https://github.com/acme/private/pull/8",
    ]
    assert choose_pr_url(issue) == "https://github.com/acme/private/pull/8"
    assert labels_match(issue.labels, ["release:ready"])
    assert not labels_match(issue.labels, ["release:missing"])
    assert release_comment_body("minimal", "queued", "https://github.com/acme/private/pull/8") == (
        "release-manager: queued\n\nhttps://github.com/acme/private/pull/8"
    )
    assert release_comment_body("none", "queued", "https://github.com/acme/private/pull/8") is None
    print(json.dumps({"ok": True, "checks": ["pr_url_extraction", "outcome_precedence", "label_filter"]}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single-writer Release Manager lane for Symphony + Linear.")
    parser.add_argument("--workflow", help="Optional workflow file to read defaults from.")
    parser.add_argument("--project-slug", help="Linear project slug.")
    parser.add_argument("--repo", help="GitHub repo in OWNER/REPO form. Optional when PR URLs are full GitHub URLs.")
    parser.add_argument("--base-branch", default="main", help="Only process PRs targeting this base branch.")
    parser.add_argument("--ready-state", action="append", dest="ready_states", help="Linear state to scan. Repeatable.")
    parser.add_argument("--label", action="append", default=[], help="Required Linear label. Repeatable.")
    parser.add_argument("--ready-label", action="append", default=[], help="Required release-ready Linear label. Repeatable.")
    parser.add_argument("--done-state", default="Done")
    parser.add_argument("--blocked-state", default="Todo")
    parser.add_argument("--queued-state", default="Merging")
    parser.add_argument("--max", type=int, default=5, help="Maximum ready issues to process in one run.")
    parser.add_argument("--lock-dir", default=".orchestration/release-manager.lock")
    parser.add_argument("--lock-timeout", type=int, default=120)
    parser.add_argument("--delete-branch", action="store_true", help="Ask gh to delete branches after merge.")
    parser.add_argument("--merge-method", choices=["merge", "squash", "rebase"], help="Optional gh merge strategy for repos without a merge queue.")
    parser.add_argument("--comment-mode", choices=["minimal", "none", "verbose"], default="minimal", help="Linear comment detail level for apply runs.")
    parser.add_argument("--apply", action="store_true", help="Mutate GitHub/Linear. Default is dry-run.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--self-test", action="store_true", help="Run local parser tests and exit.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    if args.workflow:
        defaults = workflow_defaults(Path(args.workflow).expanduser())
        args.project_slug = args.project_slug or defaults.get("project_slug")
        args.repo = args.repo or defaults.get("repo")
        args.base_branch = defaults.get("base_branch", args.base_branch)
        args.ready_states = args.ready_states or defaults.get("ready_states")
        args.label = args.label or defaults.get("labels", [])
        args.ready_label = args.ready_label or defaults.get("ready_labels", [])
        args.done_state = defaults.get("done_state", args.done_state)
        args.blocked_state = defaults.get("blocked_state", args.blocked_state)
        args.queued_state = defaults.get("queued_state", args.queued_state)
        args.lock_dir = os.path.expandvars(defaults.get("lock_dir", args.lock_dir))
        args.delete_branch = bool(defaults.get("delete_branch", args.delete_branch))
        args.merge_method = defaults.get("merge_method", args.merge_method)
        args.comment_mode = defaults.get("comment_mode", args.comment_mode)
        args.max = int(defaults.get("max_per_run", args.max))

    args.ready_states = args.ready_states or ["Ready to Merge", "In Review"]
    required_labels = list(args.label or []) + list(args.ready_label or [])

    if args.merge_method and args.merge_method not in {"merge", "squash", "rebase"}:
        parser.error("release_manager.merge_method must be one of: merge, squash, rebase")
    if args.comment_mode not in {"minimal", "none", "verbose"}:
        parser.error("release_manager.comment_mode must be one of: minimal, none, verbose")
    if not args.project_slug:
        parser.error("--project-slug is required unless --workflow supplies tracker.project_slug")
    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        parser.error("LINEAR_API_KEY is required")
    if shutil.which("gh") is None:
        parser.error("gh is required")

    def run_batch() -> list[Action]:
        issues = fetch_issues(api_key, args.project_slug, args.ready_states)
        candidates = [issue for issue in issues if labels_match(issue.labels, required_labels)]
        actions: list[Action] = []
        for issue in candidates[: args.max]:
            actions.append(process_issue(args, api_key, issue))
        return actions

    if args.apply:
        with ReleaseLock(Path(args.lock_dir).expanduser(), args.lock_timeout):
            actions = run_batch()
    else:
        actions = run_batch()

    payload = {"ok": all(action.status not in {"error"} for action in actions), "apply": args.apply, "actions": [a.to_dict() for a in actions]}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"Release Manager {mode}: {len(actions)} candidate(s)")
        for action in actions:
            pr = f" {action.pr_url}" if action.pr_url else ""
            print(f"- {action.issue}: {action.status}: {action.message}{pr}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
