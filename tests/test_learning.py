"""Unit tests for plan parsing, validation and rendering."""

from __future__ import annotations

import pytest

from server import learning


SPEC = {
    "domain": "Rust 异步编程",
    "weeks": 2,
    "hoursPerWeek": 10,
    "level": "zero",
    "goals": ["project"],
    "preferences": "偏实战",
    "moduleCount": 3,
}


def _plan(count: int = 3):
    return {
        "title": "测试路径",
        "summary": "摘要",
        "modules": [
            {
                "index": index,
                "title": f"模块 {index}",
                "summary": "s",
                "objectives": ["o1", "o2"],
                "prerequisites": [index - 1] if index > 1 else [],
                "estimatedHours": 2,
                "topics": ["t"],
                "practice": "p",
            }
            for index in range(1, count + 1)
        ],
    }


def test_parse_json_with_prose_and_fence():
    raw = "说明文字\n" + chr(96) * 3 + "json\n" + '{"title":"T","modules":[]}' + "\n" + chr(96) * 3
    data = learning.parse_plan_json(raw)
    assert data["title"] == "T"
    with pytest.raises(learning.PlanError):
        learning.parse_plan_json("没有 JSON")


def test_validate_plan_renumbers_and_checks_count():
    data = _plan(3)
    plan = learning.validate_plan(data, SPEC)
    assert [m["index"] for m in plan["modules"]] == [1, 2, 3]
    assert plan["modules"][1]["prerequisites"] == [1]
    with pytest.raises(learning.PlanError):
        learning.validate_plan(_plan(2), SPEC)


def test_validate_plan_clamps_and_fills():
    data = _plan(3)
    data["modules"][0]["estimatedHours"] = 99
    data["modules"][0]["objectives"] = []
    plan = learning.validate_plan(data, SPEC)
    assert plan["modules"][0]["estimatedHours"] == 12
    assert plan["modules"][0]["objectives"]


def test_clean_body_strips_h1_and_fence():
    raw = chr(96) * 3 + "markdown\n# 应删除\n\n## 核心概念\n\n正文" + chr(96) * 3
    body = learning.clean_body(raw)
    assert body.startswith("## 核心概念")
    assert "# 应删除" not in body


def test_make_file_name():
    assert learning.make_file_name(0, "总览") == "00-学习路径总览.md"
    assert learning.make_file_name(1, "异步基础") == "01-异步基础.md"


def test_render_module_has_numbered_h1():
    plan = learning.validate_plan(_plan(3), SPEC)
    content = learning.render_module(SPEC, plan, plan["modules"][1], "## 核心概念\n\n正文")
    assert content.splitlines()[0] == "# 02 · 模块 2"
    assert "## 本节目标" in content


def test_overview_contains_links_and_status():
    plan = learning.validate_plan(_plan(3), SPEC)
    meta = {
        "createdAt": "2026-11-14T00:00:00Z",
        "model": "mock",
        "modules": [
            {"index": 1, "status": "done"},
            {"index": 2, "status": "failed"},
            {"index": 3, "status": "pending"},
        ],
    }
    text = learning.build_overview(SPEC, plan, meta, "partial")
    assert text.startswith("# 00 · 学习路径总览")
    assert "01-模块-1.md" in text
    assert "部分完成" in text
    assert "缺少：02、03" in text
