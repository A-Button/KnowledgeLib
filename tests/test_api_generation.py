"""Generation API tests: repair, partial output, resume and CRUD."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from tests.support import iter_events, parse_sse_text


def spec(module_count: int = 3) -> dict:
    return {
        "domain": "Rust 异步编程",
        "weeks": 2,
        "hoursPerWeek": 6,
        "level": "zero",
        "goals": ["project"],
        "preferences": "偏重可运行示例",
        "moduleCount": module_count,
    }


async def create_kb(client) -> dict:
    response = await client.post("/api/kbs", json={"name": "测试库", "description": "demo"})
    assert response.status_code == 200, response.text
    return response.json()


async def generate(client, kb_id: str, module_count: int = 3):
    response = await client.post(f"/api/kbs/{kb_id}/paths", json=spec(module_count), timeout=60)
    assert response.status_code == 200, response.text
    return parse_sse_text(response.text)


@pytest.mark.asyncio
async def test_plan_repair(client, mock_url: str):
    httpx.post(mock_url + "/__mock/config", json={"plan_invalid_once": True}, timeout=5)
    kb = await create_kb(client)
    events = await generate(client, kb["id"], 3)
    done = iter_events(events, "done")[-1]
    assert done["status"] == "complete"
    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    plan_calls = [
        item
        for item in stats["requests"]
        if item["path"].endswith("/chat/completions")
        and "LEARNING_PATH_PLAN" in str(item["body"]["messages"][0]["content"])
    ]
    assert len(plan_calls) >= 2


@pytest.mark.asyncio
async def test_partial_then_resume(client, mock_url: str):
    httpx.post(mock_url + "/__mock/config", json={"fail_module_times": {"2": 2}}, timeout=5)
    kb = await create_kb(client)
    events = await generate(client, kb["id"], 3)
    done = iter_events(events, "done")[-1]
    assert done["status"] == "partial"
    detail = (await client.get(f"/api/kbs/{kb['id']}")).json()
    path = detail["paths"][0]
    assert path["status"] == "partial"
    path_dir = Path(path["absPath"])
    names = sorted(file.name for file in path_dir.glob("*.md"))
    assert "00-学习路径总览.md" in names
    assert "02-核心机制.md" not in names

    resume = await client.post(
        f"/api/kbs/{kb['id']}/paths/{path['id']}/resume",
        timeout=60,
    )
    assert resume.status_code == 200, resume.text
    resume_events = parse_sse_text(resume.text)
    assert iter_events(resume_events, "done")[-1]["status"] == "complete"
    path_dir = Path(path["absPath"])
    assert (path_dir / "02-核心机制.md").is_file()
    assert (path_dir / "00-学习路径总览.md").read_text(encoding="utf-8").count("已完成") >= 1


@pytest.mark.asyncio
async def test_invalid_specs_rejected_without_side_effect(client, data_dir: Path):
    kb = await create_kb(client)
    bad_payloads = [
        {**spec(3), "domain": "x"},
        {**spec(3), "weeks": 0},
        {**spec(3), "moduleCount": 2},
        {**spec(3), "goals": ["unknown"]},
        {**spec(3), "preferences": "x" * 501},
    ]
    for payload in bad_payloads:
        response = await client.post(f"/api/kbs/{kb['id']}/paths", json=payload)
        assert response.status_code == 400, (payload, response.text)
    detail = (await client.get(f"/api/kbs/{kb['id']}")).json()
    assert detail["paths"] == []


@pytest.mark.asyncio
async def test_generation_requires_key_on_remote_base(client):
    await client.put(
        "/api/settings",
        json={"api": {"baseUrl": "https://api.deepseek.com", "clearApiKey": True}},
    )
    kb = await create_kb(client)
    response = await client.post(f"/api/kbs/{kb['id']}/paths", json=spec(3))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_configured"


@pytest.mark.asyncio
async def test_file_endpoint_search_and_reindex(client):
    kb = await create_kb(client)
    events = await generate(client, kb["id"], 3)
    assert iter_events(events, "done")[-1]["status"] == "complete"
    detail = (await client.get(f"/api/kbs/{kb['id']}")).json()
    path = detail["paths"][0]
    files = sorted(file["name"] for file in path["files"])
    assert files[0] == "00-学习路径总览.md"

    file_response = await client.get(
        f"/api/kbs/{kb['id']}/file",
        params={"path": path["id"], "name": files[1]},
    )
    assert file_response.status_code == 200
    content = file_response.json()["content"]
    assert content.splitlines()[0].startswith("# 01 · ")

    bad = await client.get(
        f"/api/kbs/{kb['id']}/file",
        params={"path": path["id"], "name": "../x.md"},
    )
    assert bad.status_code == 400

    search = await client.get(f"/api/kbs/{kb['id']}/search", params={"q": "核心概念", "topK": 4})
    assert search.status_code == 200
    hits = search.json()["hits"]
    assert hits
    assert re.match(r"^K\d+$", hits[0]["ref"])

    reindex = await client.post(f"/api/kbs/{kb['id']}/reindex")
    assert reindex.status_code == 200
    assert reindex.json()["files"] == 4


@pytest.mark.asyncio
async def test_delete_path_and_kb(client, data_dir: Path):
    kb = await create_kb(client)
    events = await generate(client, kb["id"], 3)
    assert iter_events(events, "done")[-1]["status"] == "complete"
    detail = (await client.get(f"/api/kbs/{kb['id']}")).json()
    path = detail["paths"][0]

    deleted = await client.delete(f"/api/kbs/{kb['id']}/paths/{path['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert Path(deleted.json()["trashedTo"]).is_dir()
    detail = (await client.get(f"/api/kbs/{kb['id']}")).json()
    assert detail["paths"] == []

    deleted_kb = await client.delete(f"/api/kbs/{kb['id']}")
    assert deleted_kb.status_code == 200
    assert Path(deleted_kb.json()["trashedTo"]).is_dir()
    listing = (await client.get("/api/kbs")).json()
    assert listing["kbs"] == []

@pytest.mark.asyncio
async def test_generate_with_deepseek_web_and_thinking(client, mock_url: str):
    await client.put("/api/settings", json={"api": {"model": "deepseek-chat"}})
    created = await client.post("/api/kbs", json={"name": "联网库", "description": ""})
    kb = created.json()
    response = await client.post(
        f"/api/kbs/{kb['id']}/paths",
        json={
            "domain": "Rust 异步编程",
            "weeks": 2,
            "hoursPerWeek": 6,
            "level": "zero",
            "goals": ["project"],
            "preferences": "偏实战",
            "moduleCount": 3,
            "webEnabled": True,
            "thinking": "low",
        },
        timeout=60,
    )
    assert response.status_code == 200, response.text
    events = parse_sse_text(response.text)
    assert iter_events(events, "done")[-1]["status"] == "complete"
    detail = (await client.get(f"/api/kbs/{kb['id']}")).json()
    path = detail["paths"][0]
    assert path["spec"]["webEnabled"] is True
    assert path["spec"]["thinking"] == "low"
    assert path["model"] == "deepseek-flash"

    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    chats = [item for item in stats["requests"] if item["path"].endswith("/chat/completions")]
    assert chats
    body = chats[0]["body"]
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"
    tool_types = [item.get("type") for item in body["tools"]]
    assert "deepseek_web_search" in tool_types
    assert "deepseek_read_url" not in tool_types


@pytest.mark.asyncio
async def test_generate_web_rejected_for_generic_provider(client):
    created = await client.post("/api/kbs", json={"name": "普通库", "description": ""})
    kb = created.json()
    response = await client.post(
        f"/api/kbs/{kb['id']}/paths",
        json={
            "domain": "Rust 异步编程",
            "weeks": 2,
            "hoursPerWeek": 6,
            "moduleCount": 3,
            "webEnabled": True,
        },
        timeout=30,
    )
    assert response.status_code == 400
