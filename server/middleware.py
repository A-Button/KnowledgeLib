"""Pure ASGI middleware: security headers, access log and request size guard."""

from __future__ import annotations

import json
import time
from typing import Any

CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "worker-src 'self' blob:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)

MAX_BODY = 8 * 1024 * 1024


class Middleware:
    def __init__(self, app: Any, enabled_log: bool = True) -> None:
        self.app = app
        self.enabled_log = enabled_log

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {name.lower(): value for name, value in scope.get("headers") or []}
        path = scope.get("path") or ""

        if path.startswith("/api/"):
            length = headers.get(b"content-length")
            if length:
                try:
                    size = int(length)
                except ValueError:
                    size = 0
                if size > MAX_BODY:
                    body = json.dumps(
                        {"error": {"code": "too_large", "message": "请求体超过 8 MiB 限制"}},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 413,
                            "headers": [
                                (b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode()),
                                (b"x-content-type-options", b"nosniff"),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return

        started = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                status_holder["status"] = int(message.get("status") or 0)
                raw = list(message.get("headers") or [])
                names = {name.lower() for name, _ in raw}
                additions = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", CSP.encode()),
                    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
                ]
                for name, value in additions:
                    if name not in names:
                        raw.append((name, value))
                message = dict(message)
                message["headers"] = raw
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if self.enabled_log:
                elapsed = (time.perf_counter() - started) * 1000
                method = scope.get("method", "?")
                status = status_holder["status"] or 0
                query = scope.get("query_string") or b""
                suffix = ("?" + query.decode("latin-1")) if query else ""
                print(f"[{time.strftime('%H:%M:%S')}] {method} {path}{suffix} {status} {elapsed:.0f}ms")
