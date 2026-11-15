"""FastAPI application factory and global state."""

from __future__ import annotations

import asyncio
import threading
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import ConfigStore
from .errors import ApiError, envelope
from .git_sync import GitService
from .indexer import IndexCache
from .middleware import Middleware
from .routes_chat import router as chat_router
from .routes_git import router as git_router
from .routes_library import router as library_router
from .routes_settings import router as settings_router
from .routes_web import router as web_router

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class AppState:
    def __init__(
        self,
        data_dir: Path,
        environ: Mapping[str, str] | None = None,
        config_dir: Path | None = None,
    ) -> None:
        from .config import default_config_dir

        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir = Path(config_dir).expanduser().resolve() if config_dir else default_config_dir(environ)
        self.config = ConfigStore(self.config_dir, self.data_dir, environ)
        self.git = GitService(self.config, environ)
        self.index_cache = IndexCache()
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = threading.Lock()

    def generation_lock(self, directory: Path) -> asyncio.Lock:
        key = str(Path(directory).resolve())
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock


def _error_response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=envelope(code, message, details))


def create_app(
    data_dir: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    config_dir: str | Path | None = None,
) -> FastAPI:
    env = dict(environ or {})
    if data_dir is None:
        data_dir = env.get("KL_DATA_DIR") or "data"
    data_path = Path(data_dir)
    if not data_path.is_absolute():
        data_path = (Path.cwd() / data_path).resolve()
    config_path = Path(config_dir) if config_dir is not None else None
    state = AppState(data_path, environ, config_path)
    (WEB_DIR / "assets").mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        state.git.start_scheduler()
        try:
            yield
        finally:
            state.git.stop_scheduler()

    app = FastAPI(
        title="KnowledgeLib",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.kl = state
    quiet = bool(env.get("KL_QUIET"))
    app.add_middleware(Middleware, enabled_log=not quiet)

    app.include_router(settings_router)
    app.include_router(git_router)
    app.include_router(library_router)
    app.include_router(web_router)
    app.include_router(chat_router)

    app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "dataDir": str(state.data_dir),
            "kbRoot": str(state.config.kb_root()),
        }

    @app.get("/")
    def index() -> Any:
        target = WEB_DIR / "index.html"
        if not target.is_file():
            return _error_response(500, "internal_error", "前端资源缺失，请检查 web/index.html")
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.svg")
    def favicon() -> Any:
        target = WEB_DIR / "assets" / "favicon.svg"
        if target.is_file():
            return FileResponse(target)
        return JSONResponse(status_code=404, content=envelope("not_found", "图标不存在"))

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.payload())

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = ".".join(str(item) for item in first.get("loc", []) if item not in ("body", "query"))
        message = str(first.get("msg") or "请求参数不合法")
        if location:
            message = f"{location}: {message}"
        return _error_response(400, "invalid_request", message, {"errors": errors[:5]})

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error_response(404, "not_found", "资源不存在")
        return _error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        traceback.print_exc()
        return _error_response(500, "internal_error", "服务器内部错误，请查看终端日志")

    return app


def app_factory() -> FastAPI:
    return create_app()
