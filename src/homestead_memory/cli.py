#!/usr/bin/env python3
"""
fbt — the homestead-memory CLI.

    hsm init   [path]       scaffold / adopt a markdown vault
    hsm ingest [path]       index the vault (hybrid BM25 + vector via qmd)
    hsm ask    "question"   retrieve + answer
    hsm verify [path]       score memory integrity /100 — nonzero exit on rot
    hsm verify --demo       plant a contradiction and watch the gate catch it

Stop renting your mind. Own it, and catch it when it rots.
"""
from __future__ import annotations

import argparse
import json
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
    print("  hsm verify .   # catch rot before you trust it")
    return 0


def cmd_ingest(args) -> int:
    from .core import index, temporal
    rep = index.ingest(args.path)
    if rep["engine"] == "none":
        print(rep["note"], file=sys.stderr)
        return 1
    if not rep.get("ok"):
        print(rep.get("note") or rep.get("reason") or "qmd ingest failed", file=sys.stderr)
        return 1
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
        prov = (f"  [agent={r['agent']} session={r['session']} ts={r['ts']}]"
                if r.get("agent") or r.get("session") or r.get("ts") else "")
        print(f"  {r['valid_date']}{transition}{prov}")
        print(f"     {r['text']}")
    return 0


def cmd_ask(args) -> int:
    from .core import index
    res = index.ask(args.question, args.path, k=args.k,
                    question_type=args.type, token_budget=args.budget,
                    retrieval_mode=args.retrieval)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, default=str))
        return 0 if res["hits"] else 1
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


def cmd_search(args) -> int:
    from .core import index
    report = index.search_report(args.query, args.path, k=args.k,
                                 retrieval_mode=args.retrieval)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, default=str))
    else:
        print(f"{report['engine']} · {report['retrieval_mode']} · "
              f"{report['elapsed_ms']:.1f}ms" +
              (f" · degraded: {report['reason']}" if report["degraded"] else ""))
        for hit in report["hits"]:
            score = hit.get("score")
            rendered = f"{score:.2f}" if isinstance(score, (int, float)) else str(score)
            print(f"  [{rendered}] {hit.get('title') or hit['rel']}  ({hit['rel']})")
    return 0 if report["hits"] else 1


def cmd_qmd(args) -> int:
    from .core import index, qmd_runtime
    action = args.action
    if action == "start":
        report = qmd_runtime.start(index._QMD) if index._QMD else {
            "ok": False, "reason": "qmd_not_installed"}
    elif action == "stop":
        report = qmd_runtime.stop()
    elif action == "status":
        report = qmd_runtime.status()
    elif action == "doctor":
        root = vaultlib._resolve(args.path)
        report = qmd_runtime.doctor(index._QMD, index.collection_name(root))
    else:
        report = index.ingest(args.path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, default=str))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
    if action == "stop":
        stopped_or_absent = bool(
            report.get("stopped")
            or (not report.get("pid_alive") and not report.get("reason"))
        )
        return 0 if stopped_or_absent else 1
    return 0 if report.get("ok") else 1


def cmd_tune(args) -> int:
    from .core import tuning
    rep = tuning.tune(args.path)
    if not rep["ok"]:
        print(rep["reason"], file=sys.stderr)
        return 1
    print(f"tuned k over {rep['fixtures']} fixtures — this is FIXTURE recall, so make "
          f"them representative (a bigger k buys recall with broader context):\n")
    for k in sorted(rep["per_k"]):
        mark = "  ← chosen" if k == rep["chosen_k"] else ""
        print(f"  k={k:<3} fixture recall {rep['per_k'][k]:.0%}{mark}")
    delta = rep["recall_after"] - rep["recall_before"]
    sign = "+" if delta >= 0 else ""
    print(f"\nfixture recall {rep['recall_before']:.0%} → {rep['recall_after']:.0%} "
          f"({sign}{delta:.0%}) at k={rep['chosen_k']}, written to .hsm/tuning.json (local).")
    print("`hsm ask` now uses it. `hsm verify` still gates — tuning changed retrieval, never your notes.")
    return 0


def cmd_verify(args) -> int:
    from .core import verify
    if args.demo and args.json:
        rep = verify.demo_report()
        print(json.dumps({
            "ok": rep["ok"],
            "score": rep["score"],
            "stamp": rep["stamp"],
            "notes": rep["notes"],
            "rotbench_version": rep["rotbench_version"],
            "findings": rep["findings"],
        }))
        return 0 if rep["ok"] else 1
    if args.demo:
        return verify.run_demo()
    rep = verify.verify_vault(args.path, deep=args.deep, expect_pubkey=args.signer)
    if args.json:
        print(json.dumps({
            "ok": rep["ok"],
            "score": rep["score"],
            "stamp": rep["stamp"],
            "notes": rep["notes"],
            "rotbench_version": rep["rotbench_version"],
            "findings": rep["findings"],
        }))
        return 0 if rep["ok"] else 1
    verify.print_report(rep, quiet=args.quiet)
    return 0 if rep["ok"] else 1


