"""Unit tests for chunking and BM25 retrieval."""

from __future__ import annotations

from pathlib import Path

from server import indexer


def test_tokenize_chinese_and_ascii():
    tokens = indexer.tokenize("Future 是惰性执行的 async 函数")
    assert "future" in tokens
    assert "async" in tokens
    assert "惰性" in tokens
    assert "执行" in tokens


def test_split_markdown_respects_headings():
    text = "# 01 · 标题\n\n## 核心概念\n\n" + ("内容" * 200) + "\n\n## 练习\n\n做练习。"
    chunks = indexer.split_markdown(text, target=80, hard=120, overlap=10)
    assert len(chunks) >= 3
    assert any(chunk["heading"] == "核心概念" for chunk in chunks)
    assert all(chunk["heading"] != "" for chunk in chunks)


def test_search_ranks_relevant_file(tmp_path: Path):
    kb = tmp_path / "kb"
    path = kb / "path"
    path.mkdir(parents=True)
    (path / "01-除霉.md").write_text(
        "# 01 · 除霉\n\n## 核心概念\n\n霉斑需要用软毛刷清除，并保持干燥。\n",
        encoding="utf-8",
    )
    (path / "02-装订.md").write_text(
        "# 02 · 装订\n\n## 核心概念\n\n线装书的装订流程。\n",
        encoding="utf-8",
    )
    hits = indexer.search(kb, "除霉 霉斑", 3)
    assert hits
    assert hits[0]["file"] == "01-除霉.md"
    assert hits[0]["ref"] == "K1"


def test_cache_invalidates_after_change(tmp_path: Path):
    kb = tmp_path / "kb"
    path = kb / "path"
    path.mkdir(parents=True)
    target = path / "01-a.md"
    target.write_text("# 01 · a\n\n## 核心概念\n\n旧内容。\n", encoding="utf-8")
    assert indexer.search(kb, "旧内容", 3)
    target.write_text("# 01 · a\n\n## 核心概念\n\n全新的关键词。\n", encoding="utf-8")
    hits = indexer.search(kb, "全新的关键词", 3)
    assert hits and "全新的关键词" in hits[0]["text"]
