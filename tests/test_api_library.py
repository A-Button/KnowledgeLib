"""API tests for knowledge-base and learning-path renaming."""

from __future__ import annotations

import pytest

from tests.test_api_chat import make_generated_kb


@pytest.mark.asyncio
async def test_rename_kb_and_path_api(client):
    generated = await make_generated_kb(client)
    kb_id = generated["kb"]["id"]
    path = generated["path"]

    renamed = await client.put(f"/api/kbs/{kb_id}", json={"name": "新文库名称"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "新文库名称"
    assert renamed.json()["dirName"] == generated["kb"]["dirName"]

    listing = (await client.get("/api/kbs")).json()["kbs"]
    assert any(item["id"] == kb_id and item["name"] == "新文库名称" for item in listing)

    renamed_path = await client.put(
        f"/api/kbs/{kb_id}/paths/{path['id']}",
        json={"name": "新学习路径"},
    )
    assert renamed_path.status_code == 200, renamed_path.text
    assert renamed_path.json()["name"] == "新学习路径"
    assert renamed_path.json()["dirName"] == path["dirName"]

    detail = (await client.get(f"/api/kbs/{kb_id}/paths/{path['id']}")).json()
    assert detail["name"] == "新学习路径"

    for bad in ("", "   ", "名" * 61):
        response = await client.put(f"/api/kbs/{kb_id}", json={"name": bad})
        assert response.status_code == 400
        response = await client.put(
            f"/api/kbs/{kb_id}/paths/{path['id']}",
            json={"name": bad},
        )
        assert response.status_code == 400
