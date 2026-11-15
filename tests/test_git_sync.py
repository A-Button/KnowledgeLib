"""Offline Git synchronization tests using temporary local bare repositories."""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import library
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


def make_kb(tmp_path: Path) -> tuple[Path, dict, Path, Path]:
    root = tmp_path / "library"
    root.mkdir()
    meta = library.create_kb(root, "Git测试库")
    kb_dir = root / meta["slug"]
    (kb_dir / "01-基础.md").write_text("# 01 · 基础\n\n本地内容\n", encoding="utf-8")
    remote = make_remote(tmp_path / "remote.git")
    return root, meta, kb_dir, remote


def clone_remote(remote: Path, target: Path) -> Path:
    git("clone", str(remote), str(target))
    return target


def remote_edit(remote: Path, tmp_path: Path, text: str) -> None:
    clone = tmp_path / "remote-clone"
    if clone.exists():
        shutil.rmtree(clone)
    clone_remote(remote, clone)
    (clone / "01-基础.md").write_text("# 01 · 基础\n\n" + text + "\n", encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "-m", "remote edit", cwd=clone)
    git("push", "origin", "main", cwd=clone)


def test_init_push_and_status(tmp_path: Path):
    _root, meta, kb_dir, remote = make_kb(tmp_path)
    service = GitService(tmp_path / "config")
    before = service.status(kb_dir, meta["id"])
    assert before["available"] is True
    assert before["managed"] is False

    initialized = service.init_repo(kb_dir, meta["id"], str(remote), "main")
    assert initialized["managed"] is True
    assert initialized["branch"] == "main"
    assert initialized["remoteUrl"] == str(remote)
    assert initialized["lastCommit"]["subject"] == "Initialize KnowledgeLib knowledge base"
    assert (kb_dir / ".git").is_dir()
    assert (kb_dir / ".gitignore").is_file()

    result = service.sync(kb_dir, meta["id"], "push")
    assert result["ok"] is True
    branches = git("branch", "--list", cwd=remote).stdout
    assert "main" in branches

    status = service.status(kb_dir, meta["id"])
    assert status["dirty"] is False
    assert status["upstream"] == "origin/main"
    assert status["ahead"] == 0
    assert status["behind"] == 0
    assert status["lastStatus"] == "ok"


def test_local_change_auto_commit_and_push(tmp_path: Path):
    _root, meta, kb_dir, remote = make_kb(tmp_path)
    service = GitService(tmp_path / "config")
    service.init_repo(kb_dir, meta["id"], str(remote))
    (kb_dir / "01-基础.md").write_text("# 01 · 基础\n\n新内容\n", encoding="utf-8")
    assert service.status(kb_dir, meta["id"])["dirty"] is True
    result = service.sync(kb_dir, meta["id"], "push")
    assert result["commitCreated"] is True

    clone = clone_remote(remote, tmp_path / "verify")
    assert "新内容" in (clone / "01-基础.md").read_text(encoding="utf-8")


def test_pull_prefers_local(tmp_path: Path):
    _root, meta, kb_dir, remote = make_kb(tmp_path)
    service = GitService(tmp_path / "config")
    service.init_repo(kb_dir, meta["id"], str(remote))
    service.sync(kb_dir, meta["id"], "push")
    remote_edit(remote, tmp_path, "远端内容")

    (kb_dir / "01-基础.md").write_text("# 01 · 基础\n\n本地内容二\n", encoding="utf-8")
    result = service.sync(kb_dir, meta["id"], "pull", strategy="local")
    assert result["pulled"] is True
    assert "本地内容二" in (kb_dir / "01-基础.md").read_text(encoding="utf-8")
    status = service.status(kb_dir, meta["id"])
    assert status["conflicts"] == []
    assert status["dirty"] is False


def test_pull_prefers_remote(tmp_path: Path):
    _root, meta, kb_dir, remote = make_kb(tmp_path)
    service = GitService(tmp_path / "config")
    service.init_repo(kb_dir, meta["id"], str(remote))
    service.sync(kb_dir, meta["id"], "push")
    remote_edit(remote, tmp_path, "远端内容")

    (kb_dir / "01-基础.md").write_text("# 01 · 基础\n\n本地内容二\n", encoding="utf-8")
    result = service.sync(kb_dir, meta["id"], "pull", strategy="remote")
    assert result["pulled"] is True
    assert "远端内容" in (kb_dir / "01-基础.md").read_text(encoding="utf-8")
    assert service.status(kb_dir, meta["id"])["conflicts"] == []


