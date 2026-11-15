"""Guard rails for the Windows and POSIX launch scripts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_run_cmd_is_ascii_and_bootstraps():
    raw = (ROOT / "run.cmd").read_bytes()
    assert all(byte < 128 for byte in raw), "run.cmd must stay ASCII"
    text = raw.decode("ascii")
    assert "app.py" in text
    assert "uv sync" in text
    assert "NO_INSTALL" in text
    assert "%*" in text
    assert "exit /b" in text


def test_run_sh_shebang_and_exec():
    text = (ROOT / "run.sh").read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh")
    assert "set -eu" in text
    assert "exec \"$PY\" app.py" in text
    assert '\"$@\"' in text
    assert "uv sync" in text


def test_run_sh_syntax():
    shell = shutil.which("sh")
    if not shell:
        candidates = [
            Path("C:/Program Files/Git/usr/bin/sh.exe"),
            Path("C:/Program Files/Git/bin/sh.exe"),
        ]
        shell = next((str(path) for path in candidates if path.is_file()), None)
    if not shell:
        return
    result = subprocess.run([shell, "-n", str(ROOT / "run.sh")], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
