"""Security headers that the browser UI depends on."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_csp_allows_liquidgl_blob_worker(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "worker-src 'self' blob:" in csp
    assert "script-src 'self'" in csp
