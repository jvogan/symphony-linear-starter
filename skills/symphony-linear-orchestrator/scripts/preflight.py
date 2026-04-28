#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


REQUIRED_STATES = ["Backlog", "Todo", "In Progress", "In Review", "Done"]
PLACEHOLDER_PATTERNS = [
    r"replace-with-linear-project-slug",
    r"<clone-url>",
    r"__WORKFLOW_NAME__",
    r"__LINEAR_PROJECT_SLUG__",
    r"__CLONE_URL__",
    r"__ISSUE_LABEL__",
    r"__MODEL__",
    r"__REASONING_EFFORT__",
    r"__MAX_CONCURRENT_AGENTS__",
    r"__REQUIRED_BRANCH__",
    r"__REQUIRED_PATHS_JSON__",
]
SECRET_PATTERNS = [
    re.compile(r"api_key:\s*[\"']?(?!\$|\$\{)[A-Za-z0-9_\-]{12,}"),
    re.compile(r"https?://[^/\s:@]+(?::[^/\s@]+)?@"),
]


def make_result(name: str, status: str, message: str) -> dict:
    return {"check": name, "status": status, "message": message}


def contains_all(text: str, *snippets: str) -> bool:
    return all(snippet in text for snippet in snippets)


def extract_json_array(text: str, key: str) -> list[str] | None:
    match = re.search(rf"{re.escape(key)}:\s*(\[[^\n]*\])", text)
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def extract_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(key)}:\s*[\"']?(?P<value>[^\"'\n#]+)", text, re.MULTILINE)
    if not match:
        return None
    return match.group("value").strip()


