"""API tests for whole-library Git management."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=KnowledgeLib Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
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
async def test_git_api_whole_library(client, tmp_path: Path):
    first = await client.post("/api/kbs", json={"name": "API 第一库"})
    second = await client.post("/api/kbs", json={"name": "API 第二库"})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_kb = first.json()
    second_kb = second.json()
    (Path(first_kb["absPath"]) / "01-api.md").write_text("# 01 · API\n\n第一库 API\n", encoding="utf-8")
    (Path(second_kb["absPath"]) / "01-api2.md").write_text("# 01 · API2\n\n第二库 API\n", encoding="utf-8")
    remote = make_remote(tmp_path / "remote.git")

    initial = (await client.get("/api/git")).json()
    assert initial["available"] is True
    assert initial["managed"] is False
    assert initial["kbCount"] == 2
    assert initial["root"] == (await client.get("/api/kbs")).json()["root"]

    initialized = await client.post(
        "/api/git/init",
        json={"remoteUrl": str(remote), "branch": "main"},
    )
    assert initialized.status_code == 200, initialized.text
    status = initialized.json()["git"]
    assert status["managed"] is True
    assert status["kbCount"] == 2
    assert status["remoteUrl"] == str(remote)

    settings = await client.put(
        "/api/git/settings",
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
    assert settings.json()["settings"]["strategy"] == "local"

    pushed = await client.post("/api/git/sync", json={"direction": "sync"})
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["result"]["ok"] is True
    assert pushed.json()["git"]["dirty"] is False

    clone = tmp_path / "clone"
    git("clone", str(remote), str(clone))
    assert (clone / first_kb["dirName"] / "01-api.md").is_file()
    assert (clone / second_kb["dirName"] / "01-api2.md").is_file()

    public = (await client.get("/api/settings")).json()
    assert public["git"]["strategy"] == "local"
    assert public["git"]["autoSync"] is True

    bad = await client.put("/api/git/settings", json={"pushCommand": "echo nope"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_request"

    gh_settings = await client.put(
        "/api/git/settings",
        json={"pullCommand": "gh repo sync A-Button/KnowledgeLib"},
    )
    assert gh_settings.status_code == 200, gh_settings.text
    assert gh_settings.json()["settings"]["pullCommand"].startswith("gh ")


@pytest.mark.asyncio
async def test_git_api_errors_and_legacy_migration(client, tmp_path: Path):
    created = await client.post("/api/kbs", json={"name": "旧仓库库"})
    kb = created.json()
    kb_dir = Path(kb["absPath"])
    (kb_dir / ".git").mkdir()
    remote = make_remote(tmp_path / "remote.git")

    no_remote = await client.post("/api/git/init", json={})
    assert no_remote.status_code == 409
    assert no_remote.json()["error"]["code"] == "git_legacy_repos"
    assert "旧仓库库" in no_remote.json()["error"]["details"]["repos"]

    migrated = await client.post(
        "/api/git/init",
        json={"remoteUrl": str(remote), "migrateLegacy": True},
    )
    assert migrated.status_code == 200, migrated.text
    assert migrated.json()["git"]["legacyRepos"] == []
    assert not (kb_dir / ".git").exists()
    assert any((tmp_path / "data" / "trash" / "git-repos").glob("*/旧仓库库/.git"))

    changed = await client.put("/api/git/remote", json={"url": str(remote)})
    assert changed.status_code == 200, changed.text
    assert changed.json()["git"]["remoteUrl"] == str(remote)

    bad_remote = await client.put("/api/git/remote", json={"url": "ext::sh -c id"})
    assert bad_remote.status_code == 400

    old_route = await client.get(f"/api/kbs/{kb['id']}/git")
    assert old_route.status_code == 404


@pytest.mark.asyncio
async def test_git_api_remote_dir_setting(client):
    created = await client.post("/api/kbs", json={"name": "目录 API 库"})
    assert created.status_code == 200, created.text

    saved = await client.put(
        "/api/git/settings",
        json={"remoteDir": "文档/KnowledgeLib"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["remoteDir"] == "文档/KnowledgeLib"

    status = (await client.get("/api/git")).json()
    assert status["folderMode"] is True
    assert status["remoteDir"] == "文档/KnowledgeLib"

    cleared = await client.put("/api/git/settings", json={"remoteDir": ""})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["settings"]["remoteDir"] == ""
    assert (await client.get("/api/git")).json()["folderMode"] is False

    for bad in ("../escape", "/absolute", "C:/windows", ".git"):
        rejected = await client.put("/api/git/settings", json={"remoteDir": bad})
        assert rejected.status_code == 400, bad
        assert rejected.json()["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_git_api_rejects_unsafe_remote_dirs(client):
    for bad in ("CON", "../x"):
        rejected = await client.put("/api/git/settings", json={"remoteDir": bad})
        assert rejected.status_code == 400, bad
        assert rejected.json()["error"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_git_api_exposes_mode_switch_warning(client):
    payload = (await client.get("/api/git")).json()
    assert "modeSwitchWarning" in payload
    assert payload["modeSwitchWarning"] == ""

    client.app.state.kl.config.patch_git_state({"remoteDir": "../escape"})
    invalid = (await client.get("/api/git")).json()
    assert invalid["remoteDirError"]
    assert invalid["modeSwitchWarning"] == ""
    blocked = await client.post("/api/git/sync", json={"direction": "pull"})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "git_settings_invalid"


@pytest.mark.asyncio
async def test_git_api_mode_switch_warning_positive(client, tmp_path: Path):
    created = await client.post("/api/kbs", json={"name": "切换警告 API 库"})
    assert created.status_code == 200, created.text
    remote = make_remote(tmp_path / "remote.git")
    initialized = await client.post(
        "/api/git/init", json={"remoteUrl": str(remote), "branch": "main"}
    )
    assert initialized.status_code == 200, initialized.text

    # Folder mode was used before; the marker is internal state that survives
    # clearing refs/kl (the API request model deliberately does not expose it).
    config = client.app.state.kl.config
    config.patch_git_state({"folderSyncUsed": True})

    payload = (await client.get("/api/git")).json()
    assert payload["remoteDir"] == ""
    assert payload["modeSwitchWarning"], "folderSyncUsed 标记应产生 modeSwitchWarning"

    blocked = await client.post("/api/git/sync", json={"direction": "pull"})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "git_mode_switch"

    ignored = await client.put("/api/git/settings", json={"folderSyncUsed": False})
    assert ignored.status_code == 200, ignored.text
    assert (await client.get("/api/git")).json()["modeSwitchWarning"]
