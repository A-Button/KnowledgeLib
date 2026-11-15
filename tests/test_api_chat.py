"""Chat, SSE and web-tool API tests."""

from __future__ import annotations

import json
import re

import httpx
import pytest

from tests.support import iter_events, parse_sse_text, read_sse


async def make_generated_kb(client, module_count: int = 3) -> dict:
    created = await client.post("/api/kbs", json={"name": "学习库", "description": ""})
    assert created.status_code == 200, created.text
    kb = created.json()
    payload = {
        "domain": "Rust 异步编程",
        "weeks": 2,
        "hoursPerWeek": 6,
        "level": "zero",
        "goals": ["project"],
        "preferences": "偏实战",
        "moduleCount": module_count,
    }
    response = await client.post(f"/api/kbs/{kb['id']}/paths", json=payload, timeout=60)
    assert response.status_code == 200, response.text
    events = parse_sse_text(response.text)
    assert iter_events(events, "done")[-1]["status"] == "complete"
    detail = (await client.get(f"/api/kbs/{kb['id']}")).json()
    return {"kb": kb, "path": detail["paths"][0]}


@pytest.mark.asyncio
async def test_settings_masked_and_test_connection(client):
    settings = (await client.get("/api/settings")).json()
    assert settings["api"]["hasApiKey"] is True
    assert settings["api"]["apiKeyMasked"] == "****"
    assert "test-key" not in json.dumps(settings)
    assert str(settings["configPath"]).endswith("settings.json")
    assert "settings.json" in str(settings["configPath"])

    result = (await client.post("/api/settings/test")).json()
    assert result["ok"] is True
    assert "latencyMs" in result


