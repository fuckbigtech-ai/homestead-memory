#!/usr/bin/env python3
"""Fail if published copy claims a command the PUBLISHED artifact does not have.

Why this exists. On 2026-09-11, `hsm import-session` and `core/codex_import.py` were
committed on 2026-09-09, but v0.4.0 had been tagged on 2026-08-31, nine days earlier, and
pyproject was never bumped. PyPI therefore served a wheel with no Codex support at all
while the Show HN comment, the r/ClaudeAI draft and the X thread all described it. The
Show HN comment was already posted and past its edit window.

That was the FOURTH instance of one defect class:
  * fuckbigtech.ai/lab published 0.3.3 while PyPI served 0.4.0
  * `hook --install` emitted a bare `hsm` that a venv install could not resolve
  * the EvidencePack verifier reported "signed" for unsigned packs
  * this one

Every instance is the repository being correct while the artifact a stranger receives is
not, and there is a structural reason: the release step is the only part of this pipeline
nothing verifies automatically. `verify_artifact.py` and `wheel_smoke.py` both inspect the
wheel we are ABOUT to publish, which is useless in the failure that actually happened,
where no release was attempted.

So this asks the inverted question: given what PyPI serves RIGHT NOW, is every command our
published copy mentions actually reachable by someone who installs today?

WHAT THIS HAD TO GET RIGHT.

Grepping for `hsm \\w+` alone is too loose: prose contains "hsm and the ledger", flags like
`hsm --version`, and invented names. So a token is only treated as a claim when it is a
real subcommand of the LOCAL CLI. That reduces the check to the precise question worth
asking, "this doc names a real command, is it released yet?", and keeps the gate silent on
typos and prose.

Version skew between the working tree and PyPI is REPORTED, never failed on. Master
describing unreleased work is normal development, not a defect. The defect is only ever a
claim that has left the building.

Usage:
    python3 scripts/published_claims_gate.py
    python3 scripts/published_claims_gate.py --also ~/fuckbigtech-ai/content-strategy
    python3 scripts/published_claims_gate.py --version 0.5.0   # pin, skip "latest" lookup

Exit codes: 0 clean, 1 a claim is not backed, 2 the gate could not run.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PACKAGE = "homestead-memory"
ROOT = Path(__file__).resolve().parents[1]
# Files whose claims are read by strangers. The README is also the PyPI long_description.
DEFAULT_DOCS = ["README.md", "ROADMAP.md", "SECURITY.md", "docs", "benchmarks"]
ADD_PARSER = re.compile(r'sub\.add_parser\(\s*"([a-z][a-z0-9-]*)"')
# `hsm foo`, `$ hsm foo`, `hsm  foo` in fences and prose alike.
HSM_CALL = re.compile(r"\bhsm\s+([a-z][a-z0-9-]*)")
TIMEOUT = 30


def _fail(msg: str) -> None:
    print(f"gate could not run: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _get(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return r.read()
    except (urllib.error.URLError, TimeoutError) as e:  # pragma: no cover - network
        _fail(f"fetching {url}: {e}")
        raise  # unreachable, keeps type checkers happy


def local_version() -> str:
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text("utf-8"))
    if not m:
        _fail("could not read version from pyproject.toml")
    return m.group(1)  # type: ignore[union-attr]


def local_subcommands() -> set[str]:
    """The commands that exist on this working tree, released or not."""
    src = (ROOT / "src" / "homestead_memory" / "cli.py").read_text("utf-8")
    names = set(ADD_PARSER.findall(src))
    if not names:
        _fail("found no subcommands in cli.py; the add_parser pattern must have changed")
    return names


def published(version: str | None) -> tuple[str, set[str]]:
    """Return (version, subcommands) for the wheel PyPI serves right now."""
    url = f"https://pypi.org/pypi/{PACKAGE}/json" if not version \
        else f"https://pypi.org/pypi/{PACKAGE}/{version}/json"
    meta = json.loads(_get(url))
    ver = meta["info"]["version"]
    wheels = [u["url"] for u in meta["urls"] if u["filename"].endswith(".whl")]
    if not wheels:
        _fail(f"{PACKAGE} {ver} has no wheel on PyPI")
    z = zipfile.ZipFile(io.BytesIO(_get(wheels[0])))
    cli = [n for n in z.namelist() if n.endswith("homestead_memory/cli.py")]
    if not cli:
        _fail(f"published wheel for {ver} contains no cli.py")
    return ver, set(ADD_PARSER.findall(z.read(cli[0]).decode("utf-8", "replace")))


def claims(paths: list[Path], real: set[str]) -> dict[str, list[str]]:
    """Map each claimed command to the files claiming it.

    A token counts as a claim only if it is a real subcommand locally. Prose like
    "hsm and the ledger" and flags like `hsm --version` are therefore ignored by
    construction rather than by a blocklist that would rot.
    """
    found: dict[str, list[str]] = {}
    for p in paths:
        files = sorted(p.rglob("*.md")) if p.is_dir() else [p]
        for f in files:
            try:
                text = f.read_text("utf-8", errors="replace")
            except OSError:
                continue
            for name in set(HSM_CALL.findall(text)) & real:
                found.setdefault(name, []).append(str(f))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--also", action="append", default=[],
                    help="extra file or directory of published copy (e.g. launch docs in another repo)")
    ap.add_argument("--version", help="check this published version instead of latest")
    args = ap.parse_args()

    paths = [ROOT / d for d in DEFAULT_DOCS if (ROOT / d).exists()]
    for extra in args.also:
        p = Path(extra).expanduser()
        if not p.exists():
            _fail(f"--also path does not exist: {p}")
        paths.append(p)

    real = local_subcommands()
    pypi_version, pypi_commands = published(args.version)
    here = local_version()

    print(f"working tree : {here}  ({len(real)} subcommands)")
    print(f"published    : {pypi_version}  ({len(pypi_commands)} subcommands)")
    if here != pypi_version:
        # Reported, never fatal. Unreleased work on master is normal.
        unreleased = sorted(real - pypi_commands)
        print(f"note         : tree is at {here}, PyPI serves {pypi_version}"
              + (f"; unreleased commands: {', '.join(unreleased)}" if unreleased else ""))

    claimed = claims(paths, real)
    print(f"scanned      : {len(paths)} path(s), {len(claimed)} command(s) claimed\n")

    broken = {c: f for c, f in sorted(claimed.items()) if c not in pypi_commands}
    if not broken:
        print(f"PASS  every claimed command is in the published {pypi_version} wheel")
        return 0

    print(f"FAIL  copy claims {len(broken)} command(s) that {pypi_version} does not provide:\n")
    for cmd, files in broken.items():
        print(f"  hsm {cmd}")
        for f in files:
            print(f"      claimed in {f}")
    print("\nA reader who runs `pip install homestead-memory` today cannot do what this copy")
    print("says. Cut a release, or remove the claim. Do not ship the copy first.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