def extract_int(text: str, key: str) -> int | None:
    value = extract_scalar(text, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def extract_campaign_field(text: str, field: str) -> str | None:
    match = re.search(r"^campaign:\s*\n(?P<body>(?:[ \t]+[^\n]*\n?)+)", text, re.MULTILINE)
    if not match:
        return None
    return extract_scalar(match.group("body"), field)


def extract_required_branch(text: str) -> str | None:
    match = re.search(r'required_branch:\s*"?(?P<branch>[^"\n]+)"?', text)
    if not match:
        return None
    return match.group("branch").strip()


def current_branch(target_repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(target_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    branch = result.stdout.strip()
    if result.returncode != 0 or not branch:
        return None
    return branch


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

    guidance_text = ""
    if agents_path.exists():
        guidance_text += agents_path.read_text()
    if additions_path.exists():
        guidance_text += "\n" + additions_path.read_text()

    if guidance_text and re.search(r"\b(secret|credential|token|pii|sensitive)\b", guidance_text, re.IGNORECASE):
        results.append(make_result("guidance_sensitivity_rules", "pass", "repo guidance mentions secret or sensitive-data handling"))
    elif guidance_text:
        results.append(make_result("guidance_sensitivity_rules", "warn", "repo guidance exists but does not clearly call out secret or sensitive-data handling"))
    else:
        results.append(make_result("guidance_sensitivity_rules", "skip", "skipped because repo guidance is missing"))

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

        if contains_all(workflow_text, "assertions:", "required_branch:", "required_paths:"):
            results.append(make_result("workspace_assertions", "pass", "workspace bootstrap assertions are configured"))
        else:
            results.append(make_result("workspace_assertions", "fail", "workflow is missing workspace assertions for branch or repo anchors"))

        if contains_all(workflow_text, "guardrails:", "no_progress:", "token_thresholds:", "minutes_thresholds:", "retry_limit:"):
            results.append(make_result("no_progress_guardrail", "pass", "workflow includes a no-progress guardrail block"))
        else:
            results.append(make_result("no_progress_guardrail", "fail", "workflow is missing a no-progress guardrail block"))

        labels = extract_json_array(workflow_text, "labels")
        assignee = extract_scalar(workflow_text, "assignee")
        routing_guard = bool(labels or assignee)

        if labels:
            if all(label.startswith("sym:") for label in labels):
                results.append(make_result("routing_labels", "pass", f"workflow routes only labels: {', '.join(labels)}"))
            else:
                results.append(make_result("routing_labels", "warn", f"workflow label filters exist but are not all sym:* labels: {', '.join(labels)}"))
        elif assignee:
            results.append(make_result("routing_labels", "pass", f"workflow routes by assignee: {assignee}"))
        else:
            results.append(make_result("routing_labels", "warn", "workflow has no explicit label filters; this is acceptable only for a single-lane setup"))

        campaign_mode = extract_campaign_field(workflow_text, "mode")
        campaign_label = extract_campaign_field(workflow_text, "routing_label")
        integration_owner = extract_campaign_field(workflow_text, "integration_owner")
        campaign_trust = extract_campaign_field(workflow_text, "trust")
        missing_campaign_fields = [
            name
            for name, value in {
                "mode": campaign_mode,
                "routing_label": campaign_label,
                "trust": campaign_trust,
                "integration_owner": integration_owner,
            }.items()
            if not value
        ]
        if missing_campaign_fields:
            results.append(make_result("campaign_metadata", "warn", f"campaign metadata missing: {', '.join(missing_campaign_fields)}"))
        else:
            results.append(make_result("campaign_metadata", "pass", f"campaign mode {campaign_mode}, owner {integration_owner}"))

        if campaign_label and labels and campaign_label not in labels:
            results.append(make_result("campaign_routing_match", "warn", f"campaign routing label {campaign_label} is not in tracker labels: {', '.join(labels)}"))
        elif campaign_label and labels:
            results.append(make_result("campaign_routing_match", "pass", "campaign routing label matches tracker label filter"))
        elif campaign_label:
            results.append(make_result("campaign_routing_match", "skip", "campaign routing label present but workflow does not use label routing"))
        else:
            results.append(make_result("campaign_routing_match", "skip", "skipped because campaign routing label is missing"))

        if "shell_environment_policy.inherit=all" in workflow_text:
            results.append(make_result("codex_env_policy", "warn", "workflow inherits the full shell environment; prefer include_only for required variables"))
        elif "shell_environment_policy.include_only" in workflow_text:
            results.append(make_result("codex_env_policy", "pass", "workflow uses an explicit shell environment allowlist"))
        else:
            results.append(make_result("codex_env_policy", "warn", "workflow does not declare a Codex shell environment policy"))

        if "danger-full-access" in workflow_text and not routing_guard:
            results.append(make_result("codex_full_access_routing", "fail", "danger-full-access requires a label or assignee routing guard"))
        elif "danger-full-access" in workflow_text:
            results.append(make_result("codex_full_access_routing", "warn", "danger-full-access is configured; use only with trusted Linear issue authors"))
        else:
            results.append(make_result("codex_full_access_routing", "pass", "workflow does not use danger-full-access"))

        max_concurrent_agents = extract_int(workflow_text, "max_concurrent_agents")
        if max_concurrent_agents and max_concurrent_agents > 1:
            if contains_all(workflow_text, "Touched Areas", "overlap"):
                results.append(make_result("concurrency_overlap_guidance", "pass", "concurrent workflow includes touched-area overlap guidance"))
            else:
                results.append(make_result("concurrency_overlap_guidance", "warn", "concurrent workflow should tell workers how to handle touched-area overlap"))
        else:
            results.append(make_result("concurrency_overlap_guidance", "pass", "single-worker workflow does not need overlap-specific guidance"))

        has_snapshot_promote = "snapshot-promote" in workflow_text and "after_run:" in workflow_text
        if has_snapshot_promote and max_concurrent_agents and max_concurrent_agents > 1:
            results.append(make_result("snapshot_promote_concurrency", "fail", "snapshot-promote in after_run is unsafe with concurrent workers"))
        elif has_snapshot_promote:
            results.append(make_result("snapshot_promote_concurrency", "warn", "snapshot-promote should be reserved for single-worker or low-overlap campaigns"))
        else:
            results.append(make_result("snapshot_promote_concurrency", "pass", "workflow does not use snapshot-promote"))

        prompt_mentions_review_gate = contains_all(workflow_text, "In Review", "not directly to `Done`")
        if integration_owner == "orchestrator" and prompt_mentions_review_gate:
            results.append(make_result("closeout_contract", "pass", "worker prompt and campaign metadata both use an orchestrator review gate"))
        elif integration_owner and prompt_mentions_review_gate:
            results.append(make_result("closeout_contract", "warn", f"worker prompt uses In Review, but integration_owner is {integration_owner}"))
        elif integration_owner:
            results.append(make_result("closeout_contract", "warn", "campaign metadata exists but worker closeout instructions are unclear"))
        else:
            results.append(make_result("closeout_contract", "skip", "skipped because campaign integration owner is missing"))

        required_branch = extract_required_branch(workflow_text)
        repo_branch = current_branch(target_repo)
        if required_branch and repo_branch:
            if required_branch == repo_branch:
                results.append(make_result("required_branch", "pass", f"repo branch matches workflow assertion: {required_branch}"))
            else:
                results.append(make_result("required_branch", "fail", f"workflow expects branch {required_branch}, but repo is on {repo_branch}"))
        elif required_branch:
            results.append(make_result("required_branch", "warn", f"workflow expects branch {required_branch}, but current repo branch could not be determined"))
        else:
            results.append(make_result("required_branch", "fail", "workflow does not declare workspace.assertions.required_branch"))

        required_paths = extract_json_array(workflow_text, "required_paths")
        if required_paths:
            missing_paths = [path for path in required_paths if not (target_repo / path).exists()]
            if missing_paths:
                results.append(make_result("required_paths", "fail", f"workflow anchor paths missing in target repo: {', '.join(missing_paths)}"))
            else:
                results.append(make_result("required_paths", "pass", f"workflow anchor paths exist: {', '.join(required_paths)}"))
        else:
            results.append(make_result("required_paths", "fail", "workflow does not declare workspace.assertions.required_paths"))

        if any(pattern.search(workflow_text) for pattern in SECRET_PATTERNS):
            results.append(make_result("workflow_secret_hygiene", "fail", "workflow appears to contain an inline secret or credential-bearing clone URL"))
        else:
            results.append(make_result("workflow_secret_hygiene", "pass", "workflow does not appear to contain inline secrets"))
    else:
        results.append(make_result("workflow_placeholders", "skip", "skipped because workflow file is missing"))
        results.append(make_result("state_model", "skip", "skipped because workflow file is missing"))
        results.append(make_result("workspace_assertions", "skip", "skipped because workflow file is missing"))
        results.append(make_result("no_progress_guardrail", "skip", "skipped because workflow file is missing"))
        results.append(make_result("routing_labels", "skip", "skipped because workflow file is missing"))
        results.append(make_result("campaign_metadata", "skip", "skipped because workflow file is missing"))
        results.append(make_result("campaign_routing_match", "skip", "skipped because workflow file is missing"))
        results.append(make_result("codex_env_policy", "skip", "skipped because workflow file is missing"))
        results.append(make_result("codex_full_access_routing", "skip", "skipped because workflow file is missing"))
        results.append(make_result("concurrency_overlap_guidance", "skip", "skipped because workflow file is missing"))
        results.append(make_result("snapshot_promote_concurrency", "skip", "skipped because workflow file is missing"))
        results.append(make_result("closeout_contract", "skip", "skipped because workflow file is missing"))
        results.append(make_result("required_branch", "skip", "skipped because workflow file is missing"))
        results.append(make_result("required_paths", "skip", "skipped because workflow file is missing"))
        results.append(make_result("workflow_secret_hygiene", "skip", "skipped because workflow file is missing"))

    if os.environ.get("LINEAR_API_KEY"):
        results.append(make_result("linear_api_key", "pass", "LINEAR_API_KEY is set"))
    else:
        results.append(make_result("linear_api_key", "fail", "LINEAR_API_KEY is missing"))

    issue_template = target_repo / ".orchestration" / "LINEAR_ISSUE_TEMPLATE.md"
    if issue_template.exists():
        issue_template_text = issue_template.read_text()
        results.append(make_result("queued_work_scaffold", "pass", "issue planning scaffold exists"))
        if "<!-- symphony:schema" in issue_template_text:
            results.append(make_result("issue_schema", "pass", "issue template includes the symphony schema block"))
        else:
            results.append(make_result("issue_schema", "fail", "issue template is missing the symphony schema block"))

        if re.search(r"(Do not include secrets|personal data|tokens|session cookies)", issue_template_text, re.IGNORECASE):
            results.append(make_result("issue_redaction_note", "pass", "issue template reminds operators to redact secrets and sensitive data"))
        else:
            results.append(make_result("issue_redaction_note", "warn", "issue template exists but does not explicitly remind operators to redact secrets or sensitive data"))
    else:
        results.append(make_result("queued_work_scaffold", "warn", "cannot confirm queue readiness because LINEAR_ISSUE_TEMPLATE.md is missing"))
        results.append(make_result("issue_schema", "skip", "skipped because LINEAR_ISSUE_TEMPLATE.md is missing"))
        results.append(make_result("issue_redaction_note", "skip", "skipped because LINEAR_ISSUE_TEMPLATE.md is missing"))

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
