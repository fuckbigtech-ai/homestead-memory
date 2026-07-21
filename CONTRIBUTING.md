# Contributing to homestead-memory

Thanks for your interest. This project is a local-first, verifiable memory layer for AI agents. Contributions that make it more correct, more portable, or harder to fool are all welcome.

## Ways to contribute

- **Report a bug** — use the bug report issue template.
- **Request a feature** — use the feature request template.
- **Submit a RotBench fixture** — a "break-it" case that our integrity checks should catch but do not. This is the highest-value contribution; see [`benchmarks/SCOREBOARD.md`](benchmarks/SCOREBOARD.md) and the RotBench fixture issue template.
- **Improve the docs** — see the docs standard below.

## Development setup

```bash
git clone https://github.com/fuckbigtech-ai/homestead-memory.git
cd homestead-memory
python -m venv .venv && source .venv/bin/activate
pip install -e ".[sign]"
```

## Run the tests

```bash
pytest tests/ -q
```

The CI matrix runs the suite on macOS, Linux, and Windows (Python 3.10 and 3.12). Please keep changes cross-platform: write files with explicit newlines, and gate any POSIX-only behaviour.

## Pull request flow

1. Fork the repository and create a branch from `master`.
2. Make focused commits with clear messages.
3. Add or update tests for any behaviour change.
4. Run `pytest tests/ -q` locally and confirm it passes.
5. Open a pull request and fill in the template.

## Documentation standard

Reference and API documentation follows a scoped clarity standard — see [`DOCS_STYLE.md`](DOCS_STYLE.md). In short: on the reference docs (`docs/`, deploy and example READMEs, CLI help, SDK docstrings, and the RotBench conformance sections), use short sentences, active voice, present tense, and the canonical terminology. The README, roadmap, and results log are brand surfaces and are exempt. A Vale check enforces this on documentation pull requests.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating you agree to uphold it.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
