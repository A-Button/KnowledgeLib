"""Unit tests for DeepSeek Search/Fetch clients."""

from __future__ import annotations

import httpx
import pytest

from server.webtools import WebToolError, WebTools, join_path, validate_fetch_url


@pytest.mark.asyncio
async def test_search_and_fetch(mock_url: str):
    tools = WebTools(mock_url + "/v1", "test-key")
    results = await tools.search("DeepSeek", count=2, ori_question="DeepSeek 是什么")
    assert len(results) == 2
    assert results[0]["title"].startswith("MOCK")
    assert results[0]["rerank_score"] is not None

    page = await tools.fetch("https://example.com/page-2")
    assert page["title"] == "MOCK 页面 2"
    assert page["content"].startswith("# MOCK 页面 2")

    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    search = [item for item in stats["requests"] if item["path"].endswith("/tools/search")][-1]
    assert search["body"]["count"] == 2
    assert search["body"]["ori_question"] == "DeepSeek 是什么"
    assert search["auth_ok"] is True


def test_url_validation():
    assert validate_fetch_url("https://example.com/a")
    for value in ("file:///etc/passwd", "https://user:pass@example.com/a", "ftp://x", "", "https://"):
        with pytest.raises(WebToolError):
            validate_fetch_url(value)
    assert join_path("https://api.deepseek.com/v1", "/tools/search") == "https://api.deepseek.com/v1/tools/search"


@pytest.mark.asyncio
async def test_fetch_404(mock_url: str):
    tools = WebTools(mock_url + "/v1", "test-key")
    with pytest.raises(WebToolError) as info:
        await tools.fetch("https://example.com/unknown")
    assert info.value.status_code == 404


@pytest.mark.asyncio
async def test_auth_error(mock_url: str):
    tools = WebTools(mock_url + "/v1", "bad-key")
    with pytest.raises(WebToolError) as info:
        await tools.search("hello", count=1)
    assert info.value.status_code == 401
    assert "bad-key" not in info.value.message


@pytest.mark.asyncio
async def test_retry_on_429(mock_url: str):
    httpx.post(mock_url + "/__mock/config", json={"search_status": 429}, timeout=5)
    tools = WebTools(mock_url + "/v1", "test-key")
    with pytest.raises(WebToolError):
        await tools.search("hello", count=1)
    stats = httpx.get(mock_url + "/__mock/stats", timeout=5).json()
    assert stats["search_calls"] == 2
