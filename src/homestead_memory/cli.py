#!/usr/bin/env python3
"""
fbt — the homestead-memory CLI.

    hsm init   [path]       scaffold / adopt a markdown vault
    hsm ingest [path]       index the vault (hybrid BM25 + vector via qmd)
    hsm ask    "question"   retrieve + answer
    hsm verify [path]       score memory integrity /100 — nonzero exit on rot
    hsm verify --demo       plant a contradiction and watch the gate catch it

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
`git diff` it. `homestead-memory` never takes it anywhere you don't tell it to.

Try:

    hsm ingest .
    hsm ask "what is this vault?"
    hsm verify .

## Changelog
- 2026-07-01: created by `hsm init`.
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
    ignore = root / ".hsmignore"
    if not ignore.exists():
        ignore.write_text(
            "# .hsmignore — paths homestead-memory should NOT treat as memory notes.\n"
            "# A trailing / excludes a whole directory; otherwise it's a glob.\n"
            "# Keep generated/report output here so `hsm verify` never flags it.\n"
            "# examples:\n"
            "# reports/\n"
            "# **/*.generated.md\n",
            encoding="utf-8",
        )
    print("\nnext:")
    print(f"  export HSM_VAULT={root}")
    print("  hsm ingest .   # index it")
    print("  hsm verify .   # prove it hasn't rotted")
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
    print("try:  hsm ask \"<question>\"   |   hsm history <note>")
    return 0


def cmd_history(args) -> int:
    from .core import temporal
    rows = (temporal.as_of(args.note, args.as_of, vault=args.path)
            if args.as_of else temporal.history(args.note, vault=args.path))
    if not rows:
        print(f"no recorded history for '{args.note}' "
              f"(run `hsm ingest` first to build the temporal sidecar).", file=sys.stderr)
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
    res = index.ask(args.question, args.path, k=args.k,
                    question_type=args.type, token_budget=args.budget)
    if not res["hits"]:
        print("no matches found.", file=sys.stderr)
        return 1
    if res["answer"]:
        print(res["answer"])
        print(f"\n— sources ({res['engine']} · {res['question_type']} · "
              f"~{res['context_tokens']} ctx tokens):")
    else:
        print(f"top passages ({res['engine']}; set HSM_READER to synthesize an answer):\n")
    for h in res["hits"]:
        sc = f"{h['score']:.2f}" if isinstance(h["score"], (int, float)) else str(h["score"])
        print(f"  • [{sc}] {h['title']}  ({h['rel']})")
    return 0


def cmd_verify(args) -> int:
    from .core import verify
    if args.demo:
        return verify.run_demo()
    rep = verify.verify_vault(args.path, deep=args.deep)
    verify.print_report(rep, quiet=args.quiet)
    return 0 if rep["ok"] else 1


def cmd_distill(args) -> int:
    from .core import distill
    rep = distill.distill(args.path, model=args.model, dry=args.dry)
    print(f"distill{' (dry)' if rep['dry'] else ''}: scanned {rep['scanned']} notes, "
          f"{rep['changed']} new/changed")
    print(f"  facts kept {rep['facts']} · dropped by cite-or-drop {rep['dropped']} · "
          f"failed notes {rep['failed_notes']} (retried next run)")
    print(f"  entities: {rep['entities_created']} created, {rep['entities_updated']} updated · "
          f"{rep['changelog_lines']} changelog lines")
    if rep["changelog_lines"] and not rep["dry"]:
        print("next:  hsm ingest   # make the distilled layer searchable")
    return 0


def cmd_mcp(args) -> int:
    from .api import mcp_server
    return mcp_server.serve(args.path)


def cmd_serve(args) -> int:
    from .api import server
    server.serve(args.path, host=args.host, port=args.port,
                 require_auth=not args.no_auth, allow_remote=args.allow_remote)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hsm",
        description="homestead-memory — verifiable, local-first AI memory. "
                    "Own your mind — and prove it never rotted.",
    )
    p.add_argument("--version", action="version", version=f"homestead-memory {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    pi = sub.add_parser("init", help="scaffold / adopt a markdown vault")
    pi.add_argument("path", nargs="?", default=".", help="vault directory (default: .)")
    pi.set_defaults(func=cmd_init)

    pg = sub.add_parser("ingest", help="index the vault (qmd hybrid retrieval)")
    pg.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    pg.set_defaults(func=cmd_ingest)

    pa = sub.add_parser("ask", help="retrieve + answer")
    pa.add_argument("question")
    pa.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    pa.add_argument("-k", type=int, default=5, help="number of passages to retrieve")
    pa.add_argument("--type", dest="type", default=None,
                    choices=["temporal-reasoning", "knowledge-update", "multi-session", "default"],
                    help="question type (default: auto-classified by the heuristic router)")
    pa.add_argument("--budget", dest="budget", type=int, default=6000,
                    help="context token budget (~4 chars/token; default: 6000)")
    pa.set_defaults(func=cmd_ask)

    ph = sub.add_parser("history", help="show a note's recorded change history (temporal)")
    ph.add_argument("note", help="note stem or relpath")
    ph.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    ph.add_argument("--as-of", dest="as_of", default=None, metavar="YYYY-MM-DD",
                    help="what was recorded on/before this date")
    ph.set_defaults(func=cmd_history)

    pv = sub.add_parser("verify", help="score memory integrity /100 (nonzero exit on rot)")
    pv.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    pv.add_argument("--demo", action="store_true",
                    help="plant a contradiction and watch the gate catch it")
    pv.add_argument("--deep", action="store_true",
                    help="also run retrieval-resilience + fixtures + freshness checks")
    pv.add_argument("--quiet", action="store_true", help="print only the score line")
    pv.set_defaults(func=cmd_verify)

    pd = sub.add_parser("distill", help="build/refresh the distilled layer (write-time, cited facts)")
    pd.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    pd.add_argument("--model", default=None,
                    help="extraction model (default: $HSM_DISTILL_MODEL or llama3.1:latest via ollama)")
    pd.add_argument("--dry", action="store_true", help="report without writing")
    pd.set_defaults(func=cmd_distill)

    pm = sub.add_parser("mcp", help="run the MCP server on stdio (Claude Code/Desktop/Cursor)")
    pm.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    pm.set_defaults(func=cmd_mcp)

    ps = sub.add_parser("serve", help="run the local HTTP API (point any agent at it)")
    ps.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8848)
    ps.add_argument("--no-auth", action="store_true",
                    help="disable the bearer token (trusted single-user local use only)")
    ps.add_argument("--allow-remote", action="store_true",
                    help="permit binding a non-loopback host (exposes memory to the network)")
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
