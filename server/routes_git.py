"""Global Git management endpoints for the whole library root."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from .deps import get_state

router = APIRouter(prefix="/api/git", tags=["git"])


class GitInitRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    remoteUrl: str = ""
    branch: str = "main"
    migrateLegacy: bool = False


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
    remoteDir: str | None = None
    pushCommand: str | None = None
    pullCommand: str | None = None


class GitSyncRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    direction: Literal["sync", "pull", "push"] = "sync"
    strategy: Literal["abort", "local", "remote", "manual"] | None = None


def _invalidate(state: Any) -> None:
    try:
        state.index_cache.invalidate_dir(state.config.kb_root())
    except Exception:
        pass


@router.get("")
async def get_git(request: Request) -> dict[str, Any]:
    state = get_state(request)
    return await run_in_threadpool(state.git.status)


@router.post("/init")
async def init_git(payload: GitInitRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    result = await run_in_threadpool(
        state.git.init_repo,
        payload.remoteUrl,
        payload.branch,
        payload.migrateLegacy,
    )
    _invalidate(state)
    return {"git": result}


@router.put("/remote")
async def set_git_remote(payload: GitRemoteRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    result = await run_in_threadpool(state.git.set_remote, payload.url)
    return {"git": result}


@router.put("/settings")
async def update_git_settings(payload: GitSettingsRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    patch = payload.model_dump(exclude_unset=True)
    settings = await run_in_threadpool(state.git.update_settings, patch)
    status = await run_in_threadpool(state.git.status)
    return {"settings": settings, "git": status}


@router.post("/sync")
async def sync_git(payload: GitSyncRequest, request: Request) -> dict[str, Any]:
    state = get_state(request)
    result = await run_in_threadpool(
        state.git.sync,
        payload.direction,
        payload.strategy,
        "manual",
    )
    _invalidate(state)
    status = await run_in_threadpool(state.git.status)
    return {"result": result, "git": status}
