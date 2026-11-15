"""One-command local verification: syntax, assets and the full test suite."""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    failures: list[str] = []
    python_files = sorted(ROOT.glob("*.py")) + sorted((ROOT / "server").glob("*.py")) + sorted((ROOT / "tests").glob("*.py")) + sorted((ROOT / "tools").glob("*.py"))
    for path in python_files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"python syntax: {path}: {exc}")
    print(f"[ok] ast check ({len(python_files)} files)" if not failures else "[fail] ast check")

    node = shutil.which("node")
    if node:
        for path in sorted((ROOT / "web" / "assets" / "js").glob("*.js")):
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
            if result.returncode != 0:
                failures.append(f"js syntax: {path.name}: {result.stderr.strip()}")
        print("[ok] node --check" if not any(item.startswith("js syntax") for item in failures) else "[fail] node --check")
    else:
        print("[skip] node not found")

    result = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=str(ROOT))
    if result.returncode != 0:
        failures.append("pytest failed")
    print("[ok] pytest" if result.returncode == 0 else "[fail] pytest")

    if failures:
        print("\nFAILURES")
        for failure in failures:
            print(" -", failure)
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
