"""Knowledge base, learning path and generation endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import indexer, learning, library
from .deps import SSE_HEADERS, get_state, sse_event
from .errors import ApiError
from .llm import ensure_api_ready

router = APIRouter(prefix="/api/kbs", tags=["library"])

GOALS = {"system", "quickstart", "project", "interview", "basics"}


class KbCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    domain: str
    weeks: int
    hoursPerWeek: int
    level: Literal["zero", "beginner", "intermediate", "advanced"] = "zero"
    goals: list[str] = Field(default_factory=list)
    preferences: str = ""
    moduleCount: int | None = None
    name: str = ""
    webEnabled: bool = False
    thinking: Literal["off", "low", "high", "max"] = "off"


def _build_spec(req: GenerateRequest, deepseek: bool) -> dict[str, Any]:
    domain = (req.domain or "").strip()
    if len(domain) < 2 or len(domain) > 80:
        raise ApiError(400, "invalid_request", "学习领域需要 2..80 个字符")
    if req.weeks < 1 or req.weeks > 52:
        raise ApiError(400, "invalid_request", "周期需要在 1..52 周之间")
    if req.hoursPerWeek < 1 or req.hoursPerWeek > 40:
        raise ApiError(400, "invalid_request", "每周投入需要在 1..40 小时之间")
    goals: list[str] = []
    for goal in req.goals:
        if goal not in GOALS:
            raise ApiError(400, "invalid_request", "目标标签不合法")
        if goal not in goals:
            goals.append(goal)
    if len(goals) > 5:
        raise ApiError(400, "invalid_request", "目标标签最多 5 个")
    preferences = (req.preferences or "").strip()
    if len(preferences) > 500:
        raise ApiError(400, "invalid_request", "偏好要求不能超过 500 字符")
    total = req.weeks * req.hoursPerWeek
    default_count = max(4, min(12, int(round(total / 5))))
    module_count = req.moduleCount or default_count
    if module_count < 3 or module_count > 20:
        raise ApiError(400, "invalid_request", "模块数需要在 3..20 之间")
    name = (req.name or "").strip()
    if len(name) > 60:
        raise ApiError(400, "invalid_request", "路径名称不能超过 60 字符")
    if req.webEnabled and not deepseek:
        raise ApiError(400, "invalid_request", "联网生成目前仅支持 DeepSeek 模型（deepseek-flash）")
    if req.thinking not in ("off", "low", "high", "max"):
        raise ApiError(400, "invalid_request", "思考强度不合法")
    return {
        "domain": domain,
        "weeks": req.weeks,
        "hoursPerWeek": req.hoursPerWeek,
        "level": req.level,
        "goals": goals,
        "preferences": preferences,
        "moduleCount": module_count,
        "webEnabled": bool(req.webEnabled),
        "thinking": req.thinking,
    }


def _kb_dir(state: Any, kb_id: str):
    return library.find_kb(state.config.kb_root(), kb_id)


def _sse_stream(state: Any, ref: library.PathRef, resume: bool) -> Any:
    async def stream():
        lock = state.generation_lock(ref.directory)
        async with lock:
            async for event_name, data in learning.generation_events(state, ref, resume=resume):
                yield sse_event(event_name, data)

    return stream()


@router.get("")
def list_kbs(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return {"root": str(state.config.kb_root()), "kbs": library.scan_kbs(state.config.kb_root())}


@router.post("")
def create_kb(payload: KbCreate, request: Request) -> dict[str, Any]:
    state = get_state(request)
    root = state.config.kb_root()
    meta = library.create_kb(root, payload.name, payload.description)
    return library.kb_detail(root, meta["id"])


@router.get("/{kb_id}")
def get_kb(kb_id: str, request: Request) -> dict[str, Any]:
    state = get_state(request)
    return library.kb_detail(state.config.kb_root(), kb_id)


@router.put("/{kb_id}")
def rename_kb(kb_id: str, payload: RenameRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    root = state.config.kb_root()
    library.rename_kb(root, kb_id, payload.name)
    return library.kb_detail(root, kb_id)


@router.delete("/{kb_id}")
def delete_kb(kb_id: str, request: Request) -> dict[str, Any]:
    state = get_state(request)
    detail = library.kb_detail(state.config.kb_root(), kb_id)
    if any(item.get("status") in ("planning", "generating") for item in detail["paths"]):
        raise ApiError(409, "conflict", "知识库内有路径正在生成，暂不能删除")
    return library.delete_kb(state.config.kb_root(), state.data_dir, kb_id)


@router.post("/{kb_id}/reindex")
def reindex(kb_id: str, request: Request) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    state.index_cache.invalidate_dir(kb_dir)
    files = sum(len(ref.files) for ref in library.iter_paths(kb_dir))
    return {"ok": True, "files": files}


@router.get("/{kb_id}/search")
def search_kb(
    kb_id: str,
    request: Request,
    q: str = Query(default=""),
    topK: int = Query(default=0),
) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    query = (q or "").strip()
    if not query:
        raise ApiError(400, "invalid_request", "检索词不能为空")
    top_k = topK or state.config.top_k()
    if top_k < 1 or top_k > 20:
        raise ApiError(400, "invalid_request", "topK 需要在 1..20 之间")
    hits = indexer.search(kb_dir, query, top_k, state.index_cache)
    return {"query": query, "hits": hits}


@router.post("/{kb_id}/paths")
def generate_path(kb_id: str, payload: GenerateRequest, request: Request) -> StreamingResponse:
    state = get_state(request)
    ensure_api_ready(state.config)
    deepseek = state.config.is_deepseek()
    spec = _build_spec(payload, deepseek)
    kb_dir = _kb_dir(state, kb_id)
    name = payload.name.strip()
    model = "deepseek-flash" if spec["webEnabled"] else state.config.model()
    ref = library.create_generated_path(
        kb_dir,
        spec,
        name or f"{spec['domain']}学习路径",
        model,
    )
    ref.meta["name_locked"] = bool(name)
    library.write_path_meta(ref.directory, ref.meta)
    if state.generation_lock(ref.directory).locked():
        raise ApiError(409, "conflict", "该路径正在生成")
    return StreamingResponse(
        _sse_stream(state, ref, resume=False),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{kb_id}/paths/{path_id}/resume")
def resume_path(kb_id: str, path_id: str, request: Request) -> StreamingResponse:
    state = get_state(request)
    ensure_api_ready(state.config)
    kb_dir = _kb_dir(state, kb_id)
    ref = library.find_path(kb_dir, path_id)
    if ref.status == "complete":
        raise ApiError(409, "conflict", "该学习路径已生成完成")
    if ref.status == "external":
        raise ApiError(409, "conflict", "外部目录没有可续生成的规划")
    if state.generation_lock(ref.directory).locked():
        raise ApiError(409, "conflict", "该路径正在生成")
    return StreamingResponse(
        _sse_stream(state, ref, resume=True),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/{kb_id}/paths/{path_id}")
def get_path(kb_id: str, path_id: str, request: Request) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    ref = library.find_path(kb_dir, path_id)
    return library.path_public(ref)


@router.put("/{kb_id}/paths/{path_id}")
def rename_path(kb_id: str, path_id: str, payload: RenameRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    ref = library.rename_path(kb_dir, path_id, payload.name)
    return library.path_public(ref)


@router.delete("/{kb_id}/paths/{path_id}")
def delete_path(kb_id: str, path_id: str, request: Request) -> dict[str, Any]:
    state = get_state(request)
    return library.delete_path(state.config.kb_root(), state.data_dir, kb_id, path_id)


@router.get("/{kb_id}/file")
def read_file(
    kb_id: str,
    request: Request,
    path: str = Query(default=""),
    name: str = Query(default=""),
) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    return library.read_md(kb_dir, path, name)
