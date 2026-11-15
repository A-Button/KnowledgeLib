"""API tests for knowledge-base Git management."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def make_remote(path: Path) -> Path:
    git("init", "--bare", "--initial-branch=main", str(path))
    return path


@pytest.mark.asyncio
async def test_git_api_lifecycle(client, tmp_path: Path):
    created = await client.post("/api/kbs", json={"name": "Git API 库"})
    assert created.status_code == 200, created.text
    kb = created.json()
    remote = make_remote(tmp_path / "remote.git")

    initial = (await client.get(f"/api/kbs/{kb['id']}/git")).json()
    assert initial["available"] is True
    assert initial["managed"] is False
    assert initial["settings"]["autoSync"] is False

    initialized = await client.post(
        f"/api/kbs/{kb['id']}/git/init",
        json={"remoteUrl": str(remote), "branch": "main"},
    )
    assert initialized.status_code == 200, initialized.text
    status = initialized.json()["git"]
    assert status["managed"] is True
    assert status["branch"] == "main"
    assert status["remoteUrl"] == str(remote)

    settings = await client.put(
        f"/api/kbs/{kb['id']}/git/settings",
        json={
            "autoSync": True,
            "intervalMinutes": 7,
            "autoDirection": "sync",
            "strategy": "local",
            "autoCommit": True,
            "pullCommand": "git fetch origin --prune",
            "pushCommand": "git push origin {branch}",
        },
    )
    assert settings.status_code == 200, settings.text
    assert settings.json()["settings"]["intervalMinutes"] == 7
    assert settings.json()["git"]["settings"]["strategy"] == "local"

    kb_dir = Path(kb["absPath"])
    (kb_dir / "01-api.md").write_text("# 01 · API\n\nAPI 内容\n", encoding="utf-8")
    pushed = await client.post(f"/api/kbs/{kb['id']}/git/sync", json={"direction": "sync"})
    assert pushed.status_code == 200, pushed.text
    result = pushed.json()["result"]
    assert result["ok"] is True
    assert "已用自定义命令推送" in result["message"] or "已推送" in result["message"]
    assert pushed.json()["git"]["dirty"] is False

    clone = tmp_path / "clone"
    git("clone", str(remote), str(clone))
    assert (clone / "01-api.md").is_file()

    detail = await client.get(f"/api/kbs/{kb['id']}")
    assert detail.status_code == 200
    assert detail.json()["name"] == "Git API 库"

    bad = await client.put(
        f"/api/kbs/{kb['id']}/git/settings",
        json={"pushCommand": "echo nope"},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_request"

    missing = await client.get("/api/kbs/not-a-real-kb/git")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_git_api_remote_and_errors(client, tmp_path: Path):
    created = await client.post("/api/kbs", json={"name": "Git 错误库"})
    kb = created.json()
    remote_a = make_remote(tmp_path / "a.git")
    remote_b = make_remote(tmp_path / "b.git")

    no_remote = await client.post(f"/api/kbs/{kb['id']}/git/sync", json={"direction": "push"})
    assert no_remote.status_code == 409
    assert no_remote.json()["error"]["code"] == "git_not_initialized"

    await client.post(f"/api/kbs/{kb['id']}/git/init", json={"remoteUrl": str(remote_a)})
    changed = await client.put(
        f"/api/kbs/{kb['id']}/git/remote",
        json={"url": str(remote_b)},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["git"]["remoteUrl"] == str(remote_b)

    bad_remote = await client.put(
        f"/api/kbs/{kb['id']}/git/remote",
        json={"url": "ext::sh -c id"},
    )
    assert bad_remote.status_code == 400
