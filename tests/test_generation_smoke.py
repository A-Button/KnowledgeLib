"""End-to-end smoke test for the learning-path generation pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.support import iter_events, parse_sse_text


@pytest.mark.asyncio
async def test_generate_path_creates_numbered_files(client, data_dir: Path, mock_url: str):
    created = await client.post("/api/kbs", json={"name": "测试知识库", "description": "demo"})
    assert created.status_code == 200, created.text
    kb = created.json()

    payload = {
        "domain": "Rust 异步编程",
        "weeks": 2,
        "hoursPerWeek": 10,
        "level": "zero",
        "goals": ["project"],
        "preferences": "偏重可运行示例",
        "moduleCount": 4,
    }
    response = await client.post(f"/api/kbs/{kb['id']}/paths", json=payload, timeout=60)
    assert response.status_code == 200, response.text
    events = parse_sse_text(response.text)
    names = [name for name, _ in events]
    assert "start" in names
    assert "plan" in names
    assert names.count("module") >= 4
    assert names[-1] == "done"
    done = iter_events(events, "done")[-1]
    assert done["status"] == "complete"

    detail = await client.get(f"/api/kbs/{kb['id']}")
    assert detail.status_code == 200
    paths = detail.json()["paths"]
    assert len(paths) == 1
    path = paths[0]
    path_dir = Path(path["absPath"])
    files = sorted(path_dir.glob("*.md"))
    assert [f.name for f in files][0] == "00-学习路径总览.md"
    assert len(files) == 5
    for file in files:
        first = file.read_text(encoding="utf-8").splitlines()[0]
        assert re.match(r"^# \d{2} · ", first), (file.name, first)

    overview = (path_dir / "00-学习路径总览.md").read_text(encoding="utf-8")
    assert "# 00 · 学习路径总览" in overview
    assert "01-概念地图.md" in overview

    import httpx

    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    assert stats["chat_calls"] >= 5
    plan_calls = [item for item in stats["requests"] if item["path"].endswith("/chat/completions")]
    systems = [str(item["body"]["messages"][0]["content"]) for item in plan_calls]
    assert any("LEARNING_PATH_PLAN" in text for text in systems)
    assert any("LEARNING_MODULE_MATERIAL" in text for text in systems)