def cmd_sign(args) -> int:
    from .core import signing
    try:
        sig = signing.sign_vault(args.path, key_path=args.key)
    except RuntimeError as e:
        print(f"hsm sign: {e}", file=sys.stderr)
        return 1
    root = vaultlib._resolve(args.path)
    print(f"signed vault: {root / signing.SIG_REL}")
    print(f"  signer: {sig['signer_pubkey']}")
    print(f"  hash:   {sig['vault_hash']}")
    return 0


def cmd_distill(args) -> int:
    from .core import distill
    rep = distill.distill(args.path, model=args.model, dry=args.dry, agent=args.agent)
    print(f"distill{' (dry)' if rep['dry'] else ''}: scanned {rep['scanned']} notes, "
          f"{rep['changed']} new/changed")
    print(f"  facts kept {rep['facts']} · dropped by cite-or-drop {rep['dropped']} · "
          f"failed notes {rep['failed_notes']} (retried next run)")
    print(f"  entities: {rep['entities_created']} created, {rep['entities_updated']} updated · "
          f"{rep['changelog_lines']} changelog lines")
    if rep["changelog_lines"] and not rep["dry"]:
        print("next:  hsm ingest   # make the distilled layer searchable")
    return 0


def cmd_remember(args) -> int:
    from .core import remember
    res = remember.remember(args.entity, args.field, args.value, vault=args.path,
                            source=args.source, agent=args.agent)
    print(f"{res['action']}: {res['note']}")
    return 0


def cmd_resolve(args) -> int:
    from .core import resolve as resolve_mod
    res = resolve_mod.resolve(args.entity, vault=args.path, field=args.field,
                              strategy=args.strategy, agent=args.agent)
    if not res["note"]:
        print("no distilled note found")
        return 0
    if not res["resolved"]:
        print(f"no conflicts: {res['note']}")
        return 0
    for item in res["resolved"]:
        losers = ", ".join(item["losers"]) if item["losers"] else "(none)"
        print(f"{item['field']}: kept {item['winner']} over {losers} "
              f"({item['strategy']})")
    return 0


def cmd_export(args) -> int:
    if args.format == "okf":
        from .adapters.okf import okf_export

        res = okf_export(args.path, out_dir=args.out)
        print(f"exported {res['exported']} notes to {res['out_dir']} (OKF)")
        return 0

    from .core import portability
    res = portability.export_vault(args.path, out_path=args.out)
    print(f"exported {res['notes']} notes to {res['bundle']}")
    print(f"  vault_hash: {res['vault_hash']}")
    return 0


