#!/usr/bin/env python3
"""
api.server — a tiny local HTTP API for fbt-memory. Stdlib-only (http.server), so
it runs identically on macOS / Linux / Windows and adds zero dependencies.

This is the builder/enterprise surface: point your agent at localhost and get
retrieval, the verification gate, and change history over HTTP.

    GET  /health                      -> {ok, vault, qmd}
    POST /ask     {"query","k"}       -> {answer, hits, engine}
    POST /ingest                      -> index + temporal build report
    GET  /verify                      -> memory-integrity report (RotBench)
    GET  /history?note=X[&as_of=Y]    -> a note's recorded change history

Nothing leaves the machine: this binds to 127.0.0.1 by default.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from ..core import index, temporal, verify


def _make_handler(vault):
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

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/health":
                self._send(200, {"ok": True, "vault": str(vault),
                                 "qmd": index.qmd_available()})
            elif u.path == "/verify":
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
            b = self._body()
            if u.path == "/ask":
                query = b.get("query", "")
                if not query:
                    return self._send(400, {"error": "query required"})
                res = index.ask(query, vault, k=int(b.get("k", 5)))
                self._send(200, {"query": query, "answer": res["answer"],
                                 "engine": res["engine"],
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


def serve(vault, host: str = "127.0.0.1", port: int = 8848) -> None:
    from ..core import vault as vaultlib
    v = vaultlib._resolve(vault)
    httpd = ThreadingHTTPServer((host, port), _make_handler(v))
    print(f"fbt-memory API on http://{host}:{port}  (vault: {v})")
    print("  GET /health · POST /ask · POST /ingest · GET /verify · GET /history?note=")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
