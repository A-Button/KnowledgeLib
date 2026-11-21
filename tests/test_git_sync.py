"""Offline tests for whole-library Git synchronization."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import library
from server.config import ConfigStore
from server.errors import ApiError
from server.git_sync import (
    GitService,
    validate_command,
    validate_remote_dir,
    validate_remote_url,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


# Test helpers create throwaway repositories with raw `git commit`. Pass the
# identity and signing overrides explicitly so the suite never depends on the
# developer's global git config (a global commit.gpgsign=true would otherwise
# turn every helper commit into a hard failure).
_GIT_TEST_CONFIG = (
    "-c",
    "user.name=KnowledgeLib Test",
    "-c",
    "user.email=test@example.com",
    "-c",
    "commit.gpgsign=false",
)


def git(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *_GIT_TEST_CONFIG, *args],
        cwd=str(cwd) if cwd else None,
        env=env,
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


def seed_remote(remote: Path, tmp_path: Path, files: dict[str, str]) -> None:
    clone = tmp_path / "seed-clone"
    if clone.exists():
        shutil.rmtree(clone)
    clone_remote(remote, clone)
    for name, text in files.items():
        target = clone / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "-m", "seed remote", cwd=clone)
    git("push", "origin", "main", cwd=clone)


def edit_remote_file(remote: Path, tmp_path: Path, relative: str, text: str) -> None:
    clone = tmp_path / "folder-remote-clone"
    if clone.exists():
        shutil.rmtree(clone)
    clone_remote(remote, clone)
    target = clone / relative
    target.write_text(text, encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "-m", "remote folder edit", cwd=clone)
    git("push", "origin", "main", cwd=clone)


def replace_remote_directory_with_file(
    remote: Path, tmp_path: Path, relative: str, text: str
) -> None:
    clone = tmp_path / "df-remote-clone"
    if clone.exists():
        shutil.rmtree(clone)
    clone_remote(remote, clone)
    target = clone / relative
    shutil.rmtree(target)
    target.write_text(text, encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "-m", "replace remote directory with file", cwd=clone)
    git("push", "origin", "main", cwd=clone)


def replace_remote_file_with_directory(
    remote: Path, tmp_path: Path, relative: str, child: str, text: str
) -> None:
    clone = tmp_path / "fd-remote-clone"
    if clone.exists():
        shutil.rmtree(clone)
    clone_remote(remote, clone)
    target = clone / relative
    target.unlink()
    target.mkdir()
    (target / child).write_text(text, encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "-m", "replace remote file with directory", cwd=clone)
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


def test_folder_sync_preserves_repo_root(tmp_path: Path):
    root, remote = make_library(tmp_path)
    seed_remote(remote, tmp_path, {"README.md": "readme\n", "docs/keep.txt": "keep\n"})
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    settings = service.update_settings({"remoteDir": "知识库/"})
    assert settings["remoteDir"] == "知识库"

    result = service.sync("push")
    assert "知识库" in result["message"]
    clone = clone_remote(remote, tmp_path / "verify")
    assert (clone / "README.md").read_text(encoding="utf-8") == "readme\n"
    assert (clone / "docs" / "keep.txt").is_file()
    assert (clone / "知识库" / "第一文库" / "01-基础.md").is_file()
    assert (clone / "知识库" / "第二文库" / "01-进阶.md").is_file()
    assert not (clone / "第一文库").exists()

    status = service.status()
    assert status["folderMode"] is True
    assert status["remoteDir"] == "知识库"
    assert status["dirty"] is False
    assert status["ahead"] == 0
    assert status["behind"] == 0


def test_folder_pull_merges_remote_folder(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "notes/知识库"})
    service.sync("push")

    edit_remote_file(
        remote,
        tmp_path,
        "notes/知识库/第一文库/01-基础.md",
        "# 01 · 基础\n\n远端内容\n",
    )
    local = root / "第二文库" / "01-进阶.md"
    local.write_text("# 01 · 进阶\n\n本地内容\n", encoding="utf-8")

    result = service.sync("pull")
    assert result["pulled"] is True
    assert "远端内容" in (root / "第一文库" / "01-基础.md").read_text(encoding="utf-8")
    assert "本地内容" in local.read_text(encoding="utf-8")
    status = service.status()
    assert status["conflicts"] == []
    assert status["dirty"] is False
    assert status["ahead"] >= 1
    assert status["behind"] == 0

    clone = clone_remote(remote, tmp_path / "add-clone")
    added = clone / "notes" / "知识库" / "新目录" / "new.md"
    added.parent.mkdir(parents=True)
    added.write_text("# new\n", encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "-m", "add remote file", cwd=clone)
    git("push", "origin", "main", cwd=clone)
    service.sync("pull")
    assert (root / "新目录" / "new.md").is_file()


def test_folder_conflict_strategies(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    edit_remote_file(remote, tmp_path, "kb/第一文库/01-基础.md", "# 01 · 基础\n\n远端内容\n")
    local = root / "第一文库" / "01-基础.md"
    local.write_text("# 01 · 基础\n\n本地内容\n", encoding="utf-8")

    with pytest.raises(ApiError) as aborted:
        service.sync("pull", strategy="abort")
    assert aborted.value.code == "git_conflict"
    assert "本地内容" in local.read_text(encoding="utf-8")
    assert not (root / ".git" / "MERGE_HEAD").exists()

    result = service.sync("pull", strategy="remote")
    assert result["conflictsResolved"] >= 1
    assert "远端内容" in local.read_text(encoding="utf-8")
    assert service.status()["conflicts"] == []


def test_folder_manual_conflict_recovery_and_push_guard(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    edit_remote_file(remote, tmp_path, "kb/第一文库/01-基础.md", "# 01 · 基础\n\n远端内容\n")
    local = root / "第一文库" / "01-基础.md"
    local.write_text("# 01 · 基础\n\n本地内容\n", encoding="utf-8")

    with pytest.raises(ApiError) as manual:
        service.sync("pull", strategy="manual")
    assert manual.value.code == "git_conflict"
    status = service.status()
    assert status["conflicts"]
    assert status["mergeInProgress"] is True
    assert "<<<<<<<" in local.read_text(encoding="utf-8")

    recovered = service.sync("sync", strategy="local")
    assert recovered["conflictsResolved"] >= 1
    assert "本地内容" in local.read_text(encoding="utf-8")
    assert service.status()["conflicts"] == []

    clone = clone_remote(remote, tmp_path / "recover-check")
    assert "本地内容" in (clone / "kb" / "第一文库" / "01-基础.md").read_text(encoding="utf-8")

    edit_remote_file(remote, tmp_path, "kb/第二文库/01-进阶.md", "# 01 · 进阶\n\n远端新内容\n")
    with pytest.raises(ApiError) as rejected:
        service.sync("push")
    assert rejected.value.code == "git_push_rejected"


def test_remote_dir_validation():
    assert validate_remote_dir("") == ""
    assert validate_remote_dir(None) == ""
    assert validate_remote_dir(" 知识库/子目录/ ") == "知识库/子目录"
    assert validate_remote_dir("notes\\kb") == "notes/kb"
    for bad in (
        "/abs/path",
        "C:/abs",
        "../up",
        "a/../b",
        "a//b",
        ".git/x",
        "-secret",
        "a:b",
        "a?b",
        "x" * 101,
    ):
        with pytest.raises(ApiError):
            validate_remote_dir(bad)


def test_folder_remote_deletion_applies_cleanly(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    clone = clone_remote(remote, tmp_path / "delete-one-clone")
    (clone / "kb" / "第二文库" / "01-进阶.md").unlink()
    git("add", "-A", cwd=clone)
    git("commit", "-m", "remote delete file", cwd=clone)
    git("push", "origin", "main", cwd=clone)

    result = service.sync("pull")
    assert result["conflictsResolved"] == 0
    assert not (root / "第二文库" / "01-进阶.md").exists()
    assert (root / "第一文库" / "01-基础.md").is_file()
    assert service.status()["conflicts"] == []

    clone2 = clone_remote(remote, tmp_path / "delete-folder-clone")
    shutil.rmtree(clone2 / "kb")
    git("add", "-A", cwd=clone2)
    git("commit", "-m", "remote delete folder", cwd=clone2)
    git("push", "origin", "main", cwd=clone2)

    result = service.sync("pull")
    assert "删除" in result["message"]
    assert not (root / "第一文库").exists()
    status = service.status()
    assert status["conflicts"] == []
    assert status["dirty"] is False

    recreated = service.sync("push")
    assert recreated["ok"] is True


def test_folder_repeat_sync_does_not_create_commits(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    before = clone_remote(remote, tmp_path / "count-before")
    count_before = int(git("rev-list", "--count", "HEAD", cwd=before).stdout.strip())
    service.sync("sync")
    service.sync("push")
    after = clone_remote(remote, tmp_path / "count-after")
    assert int(git("rev-list", "--count", "HEAD", cwd=after).stdout.strip()) == count_before
    assert service.status()["dirty"] is False
    assert service.status()["ahead"] == 0
    assert service.status()["behind"] == 0


def test_folder_remote_switch_keeps_local_library(tmp_path: Path):
    root, remote_a = make_library(tmp_path)
    remote_b = make_remote(tmp_path / "remote-b.git")
    seed_remote(remote_b, tmp_path, {"README.md": "b\n"})
    service = make_service(tmp_path, root)
    service.init_repo(str(remote_a))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    service.set_remote(str(remote_b))
    pulled = service.sync("pull")
    assert pulled["pulled"] is False
    assert (root / "第一文库" / "01-基础.md").is_file()
    assert service.status()["kbCount"] == 2

    service.sync("push")
    clone = clone_remote(remote_b, tmp_path / "switch-b")
    assert (clone / "kb" / "第一文库" / "01-基础.md").is_file()
    assert (clone / "README.md").is_file()


def test_folder_existing_remote_folder_requires_pull(tmp_path: Path):
    root, remote = make_library(tmp_path)
    seed_remote(remote, tmp_path, {"kb/legacy.md": "legacy\n"})
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})

    with pytest.raises(ApiError) as info:
        service.sync("push")
    assert info.value.code == "git_pull_required"

    pulled = service.sync("pull")
    assert pulled["pulled"] is True
    assert (root / "legacy.md").is_file()

    service.sync("push")
    clone = clone_remote(remote, tmp_path / "after-import")
    assert (clone / "kb" / "legacy.md").is_file()
    assert (clone / "kb" / "第一文库" / "01-基础.md").is_file()


def test_folder_manual_resolution_outside_app_recovers(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    edit_remote_file(remote, tmp_path, "kb/第一文库/01-基础.md", "# 01 · 基础\n\n远端内容\n")
    local = root / "第一文库" / "01-基础.md"
    local.write_text("# 01 · 基础\n\n本地内容\n", encoding="utf-8")
    with pytest.raises(ApiError):
        service.sync("pull", strategy="manual")

    local.write_text("# 01 · 基础\n\n手工解决\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-m", "resolve manually", cwd=root)

    result = service.sync("pull")
    assert result["pulled"] is False
    assert service.status()["conflicts"] == []
    service.sync("sync")
    clone = clone_remote(remote, tmp_path / "manual-outside")
    assert "手工解决" in (clone / "kb" / "第一文库" / "01-基础.md").read_text(encoding="utf-8")


def test_folder_both_sides_delete_same_file(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    clone = clone_remote(remote, tmp_path / "both-delete")
    (clone / "kb" / "第二文库" / "01-进阶.md").unlink()
    git("add", "-A", cwd=clone)
    git("commit", "-m", "remote delete", cwd=clone)
    git("push", "origin", "main", cwd=clone)
    (root / "第二文库" / "01-进阶.md").unlink()

    result = service.sync("sync")
    assert result["ok"] is True
    assert not (root / "第二文库" / "01-进阶.md").exists()
    assert service.status()["conflicts"] == []


def test_folder_rejects_remote_file_at_ancestor_path(tmp_path: Path):
    root, remote = make_library(tmp_path)
    seed_remote(remote, tmp_path, {"notes": "i am a file\n"})
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "notes/kb"})

    with pytest.raises(ApiError) as info:
        service.sync("push")
    assert info.value.code == "git_path_conflict"
    clone = clone_remote(remote, tmp_path / "ancestor-check")
    assert (clone / "notes").read_text(encoding="utf-8") == "i am a file\n"


def test_folder_pull_on_detached_head(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")
    edit_remote_file(remote, tmp_path, "kb/第一文库/01-基础.md", "# 01 · 基础\n\n远端内容\n")

    git("checkout", "--detach", cwd=root)
    result = service.sync("pull")
    assert result["pulled"] is True
    assert "远端内容" in (root / "第一文库" / "01-基础.md").read_text(encoding="utf-8")
    assert service.status()["dirty"] is False


def test_folder_settings_hand_edited_value_is_normalized(tmp_path: Path):
    root, _ = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo()
    service.config.patch_git_state({"remoteDir": "  kb/  "})
    assert service.settings()["remoteDir"] == "kb"
    service.config.patch_git_state({"remoteDir": "../escape"})
    settings = service.settings()
    assert settings["remoteDir"] == ""
    assert settings["remoteDirError"]
    assert service.status()["remoteDirError"]
    with pytest.raises(ApiError) as info:
        service.sync("pull")
    assert info.value.code == "git_settings_invalid"


def test_folder_replaced_remote_history_requires_reset(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    shutil.rmtree(remote)
    make_remote(remote)
    seed_remote(remote, tmp_path, {"README.md": "new repo\n"})

    with pytest.raises(ApiError) as info:
        service.sync("pull")
    assert info.value.code == "git_remote_rewritten"
    assert (root / "第一文库" / "01-基础.md").is_file()
    assert service.status()["kbCount"] == 2

    service.set_remote(str(remote))
    pushed = service.sync("push")
    assert pushed["ok"] is True
    clone = clone_remote(remote, tmp_path / "reset-check")
    assert (clone / "kb" / "第一文库" / "01-基础.md").is_file()
    assert (clone / "README.md").is_file()


def test_folder_pull_does_not_advance_ahead(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    target = root / "第一文库" / "01-基础.md"
    target.write_text("# 01 · 基础\n\n本地未推送\n", encoding="utf-8")
    service.sync("pull")
    status = service.status()
    assert status["dirty"] is False
    assert status["ahead"] == 1
    assert status["behind"] == 0

    service.sync("push")
    assert service.status()["ahead"] == 0


def test_folder_status_tracks_behind(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")
    assert service.status()["behind"] == 0

    edit_remote_file(remote, tmp_path, "kb/第二文库/01-进阶.md", "# 01 · 进阶\n\n远端新内容\n")
    service._run(["fetch", "origin", "--prune"], root, timeout=60)
    assert service.status()["behind"] >= 1

    service.sync("pull")
    assert service.status()["behind"] == 0


def test_folder_empty_pull_reuses_snapshot_ref(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    base_ref, _, _ = service._folder_refs("main", "kb", str(remote))
    before = service._rev(root, base_ref)
    assert before
    for _ in range(3):
        service.sync("pull")
    assert service._rev(root, base_ref) == before


def test_folder_sync_fetches_once(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    original = service._run
    fetch_calls: list[list[str]] = []

    def counted(args, *rest, **kwargs):
        if list(args[:2]) == ["fetch", "origin"]:
            fetch_calls.append(list(args))
        return original(args, *rest, **kwargs)

    service._run = counted  # type: ignore[method-assign]
    service.sync("sync")
    assert len(fetch_calls) == 1


def test_folder_pull_handles_unborn_head(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    git("init", "--initial-branch=main", cwd=root)
    remote = make_remote(tmp_path / "remote.git")
    seed_remote(remote, tmp_path, {"kb/remote.md": "# remote\n"})
    git("remote", "add", "origin", str(remote), cwd=root)

    config = ConfigStore(tmp_path / "config", tmp_path / "data")
    config.update({"kb": {"root": str(root)}})
    service = GitService(config)
    service.update_settings({"remoteDir": "kb"})

    result = service.sync("pull")
    assert result["pulled"] is True
    assert (root / "remote.md").read_text(encoding="utf-8") == "# remote\n"
    assert service.status()["dirty"] is False


def test_folder_remote_dir_rename_and_clear_setting(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb-a"})
    service.sync("push")
    service.update_settings({"remoteDir": "kb-b"})
    service.sync("push")

    clone = clone_remote(remote, tmp_path / "renamed")
    assert (clone / "kb-a" / "第一文库" / "01-基础.md").is_file()
    assert (clone / "kb-b" / "第一文库" / "01-基础.md").is_file()

    service.update_settings({"remoteDir": ""})
    status = service.status()
    assert status["folderMode"] is False
    assert status["remoteDir"] == ""
    assert (root / "第一文库" / "01-基础.md").is_file()


def test_folder_custom_push_command_overrides_mapping(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings(
        {"remoteDir": "kb", "pushCommand": "git push origin {branch}"}
    )
    target = root / "第一文库" / "01-基础.md"
    target.write_text("# 01 · 基础\n\n自定义推送\n", encoding="utf-8")
    service.sync("push")

    clone = clone_remote(remote, tmp_path / "custom")
    assert (clone / "第一文库" / "01-基础.md").is_file()
    assert not (clone / "kb").exists()
    parts = service._command_parts("git push origin {dir}/x", "main", str(remote), root, "kb")
    assert parts == ["git", "push", "origin", "kb/x"]


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_folder_type_change_applies(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    clone = clone_remote(remote, tmp_path / "type-change")
    target = clone / "kb" / "第一文库" / "01-基础.md"
    target.unlink()
    os.symlink("target-file", target)
    git("add", "-A", cwd=clone)
    git("commit", "-m", "replace file with symlink", cwd=clone)
    git("push", "origin", "main", cwd=clone)

    result = service.sync("pull")
    assert result["conflictsResolved"] == 0
    local = root / "第一文库" / "01-基础.md"
    assert local.is_symlink()
    assert os.readlink(local) == "target-file"
    assert service.status()["dirty"] is False


@pytest.mark.parametrize("strategy", ["abort", "local", "remote", "manual"])
def test_folder_pull_rejects_remote_directory_replaced_by_file(
    tmp_path: Path, strategy: str
):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    kb_dir = root / "第一文库"
    document = next(kb_dir.glob("01-*.md"))
    before_head = git("rev-parse", "HEAD", cwd=root).stdout.strip()
    before_text = document.read_text(encoding="utf-8")
    replace_remote_directory_with_file(remote, tmp_path, "kb/第一文库", "remote file\n")

    with pytest.raises(ApiError) as failure:
        service.sync("pull", strategy=strategy)
    assert failure.value.code == "git_conflict"
    details = failure.value.details or {}
    assert details.get("reason") == "path_type"
    assert set(details.get("conflicts") or []) == {"第一文库"}

    assert (kb_dir / ".kb.json").is_file()
    assert document.read_text(encoding="utf-8") == before_text
    status = service.status()
    assert status["conflicts"] == []
    assert status["mergeInProgress"] is False
    assert status["dirty"] is False
    assert git("rev-parse", "HEAD", cwd=root).stdout.strip() == before_head


@pytest.mark.parametrize("strategy", ["abort", "local", "remote", "manual"])
def test_folder_pull_rejects_remote_file_replaced_by_directory(
    tmp_path: Path, strategy: str
):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    note = root / "第一文库" / "notes.txt"
    note.write_text("local note\n", encoding="utf-8")
    service.sync("push")
    before_head = git("rev-parse", "HEAD", cwd=root).stdout.strip()
    replace_remote_file_with_directory(
        remote, tmp_path, "kb/第一文库/notes.txt", "child.md", "# child\n"
    )

    with pytest.raises(ApiError) as failure:
        service.sync("pull", strategy=strategy)
    assert failure.value.code == "git_conflict"
    details = failure.value.details or {}
    assert details.get("reason") == "path_type"
    assert set(details.get("conflicts") or []) == {"第一文库/notes.txt"}

    assert note.is_file()
    assert note.read_text(encoding="utf-8") == "local note\n"
    status = service.status()
    assert status["conflicts"] == []
    assert status["mergeInProgress"] is False
    assert git("rev-parse", "HEAD", cwd=root).stdout.strip() == before_head


def test_folder_pull_path_type_conflict_keeps_local_edits(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    kb_dir = root / "第一文库"
    document = next(kb_dir.glob("01-*.md"))
    document.write_text("# 01 · 基础\n\n本地修改不能丢\n", encoding="utf-8")
    replace_remote_directory_with_file(remote, tmp_path, "kb/第一文库", "remote file\n")

    with pytest.raises(ApiError) as failure:
        service.sync("pull", strategy="local")
    assert failure.value.code == "git_conflict"
    details = failure.value.details or {}
    assert details.get("reason") == "path_type"

    assert (kb_dir / ".kb.json").is_file()
    assert document.is_file()
    assert "本地修改不能丢" in document.read_text(encoding="utf-8")
    status = service.status()
    assert status["conflicts"] == []
    assert status["mergeInProgress"] is False


def test_folder_clear_remote_dir_requires_remote_reset(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    service.update_settings({"remoteDir": ""})
    assert service.status().get("modeSwitchWarning")
    with pytest.raises(ApiError) as blocked:
        service.sync("pull")
    assert blocked.value.code == "git_mode_switch"
    with pytest.raises(ApiError) as blocked_push:
        service.sync("push")
    assert blocked_push.value.code == "git_mode_switch"

    service.set_remote(str(remote))
    assert not service.status().get("modeSwitchWarning")
    result = service.sync("pull")
    assert result["ok"] is True


def test_folder_remote_branch_deleted_is_treated_as_rewrite(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")
    tips = git("for-each-ref", "--format=%(refname)", "refs/kl/tip", cwd=root).stdout.strip()
    assert tips
    git("branch", "-D", "main", cwd=remote)

    kb_dir = root / "第一文库"
    document = next(kb_dir.glob("01-*.md"))
    with pytest.raises(ApiError) as pushed:
        service.sync("push")
    assert pushed.value.code == "git_remote_rewritten"
    with pytest.raises(ApiError) as pulled:
        service.sync("pull")
    assert pulled.value.code == "git_remote_rewritten"
    assert (kb_dir / ".kb.json").is_file()
    assert document.is_file()

    service.set_remote(str(remote))
    result = service.sync("push")
    assert result["ok"] is True
    clone = clone_remote(remote, tmp_path / "branch-recreated")
    assert (clone / "kb" / "第一文库" / document.name).is_file()


def test_folder_remote_dir_change_clears_snapshots(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb-a"})
    service.sync("push")
    assert git("for-each-ref", "--format=%(refname)", "refs/kl", cwd=root).stdout.strip()

    service.update_settings({"remoteDir": "kb-b"})
    assert git("for-each-ref", "--format=%(refname)", "refs/kl", cwd=root).stdout.strip() == ""
    assert service.sync("push")["ok"] is True

    service.update_settings({"remoteDir": "kb-a"})
    with pytest.raises(ApiError) as required:
        service.sync("push")
    assert required.value.code == "git_pull_required"


def test_folder_remote_dir_snapshot_reset_edges(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    def snapshot_refs() -> str:
        return git("for-each-ref", "--format=%(refname)", "refs/kl", cwd=root).stdout.strip()

    assert snapshot_refs()
    service.update_settings({"autoSync": True})
    assert snapshot_refs()
    service.update_settings({"remoteDir": ""})
    assert snapshot_refs()
    service.update_settings({"remoteDir": "kb"})
    assert snapshot_refs() == ""
    with pytest.raises(ApiError) as required:
        service.sync("push")
    assert required.value.code == "git_pull_required"


def test_folder_invalid_hand_edited_remote_dir_suppresses_mode_switch_warning(
    tmp_path: Path,
):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    service.config.patch_git_state({"remoteDir": "../escape"})
    status = service.status()
    assert status["remoteDirError"]
    assert "modeSwitchWarning" in status
    assert status["modeSwitchWarning"] == ""
    with pytest.raises(ApiError) as blocked:
        service.sync("pull")
    assert blocked.value.code == "git_settings_invalid"


def test_remote_dir_rejects_windows_reserved_names_and_segment_whitespace():
    reserved = ["CON", "PRN", "AUX", "NUL"]
    reserved += [f"COM{index}" for index in range(1, 10)]
    reserved += [f"LPT{index}" for index in range(1, 10)]
    for bad in (*reserved, "con.txt", "com1.txt", "a /b", "a/ b"):
        with pytest.raises(ApiError) as info:
            validate_remote_dir(bad)
        assert info.value.code == "invalid_request"
    assert validate_remote_dir(" a/ ") == "a"
    assert validate_remote_dir("notes\\kb") == "notes/kb"


def test_root_pull_merge_ignores_global_gpgsign(tmp_path: Path):
    root, remote = make_library(tmp_path)
    config = ConfigStore(tmp_path / "config", tmp_path / "data")
    config.update({"kb": {"root": str(root)}})
    service = GitService(
        config,
        environ={
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "commit.gpgsign",
            "GIT_CONFIG_VALUE_0": "true",
        },
    )
    service.init_repo(str(remote))
    service.sync("push")
    remote_edit(remote, tmp_path, "01-基础.md", "远端签名测试")

    local = next((root / "第二文库").glob("*.md"))
    local.write_text("# 01 · 进阶\n\n本地待合并\n", encoding="utf-8")

    try:
        result = service.sync("pull")
    except ApiError as exc:
        raise AssertionError(
            f"根目录模式拉取不应受 commit.gpgsign=true 影响，实际失败：{exc.code}: {exc.message}"
        ) from exc
    assert result["ok"] is True
    assert result["pulled"] is True
    assert "远端签名测试" in (root / "第一文库" / "01-基础.md").read_text(encoding="utf-8")
    assert "本地待合并" in local.read_text(encoding="utf-8")
    merge_parents = git("rev-list", "--parents", "-n", "1", "HEAD", cwd=root).stdout.split()
    assert len(merge_parents) == 3
    assert "gpgsig" not in git("cat-file", "commit", "HEAD", cwd=root).stdout


def test_folder_sync_without_global_git_identity(tmp_path: Path):
    root, remote = make_library(tmp_path)
    home = tmp_path / "empty-home"
    home.mkdir()
    missing_config = str(tmp_path / "missing-gitconfig")
    config = ConfigStore(tmp_path / "config", tmp_path / "data")
    config.update({"kb": {"root": str(root)}})
    service = GitService(
        config,
        environ={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "GIT_CONFIG_GLOBAL": missing_config,
            "GIT_CONFIG_SYSTEM": missing_config,
        },
    )
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    edit_remote_file(
        remote, tmp_path, "kb/第一文库/01-基础.md", "# 01 · 基础\n\n干净环境\n"
    )
    result = service.sync("pull")
    assert result["ok"] is True
    assert "干净环境" in (root / "第一文库" / "01-基础.md").read_text(encoding="utf-8")

    # The raw `git()` helper must not lean on the user's global identity either:
    # commit in a clean environment and check the explicit test identity wins.
    clean_env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "GIT_CONFIG_GLOBAL": missing_config,
        "GIT_CONFIG_SYSTEM": missing_config,
    }
    helper_repo = tmp_path / "helper-repo"
    git("init", "-q", "--initial-branch=main", str(helper_repo), env=clean_env)
    (helper_repo / "note.txt").write_text("identity\n", encoding="utf-8")
    git("add", "-A", cwd=helper_repo, env=clean_env)
    git("commit", "-m", "helper identity", cwd=helper_repo, env=clean_env)
    author = git("log", "-1", "--format=%an <%ae>", cwd=helper_repo, env=clean_env)
    assert author.stdout.strip() == "KnowledgeLib Test <test@example.com>"


def test_folder_conflict_remote_deletion_removes_modified_file(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    local = root / "第一文库" / "01-基础.md"
    local.write_text("# 01 · 基础\n\n本地改动\n", encoding="utf-8")

    clone = clone_remote(remote, tmp_path / "remote-delete-file")
    (clone / "kb" / "第一文库" / "01-基础.md").unlink()
    git("add", "-A", cwd=clone)
    git("commit", "-m", "remote delete file", cwd=clone)
    git("push", "origin", "main", cwd=clone)

    result = service.sync("pull", strategy="remote")
    assert result["ok"] is True
    assert not local.exists()
    assert (root / "第一文库" / ".kb.json").is_file()
    assert service.status()["conflicts"] == []


def test_folder_conflict_local_deletion_keeps_remote_change_deleted(tmp_path: Path):
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    local = root / "第一文库" / "01-基础.md"
    local.unlink()
    edit_remote_file(
        remote, tmp_path, "kb/第一文库/01-基础.md", "# 01 · 基础\n\n远端改动\n"
    )

    result = service.sync("pull", strategy="local")
    assert result["ok"] is True
    assert not local.exists()
    assert (root / "第一文库" / ".kb.json").is_file()
    assert service.status()["conflicts"] == []


def test_folder_local_type_swap_pull_succeeds(tmp_path: Path):
    """A local directory -> file swap is a one-sided change, not a D/F conflict.

    The remote folder did not change, so `git read-tree -m` keeps the local
    swap. The pull must succeed instead of tripping the remote-driven D/F
    precheck, and the following push must publish the local file.
    """
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    swapped = root / "第一文库"
    shutil.rmtree(swapped)
    swapped.write_text("local file\n", encoding="utf-8")

    try:
        result = service.sync("pull", strategy="local")
    except ApiError as exc:
        pytest.fail(
            f"远端目录未变化时本地目录↔文件互换应能拉取，实际 {exc.code}: {exc.message}"
        )
    assert result["ok"] is True
    assert swapped.is_file()
    assert swapped.read_text(encoding="utf-8") == "local file\n"
    status = service.status()
    assert status["conflicts"] == []
    assert status["mergeInProgress"] is False
    assert status["dirty"] is False

    pushed = service.sync("push")
    assert pushed["ok"] is True
    verify = clone_remote(remote, tmp_path / "type-swap-verify")
    remote_target = verify / "kb" / "第一文库"
    assert remote_target.is_file()
    assert remote_target.read_text(encoding="utf-8") == "local file\n"
    assert (verify / "kb" / "第二文库" / "01-进阶.md").is_file()


def test_folder_local_type_swap_remote_strategy_keeps_content(tmp_path: Path):
    """`strategy="remote"` must never delete the swapped-in local file.

    With an unchanged remote tree the server side has nothing to apply; if the
    sync refuses, the local file has to survive and a retry with the safe
    (local) strategy has to converge instead of silently removing content.
    """
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    swapped = root / "第一文库"
    document = root / "第二文库" / "01-进阶.md"
    other_before = document.read_text(encoding="utf-8")
    shutil.rmtree(swapped)
    swapped.write_text("local file\n", encoding="utf-8")

    failure: ApiError | None = None
    try:
        result = service.sync("pull", strategy="remote")
    except ApiError as exc:
        failure = exc
        result = None

    assert swapped.is_file(), "本地被换成的文件不应被删除"
    assert swapped.read_text(encoding="utf-8") == "local file\n"
    assert document.is_file()
    assert document.read_text(encoding="utf-8") == other_before

    if failure is not None:
        assert failure.code == "git_conflict"
        details = failure.details or {}
        assert "第一文库" in (details.get("conflicts") or [])
        # The refusal must not be "resolved" by dropping local content: retrying
        # with the safe (local) strategy has to converge and keep the file.
        try:
            recovered = service.sync("pull", strategy="local")
        except ApiError as exc:
            pytest.fail(
                f"改选本地策略后应能收敛并保留本地文件，实际 {exc.code}: {exc.message}"
            )
        assert recovered["ok"] is True
        assert swapped.is_file()
        assert swapped.read_text(encoding="utf-8") == "local file\n"
        assert service.status()["conflicts"] == []
    else:
        assert result is not None and result["ok"] is True


def test_folder_mode_switch_blocked_after_dir_rename(tmp_path: Path):
    """The folder-mode marker survives remoteDir changes until the remote is reset."""
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb-a"})
    service.sync("push")

    service.update_settings({"remoteDir": "kb-b"})
    assert git("for-each-ref", "--format=%(refname)", "refs/kl", cwd=root).stdout.strip() == ""
    service.update_settings({"remoteDir": ""})
    assert git("for-each-ref", "--format=%(refname)", "refs/kl", cwd=root).stdout.strip() == ""

    status = service.status()
    assert status["folderMode"] is False
    assert status["modeSwitchWarning"], "清空 remoteDir 后 status 应返回 modeSwitchWarning"

    for direction in ("pull", "push"):
        with pytest.raises(ApiError) as blocked:
            service.sync(direction)
        assert blocked.value.code == "git_mode_switch"
    assert (root / "第一文库" / "01-基础.md").is_file()
    assert (root / "第一文库" / ".kb.json").is_file()

    service.set_remote(str(remote))
    assert service.status()["modeSwitchWarning"] == ""
    result = service.sync("pull")
    assert result["ok"] is True


def test_folder_stale_probe_lock_does_not_block(tmp_path: Path):
    """A lock file left by a crashed probe must not block every later pull."""
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")

    (root / ".git" / "kl-probe.index.lock").write_text("stale\n", encoding="utf-8")
    (root / ".git" / "kl-probe-999999.index.lock").write_text("stale\n", encoding="utf-8")

    edit_remote_file(
        remote, tmp_path, "kb/第一文库/01-基础.md", "# 01 · 基础\n\n远端新内容\n"
    )
    try:
        result = service.sync("pull")
    except ApiError as exc:
        pytest.fail(f"残留 probe 锁不应阻断拉取，实际 {exc.code}: {exc.message}")
    assert result["ok"] is True
    assert result["pulled"] is True
    updated = root / "第一文库" / "01-基础.md"
    assert "远端新内容" in updated.read_text(encoding="utf-8")
    status = service.status()
    assert status["conflicts"] == []
    assert status["mergeInProgress"] is False
    assert status["dirty"] is False


def test_folder_sync_clears_upstream(tmp_path: Path):
    """Folder mode must leave the local branch without an upstream."""
    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.sync("push")
    assert service._upstream(root) == "origin/main"

    clone = clone_remote(remote, tmp_path / "readme-clone")
    (clone / "README.md").write_text("repo readme\n", encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "-m", "add repo readme", cwd=clone)
    git("push", "origin", "main", cwd=clone)

    service.update_settings({"remoteDir": "kb"})
    pushed = service.sync("push")
    assert pushed["ok"] is True
    assert service._upstream(root) == "", "文件夹模式推送后本地分支不应保留 upstream"
    with pytest.raises(subprocess.CalledProcessError):
        git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=root)

    verify = clone_remote(remote, tmp_path / "layout-verify")
    assert (verify / "README.md").read_text(encoding="utf-8") == "repo readme\n"
    assert (verify / "kb" / "第一文库" / "01-基础.md").is_file()


def test_folder_sync_works_in_linked_worktree(tmp_path: Path):
    """A linked worktree (`.git` is a file) must support folder sync end to end."""
    remote = make_remote(tmp_path / "remote.git")
    main_repo = tmp_path / "main-repo"
    main_repo.mkdir()
    git("init", "--initial-branch=trunk", str(main_repo))
    (main_repo / "README.md").write_text("main repo readme\n", encoding="utf-8")
    git("add", "-A", cwd=main_repo)
    git("commit", "-m", "init main repo", cwd=main_repo)
    worktree = tmp_path / "library-worktree"
    git("worktree", "add", "-b", "main", str(worktree), cwd=main_repo)
    assert (worktree / ".git").is_file()

    first = library.create_kb(worktree, "第一文库")
    first_dir = worktree / first["slug"]
    (first_dir / "01-基础.md").write_text("# 01 · 基础\n\n第一库内容\n", encoding="utf-8")

    service = make_service(tmp_path, worktree)
    service.init_repo(str(remote))
    service.sync("push")
    assert service._upstream(worktree) == "origin/main"

    service.update_settings({"remoteDir": "kb"})
    pushed = service.sync("push")
    assert pushed["ok"] is True
    assert service._upstream(worktree) == "", "worktree 内文件夹模式推送后不应保留 upstream"

    edit_remote_file(
        remote, tmp_path, "kb/第一文库/01-基础.md", "# 01 · 基础\n\n远端内容\n"
    )
    result = service.sync("pull")
    assert result["ok"] is True
    document = worktree / "第一文库" / "01-基础.md"
    assert document.is_file()
    assert "远端内容" in document.read_text(encoding="utf-8")
    assert (worktree / "第一文库" / ".kb.json").is_file()
    assert service._upstream(worktree) == "", "文件夹模式拉取后不应重新建立 upstream"
    status = service.status()
    assert status["managed"] is True
    assert status["folderMode"] is True
    assert status["conflicts"] == []
    assert status["dirty"] is False


def test_folder_pull_rejects_remote_dir_as_file(tmp_path: Path):
    """A file where the remote folder path should be must fail closed.

    Two variants matter: the configured folder itself is replaced by a file,
    and one of its ancestors is replaced by a file (the path then no longer
    resolves, and treating that as a remote folder deletion would wipe the
    local library).
    """

    def snapshot(root: Path) -> dict[str, str]:
        state: dict[str, str] = {}
        for entry in sorted(root.rglob("*")):
            relative = entry.relative_to(root).as_posix()
            if relative == ".git" or relative.startswith(".git/"):
                continue
            state[relative] = entry.read_text(encoding="utf-8") if entry.is_file() else "<dir>"
        return state

    root, remote = make_library(tmp_path)
    service = make_service(tmp_path, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb"})
    service.sync("push")
    before = snapshot(root)
    replace_remote_directory_with_file(remote, tmp_path, "kb", "remote file\n")

    with pytest.raises(ApiError) as failure:
        service.sync("pull")
    assert failure.value.code == "git_pull_failed"
    assert "不是文件夹" in failure.value.message
    assert snapshot(root) == before
    assert (root / "第一文库" / "01-基础.md").is_file()
    assert (root / "第一文库" / ".kb.json").is_file()

    nested = tmp_path / "nested"
    nested.mkdir()
    root, remote = make_library(nested)
    service = make_service(nested, root)
    service.init_repo(str(remote))
    service.update_settings({"remoteDir": "kb/sub"})
    service.sync("push")
    before = snapshot(root)
    replace_remote_directory_with_file(remote, nested, "kb", "remote file\n")

    try:
        service.sync("pull")
    except ApiError as exc:
        failure = exc
    else:
        pytest.fail("远端祖先目录被换成文件时不得按「远端删除目录」静默清空本地内容")
    assert failure.code == "git_pull_failed"
    assert "不是文件夹" in failure.message
    assert snapshot(root) == before
    assert (root / "第一文库" / "01-基础.md").is_file()
    assert (root / "第一文库" / ".kb.json").is_file()
