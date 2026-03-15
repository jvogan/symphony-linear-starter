#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
TEMPLATE_DIR = SKILL_DIR / "assets" / "templates"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Render starter orchestration artifacts into a target repo.")
    parser.add_argument("--target-repo", required=True, help="Path to the target repository.")
    parser.add_argument("--workflow-name", required=True, help="Workflow file stem.")
    parser.add_argument("--clone-url", required=True, help="Git clone URL for the target repository.")
    parser.add_argument("--linear-project-slug", required=True, help="Linear project slug ID.")
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

    repo_name = target_repo.name
    orchestration_dir = target_repo / ".orchestration"
    values = {
        "WORKFLOW_NAME": args.workflow_name,
        "CLONE_URL": args.clone_url,
        "LINEAR_PROJECT_SLUG": args.linear_project_slug,
        "REPO_NAME": repo_name,
    }

    outputs = {
        orchestration_dir / f"{args.workflow_name}.WORKFLOW.md": TEMPLATE_DIR / "workflow.WORKFLOW.md.tmpl",
        orchestration_dir / f"{args.workflow_name}.BRIEF.md": TEMPLATE_DIR / "program-brief.md.tmpl",
        orchestration_dir / "LINEAR_ISSUE_TEMPLATE.md": TEMPLATE_DIR / "linear-issue.md.tmpl",
        orchestration_dir / "AGENTS_ADDITIONS.md": TEMPLATE_DIR / "agents-additions.md.tmpl",
    }

    manifest = []
    for destination, template_path in outputs.items():
        content = render(template_path.read_text(), values)
        manifest.append({"destination": str(destination), "template": str(template_path)})
        if args.write:
            write_file(destination, content, args.force)

    print(json.dumps({"write": args.write, "files": manifest}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

