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


def _distill_default() -> str:
    """Read the real default so --help can never drift from the code again.

    Local import: cli.py imports core modules lazily inside commands to keep
    startup cheap, and build_parser runs at import time.
    """
    from .core import distill
    return distill.DEFAULT_DISTILL_MODEL

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
        from .core import refresh
        report = refresh.refresh(args.path)
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
    # Surface the extractor. This is the whole point of recording it: a launchd or
    # CI run does not inherit an interactive-shell env var and silently falls back
    # to the default, which thins the distilled layer and lowers any score over it.
    print(f"  model {rep['model']} (from {rep['model_source']})")
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
                    help=f"extraction model (default: $HSM_DISTILL_MODEL or "
                         f"{_distill_default()} via ollama)")
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
    pe.add_argument("--evidence", action="store_true",
                    help="export an auditor-verifiable EvidencePack of the agent ledger")
    pe.add_argument("--since", default=None, metavar="TS",
                    help="only records at/after this timestamp (retention window)")
    pe.add_argument("--until", default=None, metavar="TS",
                    help="only records at/before this timestamp")
    pe.add_argument("--from-seq", type=int, default=None, metavar="N",
                    help="only records at/after this sequence number (exact slicing)")
    pe.add_argument("--to-seq", type=int, default=None, metavar="N",
                    help="only records at/before this sequence number")
    pe.set_defaults(func=lambda a: cmd_export_evidence(a) if a.evidence else cmd_export(a))

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

    phook = sub.add_parser("hook", help="record one agent action (PostToolUse hook entry point)")
    phook.add_argument("path", nargs="?", default=None)
    phook.add_argument("--install", action="store_true",
                       help="print the settings.json snippet instead of recording")
    phook.set_defaults(func=lambda a: cmd_hook_install(a) if a.install else cmd_hook(a))

    pw = sub.add_parser("watch", help="show what your agent actually did (the local ledger)")
    pw.add_argument("path", nargs="?", default=None)
    pw.add_argument("-n", type=int, default=30, help="how many records to show (0 = all)")
    pw.add_argument("--tool", default=None, help="only this tool, e.g. Bash")
    pw.add_argument("--session", default=None, help="only this session id")
    pw.add_argument("--json", action="store_true")
    pw.set_defaults(func=cmd_watch)

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


def cmd_export_evidence(args) -> int:
    from .core import evidence

    try:
        res = evidence.build_pack(args.path, args.out, since=args.since, until=args.until,
                                  from_seq=args.from_seq, to_seq=args.to_seq)
    except ValueError as e:
        print(f"hsm export --evidence: {e}", file=sys.stderr)
        return 1

    print(f"EvidencePack: {res['pack']}")
    print(f"  records: {res['records']}"
          + ("  (windowed)" if res["windowed"] else "  (full ledger)"))
    if res["windowed"]:
        print(f"  anchor:  {res['anchor'][:16]}… (earlier records not included)")
    if not res["signed"]:
        # Say it here, loudly, rather than only in the manifest. An unsigned pack is a
        # materially weaker artifact and the person exporting it should know now.
        print("  UNSIGNED: install the [sign] extra to sign this pack", file=sys.stderr)
    print(f"\n  verify with:  python3 {res['pack']}/verify_evidence.py")
    return 0