@pytest.mark.asyncio
async def test_chat_kb_stream_cites_sources(client, mock_url: str):
    generated = await make_generated_kb(client)
    kb_id = generated["kb"]["id"]

    async with client.stream(
        "POST",
        "/api/chat",
        json={
            "kbId": kb_id,
            "message": "核心概念是什么",
            "mode": "kb",
            "history": [],
        },
        timeout=30,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = await read_sse(response)

    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert "sources" in names
    sources = iter_events(events, "sources")[0]["sources"]
    assert sources and sources[0]["kind"] == "kb"
    assert "K1" == sources[0]["ref"]
    text = "".join(item["text"] for item in iter_events(events, "delta"))
    assert "MOCK::" in text
    assert iter_events(events, "done")[-1]["finishReason"] == "stop"

    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    chat_requests = [item for item in stats["requests"] if item["path"].endswith("/chat/completions")]
    assert chat_requests
    system = str(chat_requests[-1]["body"]["messages"][0]["content"])
    assert "[K1]" in system
    assert "知识库资料" in system


@pytest.mark.asyncio
async def test_chat_kb_web_uses_search_and_fetch(client, mock_url: str):
    generated = await make_generated_kb(client)
    kb_id = generated["kb"]["id"]
    async with client.stream(
        "POST",
        "/api/chat",
        json={"kbId": kb_id, "message": "最新的异步运行时进展", "mode": "kb_web"},
        timeout=30,
    ) as response:
        events = await read_sse(response)

    tool_events = iter_events(events, "tool")
    assert any(item.get("tool") == "search" and item.get("status") == "done" for item in tool_events)
    assert any(item.get("tool") == "fetch" and item.get("status") == "done" for item in tool_events)
    sources = iter_events(events, "sources")[0]["sources"]
    assert any(item["kind"] == "web" for item in sources)
    assert any(item["kind"] == "kb" for item in sources)

    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    assert stats["search_calls"] >= 1
    assert stats["fetch_calls"] >= 1


@pytest.mark.asyncio
async def test_chat_non_stream_json(client):
    generated = await make_generated_kb(client, module_count=3)
    response = await client.post(
        "/api/chat",
        json={
            "kbId": generated["kb"]["id"],
            "message": "给我一个复习计划",
            "mode": "kb",
            "stream": False,
        },
        timeout=30,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"].startswith("MOCK::")
    assert body["sources"]


@pytest.mark.asyncio
async def test_chat_missing_key_returns_400(client):
    await client.put("/api/settings", json={"api": {"baseUrl": "https://api.deepseek.com", "clearApiKey": True}})
    response = await client.post(
        "/api/chat",
        json={"message": "你好", "mode": "plain"},
        timeout=10,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_configured"


@pytest.mark.asyncio
async def test_web_search_and_fetch_endpoints(client):
    search = await client.post(
        "/api/web/search",
        json={"query": "DeepSeek", "count": 2, "need_content": True},
    )
    assert search.status_code == 200, search.text
    results = search.json()["results"]
    assert len(results) == 2
    assert results[0]["url"].startswith("https://example.com/")

    page = await client.post("/api/web/fetch", json={"url": "https://example.com/page-1"})
    assert page.status_code == 200
    assert page.json()["title"] == "MOCK 页面 1"
    assert "正文" in page.json()["content"]

    bad = await client.post("/api/web/fetch", json={"url": "file:///etc/passwd"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_web_search_validation(client):
    bad = await client.post("/api/web/search", json={"query": "x" * 200, "count": 2})
    assert bad.status_code == 400
    bad = await client.post("/api/web/search", json={"query": "hello", "count": 0})
    assert bad.status_code == 400

@pytest.mark.asyncio
async def test_chat_uses_current_reading_file(client, mock_url: str):
    generated = await make_generated_kb(client, module_count=3)
    kb_id = generated["kb"]["id"]
    path = generated["path"]
    file_name = [item["name"] for item in path["files"] if item["name"].startswith("02")][0]
    response = await client.post(
        "/api/chat",
        json={
            "kbId": kb_id,
            "pathId": path["id"],
            "file": file_name,
            "message": "总结当前这一讲",
            "mode": "kb",
            "stream": False,
        },
        timeout=30,
    )
    assert response.status_code == 200, response.text
    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    chats = [item for item in stats["requests"] if item["path"].endswith("/chat/completions")]
    system = str(chats[-1]["body"]["messages"][0]["content"])
    assert "<当前阅读>" in system
    assert file_name in system
    assert "MOCK-MATERIAL-2" in system

@pytest.mark.asyncio
async def test_chat_read_tool_round_trip(client, mock_url: str):
    generated = await make_generated_kb(client)
    path = generated["path"]
    file_name = [item["name"] for item in path["files"] if item["name"].startswith("02")][0]
    httpx.post(mock_url + "/__mock/config", json={"tool_mode": "read"}, timeout=5)
    async with client.stream(
        "POST",
        "/api/chat",
        json={
            "kbId": generated["kb"]["id"],
            "pathId": path["id"],
            "file": file_name,
            "message": "读一下这一讲并总结",
            "mode": "kb",
            "enableTools": True,
        },
        timeout=30,
    ) as response:
        events = await read_sse(response)
    tools = iter_events(events, "tool")
    assert any(item.get("tool") == "read" and item.get("status") == "started" for item in tools)
    assert any(item.get("tool") == "read" and item.get("status") == "done" for item in tools)
    text = "".join(item["text"] for item in iter_events(events, "delta"))
    assert "MOCK::" in text
    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    chats = [item for item in stats["requests"] if item["path"].endswith("/chat/completions")]
    assert len(chats) >= 2
    assert any(item.get("role") == "tool" for item in chats[-1]["body"]["messages"])


@pytest.mark.asyncio
async def test_chat_edit_tool_modifies_document(client, mock_url: str):
    generated = await make_generated_kb(client)
    path = generated["path"]
    file_name = [item["name"] for item in path["files"] if item["name"].startswith("02")][0]
    httpx.post(mock_url + "/__mock/config", json={"tool_mode": "edit"}, timeout=5)
    async with client.stream(
        "POST",
        "/api/chat",
        json={
            "kbId": generated["kb"]["id"],
            "pathId": path["id"],
            "file": file_name,
            "message": "把占位内容改掉",
            "mode": "kb",
            "enableTools": True,
        },
        timeout=30,
    ) as response:
        events = await read_sse(response)
    assert any(item.get("action") == "edit" for item in iter_events(events, "doc"))
    detail = (
        await client.get(
            f"/api/kbs/{generated['kb']['id']}/file",
            params={"path": path["id"], "name": file_name},
        )
    ).json()
    assert "MOCK-EDITED" in detail["content"]


@pytest.mark.asyncio
async def test_chat_write_tool_creates_numbered_file(client, mock_url: str):
    generated = await make_generated_kb(client)
    path = generated["path"]
    httpx.post(mock_url + "/__mock/config", json={"tool_mode": "write"}, timeout=5)
    async with client.stream(
        "POST",
        "/api/chat",
        json={
            "kbId": generated["kb"]["id"],
            "pathId": path["id"],
            "message": "补一讲",
            "mode": "kb",
            "enableTools": True,
        },
        timeout=30,
    ) as response:
        events = await read_sse(response)
    docs = iter_events(events, "doc")
    assert any(item.get("action") == "write" and item.get("file") == "04-工具生成.md" for item in docs)
    detail = (
        await client.get(
            f"/api/kbs/{generated['kb']['id']}/file",
            params={"path": path["id"], "name": "04-工具生成.md"},
        )
    ).json()
    assert detail["content"].startswith("# 04 · ")


@pytest.mark.asyncio
async def test_chat_server_web_with_thinking(client, mock_url: str):
    await client.put("/api/settings", json={"api": {"model": "deepseek-chat"}})
    generated = await make_generated_kb(client)
    httpx.post(mock_url + "/__mock/config", json={"server_web": True}, timeout=5)
    async with client.stream(
        "POST",
        "/api/chat",
        json={
            "kbId": generated["kb"]["id"],
            "message": "查一下最新进展",
            "mode": "kb_web",
            "thinking": "low",
        },
        timeout=30,
    ) as response:
        events = await read_sse(response)
    assert iter_events(events, "done")[-1]["finishReason"] == "stop"
    source_events = iter_events(events, "sources")
    assert any(item["ref"] == "W1" for item in source_events[-1]["sources"])
    text = "".join(item["text"] for item in iter_events(events, "delta"))
    assert "MOCK-SERVER-WEB" in text
    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    assert stats["search_calls"] == 0
    chat = [item for item in stats["requests"] if item["path"].endswith("/chat/completions")][-1]["body"]
    assert chat["model"] == "deepseek-flash"
    assert chat["thinking"] == {"type": "enabled"}
    assert chat["reasoning_effort"] == "low"
    tool_types = [item.get("type") for item in chat["tools"]]
    assert "deepseek_web_search" in tool_types
    assert "deepseek_read_url" not in tool_types

@pytest.mark.asyncio
async def test_chat_highlight_tool(client, mock_url: str):
    generated = await make_generated_kb(client)
    path = generated["path"]
    file_name = [item["name"] for item in path["files"] if item["name"].startswith("02")][0]
    httpx.post(mock_url + "/__mock/config", json={"tool_mode": "highlight"}, timeout=5)
    async with client.stream(
        "POST",
        "/api/chat",
        json={
            "kbId": generated["kb"]["id"],
            "pathId": path["id"],
            "file": file_name,
            "message": "高亮这一讲的重点",
            "mode": "kb",
            "enableTools": True,
        },
        timeout=30,
    ) as response:
        events = await read_sse(response)
    highlights = iter_events(events, "highlight")
    assert highlights
    assert highlights[0]["quote"] == "MOCK-MATERIAL-2"
    assert highlights[0]["note"] == "测试高亮"

@pytest.mark.asyncio
async def test_chat_tool_loop_replays_reasoning_for_deepseek(client, mock_url: str):
    await client.put("/api/settings", json={"api": {"model": "deepseek-chat"}})
    generated = await make_generated_kb(client)
    path = generated["path"]
    file_name = [item["name"] for item in path["files"] if item["name"].startswith("02")][0]
    httpx.post(
        mock_url + "/__mock/config",
        json={"tool_mode": "read", "server_web": True},
        timeout=5,
    )
    async with client.stream(
        "POST",
        "/api/chat",
        json={
            "kbId": generated["kb"]["id"],
            "pathId": path["id"],
            "file": file_name,
            "message": "读一下并总结",
            "mode": "kb_web",
            "thinking": "low",
            "enableTools": True,
        },
        timeout=30,
    ) as response:
        events = await read_sse(response)
    assert iter_events(events, "done")[-1]["finishReason"] == "stop"
    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    chats = [item for item in stats["requests"] if item["path"].endswith("/chat/completions")]
    continuation = chats[-1]["body"]
    assert continuation["thinking"] == {"type": "enabled"}
    assert continuation["reasoning_effort"] == "low"
    assistant = [m for m in continuation["messages"] if m.get("role") == "assistant" and m.get("tool_calls")]
    assert assistant
    assert "MOCK-REASONING" in assistant[0].get("reasoning_content", "")


@pytest.mark.asyncio
async def test_chat_thinking_without_web_drops_client_tools(client, mock_url: str):
    await client.put("/api/settings", json={"api": {"model": "deepseek-chat"}})
    generated = await make_generated_kb(client)
    path = generated["path"]
    file_name = [item["name"] for item in path["files"] if item["name"].startswith("02")][0]
    async with client.stream(
        "POST",
        "/api/chat",
        json={
            "kbId": generated["kb"]["id"],
            "pathId": path["id"],
            "file": file_name,
            "message": "总结这一讲",
            "mode": "kb",
            "thinking": "high",
            "enableTools": True,
        },
        timeout=30,
    ) as response:
        events = await read_sse(response)
    assert iter_events(events, "done")[-1]["finishReason"] == "stop"
    assert iter_events(events, "meta")[0]["toolsSuppressed"] is True
    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    body = [item for item in stats["requests"] if item["path"].endswith("/chat/completions")][-1]["body"]
    assert not body.get("tools")
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"

@pytest.mark.asyncio
async def test_chat_web_with_edit_tools_drops_client_tools(client, mock_url: str):
    await client.put("/api/settings", json={"api": {"model": "deepseek-chat"}})
    generated = await make_generated_kb(client)
    path = generated["path"]
    file_name = [item["name"] for item in path["files"] if item["name"].startswith("02")][0]
    httpx.post(mock_url + "/__mock/config", json={"server_web": True}, timeout=5)
    async with client.stream(
        "POST",
        "/api/chat",
        json={
            "kbId": generated["kb"]["id"],
            "pathId": path["id"],
            "file": file_name,
            "message": "联网并总结",
            "mode": "kb_web",
            "thinking": "off",
            "enableTools": True,
        },
        timeout=30,
    ) as response:
        events = await read_sse(response)
    assert iter_events(events, "done")[-1]["finishReason"] == "stop"
    meta = iter_events(events, "meta")[0]
    assert meta["webMode"] == "server"
    assert meta["model"] == "deepseek-flash"
    assert meta["toolsSuppressed"] is True
    assert meta["suppressReason"] == "web"
    assert any(item["ref"] == "W1" for item in iter_events(events, "sources")[-1]["sources"])
    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    assert stats["search_calls"] == 0
    body = [item for item in stats["requests"] if item["path"].endswith("/chat/completions")][-1]["body"]
    types = [item.get("type") for item in body.get("tools", [])]
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert "deepseek_web_search" in types
    assert "function" not in types

