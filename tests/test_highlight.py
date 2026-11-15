"""Highlight matcher checks (pure JS, no browser required)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIGHLIGHT = ROOT / "web" / "assets" / "js" / "highlight.js"


def test_highlight_matcher_handles_inline_markup_and_newlines():
    node = shutil.which("node")
    if not node:
        return
    script = r"""
global.window = {};
require(process.argv[1]);
const H = window.KL.highlight;
const rendered = '补充：为什么读输入这么麻烦？ 走到这一步，你大概会问：Python 一行 input() 的事，Rust 为什么要写四五行的？ 另外，match 像 Python 的 if/elif 加强版，要求把所有情况都覆盖；return 用来提前退出。';
const quote = '**补充**：为什么读输入这么麻烦？\n\n走到这一步，你大概会问：Python 一行 `input()` 的事，Rust 为什么要写四五行的？\n\n另外，`match` 像 Python 的 `if/elif` 加强版，要求把所有情况都覆盖；`return` 用来提前退出。';
const hit = H.locate(rendered, quote);
const exact = H.locate('abcdef', 'zzz');
console.log(JSON.stringify({
  hit: hit,
  slice: hit ? rendered.slice(hit.start, hit.end) : null,
  missing: exact,
  plain: H.plainMarkdown('## 标题\n\n- **粗体** 与 [链接](https://example.com)')
}));
"""
    result = subprocess.run(
        [node, "-e", script, str(HIGHLIGHT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["hit"] is not None
    assert payload["slice"] == payload["hit"]["plain"]
    assert payload["slice"].startswith("补充：为什么读输入这么麻烦？")
    assert payload["slice"].endswith("return 用来提前退出。")
    assert payload["missing"] is None
    assert payload["plain"] == "标题\n\n粗体 与 链接"


def test_highlight_asset_is_loaded_before_views():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert html.index("/assets/js/highlight.js") < html.index("/assets/js/views.js")
    css = (ROOT / "web" / "assets" / "app.css").read_text(encoding="utf-8")
    assert "#ffe066" in css
    assert "box-decoration-break" in css
