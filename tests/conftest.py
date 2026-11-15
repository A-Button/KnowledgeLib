"""Shared pytest fixtures: mock upstream and ASGI app client."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import create_app
from tests.mock_upstream import create_mock_app
from tests.support import configure_payload, reset_mock, serve


@pytest.fixture(scope="session")
def mock_server():
    with serve(create_mock_app()) as base_url:
        yield base_url


@pytest.fixture
def mock_url(mock_server: str) -> str:
    reset_mock(mock_server)
    return mock_server


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest_asyncio.fixture
async def client(data_dir: Path, mock_url: str):
    app = create_app(data_dir, {"KL_QUIET": "1"}, config_dir=data_dir.parent / "config")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.put("/api/settings", json=configure_payload(mock_url))
        assert response.status_code == 200, response.text
        http.app = app
        yield http
