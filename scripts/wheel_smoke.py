#!/usr/bin/env python3
"""Install the single CI wheel in a disposable cross-platform venv."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

root = Path(__file__).resolve().parents[1]
wheels = sorted((root / "dist").glob("*.whl"))
if len(wheels) != 1:
    raise SystemExit(f"expected one wheel, found {len(wheels)}")
envdir = root / ".ci-wheel-smoke"
shutil.rmtree(envdir, ignore_errors=True)
venv.EnvBuilder(with_pip=True).create(envdir)
exe = envdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
subprocess.run([str(exe), "-m", "pip", "install", "--no-deps", str(wheels[0])], check=True)
subprocess.run([str(exe), "-m", "homestead_memory.cli", "--version"], check=True)
shutil.rmtree(envdir, ignore_errors=True)
