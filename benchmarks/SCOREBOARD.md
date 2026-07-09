# RotBench Break-It Scoreboard

Submit a planted-rot vault snippet that RotBench **should** flag but currently
doesn't. If it is a real gap, we add the fixture, add or tighten a check family,
and credit you here. False positives count too: if RotBench flags an intact vault,
send the smallest fixture that proves it.

| fixture | what it plants | should-detect | status | fixed-in | submitter |
|---|---|---|---|---|---|
| `self_contradiction` | flat `status:` disagrees with nested `metadata.status` | `self_contradiction` FAIL | covered | v1.1 | core suite |
| `duplicate_value` | same distilled field appears twice with conflicting values | `duplicate_value` FAIL | covered | v1.1 | core suite |
| `temporal_mismatch` | current distilled value disagrees with latest changelog value | `temporal_mismatch` FAIL | covered | v1.1 | core suite |
| `dangling_citation` | citation points outside the vault, to an absolute path, or to a missing note | `dangling_citation` FAIL | covered | v1.1 | core suite |
| `uncited_claim` | distilled claim has no `(source: ...)` citation | `uncited_claim` FAIL | covered | v1.1 | core suite |
| `updated_ahead` | `updated:` was bumped far past the latest changelog | `updated_ahead` WARN | covered | v1.1 | core suite |
| `citation_source_stale` | cited source note exists but is older than the freshness window | `citation_source_stale` WARN | covered | v1.1 | core suite |
| `index_drift` | vault content changed after the last recorded ingest hash | `index_drift` WARN | covered | v1.1 | core suite |

## How to submit

Open an issue or pull request with a small markdown vault fixture and the expected
finding JSON. Keep it minimal: one or two notes plus any `.hsm/` metadata needed
to trigger the behavior. The expected finding should use the same shape as
`hsm verify --json`:

```json
{
  "level": "fail",
  "check": "example_gap",
  "note": "distilled/user.md",
  "detail": "what RotBench should have caught"
}
```
