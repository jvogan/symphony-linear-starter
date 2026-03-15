#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def command_check(name: str) -> dict:
    path = shutil.which(name)
    return {
        "name": name,
        "ok": path is not None,
        "path": path,
        "message": "found" if path else "missing from PATH",
    }


def symphony_check() -> dict:
    env_path = os.environ.get("SYMPHONY_BIN")
    if env_path:
        path = Path(env_path).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return {
                "name": "symphony",
                "ok": True,
                "path": str(path),
                "message": "found via SYMPHONY_BIN",
            }
        return {
            "name": "symphony",
            "ok": False,
            "path": str(path),
            "message": "SYMPHONY_BIN is set but not executable",
        }
    path = shutil.which("symphony")
    return {
        "name": "symphony",
        "ok": path is not None,
        "path": path,
        "message": "found" if path else "missing from PATH and SYMPHONY_BIN",
    }


def github_auth_check() -> dict:
    gh_path = shutil.which("gh")
    if not gh_path:
        return {
            "name": "github_auth",
            "ok": False,
            "message": "gh is not installed",
        }
    result = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    output = " ".join((result.stdout + " " + result.stderr).split())
    return {
        "name": "github_auth",
        "ok": result.returncode == 0,
        "message": output or "no output",
    }


def env_check(name: str) -> dict:
    value = os.environ.get(name)
    return {
        "name": name,
        "ok": bool(value),
        "message": "set" if value else "missing",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local readiness for the Symphony + Linear starter.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    args = parser.parse_args()

    checks = [
        command_check("git"),
        command_check("gh"),
        command_check("codex"),
        command_check("python3"),
        symphony_check(),
        env_check("LINEAR_API_KEY"),
        github_auth_check(),
    ]

    ok = all(check["ok"] for check in checks)
    payload = {"ok": ok, "checks": checks}

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for check in checks:
            status = "OK" if check["ok"] else "FAIL"
            details = check.get("path") or check["message"]
            print(f"[{status}] {check['name']}: {details}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

