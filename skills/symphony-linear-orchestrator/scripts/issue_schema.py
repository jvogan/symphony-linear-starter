#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SECTION_NAMES = [
    "Summary",
    "Acceptance Criteria",
    "Validation Commands",
    "Touched Areas",
    "Dependencies",
    "Risk Notes",
    "Complexity",
]

SCHEMA_PATTERN = re.compile(r"<!--\s*symphony:schema(?P<body>.*?)-->", re.DOTALL)
HEADING_PATTERN = re.compile(r"^## (?P<name>.+)$", re.MULTILINE)
REDACTION_NOTE = (
    "> Do not include secrets, credentials, tokens, session cookies, personal data, "
    "or raw customer payloads in this issue body or later worker comments. Use redacted identifiers "
    "and secure stores instead."
)


def read_input(path: str | None) -> str:
    if path:
        return Path(path).read_text()
    return sys.stdin.read()


def ensure_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    raise ValueError("expected string or list")


def normalize_touched_areas(value) -> list[dict[str, str]]:
    touched: list[dict[str, str]] = []
    if not value:
        return touched
    if not isinstance(value, list):
        raise ValueError("touched_areas must be a list")
    for entry in value:
        if isinstance(entry, str):
            path, _, reason = entry.partition(" - ")
            touched.append({"path": path.strip(), "reason": reason.strip()})
            continue
        if isinstance(entry, dict):
            path = str(entry.get("path", "")).strip()
            reason = str(entry.get("reason", "")).strip()
            if not path:
                raise ValueError("each touched_areas entry must include a path")
            touched.append({"path": path, "reason": reason})
            continue
        raise ValueError("touched_areas entries must be strings or objects")
    return touched


def render_issue(data: dict) -> str:
    summary = str(data.get("summary", "")).strip()
    acceptance = ensure_list(data.get("acceptance_criteria"))
    validation = ensure_list(data.get("validation_commands"))
    touched = normalize_touched_areas(data.get("touched_areas"))
    dependencies = ensure_list(data.get("dependencies"))
    risk_notes = ensure_list(data.get("risk_notes"))
    complexity = str(data.get("complexity", "")).strip().lower()

    if not summary:
        raise ValueError("summary is required")
    if not acceptance:
        raise ValueError("acceptance_criteria is required")
    if not validation:
        raise ValueError("validation_commands is required")
    if not touched:
        raise ValueError("touched_areas is required")
    if complexity not in {"small", "medium", "large"}:
        raise ValueError("complexity must be small, medium, or large")

    dependency_lines = dependencies or ["Blocked by: none"]
    risk_lines = risk_notes or ["None known."]
    touched_lines = [
        f"- `{entry['path']}` - {entry['reason']}".rstrip(" -") for entry in touched
    ]
    schema_lines = "\n".join(f"  - {entry['path']}" for entry in touched)

    return f"""## Summary

{summary}

{REDACTION_NOTE}

## Acceptance Criteria

{chr(10).join(f"- [ ] {item}" for item in acceptance)}

## Validation Commands

```bash
{chr(10).join(validation)}
```

## Touched Areas

{chr(10).join(touched_lines)}

## Dependencies

{chr(10).join(dependency_lines)}

## Risk Notes

{chr(10).join(f"- {item}" for item in risk_lines)}

## Complexity

tier: {complexity}

<!-- symphony:schema
schema_version: 1
touched_areas:
{schema_lines}
complexity: {complexity}
-->
"""


def split_sections(markdown: str) -> dict[str, str]:
    matches = list(HEADING_PATTERN.finditer(markdown))
    if not matches:
        raise ValueError("no issue sections found")
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[match.group("name").strip()] = markdown[start:end].strip()
    return sections


def extract_schema(markdown: str) -> dict[str, object]:
    match = SCHEMA_PATTERN.search(markdown)
    if not match:
        return {}
    body = match.group("body")
    complexity_match = re.search(r"complexity:\s*(\w+)", body)
    touched_paths = re.findall(r"^\s*-\s*(.+?)\s*$", body, re.MULTILINE)
    return {
        "complexity": complexity_match.group(1).strip().lower() if complexity_match else None,
        "touched_areas": [path.strip() for path in touched_paths],
    }


def parse_bullets(text: str, checkbox: bool = False) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if checkbox:
            stripped = re.sub(r"^- \[[ xX]\]\s*", "", stripped)
        else:
            stripped = re.sub(r"^-\s*", "", stripped)
        if stripped:
            values.append(stripped)
    return values


def parse_validation(text: str) -> list[str]:
    block = re.search(r"```(?:bash)?\n(?P<body>.*?)```", text, re.DOTALL)
    source = block.group("body") if block else text
    return [line.rstrip() for line in source.strip().splitlines() if line.strip()]


def parse_touched_areas(text: str, schema: dict[str, object]) -> list[dict[str, str]]:
    touched: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        match = re.match(r"-\s+`(?P<path>[^`]+)`(?:\s*-\s*(?P<reason>.*))?$", stripped)
        if not match:
            match = re.match(r"-\s+(?P<path>.+?)(?:\s*-\s*(?P<reason>.*))?$", stripped)
        if match:
            touched.append(
                {
                    "path": match.group("path").strip(),
                    "reason": (match.group("reason") or "").strip(),
                }
            )
    if touched:
        return touched
    return normalize_touched_areas(schema.get("touched_areas", []))


def normalize_issue(markdown: str) -> str:
    sections = split_sections(markdown)
    missing = [name for name in SECTION_NAMES if name not in sections]
    if missing:
        raise ValueError(f"missing required sections: {', '.join(missing)}")

    schema = extract_schema(markdown)
    complexity_text = sections["Complexity"]
    complexity_match = re.search(r"tier:\s*(\w+)", complexity_text)
    complexity = (complexity_match.group(1) if complexity_match else schema.get("complexity") or "").strip().lower()

    summary_lines = [
        line
        for line in sections["Summary"].splitlines()
        if line.strip() and line.strip() != REDACTION_NOTE
    ]

    data = {
        "summary": "\n".join(summary_lines).strip(),
        "acceptance_criteria": parse_bullets(sections["Acceptance Criteria"], checkbox=True),
        "validation_commands": parse_validation(sections["Validation Commands"]),
        "touched_areas": parse_touched_areas(sections["Touched Areas"], schema),
        "dependencies": [line.strip() for line in sections["Dependencies"].splitlines() if line.strip()],
        "risk_notes": parse_bullets(sections["Risk Notes"]),
        "complexity": complexity,
    }
    return render_issue(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render or normalize Symphony Linear issue bodies.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render", help="Render a canonical issue body from structured JSON.")
    render_parser.add_argument("--input", help="Path to JSON input. Reads stdin when omitted.")

    normalize_parser = subparsers.add_parser("normalize", help="Normalize an existing markdown issue body.")
    normalize_parser.add_argument("--input", help="Path to markdown input. Reads stdin when omitted.")

    args = parser.parse_args()

    try:
        if args.command == "render":
            payload = json.loads(read_input(args.input))
            print(render_issue(payload).rstrip())
            return 0

        if args.command == "normalize":
            print(normalize_issue(read_input(args.input)).rstrip())
            return 0
    except (ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print("unknown command", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
