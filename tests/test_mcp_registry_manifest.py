"""server.json must stay true, or the MCP registry advertises a broken install.

The registry listing is a discovery surface aimed at exactly our users (Claude Code and
Cursor), which means a wrong entry there is worse than no entry: someone finds us, runs
the documented command, it errors, and they leave.

Three things rot silently and are checked here:

1. **The invocation.** `uvx <package>` runs a command NAMED after the package. Ours is
   `homestead-memory` but the console script is `hsm`, so the naive form fails with
   "An executable named `homestead-memory` is not provided by package
   `homestead-memory`". Caught by actually running it before publishing. The manifest
   therefore has to carry `--from homestead-memory hsm` as runtime arguments.
2. **The version, in two places.** server.json pins it at the top level AND inside
   packages[0]. A release that bumps pyproject and forgets either one leaves the registry
   pointing at a stale package.
3. **The ownership marker.** PyPI ownership is proven by an `mcp-name:` string in the
   README, which becomes the PyPI description. If it drifts from server.json's `name`,
   publishing is rejected, and the failure message is not obvious.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "server.json"

pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="server.json not in this checkout")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _pyproject_version() -> str:
    m = re.search(r'^version = "(.+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert m, "could not read version from pyproject.toml"
    return m.group(1)


def _console_scripts() -> set[str]:
    s = (ROOT / "pyproject.toml").read_text()
    block = re.search(r"\[project\.scripts\](.*?)(\n\[|\Z)", s, re.S)
    if not block:
        return set()
    return set(re.findall(r"^\s*([A-Za-z0-9_-]+)\s*=", block.group(1), re.M))


def test_version_matches_pyproject_everywhere():
    """Both pins, not just the top-level one."""
    d = _manifest()
    v = _pyproject_version()
    assert d["version"] == v, f"server.json version {d['version']} != pyproject {v}"
    for i, pkg in enumerate(d["packages"]):
        assert pkg["version"] == v, f"packages[{i}].version {pkg['version']} != pyproject {v}"


def test_ownership_marker_matches_the_server_name():
    """PyPI ownership is verified by this exact string in the README."""
    name = _manifest()["name"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"mcp-name:\s*(\S+?)\s*-->", readme)
    assert m, "README is missing the '<!-- mcp-name: ... -->' ownership marker"
    assert m.group(1) == name, (
        f"README marker {m.group(1)!r} != server.json name {name!r}; publishing will be rejected"
    )


def test_the_advertised_command_can_actually_run():
    """The bug this file exists for.

    Reconstruct what a client would execute from the manifest and assert it names a
    console script the package actually installs. `uvx homestead-memory mcp` looks
    plausible and does not work.
    """
    pkg = _manifest()["packages"][0]
    runtime = [a.get("value") for a in pkg.get("runtimeArguments", [])]
    scripts = _console_scripts()

    assert scripts, "pyproject declares no console scripts"
    named = [v for v in runtime if v in scripts]
    assert named, (
        f"none of the runtimeArguments {runtime} names an installed console script "
        f"{sorted(scripts)}. uvx runs a command named after the PACKAGE unless --from "
        f"is used, so the manifest must carry '--from <package> <script>'."
    )


def test_from_flag_targets_this_package():
    pkg = _manifest()["packages"][0]
    args = pkg.get("runtimeArguments", [])
    from_arg = next((a for a in args if a.get("name") == "--from"), None)
    assert from_arg, "uvx needs --from because the script name differs from the package name"
    assert from_arg["value"] == pkg["identifier"], (
        f"--from {from_arg['value']!r} does not match package identifier {pkg['identifier']!r}"
    )


def test_registry_constraints_that_the_official_validator_enforces():
    """Cheap local versions of rules the registry rejects on, so we find out here."""
    d = _manifest()
    assert len(d["description"]) <= 100, (
        f"description is {len(d['description'])} chars; the schema caps it at 100 "
        f"(this exact rule already rejected a first draft)"
    )
    assert d["name"].startswith("io.github."), "GitHub-namespaced servers must use io.github.*"
    for pkg in d["packages"]:
        if pkg["registryType"] == "pypi":
            assert pkg["registryBaseUrl"] == "https://pypi.org", (
                "the registry accepts https://pypi.org only"
            )