def cmd_hook(args) -> int:
    """PostToolUse hook entry point. Records one agent action.

    TWO NON-NEGOTIABLES, in tension with each other.

    1. NEVER damage the session. This runs after every single tool call. A crash, a
       traceback on stderr, or a stall is worse than the feature is good. So every
       failure path returns 0 and nothing is raised to the harness. (PostToolUse
       cannot block the tool anyway - it already ran - but a noisy hook still trains
       the user to uninstall it.)

    2. NEVER drop an event silently. An audit log with invisible gaps is precisely
       the defect this product exists to sell against. So a failed append writes a
       drop marker that `hsm watch` and `hsm verify` both surface.

    Reads stdin FIRST, env second. The machine this was written on had a hook that
    silently did nothing for weeks because it read CLAUDE_TOOL_NAME/CLAUDE_TOOL_INPUT,
    which current Claude Code does not send; the payload arrives as JSON on stdin.
    """
    import json
    import os

    from .core import capture, ledger

    payload: dict = {}
    try:
        raw = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        if raw.strip():
            payload = json.loads(raw)
    except (ValueError, OSError):
        payload = {}

    if not payload:
        # Env fallback for older/other harnesses. Absence of BOTH is not an error:
        # a hook fired with nothing to say should exit quietly, not log noise.
        name = os.environ.get("CLAUDE_TOOL_NAME")
        if not name:
            return 0
        payload = {"tool_name": name}
        ti = os.environ.get("CLAUDE_TOOL_INPUT")
        if ti:
            try:
                payload["tool_input"] = json.loads(ti)
            except ValueError:
                payload["tool_input"] = ti

    try:
        kwargs = capture.from_hook_payload(payload)
        ledger.append(vault=args.path, **kwargs)
    except BaseException as e:                      # noqa: BLE001 - see docstring #1
        try:
            ledger.record_drop(str(e), vault=args.path)
        except BaseException:                        # noqa: BLE001
            pass                                     # nothing left we can safely do
    return 0


def _hook_configured_in() -> str | None:
    """Return the settings file that already configures an hsm hook, or None.

    Without this, an empty ledger has one message for two opposite problems. The old
    text always said "install the hook", which tells a user who DID install it to do
    the thing they just did, and hides the real cause: a hook that is configured but
    not firing.
    """
    home = Path.home()
    candidates = [
        home / ".claude" / "settings.json",
        home / ".claude" / "settings.local.json",
        Path.cwd() / ".claude" / "settings.json",
        Path.cwd() / ".claude" / "settings.local.json",
    ]
    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue                                 # unreadable or not JSON: not evidence
        if not isinstance(data, dict):
            continue
        hooks = data.get("hooks")
        entries = (hooks or {}).get("PostToolUse") if isinstance(hooks, dict) else None
        for entry in entries or []:
            for h in (entry or {}).get("hooks", []) or []:
                cmd = str((h or {}).get("command", ""))
                if "hsm" in cmd and "hook" in cmd:
                    return str(p)
    return None


def _explain_no_records(total: int) -> None:
    """Say which of the two possible causes this is, instead of guessing one."""
    if total:
        print(f"no records match that filter. the ledger holds {total} record(s).")
        return

    where = _hook_configured_in()
    if not where:
        print("no records yet. install the hook with `hsm hook --install`.")
        return

    # The dangerous case: the user believes recording is on, and it is not.
    print(f"no records yet, but a hook IS configured in {where}.")
    print("so the hook is not firing. usual causes, in order:")
    print("  1. the command there is not resolvable by the hook's shell.")
    print(f"     this hsm lives at: {_hsm_executable()}")
    print("  2. no tool call has happened since you added it. run one, then retry.")
    print("  3. records are going to another vault. check `hsm watch <vault>`.")


def cmd_watch(args) -> int:
    """Show what the agent actually did."""
    from .core import ledger

    recs = ledger.read_all(args.path)
    total = len(recs)                                # before filtering, so we can tell
    if args.session:                                 # "empty ledger" from "filtered out"
        recs = [r for r in recs if r.get("session") == args.session]
    if args.tool:
        recs = [r for r in recs if r.get("target") == args.tool]
    shown = recs[-args.n:] if args.n else recs

    if args.json:
        print(json.dumps(shown, indent=2))
    else:
        if not shown:
            _explain_no_records(total)
        for r in shown:
            when = str(r.get("ts", ""))[11:19]
            tgt = r.get("target") or "?"
            summ = r.get("summary") or ""
            print(f"  {r.get('seq'):>5}  {when}  {tgt:<14} {summ[:70]}")

    breaks = ledger.verify_chain(args.path)
    drops = ledger.read_drops(args.path)
    if breaks or drops:
        print()
    for b in breaks[:5]:
        print(f"  !! chain break at index {b.index}: {b.kind} - {b.detail}", file=sys.stderr)
    if len(breaks) > 5:
        print(f"  !! ...and {len(breaks) - 5} more chain breaks", file=sys.stderr)
    if drops:
        print(f"  !! {len(drops)} event(s) failed to record - the log has known gaps",
              file=sys.stderr)
    return 1 if (breaks or drops) else 0


