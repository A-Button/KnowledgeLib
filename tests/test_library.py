"""Unit tests for knowledge base and path directories."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server import library
from server.errors import ApiError


def test_slug_and_title_cleaning():
    assert library.slugify_dir("古籍/修复") == "古籍修复"
    assert library.slugify_dir("  我的 书库  ") == "我的-书库"
    assert library.slugify_dir("CON").startswith("_CON")
    assert library.slugify_title("01 · 异步基础") == "01-异步基础"
    assert library.slugify_title("Go: 并发 / 通道") == "Go-并发-通道"
    assert library.slugify_title("   ") == ""


def test_create_kb_and_duplicate(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    meta = library.create_kb(root, "测试库", "描述")
    kb_dir = root / meta["slug"]
    assert (kb_dir / ".kb.json").is_file()
    assert meta["name"] == "测试库"
    second = library.create_kb(root, "测试库")
    assert (root / second["slug"]).is_dir()
    assert second["slug"] != meta["slug"]


def test_create_generated_path_and_public(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    meta = library.create_kb(root, "库")
    kb_dir = root / meta["slug"]
    ref = library.create_generated_path(kb_dir, {"domain": "Rust", "moduleCount": 3}, "Rust 路径", "mock")
    assert ref.directory.is_dir()
    assert ref.status == "planning"
    ref.meta["plan"] = {
        "title": "Rust 路径",
        "summary": "",
        "modules": [
            {"index": 1, "title": "基础", "objectives": ["a"]},
            {"index": 2, "title": "进阶", "objectives": ["b"]},
        ],
    }
    ref.meta["modules"] = [{"index": 1, "status": "done", "bytes": 3, "file": "01-基础.md"}]
    library.write_path_meta(ref.directory, ref.meta)
    public = library.path_public(library.find_path(kb_dir, ref.id))
    assert public["moduleCount"] == 2
    assert public["doneCount"] == 1
    assert public["status"] == "planning"


def test_external_directory_discovered(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    meta = library.create_kb(root, "库")
    kb_dir = root / meta["slug"]
    external = kb_dir / "外部资料"
    external.mkdir()
    (external / "01-旧文件.md").write_text("# 01 · 旧文件\n", encoding="utf-8")
    paths = library.iter_paths(kb_dir)
    assert len(paths) == 1
    ref = paths[0]
    assert ref.status == "external"
    assert ref.id.startswith("p")
    assert library.find_path(kb_dir, ref.id).directory == external


def test_read_md_rejects_traversal(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    meta = library.create_kb(root, "库")
    kb_dir = root / meta["slug"]
    ref = library.create_generated_path(kb_dir, {"domain": "x"}, "路径", "mock")
    (ref.directory / "01-正常.md").write_text("# 01 · 正常\n\n正文", encoding="utf-8")
    result = library.read_md(kb_dir, ref.id, "01-正常.md")
    assert result["content"].startswith("# 01")
    with pytest.raises(ApiError):
        library.read_md(kb_dir, ref.id, "../secret.md")
    with pytest.raises(ApiError):
        library.read_md(kb_dir, ref.id, "C:/Windows/win.ini")
    with pytest.raises(ApiError):
        library.read_md(kb_dir, ref.id, "notes.txt")


def test_delete_moves_to_trash(tmp_path):
    data_dir = tmp_path / "data"
    root = data_dir / "library"
    root.mkdir(parents=True)
    meta = library.create_kb(root, "库")
    kb_dir = root / meta["slug"]
    ref = library.create_generated_path(kb_dir, {"domain": "x"}, "路径", "mock")
    (ref.directory / "01-a.md").write_text("# 01 · a\n", encoding="utf-8")
    ref.meta["status"] = "complete"
    library.write_path_meta(ref.directory, ref.meta)
    result = library.delete_path(root, data_dir, meta["id"], ref.id)
    assert result["deleted"] is True
    assert not ref.directory.exists()
    assert Path(result["trashedTo"]).is_dir()
    assert Path(result["trashedTo"]).parent.name == "trash"


def test_list_md_files_sorted_and_numbered(tmp_path):
    directory = tmp_path / "path"
    directory.mkdir()
    (directory / "02-b.md").write_text("b", encoding="utf-8")
    (directory / "00-总览.md").write_text("a", encoding="utf-8")
    (directory / "01-a.md").write_text("c", encoding="utf-8")
    names = [item["name"] for item in library.list_md_files(directory)]
    assert names == ["00-总览.md", "01-a.md", "02-b.md"]
