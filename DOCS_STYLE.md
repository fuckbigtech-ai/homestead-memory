# Documentation style

This project has two kinds of prose, and they follow different rules.

- **Reference docs** describe how the software works. They must be clear, consistent, and boring. They follow the clarity standard below (inspired by ASD-STE100 Simplified Technical English) and are checked by a Vale linter in CI.
- **Brand docs** are the pitch. They carry the voice that makes the project recognizable. They are **exempt** from the standard.

If you add prose, first decide which kind it is.

## Scope

| Path | Kind | Linted |
| --- | --- | --- |
| `docs/**` | reference | ✅ |
| `deploy/**/README.md` | reference | ✅ |
| `examples/**/README.md` | reference | ✅ |
| `benchmarks/README.md` | reference | ✅ |
| `benchmarks/ROTBENCH.md` (conformance, schema, scoring, check families) | reference | ✅ (spec sections) |
| `SECURITY_RELEASE.md` | reference | ✅ |
| `README.md` | brand | ❌ exempt |
| `ROADMAP.md` | brand | ❌ exempt |
| `benchmarks/RESULTS.md` (the honest log) | brand | ❌ exempt |
| `benchmarks/ROTBENCH.md` (manifesto intro, "Break it") | brand | ❌ exempt |
| `verify --demo` narration, `MEMORY INTACT` / `ROT DETECTED` stamps | brand | ❌ exempt |

CLI `--help` strings and SDK docstrings should follow the same clarity rules by hand; they are not Vale-linted.

## The clarity standard (reference docs)

- **Short sentences.** Aim for 25 words or fewer in description, 20 or fewer in a procedure.
- **One idea per sentence.** Split compound sentences.
- **Active voice.** "The server writes the index," not "the index is written."
- **Simple present tense.** "The command returns a score," not "will return" or "returned."
- **No idioms, metaphors, or slang.** Say what happens plainly.
- **Controlled terminology.** One word for one thing. See the table below.

## Controlled terminology

Use the canonical term. Do not use the synonyms.

| Concept | Canonical | Do not use |
| --- | --- | --- |
| The Markdown directory of memory | **vault** | memory store, data store, collection, haystack |
| The umbrella failure mode | **rot** | corruption, decay, degradation (as a noun for the failure) |
| Named sub-types of rot | **contradiction**, **dangling citation**, **drift**, **stale value** | (use the specific name) |
| The integrity command | **`verify`** | (do not rename) |
| The 0–100 result | **integrity score** | health score, rating |
| The benchmark | **RotBench** | the integrity benchmark, the test suite |
| Write-time extraction | **distillation** / **distilled note** | fact layer, extracted facts |

## How enforcement works

- `.vale.ini` + the `styles/Homestead/` rules define the checks.
- `.github/workflows/docs-lint.yml` runs Vale on the reference paths for every pull request that touches docs.
- Banned idioms and wrong terminology **fail** the check. Long sentences and wordiness are **warnings**.

## Exempting a section inside a reference file

Wrap a brand aside in Vale comments so the surrounding spec still lints:

```markdown
<!-- vale off -->
Stop renting your mind.
<!-- vale on -->
```
