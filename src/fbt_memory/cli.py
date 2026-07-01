#!/usr/bin/env python3
"""
fbt — the fbt-memory CLI.

    fbt init   [path]       scaffold / adopt a markdown vault
    fbt ingest [path]       index the vault (hybrid BM25 + vector via qmd)
    fbt ask    "question"   retrieve + answer
    fbt verify [path]       score memory integrity /100 — nonzero exit on rot
    fbt verify --demo       plant a contradiction and watch the gate catch it

Stop renting your mind. Own it — and prove it never rotted.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .core import vault as vaultlib

STARTER_NOTE = """\
---
name: welcome
status: hot
updated: 2026-07-01
---

# Welcome to your vault

This is a plain markdown note. You can read it, edit it in any editor, and
`git diff` it. `fbt-memory` never takes it anywhere you don't tell it to.

Try:

    fbt ingest .
    fbt ask "what is this vault?"
    fbt verify .

## Changelog
- 2026-07-01: created by `fbt init`.
"""


def cmd_init(args) -> int:
    root = Path(args.path).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    existing = list(root.rglob("*.md"))
    if existing:
        print(f"adopted existing vault at {root}  ({len(existing)} markdown notes found)")
    else:
        (root / "welcome.md").write_text(STARTER_NOTE, encoding="utf-8")
        print(f"scaffolded a new vault at {root}  (created welcome.md)")
    print("\nnext:")
    print(f"  export FBT_VAULT={root}")
    print("  fbt ingest .   # index it")
    print("  fbt verify .   # prove it hasn't rotted")
    return 0


def cmd_ingest(args) -> int:
    print("fbt ingest: qmd-backed indexing lands in the next build step (core/index.py).",
          file=sys.stderr)
    return 2


def cmd_ask(args) -> int:
    print("fbt ask: retrieval + reader lands in the next build step (core/index.py).",
          file=sys.stderr)
    return 2


def cmd_verify(args) -> int:
    # The star primitive. Wired in the next build step (core/verify.py):
    #   - weighted memory-integrity score /100, nonzero exit on rot
    #   - `--demo`: plant a contradiction and watch the gate surface the current
    #     value and FAIL the stale one ("watch it catch its own rot").
    print("fbt verify: the verification gate lands in the next build step (core/verify.py).",
          file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fbt",
        description="fbt-memory — verifiable, local-first AI memory. "
                    "Own your mind — and prove it never rotted.",
    )
    p.add_argument("--version", action="version", version=f"fbt-memory {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    pi = sub.add_parser("init", help="scaffold / adopt a markdown vault")
    pi.add_argument("path", nargs="?", default=".", help="vault directory (default: .)")
    pi.set_defaults(func=cmd_init)

    pg = sub.add_parser("ingest", help="index the vault (qmd hybrid retrieval)")
    pg.add_argument("path", nargs="?", default=".")
    pg.set_defaults(func=cmd_ingest)

    pa = sub.add_parser("ask", help="retrieve + answer")
    pa.add_argument("question")
    pa.set_defaults(func=cmd_ask)

    pv = sub.add_parser("verify", help="score memory integrity /100 (nonzero exit on rot)")
    pv.add_argument("path", nargs="?", default=".")
    pv.add_argument("--demo", action="store_true",
                    help="plant a contradiction and watch the gate catch it")
    pv.set_defaults(func=cmd_verify)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
