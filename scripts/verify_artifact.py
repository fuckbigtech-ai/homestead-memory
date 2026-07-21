#!/usr/bin/env python3
"""Fail-closed inspection of a built wheel before publication."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY)")


def main() -> int:
    wheels = sorted(Path("dist").glob("*.whl"))
    if len(wheels) != 1:
        print(f"expected exactly one wheel, found {len(wheels)}", file=sys.stderr)
        return 2
    wheel = wheels[0]
    expected = "0.2.4"
    if f"-{expected}-" not in wheel.name:
        print(f"wheel version mismatch: {wheel.name}", file=sys.stderr)
        return 2
    names: list[str] = []
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            names.append(name)
            if name.endswith((".py", ".json", ".md", ".txt")):
                data = archive.read(name).decode("utf-8", "ignore")
                if SECRET.search(data):
                    print(f"possible secret in wheel member: {name}", file=sys.stderr)
                    return 1
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    Path("dist/artifact-integrity.json").write_text(
        json.dumps({"wheel": wheel.name, "sha256": digest, "members": names}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"wheel": wheel.name, "sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
