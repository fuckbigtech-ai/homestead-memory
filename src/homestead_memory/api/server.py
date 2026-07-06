#!/usr/bin/env python3
"""
api.server — a tiny local HTTP API for homestead-memory. Stdlib-only (http.server), so
it runs identically on macOS / Linux / Windows and adds zero dependencies.

The builder/enterprise surface: point your agent at localhost and get retrieval,
the verification gate, and change history over HTTP.

    GET  /health                      -> {ok, vault, qmd}   (no auth)
    POST /ask     {"query","k"}       -> {answer, hits, engine}
    POST /ingest                      -> index + temporal build report
    GET  /verify                      -> memory-integrity report (RotBench)
    GET  /history?note=X[&as_of=Y]    -> a note's recorded change history

Security (this API can read your whole memory — treat it like one):
  - Binds to 127.0.0.1 by default. Non-loopback binds require --allow-remote.
  - **Host-header allowlist** rejects DNS-rebinding (a malicious web page can point
    a hostname at 127.0.0.1, but the browser still sends that hostname as Host).
  - **Bearer token** required on every endpoint except /health. Auto-generated and
    printed at startup, or set HSM_API_TOKEN. Disable only with --no-auth.
"""
from __future__ import annotations

import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from ..core import index, temporal, verify

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _host_only(host_header: str) -> str:
    h = (host_header or "").strip()
    if h.startswith("["):                    # [::1]:8848
        return h[: h.find("]") + 1] if "]" in h else h
    return h.rsplit(":", 1)[0] if ":" in h else h


def _make_handler(vault, token: str | None, allowed_hosts: set[str]):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0) or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return {}

        def _gate(self, *, needs_auth: bool) -> bool:
            """Host-allowlist (anti DNS-rebind) + bearer token. Returns True if OK."""
            if _host_only(self.headers.get("Host", "")) not in allowed_hosts:
                self._send(403, {"error": "host not allowed"})
                return False
            if needs_auth and token is not None:
                auth = self.headers.get("Authorization", "")
                sent = auth[7:] if auth.startswith("Bearer ") else ""
                if not (sent and secrets.compare_digest(sent, token)):
                    self._send(401, {"error": "unauthorized: send 'Authorization: Bearer <token>'"})
                    return False
            return True

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/health":
                if not self._gate(needs_auth=False):
                    return
                return self._send(200, {"ok": True, "vault": str(vault),
                                        "qmd": index.qmd_available()})
            if not self._gate(needs_auth=True):
                return
            q = parse_qs(u.query)
            if u.path == "/verify":
                rep = verify.verify_vault(vault)
                self._send(200, {"ok": rep["ok"], "score": rep["score"],
                                 "notes": rep["n_notes"], "fails": len(rep["fails"]),
                                 "warns": len(rep["warns"])})
            elif u.path == "/history":
                note = (q.get("note") or [""])[0]
                if not note:
                    return self._send(400, {"error": "note= required"})
                as_of = (q.get("as_of") or [None])[0]
                rows = (temporal.as_of(note, as_of, vault=vault) if as_of
                        else temporal.history(note, vault=vault))
                self._send(200, {"note": note, "history": rows})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            if not self._gate(needs_auth=True):
                return
            b = self._body()
            if u.path == "/ask":
                query = b.get("query", "")
                if not query:
                    return self._send(400, {"error": "query required"})
                try:
                    k = int(b.get("k", 5))
                    budget = int(b.get("budget", 6000))
                except (TypeError, ValueError):
                    return self._send(400, {"error": "k and budget must be integers"})
                qt = b.get("type")
                res = index.ask(query, vault, k=k,
                                question_type=str(qt) if qt is not None else None,
                                token_budget=budget)
                self._send(200, {"query": query, "answer": res["answer"],
                                 "engine": res["engine"],
                                 "question_type": res["question_type"],
                                 "context_tokens": res["context_tokens"],
                                 "hits": [{"title": h["title"], "rel": h["rel"],
                                           "score": h["score"]} for h in res["hits"]]})
            elif u.path == "/ingest":
                ing = index.ingest(vault)
                t = temporal.build(vault)
                self._send(200, {"index": ing, "temporal": t})
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, *a):  # keep the console quiet
            pass

    return Handler


def serve(vault, host: str = "127.0.0.1", port: int = 8848,
          require_auth: bool = True, allow_remote: bool = False) -> None:
    from ..core import vault as vaultlib
    v = vaultlib._resolve(vault)

    if host not in _LOOPBACK and not allow_remote:
        print(f"refusing to bind non-loopback host {host!r} without --allow-remote "
              f"(this API can read your whole memory).")
        raise SystemExit(2)

    token = None
    if require_auth:
        token = os.environ.get("HSM_API_TOKEN") or os.environ.get("FBT_API_TOKEN") or secrets.token_urlsafe(18)
    # Host header we'll accept: loopback names + the exact host:port we bound to.
    allowed = set(_LOOPBACK) | {host}

    httpd = ThreadingHTTPServer((host, port), _make_handler(v, token, allowed))
    print(f"homestead-memory API on http://{host}:{port}  (vault: {v})")
    if token:
        print(f"  auth: send  Authorization: Bearer {token}")
    else:
        print("  auth: DISABLED (--no-auth) — anything local can read/write this memory")
    print("  GET /health · POST /ask · POST /ingest · GET /verify · GET /history?note=")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
