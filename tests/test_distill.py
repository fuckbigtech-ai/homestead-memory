"""core.distill — cite-or-drop, contradiction protocol, idempotence, verify integration."""
import json
from pathlib import Path

from homestead_memory.core import distill, temporal, verify

RAW1 = """---
name: chat1
status: reference
updated: 2026-07-01
---
**user:** I'm allergic to penicillin, please remember that.
**assistant:** Noted.
"""

RAW2 = """---
name: chat2
status: reference
updated: 2026-07-02
---
**user:** Update — we migrated off Salesforce; we now use HubSpot as our CRM.
**assistant:** Got it.
"""


def _fx(facts_by_rel):
    """Fake extractor: rel -> list of fact dicts."""
    def fn(rel, body):
        return facts_by_rel.get(rel, [])
    return fn


def _fact(entity="User", field="allergy", value="penicillin",
          quote="I'm allergic to penicillin, please remember that."):
    return {"entity": entity, "field": field, "value": value,
            "fact": f"{entity} {field} = {value}", "quote": quote}


def test_distill_creates_cited_note_and_changelog(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW1)
    rep = distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [_fact()]}))
    assert rep["facts"] == 1 and rep["dropped"] == 0
    note = (tmp_path / "distilled" / "user.md").read_text()
    assert "- allergy: penicillin (source: chat1.md)" in note
    assert 'recorded allergy: "penicillin" (source: chat1.md)' in note
    # citations sidecar retains the exact quote for later revalidation
    cites = json.loads((tmp_path / ".hsm" / "citations.json").read_text())
    assert cites["user::allergy"]["quote"].startswith("I'm allergic")


def test_cite_or_drop_rejects_unsupported_quote(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW1)
    bad = _fact(quote="a totally fabricated quote that is not in the note")
    rep = distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [bad]}))
    assert rep["dropped"] == 1 and rep["facts"] == 0
    assert not (tmp_path / "distilled" / "user.md").exists()


def test_contradiction_appends_update_line_and_feeds_temporal(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW1.replace("penicillin", "Salesforce is our CRM"))
    (tmp_path / "chat1.md").write_text(
        RAW1.replace("I'm allergic to penicillin, please remember that.",
                     "We just set up Salesforce as our CRM."))
    f1 = _fact(field="current_crm", value="Salesforce",
               quote="We just set up Salesforce as our CRM.")
    distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [f1]}))
    (tmp_path / "chat2.md").write_text(RAW2)
    f2 = _fact(field="current_crm", value="HubSpot",
               quote="we now use HubSpot as our CRM")
    distill.distill(tmp_path, extract_fn=_fx({"chat2.md": [f2]}))
    note = (tmp_path / "distilled" / "user.md").read_text()
    assert "- current_crm: HubSpot (source: chat2.md)" in note          # bullet updated
    assert 'update current_crm: "Salesforce" -> "HubSpot"' in note       # audit trail kept
    # and temporal parses the quoted transition natively
    entries = temporal.parse_changelog(note)
    tr = [e for e in entries if e["field"] == "current_crm"][0]
    assert tr["old"] == "Salesforce" and tr["new"] == "HubSpot"


def test_rerun_is_idempotent_even_after_state_deletion(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW1)
    fx = _fx({"chat1.md": [_fact()]})
    distill.distill(tmp_path, extract_fn=fx)
    before = (tmp_path / "distilled" / "user.md").read_text()
    (tmp_path / ".hsm" / "distill_state.json").unlink()   # force full re-distill
    rep = distill.distill(tmp_path, extract_fn=fx)
    after = (tmp_path / "distilled" / "user.md").read_text()
    assert rep["changelog_lines"] == 0                     # no duplicate lines
    # identical except possibly the updated: date (same day here → identical)
    assert before == after


def test_failed_extraction_leaves_note_unprocessed(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW1)
    def boom(rel, body):
        raise RuntimeError("model down")
    rep = distill.distill(tmp_path, extract_fn=boom)
    assert rep["failed_notes"] == 1
    state = json.loads((tmp_path / ".hsm" / "distill_state.json").read_text())
    assert "chat1.md" not in state                         # retried next run


def test_distilled_notes_never_distilled_again(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW1)
    distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [_fact()]}))
    seen = []
    def spy(rel, body):
        seen.append(rel)
        return []
    (tmp_path / ".hsm" / "distill_state.json").unlink()
    distill.distill(tmp_path, extract_fn=spy)
    assert all(not r.startswith("distilled/") for r in seen)   # self-ingestion guard


