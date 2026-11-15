"""Test helpers: uvicorn threads, SSE parsing and app configuration."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Any, AsyncIterator, Iterator

import httpx
import uvicorn


@contextmanager
def serve(app: Any) -> Iterator[str]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("uvicorn server did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(8)


def reset_mock(base_url: str) -> None:
    httpx.post(base_url + "/__mock/reset", timeout=5)


def configure_payload(mock_url: str) -> dict[str, Any]:
    return {
        "api": {"baseUrl": mock_url, "apiKey": "test-key", "model": "mock-model"},
        "web": {
            "baseUrl": mock_url + "/v1",
            "apiKey": "test-key",
            "searchCount": 3,
            "fetchTopN": 2,
        },
        "kb": {},
    }


async def read_sse(response: httpx.Response) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    name = ""
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif not line.strip():
            if name and data_lines:
                payload = "".join(data_lines)
                try:
                    events.append((name, json.loads(payload)))
                except json.JSONDecodeError:
                    events.append((name, payload))
            name = ""
            data_lines = []
    if name and data_lines:
        payload = "".join(data_lines)
        try:
            events.append((name, json.loads(payload)))
        except json.JSONDecodeError:
            events.append((name, payload))
    return events


def parse_sse_text(text: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    name = ""
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif not line.strip():
            if name and data_lines:
                try:
                    events.append((name, json.loads("".join(data_lines))))
                except json.JSONDecodeError:
                    events.append((name, "".join(data_lines)))
            name = ""
            data_lines = []
    if name and data_lines:
        try:
            events.append((name, json.loads("".join(data_lines))))
        except json.JSONDecodeError:
            events.append((name, "".join(data_lines)))
    return events


async def collect_stream(response: httpx.Response) -> list[tuple[str, Any]]:
    return await read_sse(response)


def iter_events(events: list[tuple[str, Any]], name: str) -> list[Any]:
    return [payload for event, payload in events if event == name]
