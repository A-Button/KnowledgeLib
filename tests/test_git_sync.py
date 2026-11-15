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
from server.git_sync import (
    GitService,
    validate_command,
    validate_remote_dir,
    validate_remote_url,
)

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
    assert status["ahead"] == 0
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
    assert service.settings()["remoteDir"] == ""


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
