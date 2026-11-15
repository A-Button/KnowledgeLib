"""Tests for the reading assistant's document tools."""

from __future__ import annotations

import json

from server import doc_tools, indexer, library


def make_ref(tmp_path):
    path_dir = tmp_path / "01-测试路径"
    path_dir.mkdir()
    (path_dir / "02-核心机制.md").write_text(
        "# 02 · 核心机制\n\n## 核心概念\n\nMOCK-MATERIAL-2 原始内容。\n\n## 练习\n\n完成练习。\n",
        encoding="utf-8",
    )
    ref = library.PathRef({"id": "path-test", "name": "测试路径"}, path_dir, library.list_md_files(path_dir))
    return ref, indexer.IndexCache()


def test_read_and_highlight(tmp_path):
    ref, cache = make_ref(tmp_path)
    read = doc_tools.execute("read", {"file": "02-核心机制.md"}, ref=ref, current_file="", data_dir=tmp_path, index_cache=cache)
    assert read["ok"] is True
    assert "MOCK-MATERIAL-2" in read["content"]
    assert read["first_line"].startswith("# 02 · ")

    highlighted = doc_tools.execute(
        "highlight",
        {"quote": "MOCK-MATERIAL-2", "note": "重点"},
        ref=ref,
        current_file="02-核心机制.md",
        data_dir=tmp_path,
        index_cache=cache,
    )
    assert highlighted["ok"] is True
    assert highlighted["note"] == "重点"

    missing = doc_tools.execute(
        "highlight",
        {"quote": "不存在的文字"},
        ref=ref,
        current_file="02-核心机制.md",
        data_dir=tmp_path,
        index_cache=cache,
    )
    assert missing["ok"] is False


def test_edit_requires_unique_text_and_keeps_header(tmp_path):
    ref, cache = make_ref(tmp_path)
    result = doc_tools.execute(
        "edit",
        {
            "file": "02-核心机制.md",
            "old_text": "MOCK-MATERIAL-2",
            "new_text": "MOCK-EDITED",
            "reason": "测试",
        },
        ref=ref,
        current_file="02-核心机制.md",
        data_dir=tmp_path,
        index_cache=cache,
    )
    assert result["ok"] is True
    text = (ref.directory / "02-核心机制.md").read_text(encoding="utf-8")
    assert "MOCK-EDITED" in text
    assert text.startswith("# 02 · ")
    assert list((tmp_path / "trash" / "edits").glob("*"))

    not_unique = doc_tools.execute(
        "edit",
        {"file": "02-核心机制.md", "old_text": "无", "new_text": "有"},
        ref=ref,
        current_file="02-核心机制.md",
        data_dir=tmp_path,
        index_cache=cache,
    )
    assert not_unique["ok"] is False
    assert "0 次" in not_unique["error"]


def test_write_enforces_numbering_spec(tmp_path):
    ref, cache = make_ref(tmp_path)
    bad = doc_tools.execute(
        "write",
        {"file": "04-新讲义.md", "content": "# 05 · 错误编号\n\n## 核心概念\n\n正文"},
        ref=ref,
        current_file="",
        data_dir=tmp_path,
        index_cache=cache,
    )
    assert bad["ok"] is False
    assert "必须以" in bad["error"]

    bad_name = doc_tools.execute(
        "write",
        {"file": "新讲义.md", "content": "# 04 · 新讲义\n\n## 核心概念\n\n正文"},
        ref=ref,
        current_file="",
        data_dir=tmp_path,
        index_cache=cache,
    )
    assert bad_name["ok"] is False

    good = doc_tools.execute(
        "write",
        {
            "file": "04-新讲义.md",
            "content": "# 04 · 新讲义\n\n## 本节目标\n\n目标。\n\n## 核心概念\n\n正文。",
        },
        ref=ref,
        current_file="",
        data_dir=tmp_path,
        index_cache=cache,
    )
    assert good["ok"] is True
    assert (ref.directory / "04-新讲义.md").is_file()
    assert doc_tools.execute(
        "write",
        {"file": "04-新讲义.md", "content": "# 04 · 新讲义\n\n## 核心概念\n\n重复"},
        ref=ref,
        current_file="",
        data_dir=tmp_path,
        index_cache=cache,
    )["ok"] is False


def test_tools_reject_traversal(tmp_path):
    ref, cache = make_ref(tmp_path)
    result = doc_tools.execute(
        "read",
        {"file": "../outside.md"},
        ref=ref,
        current_file="",
        data_dir=tmp_path,
        index_cache=cache,
    )
    assert result["ok"] is False
    assert "文件名不合法" in result["error"]