def _hsm_executable() -> str:
    """Absolute path to THIS hsm, for the hook snippet.

    Measured, not assumed: a bare `hsm` in the snippet only works if the shell the
    harness spawns for the hook happens to have it on PATH. For the ordinary
    `python -m venv` + `pip install homestead-memory` it does NOT, because the venv is
    not active in that shell. The hook then fails, PostToolUse failures are invisible
    to the user, and `hsm watch` reports nothing.

    That is exactly the silent-success defect this tool exists to catch, and this repo
    has already shipped its own version of it once: a sibling hook read env vars the
    harness never sent, so it quietly did nothing for weeks. Emitting an absolute path
    costs nothing and removes the failure mode entirely.
    """
    import os
    import shutil

    name = "hsm.exe" if os.name == "nt" else "hsm"
    argv0 = Path(sys.argv[0])
    if argv0.is_absolute() and argv0.exists() and argv0.stem == "hsm":
        return str(argv0)
    beside = Path(sys.executable).parent / name      # console script sits by the interpreter
    if beside.exists():
        return str(beside)
    return shutil.which("hsm") or "hsm"


def _shell_quote(path: str) -> str:
    """Quote a path for the command string the harness hands to a shell."""
    import os
    import shlex

    if os.name == "nt":
        return f'"{path}"' if " " in path else path
    return shlex.quote(path)


def cmd_hook_install(args) -> int:
    """Print the settings.json snippet. Deliberately does NOT edit the file.

    This turns on recording of every tool call the user makes. Editing their config
    behind a --install flag is the kind of thing that should require them to look at
    it first, so we print it and let them paste it.
    """
    import json as _json
    import shutil

    exe = _hsm_executable()
    command = f"{_shell_quote(exe)} hook"

    snippet = {
        "hooks": {
            "PostToolUse": [{
                "matcher": "*",
                "hooks": [{
                    "type": "command",
                    "command": command,
                    # Explicit, because the harness default is 600s. A hung hook
                    # inheriting that would stall a session for ten minutes.
                    "timeout": 5,
                }],
            }]
        }
    }
    print("Add to ~/.claude/settings.json (records EVERY tool call locally):\n")
    print(_json.dumps(snippet, indent=2))
    print("\nThen: hsm watch          # see what your agent did")
    print("      hsm watch --json   # machine-readable")

    # Say why the path is absolute, so nobody "tidies" it back to a bare name.
    on_path = shutil.which("hsm")
    if exe != "hsm":
        print(f"\nThe absolute path is deliberate: {exe}")
        if not on_path:
            print("`hsm` is not on PATH here, so a bare `hsm hook` would fail silently.")
        else:
            print("A bare `hsm hook` works only if the hook's shell has it on PATH,")
            print("which a venv install does not guarantee. Absolute always resolves.")
    else:
        print("\nWARNING: could not resolve an absolute path to hsm, so the snippet uses")
        print("a bare `hsm`. If the hook's shell lacks it on PATH it will fail silently.")
        print("Verify with `hsm watch` after your next tool call.")

    print("\nRecords stay on this machine. Payloads are truncated and secret-shaped")
    print("values are redacted, but treat the ledger as sensitive: it describes your work.")
    return 0


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
