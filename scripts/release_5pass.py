#!/usr/bin/env python3
"""Run the adversarial release review with a bounded, fail-closed contract."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    timeout = int(os.environ.get("FIVEPASS_TIMEOUT_SECONDS", "600"))
    context = os.environ.get("FIVEPASS_CONTEXT", "Homestead Memory release candidate")
    cmd = [
        "5pass", "--backend", os.environ.get("FIVEPASS_BACKEND", "claude"), "--json",
        "--lenses", "correctness,edge-cases,security,tests,integration",
        "--context", context,
    ]
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        print("release gate blocked: 5pass is not installed", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print(f"release gate blocked: 5pass exceeded {timeout}s", file=sys.stderr)
        return 2
    if result.returncode != 0:
        print(result.stdout or result.stderr, file=sys.stderr)
        return 2
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"release gate blocked: invalid 5pass JSON: {exc}", file=sys.stderr)
        return 2
    findings = report.get("findings")
    if not isinstance(findings, list):
        print("release gate blocked: 5pass response has no findings list", file=sys.stderr)
        return 2
    failed_lenses = report.get("failed_lenses", [])
    if failed_lenses:
        print(f"release gate blocked: failed lenses: {', '.join(map(str, failed_lenses))}", file=sys.stderr)
        return 2
    Path("fivepass-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    blocking = [f for f in findings if str(f.get("severity", "")).lower() in {"critical", "major", "high"}]
    if blocking:
        print(json.dumps({"blocking_findings": blocking}, indent=2), file=sys.stderr)
        return 1
    print(f"five-agent release review passed ({len(findings)} non-blocking findings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
