#!/usr/bin/env python3
"""Direct, provenance-stamped writes into the distilled layer."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from . import distill
from . import provenance
from . import store
from . import vault as vaultlib


def _san_source(source: str | None) -> str:
    src = re.sub(r"\s+", " ", str(source or "remember")).strip()
    src = src.replace("/", "_").replace("\\", "_").replace(")", "]")
    src = re.sub(r"(?i)\.md$", "_md", src)
    return src or "remember"


def remember(entity, field, value, vault=None, source=None, agent=None, session=None) -> dict:
    """Record or update one distilled fact under the vault write lock."""
    v = vaultlib._resolve(vault)
    writer_agent = provenance.resolve_agent(agent)
    writer_session = provenance.resolve_session(session)
    writer_ts = provenance.now_ts()
    prov_token = provenance.format_token(writer_agent, writer_session, writer_ts)
    today = date.today().isoformat()
    entity_name = str(entity or "")
    fld = distill._san_field(field)
    val = distill._san_value(value)
    src = _san_source(source)

    with store.vault_lock(v):
        slug = distill.slugify(entity_name)
        target = v / distill.DISTILLED_DIR / f"{slug}.md"
        existing = target.read_text(errors="replace") if target.exists() else ""
        fields = distill._parse_distilled(existing)
        changelog = distill._existing_changelog(existing)
        prior = fields.get(fld)
        prior_value = prior[0] if prior else None

        line = None
        if prior_value is not None and prior_value != val:
            line = (f'- {today}: update {fld}: "{prior_value}" -> "{val}" '
                    f"(source: {src}) {prov_token}")
            action = "updated"
        elif prior_value is None:
            line = f'- {today}: recorded {fld}: "{val}" (source: {src}) {prov_token}'
            action = "recorded"
        else:
            action = "unchanged"

        fields[fld] = (val, src)
        if line is not None:
            changelog = changelog + [line]

        cite_p = distill._sidecar(v, distill.CITATIONS_FILE)
        citations = distill._load_json(cite_p)
        citations[f"{slug}::{fld}"] = {
            "source": src,
            "quote": "",
            "value": val,
            "date": today,
            "agent": writer_agent,
            "session": writer_session,
            "ts": writer_ts,
            "sha256": distill._sha256(val),
        }
        store.atomic_write(cite_p, json.dumps(citations, indent=1, sort_keys=True))
        store.atomic_write(target, distill._render_distilled(slug, entity_name, fields, changelog))

    return {
        "entity": entity,
        "field": fld,
        "value": val,
        "note": str(Path(distill.DISTILLED_DIR) / f"{slug}.md"),
        "action": action,
        "agent": writer_agent,
        "session": writer_session,
        "ts": writer_ts,
    }
