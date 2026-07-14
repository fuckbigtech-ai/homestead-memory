#!/usr/bin/env python3
"""Narrated model hot-swap demo for homestead-memory."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from homestead_memory import Memory
from homestead_memory.core import index


# This example forces the built-in lexical retrieval fallback (index._QMD = None)
# so it runs anywhere, deterministically, with no qmd embedding backend. The
# hot-swap thesis — same vault, swap the model, memory + verify survive — is
# independent of which retrieval engine is used.
def _use_portable_index() -> None:
    index._QMD = None


FACTS_A = [
    ("User", "name", "Maya"),
    ("User", "project", "orchard-ledger"),
    ("User", "stack", "Python and SQLite"),
    ("Project", "deadline", "2026-08-15"),
]
FACTS_B = [
    ("User", "handoff_model", "glm-4.7"),
    ("Project", "router_layer", "OpenAI-compatible adapter"),
]
PROOF_VALUE = "orchard-ledger"


def _search_text(hits: list[dict[str, Any]]) -> str:
    return "\n".join(str(hit.get("snippet", "")) for hit in hits)


def _snippet(text: str, needle: str, width: int = 180) -> str:
    idx = text.find(needle)
    if idx < 0:
        return text[:width].strip()
    start = max(0, idx - 60)
    end = min(len(text), idx + len(needle) + 100)
    return " ".join(text[start:end].split())


def _print_history(mem: Memory, note: str) -> None:
    for row in mem.history(note):
        print(
            "   - "
            f"field={row.get('field')} "
            f"value={row.get('new_val')} "
            f"by={row.get('agent')} "
            f"ts={row.get('ts')}"
        )


def _simulated_run() -> int:
    with tempfile.TemporaryDirectory(prefix="hsm-hot-swap-") as d:
        vault = Path(d)

        mem_a = Memory(vault, agent="assistant@claude-sonnet-4.7")
        print(f"1. MODEL A (claude-sonnet-4.7) writes {len(FACTS_A)} facts.")
        for entity, field, value in FACTS_A:
            mem_a.remember(entity, field, value)
        mem_a.ingest()
        first = mem_a.verify()
        print(f"   verify: {first['stamp']} — {first['score']}/100")
        assert first["ok"] is True

        mem_b = Memory(vault, agent="assistant@glm-4.7")
        print("\n2. HOT-SWAP to MODEL B (glm-4.7) — same vault, different model.")
        mem_b.ingest()
        hits = mem_b.search("the user's project orchard-ledger", k=5)
        text = _search_text(hits)
        assert PROOF_VALUE in text
        print(f"   retrieved: {_snippet(text, PROOF_VALUE)}")
        for entity, field, value in FACTS_B:
            mem_b.remember(entity, field, value)

        print("\n3. Cross-model provenance timeline.")
        print("   User:")
        _print_history(mem_b, "user")
        print("   Project:")
        _print_history(mem_b, "project")

        print("\n4. INTACT again.")
        mem_b.ingest()
        final = mem_b.verify()
        print(f"   verify: {final['stamp']} — {final['score']}/100")
        assert final["ok"] is True

        print("\nHot-swap the model. Keep the mind.")
        return 0 if first["ok"] and final["ok"] and PROOF_VALUE in text else 1


def _chat(base_url: str, api_key: str, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({"model": model, "messages": messages, "temperature": 0}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _usage_tokens(response: dict[str, Any]) -> int | None:
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    return int(total) if isinstance(total, int) else None


def _live_facts(model: str, response: dict[str, Any]) -> list[tuple[str, str, str]]:
    content = ""
    try:
        content = str(response["choices"][0]["message"]["content"])
    except Exception:
        content = json.dumps(response, sort_keys=True)[:500]
    return [
        ("LiveDemo", "served_model", model),
        ("LiveDemo", f"reply_{model.replace('/', '_').replace(':', '_')}", content[:300]),
    ]


def _live_run(args: argparse.Namespace) -> int:
    required = {
        "HSM_DEMO_BASE_URL": os.environ.get("HSM_DEMO_BASE_URL"),
        "HSM_DEMO_API_KEY": os.environ.get("HSM_DEMO_API_KEY"),
        "HSM_DEMO_MODEL_A": os.environ.get("HSM_DEMO_MODEL_A"),
        "HSM_DEMO_MODEL_B": os.environ.get("HSM_DEMO_MODEL_B"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        print(f"--live requested but missing {', '.join(missing)}; falling back to simulated run.")
        return _simulated_run()

    base_url = str(required["HSM_DEMO_BASE_URL"])
    api_key = str(required["HSM_DEMO_API_KEY"])
    model_a = str(required["HSM_DEMO_MODEL_A"])
    model_b = str(required["HSM_DEMO_MODEL_B"])

    try:
        with tempfile.TemporaryDirectory(prefix="hsm-hot-swap-live-") as d:
            vault = Path(d)
            prompt = "Reply in one short sentence with one durable project fact: project=orchard-ledger."

            print(f"1. MODEL A ({model_a}) writes live facts.")
            resp_a = _chat(base_url, api_key, model_a, [{"role": "user", "content": prompt}])
            mem_a = Memory(vault, agent=f"assistant@{model_a}")
            mem_a.remember("User", "project", PROOF_VALUE)
            for entity, field, value in _live_facts(model_a, resp_a):
                mem_a.remember(entity, field, value)
            mem_a.ingest()
            first = mem_a.verify()
            print(f"   verify: {first['stamp']} — {first['score']}/100")
            assert first["ok"] is True

            print(f"\n2. HOT-SWAP to MODEL B ({model_b}) — same vault, different model.")
            mem_b = Memory(vault, agent=f"assistant@{model_b}")
            mem_b.ingest()
            hits = mem_b.search("orchard-ledger project", k=5)
            text = _search_text(hits)
            assert PROOF_VALUE in text
            print(f"   retrieved: {_snippet(text, PROOF_VALUE)}")
            resp_b = _chat(base_url, api_key, model_b, [{"role": "user", "content": "What project is remembered?"}])
            for entity, field, value in _live_facts(model_b, resp_b):
                mem_b.remember(entity, field, value)

            print("\n3. Cross-model provenance timeline.")
            _print_history(mem_b, "livedemo")
            _print_history(mem_b, "user")

            print("\n4. INTACT again.")
            mem_b.ingest()
            final = mem_b.verify()
            print(f"   verify: {final['stamp']} — {final['score']}/100")
            assert final["ok"] is True

            tok_a = _usage_tokens(resp_a)
            tok_b = _usage_tokens(resp_b)
            if tok_a is not None and tok_b is not None:
                cost_a = tok_a * float(args.price_a) / 1_000_000
                cost_b = tok_b * float(args.price_b) / 1_000_000
                print(f"   measured tokens: model A={tok_a}, model B={tok_b}")
                print(f"   measured estimated cost: model A=${cost_a:.6f}, model B=${cost_b:.6f}")
            else:
                print("   cost comparison unavailable: endpoint did not return measured token counts.")

            print("\nHot-swap the model. Keep the mind.")
            return 0
    except (urllib.error.URLError, TimeoutError, AssertionError, OSError, ValueError) as exc:
        print(f"--live failed ({exc}); falling back to simulated run.")
        return _simulated_run()


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use OpenAI-compatible chat endpoints.")
    parser.add_argument("--price-a", type=float, default=0.0, help="Model A USD per 1M tokens.")
    parser.add_argument("--price-b", type=float, default=0.0, help="Model B USD per 1M tokens.")
    args = parser.parse_args(argv or [])
    _use_portable_index()
    if args.live:
        return _live_run(args)
    return _simulated_run()


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