def cmd_import(args) -> int:
    if args.format == "okf":
        from .adapters.okf import okf_import

        res = okf_import(args.source, vault=args.path, agent=args.agent or "okf-import")
        print(f"imported {res['imported']} memories from okf ({res['skipped']} skipped)")
        return 0

    from .core import portability
    res = portability.import_memories(args.source, vault=args.path, fmt=args.format,
                                      agent=args.agent)
    print(f"imported {res['imported']} memories from {res['format']} "
          f"({res['skipped']} skipped)")
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
                    "Own your mind. Catch it when it rots.",
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
    pa.add_argument("-k", type=int, default=None,
                    help="passages to retrieve (default: tuned via `hsm tune`, else 5)")
    pa.add_argument("--type", dest="type", default=None,
                    choices=["temporal-reasoning", "knowledge-update", "multi-session", "default"],
                    help="question type (default: auto-classified by the heuristic router)")
    pa.add_argument("--budget", dest="budget", type=int, default=6000,
                    help="context token budget (~4 chars/token; default: 6000)")
    pa.add_argument("--retrieval", choices=["fast", "balanced", "quality"],
                    default="balanced", help="retrieval profile (default: balanced)")
    pa.add_argument("--json", action="store_true",
                    help="emit the complete machine-readable retrieval result")
    pa.set_defaults(func=cmd_ask)

    psearch = sub.add_parser("search", help="retrieve ranked passages without a reader")
    psearch.add_argument("query")
    psearch.add_argument("path", nargs="?", default=None,
                         help="vault directory (default: $HSM_VAULT, else cwd)")
    psearch.add_argument("-k", type=int, default=5)
    psearch.add_argument("--retrieval", choices=["fast", "balanced", "quality"],
                         default="balanced", help="retrieval profile (default: balanced)")
    psearch.add_argument("--json", action="store_true")
    psearch.set_defaults(func=cmd_search)

    pqmd = sub.add_parser("qmd", help="manage Homestead's dedicated qmd runtime")
    pqmd.add_argument("action", choices=["start", "stop", "status", "doctor", "refresh"])
    pqmd.add_argument("path", nargs="?", default=None,
                      help="vault directory used by doctor/refresh")
    pqmd.add_argument("--json", action="store_true")
    pqmd.set_defaults(func=cmd_qmd)

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
    pv.add_argument("--json", action="store_true",
                    help="emit a machine-readable verification report")
    pv.add_argument("--signer", default=None, metavar="PUBKEY",
                    help="require this Ed25519 public key when --deep verifies .hsm/vault.sig")
    pv.set_defaults(func=cmd_verify)

    psign = sub.add_parser("sign", help="sign the vault's canonical markdown state")
    psign.add_argument("path", nargs="?", default=None,
                       help="vault directory (default: $HSM_VAULT, else cwd)")
    psign.add_argument("--key", default=None, metavar="PATH",
                       help="Ed25519 private seed path (default: $HSM_SIGNING_KEY or ~/.config/...)")
    psign.set_defaults(func=cmd_sign)

    pd = sub.add_parser("distill", help="build/refresh the distilled layer (write-time, cited facts)")
    pd.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    pd.add_argument("--model", default=None,
                    help="extraction model (default: $HSM_DISTILL_MODEL or llama3.1:latest via ollama)")
    pd.add_argument("--dry", action="store_true", help="report without writing")
    pd.add_argument("--agent", default=None,
                    help="writer identity stamped on distilled changelog provenance")
    pd.set_defaults(func=cmd_distill)

    pr = sub.add_parser("remember",
                        help="directly write one provenance-stamped distilled fact")
    pr.add_argument("entity")
    pr.add_argument("field")
    pr.add_argument("value")
    pr.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    pr.add_argument("--source", default=None,
                    help="source label for the distilled citation (default: remember)")
    pr.add_argument("--agent", default=None,
                    help="writer identity stamped on distilled changelog provenance")
    pr.set_defaults(func=cmd_remember)

    prs = sub.add_parser("resolve",
                         help="resolve duplicate-value conflicts in a distilled note")
    prs.add_argument("entity")
    prs.add_argument("path", nargs="?", default=None,
                     help="vault directory (default: $HSM_VAULT, else cwd)")
    prs.add_argument("--field", default=None, help="field to resolve")
    prs.add_argument("--strategy", choices=["latest", "keep-both"], default="latest",
                     help="resolution policy (default: latest)")
    prs.add_argument("--agent", default=None,
                     help="resolver identity stamped on distilled changelog provenance")
    prs.set_defaults(func=cmd_resolve)

    pe = sub.add_parser("export", help="export the vault as a Homestead bundle or OKF directory")
    pe.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    pe.add_argument("-o", "--out", default=None, metavar="OUT",
                    help="bundle path or OKF directory (default: format-specific path in cwd)")
    pe.add_argument("--format", default="homestead", choices=["homestead", "okf"],
                    help="export format (default: homestead)")
    pe.set_defaults(func=cmd_export)

    pim = sub.add_parser("import", help="import memories from Mem0, Zep, JSON, Homestead, or OKF")
    pim.add_argument("source", help="JSON export, Homestead bundle, OKF markdown, or directory")
    pim.add_argument("path", nargs="?", default=None,
                     help="vault directory (default: $HSM_VAULT, else cwd)")
    pim.add_argument("--format", default="auto",
                     choices=["auto", "mem0", "zep", "homestead", "generic", "okf"],
                     help="source format (default: auto)")
    pim.add_argument("--agent", default=None,
                     help="writer identity stamped on imported-note provenance")
    pim.set_defaults(func=cmd_import)

    pt = sub.add_parser("tune",
                        help="grid-search retrieval on your fixtures → .hsm/tuning.json "
                             "(measured, local self-improvement)")
    pt.add_argument("path", nargs="?", default=None,
                    help="vault directory (default: $HSM_VAULT, else cwd)")
    pt.set_defaults(func=cmd_tune)

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
    import sys

    # Windows consoles default to cp1252, which can't encode the ✅/🔴 output and
    # would crash print_report with UnicodeEncodeError. Reconfigure to UTF-8 so hsm
    # renders (and never crashes) on any console.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
