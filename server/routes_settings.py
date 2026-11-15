"""Settings and connection-test endpoints."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from .deps import get_state
from .llm import LLMError, client_from_config, ensure_api_ready
from .webtools import WebToolError, webtools_from_config

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read_settings(request: Request) -> dict[str, Any]:
    return get_state(request).config.public()


@router.put("")
def update_settings(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    return get_state(request).config.update(payload)


@router.post("/test")
async def test_chat(request: Request) -> dict[str, Any]:
    state = get_state(request)
    ensure_api_ready(state.config)
    client = client_from_config(state.config)
    started = time.monotonic()
    try:
        reply = await client.complete(
            [{"role": "user", "content": "请只回复 pong 两个字母。"}],
            retries=0,
            max_tokens=16,
        )
    except LLMError as exc:
        return {
            "ok": False,
            "code": exc.code,
            "message": exc.message,
            "status": exc.status_code,
            "latencyMs": int((time.monotonic() - started) * 1000),
        }
    return {
        "ok": True,
        "model": state.config.model(),
        "baseUrl": state.config.base_url(),
        "latencyMs": int((time.monotonic() - started) * 1000),
        "reply": (reply or "").strip()[:200],
    }


@router.post("/test-web")
async def test_web(request: Request) -> dict[str, Any]:
    state = get_state(request)
    tools = webtools_from_config(state.config)
    checks: list[dict[str, Any]] = []

    started = time.monotonic()
    try:
        results = await tools.search("DeepSeek", count=1)
        checks.append(
            {
                "name": "search",
                "ok": True,
                "latencyMs": int((time.monotonic() - started) * 1000),
                "detail": f"返回 {len(results)} 条结果",
            }
        )
    except WebToolError as exc:
        checks.append(
            {
                "name": "search",
                "ok": False,
                "latencyMs": int((time.monotonic() - started) * 1000),
                "code": exc.code,
                "message": exc.message,
            }
        )

    started = time.monotonic()
    try:
        page = await tools.fetch("https://api-docs.deepseek.com/zh-cn/")
        checks.append(
            {
                "name": "fetch",
                "ok": True,
                "latencyMs": int((time.monotonic() - started) * 1000),
                "detail": f"{page.get('title') or '无标题'}（{len(page.get('content') or '')} 字符）",
            }
        )
    except WebToolError as exc:
        checks.append(
            {
                "name": "fetch",
                "ok": False,
                "latencyMs": int((time.monotonic() - started) * 1000),
                "code": exc.code,
                "message": exc.message,
            }
        )
    return {"ok": all(item["ok"] for item in checks), "checks": checks}
