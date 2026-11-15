"""Offline tests for whole-library Git synchronization."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import library
from server.config import ConfigStore
from server.errors import ApiError
from server.git_sync import GitService, validate_command, validate_remote_url

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


def make_library(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "library"
    root.mkdir()
    first = library.create_kb(root, "第一文库")
    first_dir = root / first["slug"]
    (first_dir / "01-基础.md").write_text("# 01 · 基础\n\n第一库内容\n", encoding="utf-8")
    second = library.create_kb(root, "第二文库")
    second_dir = root / second["slug"]
    (second_dir / "01-进阶.md").write_text("# 01 · 进阶\n\n第二库内容\n", encoding="utf-8")
    return root, make_remote(tmp_path / "remote.git")


def make_service(tmp_path: Path, root: Path) -> GitService:
    config = ConfigStore(tmp_path / "config", tmp_path / "data")
    config.update({"kb": {"root": str(root)}})
    return GitService(config)


def clone_remote(remote: Path, target: Path) -> Path:
    git("clone", str(remote), str(target))
    return target


def remote_edit(remote: Path, tmp_path: Path, filename: str, text: str) -> None:
    clone = tmp_path / "remote-clone"
    if clone.exists():
        shutil.rmtree(clone)
    clone_remote(remote, clone)
    target = next(clone.glob(f"*/{filename}"))
    target.write_text(target.read_text(encoding="utf-8").split("\n\n")[0] + "\n\n" + text + "\n", encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "-m", "remote edit", cwd=clone)
    git("push", "origin", "main", cwd=clone)


def test_init_push_and_status(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    before = service.status()
    assert before["available"] is True
    assert before["managed"] is False
    assert before["kbCount"] == 2

    initialized = service.init_repo(str(remote), "main")
    assert initialized["managed"] is True
    assert initialized["root"] == str(root)
    assert initialized["kbCount"] == 2
    assert initialized["remoteUrl"] == str(remote)
    assert initialized["lastCommit"]["subject"] == "Initialize KnowledgeLib library"
    assert (root / ".git").is_dir()

    result = service.sync("push")
    assert result["ok"] is True
    clone = clone_remote(remote, tmp_path / "verify")
    assert (clone / "第一文库" / "01-基础.md").is_file()
    assert (clone / "第二文库" / "01-进阶.md").is_file()

    status = service.status()
    assert status["dirty"] is False
    assert status["upstream"] == "origin/main"
    assert status["ahead"] == 0
    assert status["behind"] == 0
    assert status["lastStatus"] == "ok"


def test_local_change_auto_commit_and_push(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    target = next((root / "第二文库").glob("*.md"))
    target.write_text("# 01 · 进阶\n\n新内容\n", encoding="utf-8")
    assert service.status()["dirty"] is True
    result = service.sync("push")
    assert result["commitCreated"] is True
    clone = clone_remote(remote, tmp_path / "verify")
    assert "新内容" in next(clone.glob("*/01-进阶.md")).read_text(encoding="utf-8")


def test_pull_prefers_local(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.sync("push")
    remote_edit(remote, tmp_path, "01-基础.md", "远端内容")
    local = root / "第一文库" / "01-基础.md"
    local.write_text("# 01 · 基础\n\n本地内容二\n", encoding="utf-8")

    result = service.sync("pull", strategy="local")
    assert result["pulled"] is True
    assert "本地内容二" in local.read_text(encoding="utf-8")
    assert service.status()["conflicts"] == []
    assert service.status()["dirty"] is False


def test_pull_prefers_remote(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.sync("push")
    remote_edit(remote, tmp_path, "01-基础.md", "远端内容")
    local = root / "第一文库" / "01-基础.md"
    local.write_text("# 01 · 基础\n\n本地内容二\n", encoding="utf-8")

    result = service.sync("pull", strategy="remote")
    assert result["pulled"] is True
    assert "远端内容" in local.read_text(encoding="utf-8")
    assert service.status()["conflicts"] == []


def test_conflict_abort_and_manual_recovery(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.sync("push")
    remote_edit(remote, tmp_path, "01-基础.md", "远端内容")
    local = root / "第一文库" / "01-基础.md"
    local.write_text("# 01 · 基础\n\n本地内容二\n", encoding="utf-8")

    with pytest.raises(ApiError) as aborted:
        service.sync("pull", strategy="abort")
    assert aborted.value.code == "git_conflict"
    assert "本地内容二" in local.read_text(encoding="utf-8")
    assert not (root / ".git" / "MERGE_HEAD").exists()

    with pytest.raises(ApiError) as manual:
        service.sync("pull", strategy="manual")
    assert manual.value.code == "git_conflict"
    status = service.status()
    assert status["conflicts"]
    assert status["mergeInProgress"] is True

    recovered = service.sync("sync", strategy="local")
    assert recovered["conflictsResolved"] >= 1
    assert "本地内容二" in local.read_text(encoding="utf-8")
    assert service.status()["conflicts"] == []


def test_custom_commands_and_global_settings(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    settings = service.update_settings(
        {
            "autoSync": True,
            "intervalMinutes": 5,
            "autoDirection": "sync",
            "strategy": "local",
            "autoCommit": True,
            "pullCommand": "git fetch origin --prune",
            "pushCommand": "git push origin {branch}",
        }
    )
    assert settings["autoSync"] is True
    assert service.status()["nextAutoSyncAt"]
    target = next((root / "第一文库").glob("*.md"))
    target.write_text("# 01 · 基础\n\n自定义命令\n", encoding="utf-8")
    result = service.sync("sync")
    assert "已用自定义命令拉取" in result["message"]
    assert "自定义命令" in target.read_text(encoding="utf-8")

    stored = json.loads(service.config.path.read_text(encoding="utf-8"))
    assert stored["git"]["pushCommand"] == "git push origin {branch}"
    assert "git-sync.json" not in json.dumps(stored)

    assert validate_command("gh repo sync owner/repo --source main", "命令")
    assert validate_command("gh api --jq '.name | .id' repos/A-Button/KnowledgeLib", "命令")
    service._gh = lambda: sys.executable
    gh_parts = service._command_parts("gh -c \"print('gh-ok')\"", "main", "origin", root)
    code, out, _ = service._run_custom(gh_parts, root, timeout=30)
    assert code == 0 and "gh-ok" in out

    with pytest.raises(ApiError):
        service.update_settings({"intervalMinutes": 0})
    with pytest.raises(ApiError):
        service.update_settings({"strategy": "unknown"})
    with pytest.raises(ApiError):
        service.update_settings({"pushCommand": "echo not-git"})


def test_scheduler_respects_interval(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.sync("push")
    service.update_settings({"autoSync": True, "intervalMinutes": 10, "autoDirection": "push"})
    target = next((root / "第一文库").glob("*.md"))
    target.write_text("# 01 · 基础\n\n定时同步\n", encoding="utf-8")

    base = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert service.scheduler_tick(base) == []
    assert service.status()["dirty"] is True

    later = base + timedelta(minutes=11)
    results = service.scheduler_tick(later)
    assert results and results[0]["ok"] is True
    clone = clone_remote(remote, tmp_path / "scheduled")
    assert "定时同步" in next(clone.glob("*/01-基础.md")).read_text(encoding="utf-8")


def test_validation_and_legacy_migration(tmp_path: Path):
    root, remote = make_library(tmp_path)
    first_dir = root / "第一文库"
    (first_dir / ".git").mkdir()
    service = make_service(tmp_path, root)

    with pytest.raises(ApiError) as legacy:
        service.init_repo()
    assert legacy.value.code == "git_legacy_repos"
    assert "第一文库" in legacy.value.details["repos"]

    initialized = service.init_repo(str(remote), migrate_legacy=True)
    assert initialized["managed"] is True
    assert initialized["legacyRepos"] == []
    assert not (first_dir / ".git").exists()
    backups = list((tmp_path / "data" / "trash" / "git-repos").glob("*/第一文库/.git"))
    assert backups

    service.set_remote(str(remote))
    with pytest.raises(ApiError) as dirty:
        service.update_settings({"autoCommit": False})
        target = next((root / "第二文库").glob("*.md"))
        target.write_text("# 01 · 进阶\n\n未提交\n", encoding="utf-8")
        service.sync("push")
    assert dirty.value.code == "git_dirty"

    with pytest.raises(ApiError):
        validate_remote_url("ext::sh -c id")
    with pytest.raises(ApiError):
        validate_remote_url("-danger")
    with pytest.raises(ApiError):
        validate_command("rm -rf /", "命令")
    with pytest.raises(ApiError):
        validate_command("git push; echo bad", "命令")
    with pytest.raises(ApiError):
        validate_command("git --git-dir=D:/other push", "命令")
    with pytest.raises(ApiError):
        service.init_repo("", "bad branch")
    assert GitService(service.config)._env_value("ProgramFiles") == service._env_value("ProgramFiles")