def test_conflict_abort_and_manual_recovery(tmp_path: Path):
    _root, meta, kb_dir, remote = make_kb(tmp_path)
    service = GitService(tmp_path / "config")
    service.init_repo(kb_dir, meta["id"], str(remote))
    service.sync(kb_dir, meta["id"], "push")
    remote_edit(remote, tmp_path, "远端内容")
    (kb_dir / "01-基础.md").write_text("# 01 · 基础\n\n本地内容二\n", encoding="utf-8")

    with pytest.raises(ApiError) as aborted:
        service.sync(kb_dir, meta["id"], "pull", strategy="abort")
    assert aborted.value.code == "git_conflict"
    assert "本地内容二" in (kb_dir / "01-基础.md").read_text(encoding="utf-8")
    assert not (kb_dir / ".git" / "MERGE_HEAD").exists()

    with pytest.raises(ApiError) as manual:
        service.sync(kb_dir, meta["id"], "pull", strategy="manual")
    assert manual.value.code == "git_conflict"
    status = service.status(kb_dir, meta["id"])
    assert status["conflicts"]
    assert status["mergeInProgress"] is True

    recovered = service.sync(kb_dir, meta["id"], "sync", strategy="local")
    assert recovered["conflictsResolved"] >= 1
    assert "本地内容二" in (kb_dir / "01-基础.md").read_text(encoding="utf-8")
    assert service.status(kb_dir, meta["id"])["conflicts"] == []


def test_custom_commands_and_settings(tmp_path: Path):
    _root, meta, kb_dir, remote = make_kb(tmp_path)
    service = GitService(tmp_path / "config")
    service.init_repo(kb_dir, meta["id"], str(remote))
    settings = service.update_settings(
        meta["id"],
        {
            "autoSync": True,
            "intervalMinutes": 5,
            "autoDirection": "sync",
            "strategy": "local",
            "autoCommit": True,
            "pullCommand": "git fetch origin --prune",
            "pushCommand": "git push origin {branch}",
        },
    )
    assert settings["autoSync"] is True
    assert service.status(kb_dir, meta["id"])["nextAutoSyncAt"]
    (kb_dir / "01-基础.md").write_text("# 01 · 基础\n\n自定义命令\n", encoding="utf-8")
    result = service.sync(kb_dir, meta["id"], "sync")
    assert "自定义" in (kb_dir / "01-基础.md").read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "已用自定义命令拉取" in result["message"]

    assert validate_command("gh repo sync owner/repo --source main", "命令")
    assert validate_command("gh api --jq '.name | .id' repos/A-Button/KnowledgeLib", "命令")
    service._gh = lambda: sys.executable
    gh_parts = service._command_parts("gh -c \"print('gh-ok')\"", "main", "origin", kb_dir)
    code, out, _ = service._run_custom(gh_parts, kb_dir, timeout=30)
    assert code == 0
    assert "gh-ok" in out

    with pytest.raises(ApiError):
        service.update_settings(meta["id"], {"intervalMinutes": 0})
    with pytest.raises(ApiError):
        service.update_settings(meta["id"], {"strategy": "unknown"})
    with pytest.raises(ApiError):
        service.update_settings(meta["id"], {"pushCommand": "echo not-git"})


def test_scheduler_respects_interval(tmp_path: Path):
    root, meta, kb_dir, remote = make_kb(tmp_path)
    service = GitService(tmp_path / "config")
    service.init_repo(kb_dir, meta["id"], str(remote))
    service.sync(kb_dir, meta["id"], "push")
    service.update_settings(meta["id"], {"autoSync": True, "intervalMinutes": 10, "autoDirection": "push"})
    (kb_dir / "01-基础.md").write_text("# 01 · 基础\n\n定时同步\n", encoding="utf-8")

    base = datetime.now(timezone.utc) + timedelta(minutes=1)
    assert service.scheduler_tick(root, base) == []
    assert service.status(kb_dir, meta["id"])["dirty"] is True

    later = base + timedelta(minutes=11)
    results = service.scheduler_tick(root, later)
    assert results and results[0]["ok"] is True
    clone = clone_remote(remote, tmp_path / "scheduled")
    assert "定时同步" in (clone / "01-基础.md").read_text(encoding="utf-8")


def test_validation_and_missing_remote(tmp_path: Path):
    _root, meta, kb_dir, remote = make_kb(tmp_path)
    service = GitService(tmp_path / "config")
    service.init_repo(kb_dir, meta["id"])
    with pytest.raises(ApiError) as no_remote:
        service.sync(kb_dir, meta["id"], "push")
    assert no_remote.value.code == "git_no_remote"

    service.set_remote(kb_dir, meta["id"], str(remote))
    service.update_settings(meta["id"], {"autoCommit": False})
    (kb_dir / "01-基础.md").write_text("# 01 · 基础\n\n未提交\n", encoding="utf-8")
    with pytest.raises(ApiError) as dirty:
        service.sync(kb_dir, meta["id"], "push")
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
        validate_command("git --work-tree=D:/other status", "命令")
    with pytest.raises(ApiError):
        service.init_repo(kb_dir, meta["id"], "", "bad branch")
    assert GitService(tmp_path / "env-config", {"PROGRAMFILES": "X:/Program Files"})._env_value("ProgramFiles") == "X:/Program Files"