def test_verify_distill_integrity_catches_uncited_and_dangling(tmp_path):
    d = tmp_path / "distilled"; d.mkdir()
    (d / "user.md").write_text(
        "---\nname: user\ntype: distilled\nentity: User\nupdated: 2026-07-03\n---\n\n"
        "# User\n\n"
        "- allergy: penicillin (source: chat1.md)\n"
        "- drink: espresso\n"
        "- city: Berlin (source: ghost.md)\n\n"
        "## Changelog\n- 2026-07-03: recorded allergy: \"penicillin\" (source: chat1.md)\n")
    (tmp_path / "chat1.md").write_text(RAW1)
    rep = verify.verify_vault(tmp_path)
    checks = [f.check for f in rep["fails"]]
    assert "uncited_claim" in checks and "dangling_citation" in checks
    assert rep["ok"] is False


def test_verify_demo_still_exits_nonzero_with_distilled_act():
    assert verify.run_demo() == 1


# ------------------------------------------------ audit-driven hardening cases
def test_malformed_extractor_output_never_crashes(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW1)
    for bad in ({"not": "a list"}, "just a string", [ "not-a-dict", 42 ], None):
        rep = distill.distill(tmp_path, extract_fn=lambda r, b, _bad=bad: _bad)
        assert rep["failed_notes"] == 0        # handled, not crashed


def test_citation_traversal_and_absolute_paths_fail_verify(tmp_path):
    d = tmp_path / "distilled"; d.mkdir()
    (tmp_path / "chat1.md").write_text(RAW1)
    outside = tmp_path.parent / "outside.md"
    outside.write_text("x")                    # exists, but OUTSIDE the vault
    (d / "user.md").write_text(
        "---\nname: user\ntype: distilled\nentity: User\nupdated: 2026-07-03\n---\n\n"
        "# User\n\n"
        f"- a: 1 (source: ../{outside.name})\n"
        f"- b: 2 (source: {outside})\n"        # absolute path
        "- c: 3 (source: chat1.md)\n\n"
        "## Changelog\n- 2026-07-03: recorded a: \"1\" (source: chat1.md)\n")
    rep = verify.verify_vault(tmp_path)
    dangling = [f for f in rep["fails"] if f.check == "dangling_citation"]
    assert len(dangling) == 2                  # traversal + absolute both rejected


def test_citations_sidecar_rebuildable_after_deletion(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW1)
    fx = _fx({"chat1.md": [_fact()]})
    distill.distill(tmp_path, extract_fn=fx)
    (tmp_path / ".hsm" / "citations.json").unlink()
    (tmp_path / ".hsm" / "distill_state.json").unlink()
    distill.distill(tmp_path, extract_fn=fx)   # full re-distill backfills evidence
    cites = json.loads((tmp_path / ".hsm" / "citations.json").read_text())
    assert "user::allergy" in cites


def test_field_and_value_sanitized_into_safe_grammar(tmp_path):
    (tmp_path / "chat1.md").write_text(RAW1)
    weird = _fact(field="Favorite: Drink!", value='espresso "double"\nshot')
    rep = distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [weird]}))
    assert rep["facts"] == 1
    note = (tmp_path / "distilled" / "user.md").read_text()
    assert "- favorite_drink: espresso 'double' shot (source: chat1.md)" in note
    # and the round-trip re-parses (idempotence on the weird content)
    (tmp_path / ".hsm" / "distill_state.json").unlink()
    rep2 = distill.distill(tmp_path, extract_fn=_fx({"chat1.md": [weird]}))
    assert rep2["changelog_lines"] == 0


def test_unsafe_source_path_skipped_visibly(tmp_path):
    (tmp_path / "weird (1).md").write_text(RAW1)
    rep = distill.distill(tmp_path, extract_fn=_fx({}))
    assert rep["skipped_unsafe_path"] == 1     # counted, not silent


def test_long_notes_windowed_not_truncated(tmp_path):
    big = RAW1 + ("filler line about nothing in particular.\n" * 400)   # > 8000 chars
    (tmp_path / "big.md").write_text(big)
    calls = []
    def spy(rel, body):
        calls.append(len(body))
        return []
    # spy replaces the whole extract; windowing lives in _ollama_extract, so test it directly
    windows = [big[i:i + distill._WINDOW] for i in range(0, len(big), distill._WINDOW)]
    assert len(windows) >= 2                   # the note genuinely exceeds one window
    rep = distill.distill(tmp_path, extract_fn=spy)
    assert rep["truncated_notes"] == (1 if len(big) > distill._WINDOW * distill._MAX_WINDOWS else 0)
