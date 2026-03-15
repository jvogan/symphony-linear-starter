#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from pathlib import Path


REQUIRED_STATES = ["Backlog", "Todo", "In Progress", "In Review", "Done"]
PLACEHOLDER_PATTERNS = [
    r"replace-with-linear-project-slug",
    r"<clone-url>",
    r"__LINEAR_PROJECT_SLUG__",
    r"__CLONE_URL__",
]


def make_result(name: str, status: str, message: str) -> dict:
    return {"check": name, "status": status, "message": message}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate local readiness before starting Symphony.")
    parser.add_argument("--target-repo", required=True, help="Path to the target repository.")
    parser.add_argument("--workflow", required=True, help="Path to the rendered workflow file.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    args = parser.parse_args()

    target_repo = Path(args.target_repo).expanduser().resolve()
    workflow = Path(args.workflow).expanduser().resolve()
    results = []

    if target_repo.exists():
        results.append(make_result("target_repo", "pass", f"found {target_repo}"))
    else:
        results.append(make_result("target_repo", "fail", "target repo is missing"))

    if (target_repo / ".git").exists():
        results.append(make_result("git_repo", "pass", "git metadata found"))
    else:
        results.append(make_result("git_repo", "fail", "target repo is not a git repository"))

    agents_path = target_repo / "AGENTS.md"
    additions_path = target_repo / ".orchestration" / "AGENTS_ADDITIONS.md"
    if agents_path.exists() or additions_path.exists():
        source = "AGENTS.md" if agents_path.exists() else "AGENTS_ADDITIONS.md"
        results.append(make_result("repo_guidance", "pass", f"found {source}"))
    else:
        results.append(make_result("repo_guidance", "fail", "missing AGENTS.md and generated AGENTS additions"))

    if workflow.exists():
        workflow_text = workflow.read_text()
        results.append(make_result("workflow_exists", "pass", f"found {workflow}"))
    else:
        workflow_text = ""
        results.append(make_result("workflow_exists", "fail", "workflow file is missing"))

    if workflow_text:
        unresolved = [pattern for pattern in PLACEHOLDER_PATTERNS if re.search(pattern, workflow_text)]
        if unresolved:
            results.append(make_result("workflow_placeholders", "fail", f"unresolved placeholders: {', '.join(unresolved)}"))
        else:
            results.append(make_result("workflow_placeholders", "pass", "core placeholders resolved"))

        missing_states = [state for state in REQUIRED_STATES if state not in workflow_text]
        if missing_states:
            results.append(make_result("state_model", "fail", f"missing states: {', '.join(missing_states)}"))
        else:
            results.append(make_result("state_model", "pass", "required Linear state model present"))
    else:
        results.append(make_result("workflow_placeholders", "skip", "skipped because workflow file is missing"))
        results.append(make_result("state_model", "skip", "skipped because workflow file is missing"))

    if os.environ.get("LINEAR_API_KEY"):
        results.append(make_result("linear_api_key", "pass", "LINEAR_API_KEY is set"))
    else:
        results.append(make_result("linear_api_key", "fail", "LINEAR_API_KEY is missing"))

    issue_template = target_repo / ".orchestration" / "LINEAR_ISSUE_TEMPLATE.md"
    if issue_template.exists():
        results.append(make_result("queued_work_scaffold", "pass", "issue planning scaffold exists"))
    else:
        results.append(make_result("queued_work_scaffold", "warn", "cannot confirm queue readiness because LINEAR_ISSUE_TEMPLATE.md is missing"))

    runbook = target_repo / ".orchestration" / "RUNBOOK.md"
    if runbook.exists():
        results.append(make_result("runbook", "pass", "operator runbook scaffold exists"))
    else:
        results.append(make_result("runbook", "warn", "missing RUNBOOK.md; the repo has no durable operator playbook yet"))

    learnings = target_repo / ".orchestration" / "LEARNINGS.md"
    if learnings.exists():
        results.append(make_result("learnings", "pass", "learnings log scaffold exists"))
    else:
        results.append(make_result("learnings", "warn", "missing LEARNINGS.md; the repo has no built-in improvement loop yet"))

    failures = [result for result in results if result["status"] == "fail"]
    warnings = [result for result in results if result["status"] == "warn"]
    exit_code = 1 if failures else 0
    payload = {"ok": not failures, "warnings": len(warnings), "results": results}

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for result in results:
            print(f"[{result['status'].upper()}] {result['check']}: {result['message']}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
