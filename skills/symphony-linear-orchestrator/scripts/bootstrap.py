#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATE_DIR = SKILL_DIR / "assets" / "templates"

LANES = {
    "small": {"label": "sym:small", "model": "gpt-5.4-mini", "reasoning": "medium"},
    "medium": {"label": "sym:medium", "model": "gpt-5.4-mini", "reasoning": "high"},
    "large": {"label": "sym:large", "model": "gpt-5.4", "reasoning": "high"},
    "content": {"label": "sym:content", "model": "gpt-5.4-mini", "reasoning": "medium"},
}

ANCHOR_CANDIDATES = [
    "README.md",
    "package.json",
    "pnpm-workspace.yaml",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "mix.exs",
    "pom.xml",
    "composer.json",
    "requirements.txt",
    "src",
    "app",
    "lib",
    "services",
    "packages",
]


def render(text: str, values: dict[str, str]) -> str:
    rendered = text
    for key, value in values.items():
        rendered = rendered.replace(f"__{key}__", value)
    return rendered


def write_file(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def detect_current_branch(target_repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(target_repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    branch = result.stdout.strip()
    if result.returncode != 0 or not branch or branch == "HEAD":
        return "main"
    return branch


def infer_required_paths(target_repo: Path) -> list[str]:
    anchors: list[str] = []
    for candidate in ANCHOR_CANDIDATES:
        if (target_repo / candidate).exists():
            anchors.append(candidate)
        if len(anchors) >= 3:
            break

    if anchors:
        return anchors

    for child in sorted(target_repo.iterdir()):
        if child.name in {".git", ".orchestration"}:
            continue
        anchors.append(child.name)
        break
    return anchors


def infer_github_repo(clone_url: str) -> str:
    candidates = [
        r"github\.com[:/](?P<owner>[^/\s:]+)/(?P<repo>[^/\s]+?)(?:\.git)?$",
        r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?$",
    ]
    for pattern in candidates:
        match = re.search(pattern, clone_url)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render starter orchestration artifacts into a target repo.")
    parser.add_argument("--target-repo", required=True, help="Path to the target repository.")
    parser.add_argument("--workflow-name", required=True, help="Workflow file stem.")
    parser.add_argument("--clone-url", required=True, help="Git clone URL for the target repository.")
    parser.add_argument("--linear-project-slug", required=True, help="Linear project slug ID.")
    parser.add_argument(
        "--lane",
        choices=sorted(LANES.keys()),
        default="medium",
        help="Routing lane label plus pinned model/reasoning profile.",
    )
    parser.add_argument(
        "--max-concurrent-agents",
        type=int,
        default=1,
        help="Worker slots for this workflow. Default is conservative for a first run.",
    )
    parser.add_argument(
        "--required-branch",
        help="Workspace bootstrap branch assertion. Defaults to the target repo's current branch.",
    )
    parser.add_argument(
        "--required-path",
        action="append",
        default=[],
        help="Repo-root anchor path that must exist after workspace setup. Repeat to add more paths.",
    )
    parser.add_argument("--with-release-manager", action="store_true", help="Also render a single-writer Release Manager lane template.")
    parser.add_argument("--github-repo", help="GitHub repo in OWNER/REPO form for the Release Manager lane. Defaults to inferring from --clone-url.")
    parser.add_argument("--write", action="store_true", help="Write files instead of dry-running.")
    parser.add_argument("--force", action="store_true", help="Overwrite rendered files if they already exist.")
    args = parser.parse_args()

    target_repo = Path(args.target_repo).expanduser().resolve()
    if not target_repo.exists():
        print(f"Target repo does not exist: {target_repo}", file=sys.stderr)
        return 1

    if not (target_repo / ".git").exists():
        print(f"Target path is not a git repository: {target_repo}", file=sys.stderr)
        return 1

    if args.max_concurrent_agents < 1:
        print("--max-concurrent-agents must be at least 1", file=sys.stderr)
        return 1

    lane = LANES[args.lane]
    required_branch = args.required_branch or detect_current_branch(target_repo)
    required_paths = args.required_path or infer_required_paths(target_repo)
    if not required_paths:
        print(
            "Could not infer any required workspace anchor paths. Pass --required-path explicitly.",
            file=sys.stderr,
        )
        return 1

    repo_name = target_repo.name
    orchestration_dir = target_repo / ".orchestration"
    values = {
        "WORKFLOW_NAME": args.workflow_name,
        "CLONE_URL": args.clone_url,
        "LINEAR_PROJECT_SLUG": args.linear_project_slug,
        "REPO_NAME": repo_name,
        "ISSUE_LABEL": lane["label"],
        "MODEL": lane["model"],
        "REASONING_EFFORT": lane["reasoning"],
        "MAX_CONCURRENT_AGENTS": str(args.max_concurrent_agents),
        "REQUIRED_BRANCH": required_branch,
        "REQUIRED_PATHS_JSON": json.dumps(required_paths),
        "GITHUB_REPO": args.github_repo or infer_github_repo(args.clone_url),
    }

    if args.with_release_manager and not values["GITHUB_REPO"]:
        print("--with-release-manager requires --github-repo when --clone-url is not a GitHub URL", file=sys.stderr)
        return 1

    outputs = {
        orchestration_dir / f"{args.workflow_name}.WORKFLOW.md": TEMPLATE_DIR / "workflow.WORKFLOW.md.tmpl",
        orchestration_dir / "RUNBOOK.md": TEMPLATE_DIR / "runbook.md.tmpl",
        orchestration_dir / "LEARNINGS.md": TEMPLATE_DIR / "learnings.md.tmpl",
        orchestration_dir / f"{args.workflow_name}.BRIEF.md": TEMPLATE_DIR / "program-brief.md.tmpl",
        orchestration_dir / "LINEAR_ISSUE_TEMPLATE.md": TEMPLATE_DIR / "linear-issue.md.tmpl",
        orchestration_dir / "AGENTS_ADDITIONS.md": TEMPLATE_DIR / "agents-additions.md.tmpl",
    }
    if args.with_release_manager:
        outputs[orchestration_dir / "release-manager.WORKFLOW.md"] = TEMPLATE_DIR / "release-manager.WORKFLOW.md.tmpl"

    manifest = []
    for destination, template_path in outputs.items():
        content = render(template_path.read_text(), values)
        entry = {"destination": str(destination), "template": str(template_path)}
        if not args.write:
            entry["rendered"] = content
        else:
            write_file(destination, content, args.force)
        manifest.append(entry)

    print(
        json.dumps(
            {
                "write": args.write,
                "lane": args.lane,
                "label": lane["label"],
                "model": lane["model"],
                "reasoning": lane["reasoning"],
                "required_branch": required_branch,
                "required_paths": required_paths,
                "max_concurrent_agents": args.max_concurrent_agents,
                "release_manager": args.with_release_manager,
                "github_repo": values["GITHUB_REPO"] or None,
                "files": manifest,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
