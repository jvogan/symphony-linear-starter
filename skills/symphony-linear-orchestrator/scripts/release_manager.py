#!/usr/bin/env python3
"""Release Manager lane for Symphony + Linear.

The lane is intentionally single-writer: workers prepare PRs and mark Linear
issues ready; this script owns queueing/merging and Linear closeout.

It is built for the burst pattern where many workers finish at once:

* It never lets workers race to update ``main`` -- one lane enqueues PRs.
* It prefers GitHub Merge Queue (``gh pr merge --auto``) so a burst of PRs is
  validated and merged by GitHub's queue instead of serial manual rebases.
* It verifies a merge queue is actually configured before relying on it, so the
  lane does not silently degrade to one-at-a-time auto-merge.
* It is safe to re-run: already-queued PRs are not re-enqueued, and issues that
  merged since the last pass are finalized to the done state.
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
from urllib.parse import quote


GRAPHQL_URL = "https://api.linear.app/graphql"
PR_URL_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[0-9]+")
OUTCOME_RE = re.compile(r"<!--\s*symphony-outcome(?P<body>.*?)-->", re.DOTALL | re.IGNORECASE)
MERGE_QUEUE_MODE = "github-merge-queue"
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MERGE_QUEUE_QUERY = """
query MergeQueueStatus($owner: String!, $repo: String!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    mergeQueue(branch: $branch) { id }
  }
}
"""


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


# Statuses that always make the run fail (exit non-zero), regardless of progress.
# 'misconfigured' means a required Linear state is missing, so closeout silently
# no-ops -- a real problem the operator must fix, not a benign skip.
FAILURE_STATUSES = {"error", "misconfigured"}


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


def run(cmd: list[str], timeout: int = 90) -> subprocess.CompletedProcess[str]:
    # A hung gh call must not wedge the burst while the release lock is held, so
    # every external call is bounded. A timeout surfaces as a normal non-zero
    # result and flows through the existing error handling.
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"command timed out after {timeout}s: {' '.join(cmd[:3])}")


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
        for bool_key in ["delete_branch", "require_merge_queue"]:
            value = extract_scalar(block, bool_key)
            if value:
                defaults[bool_key] = value.lower() == "true"
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


def parse_owner_repo(repo: str | None) -> tuple[str, str] | None:
    """Split a clean ``OWNER/REPO`` string into its parts, or return None.

    Rejects anything that is not a plain ``owner/name`` slug (e.g. SCP-style
    git URLs, full https URLs, or paths with extra segments) so a malformed
    ``repo`` value cannot produce a bogus ``gh api`` call.
    """
    if not repo:
        return None
    parts = repo.strip().removesuffix(".git").split("/")
    if len(parts) != 2:
        return None
    owner, name = parts[0].strip(), parts[1].strip()
    if not OWNER_RE.match(owner) or not REPO_NAME_RE.match(name):
        return None
    return owner, name


def merge_queue_status(repo: str | None, branch: str) -> dict[str, Any]:
    """Report whether a GitHub merge queue is enabled for ``branch``.

    Returns ``{"enabled": True|False|None, "detail": str}``. ``enabled`` is
    ``None`` when the status could not be determined (gh missing, not
    authenticated, network/permission error) so callers can warn instead of
    making a false claim either way.
    """
    if shutil.which("gh") is None:
        return {"enabled": None, "detail": "gh is not installed"}
    parsed = parse_owner_repo(repo)
    if not parsed:
        return {"enabled": None, "detail": "repo must be OWNER/REPO to check the merge queue"}
    owner, name = parsed
    result = run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={MERGE_QUEUE_QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={name}",
            "-f",
            f"branch={branch}",
        ]
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return {"enabled": None, "detail": (detail[-1] if detail else "gh api graphql failed")[:200]}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"enabled": None, "detail": "could not parse gh api graphql response"}
    if data.get("errors"):
        return {"enabled": None, "detail": f"graphql errors: {str(data['errors'])[:200]}"}
    repository = (data.get("data") or {}).get("repository") or {}
    queue = repository.get("mergeQueue") or {}
    enabled = bool(queue.get("id"))
    return {"enabled": enabled, "detail": f"merge queue {'enabled' if enabled else 'not enabled'} on {branch}"}


def repo_owner_type(repo: str | None) -> dict[str, Any]:
    """Classify the repo owner and visibility for merge-queue availability.

    Returns ``{"type": "User"|"Organization"|None, "private": True|False|None, "detail": str}``.
    GitHub offers merge queues only on organization-owned repos, and on *private*
    org repos only with GitHub Enterprise Cloud -- so a ``User`` owner means a queue
    is impossible, and a private ``Organization`` owner means it may be impossible
    (plan-dependent). Both are different instructions to the operator than "an org
    repo that simply hasn't configured one". ``None`` fields when undetermined.
    """
    unknown = {"type": None, "private": None, "detail": "gh is not installed"}
    if shutil.which("gh") is None:
        return unknown
    parsed = parse_owner_repo(repo)
    if not parsed:
        return {"type": None, "private": None, "detail": "repo must be OWNER/REPO"}
    owner, name = parsed
    result = run(["gh", "api", f"repos/{owner}/{name}", "--jq", "{type: .owner.type, private: .private}"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return {"type": None, "private": None, "detail": (detail[-1] if detail else "gh api failed")[:200]}
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"type": None, "private": None, "detail": "could not parse repo response"}
    if not isinstance(info, dict):
        return {"type": None, "private": None, "detail": "unexpected repo response shape"}
    owner_type = info.get("type")
    private = info.get("private")
    private = bool(private) if isinstance(private, bool) else None
    if owner_type not in ("User", "Organization"):
        return {"type": None, "private": private, "detail": f"unexpected owner type: {str(owner_type)[:80] or 'empty'}"}
    return {"type": owner_type, "private": private, "detail": f"owner is a {owner_type}" + (" (private)" if private else "")}


def base_branch_strict(repo: str | None, branch: str) -> dict[str, Any]:
    """Report whether ``branch`` enforces strict (up-to-date) required status checks.

    Returns ``{"strict": True|False|None, "detail": str}``. Strict required checks
    WITHOUT a merge queue are what turn a PR burst into a serial rebase storm, so
    this is the signal that decides whether the no-queue fallback is merely slow or
    actively the failure this lane prevents.

    Strict checks can be configured two independent ways, and they live at two
    different endpoints, so both are consulted:

    * **Rulesets** -- ``/repos/{repo}/rules/branches/{branch}`` (read access; returns
      ruleset rules only). A ``required_status_checks`` rule with
      ``strict_required_status_checks_policy: true`` means strict.
    * **Classic branch protection** -- ``/repos/{repo}/branches/{branch}/protection``
      (admin access; invisible to the rules endpoint). ``required_status_checks.strict``.

    ``None`` when it could not be determined either way (e.g. gh missing, or both
    probes error/permission-denied) -- callers must then stay conservative rather
    than assert the benign "no strict checks" outcome.
    """
    if shutil.which("gh") is None:
        return {"strict": None, "detail": "gh is not installed"}
    parsed = parse_owner_repo(repo)
    if not parsed:
        return {"strict": None, "detail": "repo must be OWNER/REPO"}
    owner, name = parsed
    # A branch name may contain '/', which must be percent-encoded as a single
    # path segment or the REST path would split it into extra segments.
    enc = quote(branch, safe="")

    # Source 1: rulesets. None=unknown, True=strict found, False=confirmed absent.
    ruleset_strict: bool | None = None
    result = run(["gh", "api", f"repos/{owner}/{name}/rules/branches/{enc}"])
    if result.returncode == 0:
        try:
            rules = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"strict": None, "detail": "could not parse rules response"}
        if not isinstance(rules, list):
            return {"strict": None, "detail": "unexpected rules response shape"}
        ruleset_strict = False
        for rule in rules:
            if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
                params = rule.get("parameters")
                if isinstance(params, dict) and params.get("strict_required_status_checks_policy"):
                    return {"strict": True, "detail": "strict required status checks enforced (ruleset)"}

    # Source 2: classic branch protection -- invisible to the rules endpoint, so a
    # strict CLASSIC repo would otherwise read as non-strict (a dangerous false
    # negative). Needs admin; degrade to unknown on permission errors.
    classic_strict: bool | None = None
    classic = run(["gh", "api", f"repos/{owner}/{name}/branches/{enc}/protection/required_status_checks", "--jq", ".strict"])
    if classic.returncode == 0:
        verdict = classic.stdout.strip().lower()
        if verdict == "true":
            return {"strict": True, "detail": "strict required status checks enforced (classic protection)"}
        if verdict == "false":
            classic_strict = False
    else:
        body = (classic.stderr or classic.stdout).lower()
        # Only an explicit "branch not protected" or "required status checks not
        # enabled" proves the branch definitively has no strict checks. A bare 404
        # "Not Found" is NOT proof: GitHub returns 404 (not 403) to non-admins to
        # hide that protection exists, and a mistyped branch returns "Branch not
        # found" -- both must stay unknown (None) so the diagnosis does not assert a
        # benign outcome it could not confirm. Do NOT match on the bare "404".
        if "not protected" in body or "not enabled" in body:
            classic_strict = False

    # Neither source found strict. Only report a confident False when BOTH sources
    # were actually readable; if either was unknown, stay conservative (None).
    if ruleset_strict is False and classic_strict is False:
        return {"strict": False, "detail": "no strict required status checks on branch"}
    return {"strict": None, "detail": "could not confirm branch protection (permission or API error)"}


def merge_queue_gap_message(base_branch: str, owner_type: str | None, strict: bool | None, private: bool | None = None) -> str:
    """Compose the no-merge-queue diagnosis, escalating by repo type and strict checks.

    Pure (string-only) so the wording/severity is unit-testable without shelling
    out. ``owner_type``, ``private``, and ``strict`` may be ``None`` when
    undetermined; the message then stays conservative and never asserts an outcome
    (benign *or* catastrophic) it could not confirm. The remedy it recommends always
    respects merge-queue availability -- it never tells a repo that cannot have a
    queue to "enable" one.
    """
    parts = [f"mode={MERGE_QUEUE_MODE} but no merge queue is enabled on {base_branch}."]
    # Availability clause: why there is no queue, and whether one is even possible.
    if owner_type == "User":
        queue_remedy = "move the repo under an org to use a merge queue"
        parts.append(
            "This repo is personal-account-owned, where GitHub does not offer a merge queue at all "
            "(organization-owned repos only) -- do not wait for one; move it under an org, or run "
            "serial auto-merge with an explicit merge_method."
        )
    elif owner_type == "Organization" and private is True:
        queue_remedy = "enable a merge queue (it needs GitHub Enterprise Cloud on a private repo)"
        parts.append(
            "This is a private org repo, where a merge queue needs GitHub Enterprise Cloud -- on "
            "Team/Free it is unavailable (make the repo public, or upgrade); otherwise enable one."
        )
    else:
        queue_remedy = "enable a merge queue"
    # Severity/throughput clause, with a remedy that respects the availability above.
    if strict is True:
        parts.append(
            "Branch protection requires up-to-date branches (strict required status checks), so a "
            "burst serializes into a rebase storm -- one PR per CI cycle, the exact failure this lane "
            f"exists to prevent. To fix, {queue_remedy}, or drop the strict requirement (accepting "
            "that PRs then merge without being tested against the others)."
        )
    elif strict is False:
        parts.append(
            "Without a queue the burst falls back to serial auto-merge -- roughly one CI cycle per PR."
        )
    else:
        parts.append(
            "Branch protection could not be read, so whether the no-queue fallback is merely slow "
            "(serial auto-merge) or a rebase storm (if strict required checks are enforced) is unknown "
            "-- verify the branch rules before a burst."
        )
    parts.append("See references/release-manager-lane.md.")
    return " ".join(parts)


def merge_queue_gate(args: argparse.Namespace) -> Action | None:
    """Pre-run check: only meaningful when the workflow opts into merge-queue mode.

    Returns an Action describing merge-queue readiness, or None when the lane is
    not in github-merge-queue mode. An ``error`` status means the run must stop
    before mutating anything (only when --require-merge-queue is set).
    """
    if getattr(args, "mode", None) != MERGE_QUEUE_MODE:
        return None
    status = merge_queue_status(args.repo, args.base_branch)
    enabled = status.get("enabled")
    if enabled is True:
        return Action("merge-queue", "ok", status.get("detail", "merge queue enabled"))
    if enabled is None:
        return Action("merge-queue", "warn", f"could not verify merge queue: {status.get('detail', 'unknown')}")
    # No queue: enrich the diagnosis with WHY (owner is a personal account, so a
    # queue is impossible) and HOW BAD (strict checks make the fallback a rebase
    # storm, not merely slow). Both probes degrade to None on error, so a failed
    # lookup never invents a claim -- it just yields the conservative message.
    owner = repo_owner_type(args.repo)
    strict = base_branch_strict(args.repo, args.base_branch).get("strict")
    message = merge_queue_gap_message(args.base_branch, owner.get("type"), strict, owner.get("private"))
    if getattr(args, "require_merge_queue", False):
        return Action("merge-queue", "error", "required merge queue missing -- " + message)
    return Action("merge-queue", "warn", message)


def scan_states(ready_states: list[str], queued_state: str | None) -> list[str]:
    """States to fetch: the ready states plus the queued state.

    Scanning the queued state is what lets a re-run finalize issues whose PRs
    merged asynchronously through the queue since the last pass; without it,
    queued issues would never reach the done state.
    """
    states = list(dict.fromkeys(ready_states))
    if queued_state and queued_state not in states:
        states.append(queued_state)
    return states


def select_candidates(issues: list[Issue], required_labels: list[str], max_count: int) -> tuple[list[Issue], int]:
    """Filter by label and bound by --max, reporting how many were deferred."""
    matched = [issue for issue in issues if labels_match(issue.labels, required_labels)]
    selected = matched[:max_count] if max_count and max_count > 0 else matched
    deferred = len(matched) - len(selected)
    return selected, deferred


def gh_pr_view(pr_url: str, repo: str | None) -> dict[str, Any]:
    fields = "number,url,state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefOid,baseRefName,title,autoMergeRequest"
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


def closeout(
    args: argparse.Namespace,
    api_key: str,
    issue: Issue,
    target_state: str,
    event: str,
    status_apply: str,
    status_dryrun: str,
    base_message: str,
    pr_url: str,
    gh_state: str | None,
) -> Action:
    """Move an issue to a terminal/queued state and comment.

    If the target state is missing on an apply run, the move silently no-ops in
    Linear. Rather than reporting success and re-posting the comment every pass,
    surface it as 'misconfigured' (a real failure) so the operator fixes the
    state mapping. This is what makes the idempotent re-run and async finalize
    guarantees hold.
    """
    if args.apply:
        if not maybe_move(api_key, issue, target_state, True):
            return Action(
                issue.identifier,
                "misconfigured",
                f"Linear state '{target_state}' is not configured for this issue's team; "
                f"left in '{issue.state}' and not commented ({base_message})",
                pr_url,
                gh_state,
            )
        maybe_comment(args, api_key, issue, event, pr_url)
        return Action(issue.identifier, status_apply, f"{base_message}; moved issue to {target_state}", pr_url, gh_state)
    if target_state in issue.team_states:
        return Action(issue.identifier, status_dryrun, f"{base_message}; would move issue to {target_state}", pr_url, gh_state)
    return Action(
        issue.identifier,
        status_dryrun,
        f"{base_message}; cannot move issue: Linear state '{target_state}' is not configured for this team",
        pr_url,
        gh_state,
    )


def process_issue(args: argparse.Namespace, api_key: str, issue: Issue) -> Action:
    pr_url = choose_pr_url(issue)
    if not pr_url:
        return Action(issue.identifier, "skipped", "no GitHub PR URL found in issue body or comments")
    try:
        pr = gh_pr_view(pr_url, args.repo)
    except Exception as exc:
        # If we already handed this issue to the queue, a transient inability to
        # inspect the PR must not orphan it: report 'retry' (counted as pending)
        # so the drain loop keeps reconciling instead of declaring it drained.
        already_queued = bool(args.queued_state) and args.queued_state not in (args.ready_states or []) and issue.state == args.queued_state
        return Action(issue.identifier, "retry" if already_queued else "error", f"could not inspect PR: {exc}", pr_url=pr_url)

    state = pr.get("state")
    merge_state = pr.get("mergeStateStatus")
    base_branch = pr.get("baseRefName")
    if args.base_branch and base_branch and base_branch != args.base_branch:
        return Action(issue.identifier, "skipped", f"PR targets {base_branch}, expected {args.base_branch}", pr_url, state)

    if state == "MERGED":
        return closeout(args, api_key, issue, args.done_state, "merged", "finalized", "would_finalize", "PR already merged", pr_url, state)
    if state == "CLOSED":
        return closeout(args, api_key, issue, args.blocked_state, "closed", "blocked", "would_block", "PR is closed without merge", pr_url, state)
    if pr.get("isDraft"):
        return Action(issue.identifier, "skipped", "PR is still draft", pr_url, state)
    if merge_state == "DIRTY":
        return closeout(args, api_key, issue, args.blocked_state, "dirty", "blocked", "would_block", "PR has merge conflicts", pr_url, state)

    # Idempotency anchor: once we enqueue a PR we move its issue to queued_state.
    # If the issue is already there with the PR still open, never re-enqueue. The
    # PR is either still in the merge queue or was evicted; either way blindly
    # re-running gh pr merge --auto is what caused both double-enqueue and the
    # infinite re-enqueue loop for evicted PRs. It will finalize (MERGED) or
    # block (DIRTY/CLOSED) on a later pass; until then it is reported in_flight.
    # (gh 2.x cannot read mergeQueueEntry, so the Linear state is the reliable
    # signal -- which is why a failed move to queued_state is a hard error above.)
    queued_distinct = bool(args.queued_state) and args.queued_state not in (args.ready_states or [])
    if queued_distinct and issue.state == args.queued_state:
        return Action(
            issue.identifier,
            "in_flight",
            "PR is open and already handed to the merge queue; awaiting GitHub (no re-enqueue)",
            pr_url,
            state,
        )

    # A fresh ready issue that already has auto-merge enabled: record it, do not re-enqueue.
    if pr.get("autoMergeRequest"):
        return closeout(args, api_key, issue, args.queued_state, "queued", "queued", "would_queue", "auto-merge already enabled; no re-enqueue", pr_url, state)

    if not args.apply:
        return Action(issue.identifier, "would_queue", f"would run gh pr merge --auto; mergeStateStatus={merge_state}", pr_url, state)
    try:
        gh_enqueue(pr_url, args.repo, pr.get("headRefOid"), args.delete_branch, args.merge_method)
    except Exception as exc:
        # A single failed enqueue (e.g. the head moved under us) must not crash
        # the whole burst. Surface it; the pass exit code reflects total breakage.
        return Action(issue.identifier, "retry", f"enqueue failed, will retry next pass: {exc}", pr_url, state)
    return closeout(args, api_key, issue, args.queued_state, "queued", "queued", "would_queue", "queued with gh pr merge --auto", pr_url, state)


def summarize(actions: list[Action], gate_failed: bool = False) -> dict[str, Any]:
    """Roll up actions into an exit decision and a drain signal.

    Exit is non-zero on a hard failure (a required merge queue is missing, or a
    Linear state is misconfigured) or when there were candidate issues but none
    made forward progress -- a single PR error does not tank a pass that queued
    others. 'drained' is True when nothing is left in flight, so a scheduler can
    stop re-running the lane.
    """
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.status] = counts.get(action.status, 0) + 1
    issue_actions = [a for a in actions if a.issue not in {"merge-queue", "release-manager"}]
    forward = sum(counts.get(status, 0) for status in ("queued", "finalized", "blocked", "in_flight", "would_queue", "would_finalize", "would_block"))
    stuck = sum(counts.get(status, 0) for status in ("error", "retry"))
    # 'error' keeps the burst un-drained: a queued issue whose PR view failed
    # transiently must not be declared drained and abandoned. would_queue keeps a
    # dry-run from reporting drained while it still shows work it would enqueue.
    pending = sum(counts.get(status, 0) for status in ("queued", "in_flight", "retry", "deferred", "error", "would_queue"))
    hard_fail = gate_failed or counts.get("misconfigured", 0) > 0
    no_progress = bool(issue_actions) and forward == 0 and stuck > 0
    return {"ok": not (hard_fail or no_progress), "drained": pending == 0, "counts": counts}


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
    assert parse_owner_repo("acme/private") == ("acme", "private")
    assert parse_owner_repo("git@github.com:acme/private.git") is None
    assert parse_owner_repo("noslash") is None
    assert scan_states(["Ready to Merge", "In Review"], "Merging") == ["Ready to Merge", "In Review", "Merging"]
    assert scan_states(["Ready to Merge"], "Ready to Merge") == ["Ready to Merge"]
    selected, deferred = select_candidates([issue, issue, issue], ["release:ready"], 2)
    assert len(selected) == 2 and deferred == 1
    assert release_comment_body("minimal", "queued", "https://github.com/acme/private/pull/8") == (
        "release-manager: queued\n\nhttps://github.com/acme/private/pull/8"
    )
    assert release_comment_body("none", "queued", "https://github.com/acme/private/pull/8") is None
    msg_user = merge_queue_gap_message("main", "User", False)
    assert "personal-account" in msg_user and "organization-owned" in msg_user
    msg_strict = merge_queue_gap_message("main", "Organization", True)
    assert "rebase storm" in msg_strict and "personal-account" not in msg_strict
    msg_plain = merge_queue_gap_message("main", "Organization", False)
    assert "serial auto-merge" in msg_plain and "rebase storm" not in msg_plain
    msg_unknown = merge_queue_gap_message("main", None, None)
    assert "could not be read" in msg_unknown and "roughly one CI cycle" not in msg_unknown
    msg_private = merge_queue_gap_message("main", "Organization", False, True)
    assert "Enterprise Cloud" in msg_private
    msg_user_strict = merge_queue_gap_message("main", "User", True)
    assert "rebase storm" in msg_user_strict and "enable a merge queue" not in msg_user_strict.lower()
    print(
        json.dumps(
            {
                "ok": True,
                "checks": [
                    "pr_url_extraction",
                    "outcome_precedence",
                    "label_filter",
                    "owner_repo_parsing",
                    "scan_states",
                    "candidate_deferral",
                    "merge_queue_gap_message",
                ],
            },
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single-writer Release Manager lane for Symphony + Linear.")
    parser.add_argument("--workflow", help="Optional workflow file to read defaults from.")
    parser.add_argument("--project-slug", help="Linear project slug.")
    parser.add_argument("--repo", help="GitHub repo in OWNER/REPO form. Optional when PR URLs are full GitHub URLs.")
    parser.add_argument("--base-branch", default="main", help="Only process PRs targeting this base branch.")
    parser.add_argument("--mode", help=f"Release mode hint, e.g. {MERGE_QUEUE_MODE}. Usually set via the workflow.")
    parser.add_argument("--ready-state", action="append", dest="ready_states", help="Linear state to scan. Repeatable.")
    parser.add_argument("--label", action="append", default=[], help="Required Linear label. Repeatable.")
    parser.add_argument("--ready-label", action="append", default=[], help="Required release-ready Linear label. Repeatable.")
    parser.add_argument("--done-state", default="Done")
    parser.add_argument("--blocked-state", default="Todo")
    parser.add_argument("--queued-state", default="Merging")
    parser.add_argument("--max", type=int, default=10, help="Maximum ready issues to process in one run.")
    parser.add_argument("--lock-dir", default=".orchestration/release-manager.lock")
    parser.add_argument("--lock-timeout", type=int, default=120)
    parser.add_argument("--delete-branch", action="store_true", help="Ask gh to delete branches after merge.")
    parser.add_argument("--merge-method", choices=["merge", "squash", "rebase"], help="Optional gh merge strategy for repos without a merge queue.")
    parser.add_argument("--comment-mode", choices=["minimal", "none", "verbose"], default="minimal", help="Linear comment detail level for apply runs.")
    parser.add_argument("--require-merge-queue", action="store_true", help=f"Stop without enqueuing if mode={MERGE_QUEUE_MODE} but no merge queue is enabled on the base branch.")
    parser.add_argument("--check-merge-queue", action="store_true", help="Report whether a merge queue is enabled on --base-branch and exit. Needs --repo or --workflow.")
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
        args.mode = args.mode or defaults.get("mode")
        args.ready_states = args.ready_states or defaults.get("ready_states")
        args.label = args.label or defaults.get("labels", [])
        args.ready_label = args.ready_label or defaults.get("ready_labels", [])
        args.done_state = defaults.get("done_state", args.done_state)
        args.blocked_state = defaults.get("blocked_state", args.blocked_state)
        args.queued_state = defaults.get("queued_state", args.queued_state)
        args.lock_dir = os.path.expandvars(defaults.get("lock_dir", args.lock_dir))
        args.delete_branch = bool(defaults.get("delete_branch", args.delete_branch))
        args.require_merge_queue = bool(defaults.get("require_merge_queue", args.require_merge_queue))
        args.merge_method = defaults.get("merge_method", args.merge_method)
        args.comment_mode = defaults.get("comment_mode", args.comment_mode)
        args.max = int(defaults.get("max_per_run", args.max))

    # Standalone readiness probe. Does not require Linear; handy in preflight/CI.
    if args.check_merge_queue:
        if shutil.which("gh") is None:
            parser.error("gh is required for --check-merge-queue")
        if not args.repo:
            parser.error("--check-merge-queue needs --repo OWNER/REPO (or --workflow with release_manager.repo)")
        status = merge_queue_status(args.repo, args.base_branch)
        payload: dict[str, Any] = {"merge_queue": status, "repo": args.repo, "base_branch": args.base_branch}
        if not status.get("enabled"):
            # Only probe the extra signals when there is a gap to diagnose, so the
            # healthy path stays a single API call.
            owner = repo_owner_type(args.repo)
            strict = base_branch_strict(args.repo, args.base_branch)
            payload["owner_type"] = owner.get("type")
            payload["private"] = owner.get("private")
            payload["strict_required_checks"] = strict.get("strict")
            payload["diagnosis"] = merge_queue_gap_message(
                args.base_branch, owner.get("type"), strict.get("strict"), owner.get("private")
            )
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            line = f"merge queue on {args.repo or '?'}@{args.base_branch}: {status['detail']}"
            if payload.get("diagnosis"):
                line += f"\n{payload['diagnosis']}"
            print(line)
        return 0 if status.get("enabled") else 1

    args.ready_states = args.ready_states or ["Ready to Merge", "In Review"]
    if args.queued_state and args.queued_state in args.ready_states:
        parser.error(
            "release_manager.queued_state must be distinct from ready_states; an overlap breaks the "
            "idempotency anchor and would re-enqueue in-flight PRs every pass"
        )
    required_labels = list(args.label or []) + list(args.ready_label or [])

    if args.merge_method and args.merge_method not in {"merge", "squash", "rebase"}:
        parser.error("release_manager.merge_method must be one of: merge, squash, rebase")
    if args.mode != MERGE_QUEUE_MODE and not args.merge_method:
        parser.error(
            "set --merge-method (merge|squash|rebase) when not using a merge queue "
            "(mode != github-merge-queue); otherwise gh pr merge --auto has no method and fails non-interactively"
        )
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
        states = scan_states(args.ready_states, args.queued_state)
        issues = fetch_issues(api_key, args.project_slug, states)
        qstate = args.queued_state
        q_distinct = bool(qstate) and qstate not in args.ready_states
        # Already-queued issues are reconciled every pass (finalize / block /
        # in_flight) and are NOT bounded by --max, so closeout never starves
        # under sustained inflow. Only fresh enqueues are bounded by --max.
        # Reconcile every issue WE queued regardless of later label edits -- the
        # lane put it in queued_state, so a label change must not strand a merged
        # PR. The label filter gates new enqueues (ready_issues) only.
        queued_issues = [i for i in issues if q_distinct and i.state == qstate]
        ready_issues = [i for i in issues if not (q_distinct and i.state == qstate)]
        selected, deferred = select_candidates(ready_issues, required_labels, args.max)
        actions: list[Action] = [process_issue(args, api_key, issue) for issue in queued_issues]
        actions += [process_issue(args, api_key, issue) for issue in selected]
        if deferred:
            actions.append(
                Action(
                    "release-manager",
                    "deferred",
                    f"{deferred} more ready issue(s) beyond --max={args.max}; re-run the lane to drain them",
                )
            )
        return actions

    def collect() -> tuple[list[Action], bool]:
        gate = merge_queue_gate(args)
        if gate and gate.status == "error":
            # Hard stop before any mutation: a merge queue was required and is missing.
            return [gate], True
        return ([gate] if gate else []) + run_batch(), False

    if args.apply:
        with ReleaseLock(Path(args.lock_dir).expanduser(), args.lock_timeout):
            actions, gate_failed = collect()
    else:
        actions, gate_failed = collect()

    summary = summarize(actions, gate_failed)
    payload = {
        "ok": summary["ok"],
        "apply": args.apply,
        "drained": summary["drained"],
        "counts": summary["counts"],
        "actions": [a.to_dict() for a in actions],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        drained = "drained" if summary["drained"] else "more passes needed"
        print(f"Release Manager {mode}: {len(actions)} action(s); {drained}")
        for action in actions:
            pr = f" {action.pr_url}" if action.pr_url else ""
            print(f"- {action.issue}: {action.status}: {action.message}{pr}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
