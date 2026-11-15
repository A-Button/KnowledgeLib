"""Accessors for application state and SSE helpers."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from .errors import ApiError

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def get_state(request: Request) -> Any:
    state = getattr(request.app.state, "kl", None)
    if state is None:
        raise ApiError(500, "internal_error", "应用状态未初始化")
    return state


def sse_event(name: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n"
