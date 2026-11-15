"""Static asset and front-end consistency checks (no browser required)."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
ASSETS = WEB / "assets"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_required_assets_exist():
    for name in (
        "index.html",
        "assets/app.css",
        "assets/theme.css",
        "assets/favicon.svg",
        "assets/vendor/liquidGL.js",
        "assets/js/theme-init.js",
        "assets/js/theme.js",
        "assets/js/md.js",
        "assets/js/api.js",
        "assets/js/ui.js",
        "assets/js/highlight.js",
        "assets/js/views.js",
        "assets/js/app.js",
    ):
        assert (WEB / name).is_file(), name


def test_head_links_and_scripts_resolve():
    html = read(WEB / "index.html")
    assert '<html lang="zh-CN" data-theme="archive">' in html
    assert "Content-Security-Policy" not in html
    refs = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
    assert refs
    for ref in refs:
        assert (WEB / ref.split("?", 1)[0].lstrip("/")).is_file(), ref
    assert not re.search(r'(?:src|href)="https?://', html)
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)


def test_css_has_design_tokens():
    css = read(ASSETS / "app.css")
    for token in ("--paper:", "--ink:", "--accent:", "--serif:", "--sans:", "--mono:"):
        assert token in css
    assert "http://" not in css and "https://" not in css
    assert "@media (max-width: 860px)" in css




def test_theme_assets_and_order():
    html = read(WEB / "index.html")
    assert 'data-theme="archive"' in html
    assert html.index("/assets/js/theme-init.js") < html.index("/assets/app.css")
    assert html.index("/assets/app.css") < html.index("/assets/theme.css")
    assert html.index("/assets/js/theme.js") < html.index("/assets/js/views.js")
    css = read(ASSETS / "theme.css")
    for token in ('html[data-theme="modern"]', "html.no-serif", "glass-on", ".theme-picker"):
        assert token in css, token
    license_text = read(ASSETS / "vendor" / "liquidGL-LICENSE.txt")
    assert "MIT License" in license_text


def test_by_id_references_exist():
    html = read(WEB / "index.html")
    js = "\n".join(read(path) for path in sorted((ASSETS / "js").glob("*.js")))
    ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html + "\n" + js))
    refs = set(re.findall(r'byId\(["\']([A-Za-z0-9_-]+)["\']\)', js))
    refs.update(re.findall(r'getElementById\(["\']([A-Za-z0-9_-]+)["\']\)', js))
    ids.update(re.findall(r'\b(?:input|textarea|select)\(["\']([A-Za-z0-9_-]+)["\']', js))
    missing = sorted(refs - ids)
    assert not missing, missing


def test_javascript_syntax():
    node = shutil.which("node")
    if not node:
        return
    for path in sorted((ASSETS / "js").glob("*.js")):
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


def test_python_syntax():
    for path in sorted(ROOT.glob("*.py")) + sorted((ROOT / "server").glob("*.py")) + sorted((ROOT / "tests").glob("*.py")):
        ast.parse(read(path), filename=str(path))
