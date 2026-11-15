"""DeepSeek Search / Fetch proxy endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from .deps import get_state
from .errors import ApiError
from .webtools import WebToolError, webtools_from_config

router = APIRouter(prefix="/api/web", tags=["web"])


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    query: str
    count: int = 5
    ori_question: str | None = None
    need_content: bool = False


class FetchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str


def _raise(exc: WebToolError) -> None:
    status = 400 if exc.status_code == 400 else 502
    raise ApiError(
        status,
        exc.code,
        exc.message,
        {"retryable": exc.retryable, "upstreamStatus": exc.status_code},
    )


@router.post("/search")
async def search(payload: SearchBody, request: Request) -> dict[str, Any]:
    state = get_state(request)
    tools = webtools_from_config(state.config)
    try:
        results = await tools.search(
            payload.query,
            count=payload.count,
            ori_question=payload.ori_question,
            need_content=payload.need_content,
        )
    except WebToolError as exc:
        _raise(exc)
    return {"query": payload.query, "count": len(results), "results": results}


@router.post("/fetch")
async def fetch(payload: FetchBody, request: Request) -> dict[str, Any]:
    state = get_state(request)
    tools = webtools_from_config(state.config)
    try:
        page = await tools.fetch(payload.url)
    except WebToolError as exc:
        _raise(exc)
    return page
