"""Git management endpoints for individual knowledge bases."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from . import library
from .deps import get_state

router = APIRouter(prefix="/api/kbs", tags=["git"])


class GitInitRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    remoteUrl: str = ""
    branch: str = "main"


class GitRemoteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str


class GitSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    autoSync: bool | None = None
    autoCommit: bool | None = None
    intervalMinutes: int | None = None
    autoDirection: Literal["sync", "pull", "push"] | None = None
    strategy: Literal["abort", "local", "remote", "manual"] | None = None
    pushCommand: str | None = None
    pullCommand: str | None = None


class GitSyncRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    direction: Literal["sync", "pull", "push"] = "sync"
    strategy: Literal["abort", "local", "remote", "manual"] | None = None


def _kb_dir(state: Any, kb_id: str):
    return library.find_kb(state.config.kb_root(), kb_id)


def _invalidate(state: Any, kb_dir) -> None:
    try:
        state.index_cache.invalidate_dir(kb_dir)
    except Exception:
        pass


@router.get("/{kb_id}/git")
async def get_git(kb_id: str, request: Request) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    return await run_in_threadpool(state.git.status, kb_dir, kb_id)


@router.post("/{kb_id}/git/init")
async def init_git(kb_id: str, payload: GitInitRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    result = await run_in_threadpool(
        state.git.init_repo,
        kb_dir,
        kb_id,
        payload.remoteUrl,
        payload.branch,
    )
    _invalidate(state, kb_dir)
    return {"git": result}


@router.put("/{kb_id}/git/remote")
async def set_git_remote(kb_id: str, payload: GitRemoteRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    result = await run_in_threadpool(state.git.set_remote, kb_dir, kb_id, payload.url)
    return {"git": result}


@router.put("/{kb_id}/git/settings")
async def update_git_settings(kb_id: str, payload: GitSettingsRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    patch = payload.model_dump(exclude_unset=True)
    settings = await run_in_threadpool(state.git.update_settings, kb_id, patch)
    status = await run_in_threadpool(state.git.status, kb_dir, kb_id)
    return {"settings": settings, "git": status}


@router.post("/{kb_id}/git/sync")
async def sync_git(kb_id: str, payload: GitSyncRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    kb_dir = _kb_dir(state, kb_id)
    result = await run_in_threadpool(
        state.git.sync,
        kb_dir,
        kb_id,
        payload.direction,
        payload.strategy,
        "manual",
    )
    _invalidate(state, kb_dir)
    status = await run_in_threadpool(state.git.status, kb_dir, kb_id)
    return {"result": result, "git": status}
