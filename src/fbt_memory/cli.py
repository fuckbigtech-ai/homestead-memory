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
    ignore = root / ".fbtignore"
    if not ignore.exists():
        ignore.write_text(
            "# .fbtignore — paths fbt-memory should NOT treat as memory notes.\n"
            "# A trailing / excludes a whole directory; otherwise it's a glob.\n"
            "# Keep generated/report output here so `fbt verify` never flags it.\n"
            "# examples:\n"
            "# reports/\n"
            "# **/*.generated.md\n",
            encoding="utf-8",
        )
    print("\nnext:")
    print(f"  export FBT_VAULT={root}")
    print("  fbt ingest .   # index it")
    print("  fbt verify .   # prove it hasn't rotted")
    return 0


def cmd_ingest(args) -> int:
    from .core import index, temporal
    rep = index.ingest(args.path)
    if rep["engine"] == "none":
        print(rep["note"], file=sys.stderr)
    else:
        tail = " ".join(rep.get("embed_tail") or [])
        print(f"indexed vault into qmd collection '{rep['collection']}'  {tail}".rstrip())
    t = temporal.build(args.path)
    print(f"temporal: {t['entries']} dated changes across {t['notes_with_history']} notes "
          f"→ {t['db']}")
    print("try:  fbt ask \"<question>\"   |   fbt history <note>")
    return 0


def cmd_history(args) -> int:
    from .core import temporal
    rows = (temporal.as_of(args.note, args.as_of, vault=args.path)
            if args.as_of else temporal.history(args.note, vault=args.path))
    if not rows:
        print(f"no recorded history for '{args.note}' "
              f"(run `fbt ingest` first to build the temporal sidecar).", file=sys.stderr)
        return 1
    header = f"history of '{args.note}'" + (f" as of {args.as_of}" if args.as_of else "")
    print(header + ":\n")
    for r in rows:
        transition = (f"  [{r['field']}: {r['old_val']} → {r['new_val']}]"
                      if r["field"] else "")
        print(f"  {r['valid_date']}{transition}")
        print(f"     {r['text']}")
    return 0


def cmd_ask(args) -> int:
    from .core import index
    res = index.ask(args.question, args.path, k=args.k)
    if not res["hits"]:
        print("no matches found.", file=sys.stderr)
        return 1
    if res["answer"]:
        print(res["answer"])
        print(f"\n— sources ({res['engine']}):")
    else:
        print(f"top passages ({res['engine']}; set FBT_READER to synthesize an answer):\n")
    for h in res["hits"]:
        sc = f"{h['score']:.2f}" if isinstance(h["score"], (int, float)) else str(h["score"])
        print(f"  • [{sc}] {h['title']}  ({h['rel']})")
    return 0


def cmd_verify(args) -> int:
    from .core import verify
    if args.demo:
        return verify.run_demo()
    rep = verify.verify_vault(args.path)
    verify.print_report(rep, quiet=args.quiet)
    return 0 if rep["ok"] else 1


def cmd_serve(args) -> int:
    from .api import server
    server.serve(args.path, host=args.host, port=args.port)
    return 0


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
    pg.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $FBT_VAULT, else cwd)")
    pg.set_defaults(func=cmd_ingest)

    pa = sub.add_parser("ask", help="retrieve + answer")
    pa.add_argument("question")
    pa.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $FBT_VAULT, else cwd)")
    pa.add_argument("-k", type=int, default=5, help="number of passages to retrieve")
    pa.set_defaults(func=cmd_ask)

    ph = sub.add_parser("history", help="show a note's recorded change history (temporal)")
    ph.add_argument("note", help="note stem or relpath")
    ph.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $FBT_VAULT, else cwd)")
    ph.add_argument("--as-of", dest="as_of", default=None, metavar="YYYY-MM-DD",
                    help="what was recorded on/before this date")
    ph.set_defaults(func=cmd_history)

    pv = sub.add_parser("verify", help="score memory integrity /100 (nonzero exit on rot)")
    pv.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $FBT_VAULT, else cwd)")
    pv.add_argument("--demo", action="store_true",
                    help="plant a contradiction and watch the gate catch it")
    pv.add_argument("--quiet", action="store_true", help="print only the score line")
    pv.set_defaults(func=cmd_verify)

    ps = sub.add_parser("serve", help="run the local HTTP API (point any agent at it)")
    ps.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $FBT_VAULT, else cwd)")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8848)
    ps.set_defaults(func=cmd_serve)

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
