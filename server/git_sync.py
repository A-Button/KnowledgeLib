"""Per-knowledge-base Git repository management and synchronization.

Git itself is the source of truth for remotes and commits. Only user-facing
automation preferences (auto-sync interval, conflict strategy and optional
custom commands) live in the per-user config directory, never inside the
knowledge base and never in the repository.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .errors import ApiError
from .storage import utc_now_iso

STRATEGIES = ("abort", "local", "remote", "manual")
DIRECTIONS = ("sync", "pull", "push")

DEFAULT_SETTINGS: dict[str, Any] = {
    "autoSync": False,
    "intervalMinutes": 15,
    "autoDirection": "sync",
    "strategy": "abort",
    "autoCommit": True,
    "remoteDir": "",
    "pushCommand": "",
    "pullCommand": "",
    "updatedAt": "",
    "lastAttemptAt": "",
    "lastSyncAt": "",
    "lastStatus": "never",
    "lastMessage": "",
    "lastDirection": "",
    "folderSyncUsed": False,
}

_COMMAND_FORBIDDEN = (";", ">", "<", chr(96), "$(")
_COMMAND_BLOCKED_ARGS = {
    "-C",
    "--git-dir",
    "--work-tree",
    "--exec-path",
    "--upload-pack",
    "--receive-pack",
}
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_MODE_SWITCH_WARNING = (
    "检测到「仓库内目录」同步快照，但当前为仓库根目录模式；切换会把整个远端仓库合并进知识库。\n"
    "请先清理远端旧目录，再重新保存 remote 重置快照；"
    "或重新填写「仓库内目录」并先执行一次「拉取」导入。"
)
_REMOTE_BRANCH_GONE = "远端分支已消失（可能被删除或仓库被重建）；请重新保存 remote 重置同步快照"


def _now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _short(text: str, limit: int = 4000) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > limit:
        return text[:limit] + "\n…（输出已截断）"
    return text


def _tail(text: str, limit: int = 400) -> str:
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if len(text) > limit:
        return "…" + text[-(limit - 1):]
    return text


def validate_remote_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "invalid_request", "Git remote 必须是字符串")
    text = value.strip()
    if not text or len(text) > 500:
        raise ApiError(400, "invalid_request", "Git remote 不能为空且不超过 500 字符")
    if any(ord(ch) < 32 for ch in text):
        raise ApiError(400, "invalid_request", "Git remote 不能包含控制字符")
    if text.startswith("-"):
        raise ApiError(400, "invalid_request", "Git remote 不能以 - 开头")
    lower = text.lower()
    if "::" in text or lower.startswith("ext::"):
        raise ApiError(400, "invalid_request", "不支持 ext:: 等 Git 传输辅助协议")
    if lower.startswith(("http://", "https://", "ssh://", "git://")):
        parsed = urlsplit(text)
        if not parsed.netloc:
            raise ApiError(400, "invalid_request", "Git remote 缺少主机名")
        return text
    if lower.startswith("file://"):
        return text
    if re.match(r"^[\w.+-]+@[\w.-]+:.+$", text):
        return text
    # Local paths are useful for repositories on a mounted disk and for tests.
    return text


def validate_branch(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "invalid_request", "分支名必须是字符串")
    branch = value.strip() or "main"
    if not _BRANCH_RE.match(branch):
        raise ApiError(400, "invalid_request", "分支名不合法")
    if ".." in branch or "//" in branch or branch.endswith((".", "/")):
        raise ApiError(400, "invalid_request", "分支名不合法")
    if branch in ("HEAD", "@"):
        raise ApiError(400, "invalid_request", "分支名不合法")
    return branch


def validate_remote_dir(value: Any) -> str:
    """Normalize the optional folder inside the remote repository.

    Empty means "repository root" (the historical behavior). The folder is a
    relative POSIX-style path; backslashes are accepted for convenience so that
    Windows users can paste a path.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError(400, "invalid_request", "仓库内目录必须是字符串")
    text = value.strip().replace("\\", "/")
    if not text:
        return ""
    if len(text) > 200:
        raise ApiError(400, "invalid_request", "仓库内目录不能超过 200 字符")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise ApiError(400, "invalid_request", "仓库内目录必须是相对路径")
    if any(ord(ch) < 32 for ch in text):
        raise ApiError(400, "invalid_request", "仓库内目录不能包含换行或控制字符")
    parts = text.strip("/").split("/")
    if not parts or any(not part for part in parts):
        raise ApiError(400, "invalid_request", "仓库内目录不合法")
    for part in parts:
        if part in (".", "..") or part.lower() == ".git":
            raise ApiError(400, "invalid_request", "仓库内目录不合法")
        if part != part.strip():
            raise ApiError(400, "invalid_request", "仓库内目录每级名称不能以空白开头或结尾")
        if part.split(".")[0].strip().lower() in _WINDOWS_RESERVED_NAMES:
            raise ApiError(400, "invalid_request", "仓库内目录不能包含 Windows 保留名")
        if part.startswith("-") or part.endswith("."):
            raise ApiError(400, "invalid_request", "仓库内目录不合法")
        if any(ch in part for ch in ':<>"|?*^~{}[]'):
            raise ApiError(400, "invalid_request", '仓库内目录不能包含 : < > " | ? * ^ ~ { } [ ]')
        if len(part) > 100:
            raise ApiError(400, "invalid_request", "仓库内目录每级名称不能超过 100 字符")
    return "/".join(parts)


def validate_command(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ApiError(400, "invalid_request", f"{field}必须是字符串")
    text = value.strip()
    if not text:
        return ""
    if len(text) > 1000:
        raise ApiError(400, "invalid_request", f"{field}不能超过 1000 字符")
    if any(ord(ch) < 32 for ch in text):
        raise ApiError(400, "invalid_request", f"{field}不能包含换行或控制字符")
    if any(token in text for token in _COMMAND_FORBIDDEN):
        raise ApiError(400, "invalid_request", f"{field}不支持 shell 管道、重定向或命令拼接")
    try:
        parts = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", f"{field}无法解析：{exc}")
    if not parts:
        return ""
    executable = Path(parts[0]).name.lower()
    if executable not in ("git", "git.exe", "gh", "gh.exe"):
        raise ApiError(400, "invalid_request", f"{field}必须以 git 或 gh 开头，不能执行其他程序")
    blocked_prefixes = (
        "-C",
        "--git-dir=",
        "--work-tree=",
        "--exec-path=",
        "--upload-pack=",
        "--receive-pack=",
    )
    if any(part in _COMMAND_BLOCKED_ARGS or part.startswith(blocked_prefixes) for part in parts[1:]):
        raise ApiError(400, "invalid_request", f"{field}不能切换仓库目录或执行外部辅助程序")
    return text


def normalize_git_settings(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, Mapping):
        raise ApiError(400, "invalid_request", "Git 设置必须是对象")
    result = dict(DEFAULT_SETTINGS)
    if isinstance(current, Mapping):
        for key in result:
            if key in current:
                result[key] = current[key]
    if "autoSync" in patch:
        if not isinstance(patch["autoSync"], bool):
            raise ApiError(400, "invalid_request", "autoSync 必须是布尔值")
        result["autoSync"] = patch["autoSync"]
    if "autoCommit" in patch:
        if not isinstance(patch["autoCommit"], bool):
            raise ApiError(400, "invalid_request", "autoCommit 必须是布尔值")
        result["autoCommit"] = patch["autoCommit"]
    if "intervalMinutes" in patch:
        try:
            interval = int(patch["intervalMinutes"])
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_request", "同步间隔必须是整数分钟")
        if interval < 1 or interval > 1440:
            raise ApiError(400, "invalid_request", "同步间隔需要在 1..1440 分钟之间")
        result["intervalMinutes"] = interval
    if "autoDirection" in patch:
        direction = str(patch["autoDirection"] or "")
        if direction not in DIRECTIONS:
            raise ApiError(400, "invalid_request", "自动同步方向不合法")
        result["autoDirection"] = direction
    if "strategy" in patch:
        strategy = str(patch["strategy"] or "")
        if strategy not in STRATEGIES:
            raise ApiError(400, "invalid_request", "冲突处理策略不合法")
        result["strategy"] = strategy
    if "remoteDir" in patch:
        result["remoteDir"] = validate_remote_dir(patch["remoteDir"])
    if "pushCommand" in patch:
        result["pushCommand"] = validate_command(patch["pushCommand"], "自定义推送命令")
    if "pullCommand" in patch:
        result["pullCommand"] = validate_command(patch["pullCommand"], "自定义拉取命令")
    if "folderSyncUsed" in patch:
        if not isinstance(patch["folderSyncUsed"], bool):
            raise ApiError(400, "invalid_request", "folderSyncUsed 必须是布尔值")
        result["folderSyncUsed"] = patch["folderSyncUsed"]
    result["updatedAt"] = utc_now_iso()
    return result


class GitService:
    """Run git for the whole knowledge-base root and track global preferences."""

    def __init__(
        self,
        config: Any,
        environ: Mapping[str, str] | None = None,
        poll_seconds: float = 30.0,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self.on_change = on_change
        self.environ = dict(os.environ)
        if environ:
            self.environ.update({str(key): str(value) for key, value in environ.items()})
        self.poll_seconds = max(5.0, float(poll_seconds))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def root(self) -> Path:
        return Path(self.config.kb_root())

    def settings(self) -> dict[str, Any]:
        data = dict(self.config.git_settings())
        try:
            data["remoteDir"] = validate_remote_dir(data.get("remoteDir", ""))
        except ApiError as exc:
            # A hand-edited settings.json must never drive git commands directly.
            # Keep the error visible instead of silently switching to root mode.
            data["remoteDir"] = ""
            data["remoteDirError"] = exc.message
        return data

    def update_settings(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        before = str(self.settings().get("remoteDir", ""))
        self.config.update({"git": patch})
        after = self.settings()
        remote_dir = str(after.get("remoteDir", ""))
        if remote_dir and remote_dir != before:
            # Switching to another folder starts from a clean merge base.
            # Clearing the folder back to root mode keeps the snapshots so the
            # sync guard can reject the mode switch; `set_remote` is the
            # explicit reset.
            try:
                self._clear_folder_refs(self.root())
            except ApiError:
                # Defensive only: saving settings must not fail when git is not
                # installed or the repository is temporarily unavailable. The
                # snapshots are cleared again by the next `set_remote`.
                pass
        return after

    def _notify_change(self) -> None:
        if not self.on_change:
            return
        try:
            self.on_change()
        except Exception:
            pass

    def _record_attempt(self, at: str, direction: str) -> None:
        self.config.patch_git_state({"lastAttemptAt": at, "lastDirection": direction})

    def _record_result(self, status: str, message: str, direction: str) -> None:
        fields: dict[str, Any] = {
            "lastDirection": direction,
            "lastStatus": status,
            "lastMessage": _tail(message, 600),
        }
        if status == "ok":
            fields["lastSyncAt"] = utc_now_iso()
        self.config.patch_git_state(fields)

    # ------------------------------------------------------------------- git
    def _git(self) -> str:
        executable = shutil.which("git")
        if not executable:
            raise ApiError(400, "git_unavailable", "未找到 git，请先安装 Git 并确保它在 PATH 中")
        return executable

    def _env(self) -> dict[str, str]:
        env = dict(self.environ)
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
                "GIT_PAGER": "cat",
                "GIT_OPTIONAL_LOCKS": "0",
                "GH_PROMPT_DISABLED": "1",
                "GH_NO_UPDATE_NOTIFIER": "1",
                "GH_PAGER": "cat",
                "NO_COLOR": "1",
            }
        )
        return env

    def _env_value(self, key: str) -> str:
        if key in self.environ:
            return str(self.environ.get(key) or "")
        target = key.upper()
        for env_key, value in self.environ.items():
            if str(env_key).upper() == target:
                return str(value or "")
        return ""

    def _gh(self) -> str:
        override = self._env_value("KL_GH_PATH").strip()
        candidates: list[str] = [override] if override else []
        for name in ("gh", "gh.exe"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
        if os.name == "nt":
            for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
                base = self._env_value(key).strip()
                if base:
                    candidates.append(str(Path(base) / "GitHub CLI" / "gh.exe"))
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                if Path(candidate).is_file():
                    return candidate
            except OSError:
                continue
        raise ApiError(400, "gh_unavailable", "未找到 GitHub CLI（gh）；请安装 gh，或设置 KL_GH_PATH")

    def _run(
        self,
        args: list[str],
        cwd: Path,
        timeout: float = 120.0,
        executable: str | None = None,
        input_text: str | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[int, str, str]:
        program = executable or self._git()
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = self._env()
        if extra_env:
            env.update({str(key): str(value) for key, value in extra_env.items()})
        try:
            process = subprocess.run(
                [program, *args],
                cwd=str(cwd),
                env=env,
                input=input_text.encode("utf-8") if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                **options,
            )
        except subprocess.TimeoutExpired:
            raise ApiError(504, "git_timeout", f"命令超过 {int(timeout)} 秒未完成")
        except OSError as exc:
            raise ApiError(400, "git_unavailable", f"无法运行 {Path(program).name}：{exc}")
        return process.returncode, _decode(process.stdout), _decode(process.stderr)

    def _run_custom(self, parts: list[str], cwd: Path, timeout: float = 180.0) -> tuple[int, str, str]:
        if not parts:
            raise ApiError(400, "invalid_request", "自定义命令不能为空")
        program = Path(parts[0]).name.lower()
        if program in ("git", "git.exe"):
            executable = self._git()
        elif program in ("gh", "gh.exe"):
            executable = self._gh()
        else:
            raise ApiError(400, "invalid_request", "自定义命令必须以 git 或 gh 开头")
        return self._run(parts[1:], cwd, timeout, executable=executable)

    def _git_path(self, kb_dir: Path, name: str) -> Path:
        """Resolve a path inside the repository's git directory.

        `rev-parse --git-path` keeps working when `.git` is a file (linked
        worktrees) instead of assuming `<kb_dir>/.git` is a directory.
        """
        code, out, err = self._run(["rev-parse", "--git-path", name], kb_dir, timeout=15)
        if code != 0 or not out.strip():
            raise ApiError(500, "git_sync_failed", f"无法定位 Git 内部路径：{_tail(err or out)}")
        path = Path(out.strip())
        if not path.is_absolute():
            path = Path(kb_dir) / path
        return path

    def _git_dir(self, kb_dir: Path) -> Path:
        """Resolve the git directory once for internal file checks."""
        code, out, err = self._run(["rev-parse", "--git-dir"], kb_dir, timeout=15)
        if code != 0 or not out.strip():
            raise ApiError(500, "git_sync_failed", f"无法定位 Git 目录：{_tail(err or out)}")
        path = Path(out.strip())
        if not path.is_absolute():
            path = Path(kb_dir) / path
        return path

    @staticmethod
    def _discard_index(index_path: Path) -> None:
        """Best-effort removal of a temporary index file and its lock file.

        An interrupted git process can leave `<index>.lock` behind, which would
        block every later run that uses the same index path. The temporary
        indexes are per-process (`kl-*-{pid}.index`), so a leftover lock from a
        killed previous process can never block later pulls or pushes; the
        lock is still removed with its index to keep the git directory clean.
        """
        for candidate in (index_path, index_path.with_name(index_path.name + ".lock")):
            try:
                candidate.unlink()
            except OSError:
                pass

    def _managed(self, kb_dir: Path) -> bool:
        if not (Path(kb_dir) / ".git").exists():
            return False
        try:
            code, out, _ = self._run(["rev-parse", "--show-toplevel"], Path(kb_dir), timeout=15)
        except ApiError:
            return False
        if code != 0 or not out.strip():
            return False
        try:
            return Path(out.strip()).resolve() == Path(kb_dir).resolve()
        except OSError:
            return False

    def _ensure_repo(self, kb_dir: Path) -> None:
        if not (Path(kb_dir) / ".git").exists():
            raise ApiError(409, "git_not_initialized", "这个知识库还不是 Git 仓库，请先初始化")
        if not self._managed(kb_dir):
            raise ApiError(409, "git_not_initialized", "当前目录不是独立的 Git 仓库根目录")

    def _branch(self, kb_dir: Path) -> str:
        code, out, _ = self._run(["symbolic-ref", "--short", "-q", "HEAD"], kb_dir, timeout=15)
        if code == 0 and out.strip():
            return out.strip()
        code, out, _ = self._run(["rev-parse", "--abbrev-ref", "HEAD"], kb_dir, timeout=15)
        branch = out.strip() if code == 0 else ""
        return branch if branch and branch != "HEAD" else "main"

    def _remote_url(self, kb_dir: Path) -> str:
        code, out, _ = self._run(["remote", "get-url", "origin"], kb_dir, timeout=15)
        return out.strip() if code == 0 else ""

    def _upstream(self, kb_dir: Path) -> str:
        code, out, _ = self._run(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
            kb_dir,
            timeout=15,
        )
        return out.strip() if code == 0 else ""

    def _conflicts(self, kb_dir: Path) -> list[str]:
        code, out, _ = self._run(["diff", "--name-only", "--diff-filter=U"], kb_dir, timeout=15)
        if code != 0:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    def _unmerged_stages(
        self,
        kb_dir: Path,
        extra_env: Mapping[str, str] | None = None,
        strict: bool = False,
    ) -> dict[str, dict[int, tuple[str, str]]]:
        """Parse `ls-files -u` into path -> {stage: (mode, object)}.

        `strict` is used by the merge probe: a failing `ls-files` there means
        the probe cannot prove the merge is safe, so the pull must fail closed
        instead of assuming "no conflicts". Conflict resolution keeps the
        fail-open default because a partial listing must not lose data.
        """
        code, out, err = self._run(
            ["ls-files", "-u", "-z"], kb_dir, timeout=30, extra_env=extra_env
        )
        if code != 0:
            if strict:
                raise ApiError(409, "git_pull_failed", f"预检合并结果失败：{_tail(err or out)}")
            return {}
        if not out:
            return {}
        entries: dict[str, dict[int, tuple[str, str]]] = {}
        for record in out.split("\0"):
            if not record:
                continue
            meta, _, path = record.partition("\t")
            fields = meta.split(" ")
            if len(fields) < 3 or not path:
                continue
            entries.setdefault(path, {})[int(fields[2])] = (fields[0], fields[1])
        return entries

    def _merge_in_progress(self, kb_dir: Path) -> bool:
        try:
            git_dir = self._git_dir(kb_dir)
        except ApiError:
            return False
        return any(
            (git_dir / name).exists() for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply")
        )

    def _has_changes(self, kb_dir: Path) -> bool:
        code, out, _ = self._run(
            ["status", "--porcelain=v1", "--untracked-files=all"],
            kb_dir,
            timeout=20,
        )
        return code == 0 and bool(out.strip())

    def _ensure_identity(self, kb_dir: Path) -> None:
        for key, fallback in (("user.name", "KnowledgeLib"), ("user.email", "knowledgelib@localhost")):
            code, out, _ = self._run(["config", key], kb_dir, timeout=15)
            if code != 0 or not out.strip():
                code, _, err = self._run(["config", key, fallback], kb_dir, timeout=15)
                if code != 0:
                    raise ApiError(500, "git_config_failed", f"无法写入 Git 本地身份：{_tail(err)}")

    def _commit_all(self, kb_dir: Path, message: str) -> bool:
        self._ensure_identity(kb_dir)
        code, _, err = self._run(["add", "-A"], kb_dir, timeout=60)
        if code != 0:
            raise ApiError(500, "git_commit_failed", f"暂存文件失败：{_tail(err)}")
        code, out, err = self._run(["-c", "commit.gpgsign=false", "commit", "-m", message], kb_dir, timeout=120)
        if code == 0:
            return True
        combined = (out + "\n" + err).lower()
        if "nothing to commit" in combined or "no changes added" in combined:
            return False
        raise ApiError(500, "git_commit_failed", f"创建提交失败：{_tail(err or out)}")

    # ----------------------------------------------------------------- status
    def _kb_count(self, root: Path) -> int:
        try:
            return sum(
                1
                for child in root.iterdir()
                if child.is_dir() and not child.name.startswith(".") and (child / ".kb.json").is_file()
            )
        except OSError:
            return 0

    def _legacy_repos(self, root: Path) -> list[str]:
        try:
            return sorted(
                child.name
                for child in root.iterdir()
                if child.is_dir() and not child.name.startswith(".") and (child / ".git").exists()
            )
        except OSError:
            return []

    def status(self) -> dict[str, Any]:
        root = self.root()
        settings = self.settings()
        remote_dir = str(settings.get("remoteDir") or "")
        remote_dir_error = str(settings.get("remoteDirError") or "")
        mode_switch_warning = ""
        if not remote_dir and not remote_dir_error:
            # Folder mode is remembered even after its refs/kl snapshots were
            # cleared: root mode would merge the whole remote repository into
            # the library root, so a stale folder history must keep blocking.
            if bool(settings.get("folderSyncUsed")):
                mode_switch_warning = _MODE_SWITCH_WARNING
            else:
                try:
                    if self._has_folder_refs(root):
                        mode_switch_warning = _MODE_SWITCH_WARNING
                except ApiError:
                    mode_switch_warning = ""
        result: dict[str, Any] = {
            "available": bool(shutil.which("git")),
            "managed": False,
            "root": str(root),
            "remoteDir": remote_dir,
            "remoteDirError": remote_dir_error,
            "modeSwitchWarning": mode_switch_warning,
            "folderMode": bool(remote_dir),
            "kbCount": self._kb_count(root),
            "legacyRepos": self._legacy_repos(root),
            "branch": "",
            "remoteUrl": "",
            "upstream": "",
            "dirty": False,
            "changedFiles": 0,
            "changed": [],
            "ahead": None,
            "behind": None,
            "conflicts": [],
            "mergeInProgress": False,
            "lastCommit": None,
            "settings": settings,
            "lastSyncAt": settings.get("lastSyncAt", ""),
            "lastStatus": settings.get("lastStatus", "never"),
            "lastMessage": settings.get("lastMessage", ""),
            "nextAutoSyncAt": self._next_sync_at(settings),
            "error": "",
        }
        if not result["available"]:
            result["error"] = "未找到 git，请先安装 Git 并确保它在 PATH 中"
            return result
        if result["legacyRepos"]:
            result["error"] = "检测到知识库目录内仍有独立 Git 仓库；请迁移为文库集仓库后再同步"
            return result
        if not self._managed(root):
            return result
        result["managed"] = True
        try:
            branch = self._branch(root)
            result["branch"] = branch
            result["remoteUrl"] = self._remote_url(root)
            if remote_dir:
                result["upstream"] = (
                    f"origin/{branch}"
                    if self._rev(root, f"refs/remotes/origin/{branch}")
                    else ""
                )
                self._folder_progress(root, branch, remote_dir, result["remoteUrl"], result)
            else:
                result["upstream"] = self._upstream(root)
            code, out, _ = self._run(
                ["status", "--porcelain=v1", "--untracked-files=all"],
                root,
                timeout=20,
            )
            if code == 0:
                lines = [line for line in out.splitlines() if line.strip()]
                result["changedFiles"] = len(lines)
                result["dirty"] = bool(lines)
                result["changed"] = [line[3:] if len(line) > 3 else line for line in lines[:20]]
            result["conflicts"] = self._conflicts(root)
            result["mergeInProgress"] = self._merge_in_progress(root)
            if result["upstream"] and not remote_dir:
                code, out, _ = self._run(
                    ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                    root,
                    timeout=20,
                )
                if code == 0:
                    parts = out.split()
                    if len(parts) == 2:
                        result["ahead"] = int(parts[0])
                        result["behind"] = int(parts[1])
            code, out, _ = self._run(
                ["log", "-1", "--format=%H%x00%h%x00%s%x00%cI"],
                root,
                timeout=15,
            )
            if code == 0 and out.strip():
                parts = out.strip("\n").split("\x00")
                if len(parts) >= 4:
                    result["lastCommit"] = {
                        "hash": parts[0],
                        "short": parts[1],
                        "subject": parts[2],
                        "at": parts[3],
                    }
        except ApiError as exc:
            result["error"] = exc.message
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"读取 Git 状态失败：{exc}"
        return result

    def _folder_progress(
        self,
        root: Path,
        branch: str,
        remote_dir: str,
        remote_url: str,
        result: dict[str, Any],
    ) -> None:
        """Fill ahead/behind for folder mode using local sync markers.

        Local and remote histories have different trees (root vs. subfolder), so
        a plain HEAD...upstream comparison would be meaningless.
        """
        _, tip_ref, local_ref = self._folder_refs(branch, remote_dir, remote_url)
        local_marker = self._rev(root, local_ref)
        if local_marker:
            code, out, _ = self._run(
                ["rev-list", "--first-parent", "--count", f"{local_ref}..HEAD"],
                root,
                timeout=20,
            )
            if code == 0 and out.strip().isdigit():
                result["ahead"] = int(out.strip())
        remote_tip = self._rev(root, tip_ref)
        remote_head = self._rev(root, f"refs/remotes/origin/{branch}")
        if remote_tip and remote_head:
            code, _, _ = self._run(
                ["merge-base", "--is-ancestor", remote_tip, remote_head], root, timeout=20
            )
            if code == 0:
                code, out, _ = self._run(
                    ["rev-list", "--count", f"{remote_tip}..{remote_head}"], root, timeout=20
                )
                if code == 0 and out.strip().isdigit():
                    result["behind"] = int(out.strip())

    @staticmethod
    def _next_sync_at(settings: Mapping[str, Any]) -> str:
        if not settings.get("autoSync"):
            return ""
        base = _parse_time(settings.get("lastAttemptAt")) or _parse_time(settings.get("updatedAt"))
        if not base:
            return ""
        minutes = int(settings.get("intervalMinutes") or 15)
        return _iso(base + timedelta(minutes=minutes))

    # ------------------------------------------------------------------- init
    def _archive_legacy_repos(self, root: Path, names: list[str]) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        trash = Path(self.config.data_dir) / "trash" / "git-repos" / stamp
        for name in names:
            source = root / name / ".git"
            target = trash / name / ".git"
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(source), str(target))
            except OSError as exc:
                raise ApiError(500, "git_migrate_failed", f"备份旧仓库 {name}/.git 失败：{exc}")
        return trash

    def init_repo(
        self,
        remote_url: str = "",
        branch: str = "main",
        migrate_legacy: bool = False,
    ) -> dict[str, Any]:
        root = self.root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ApiError(400, "invalid_path", f"知识库根目录不可用：{exc}")
        branch = validate_branch(branch)
        remote = validate_remote_url(remote_url) if (remote_url or "").strip() else ""
        legacy = self._legacy_repos(root)
        if legacy:
            if not migrate_legacy:
                raise ApiError(
                    409,
                    "git_legacy_repos",
                    "检测到知识库目录内仍有独立 Git 仓库；请先备份，或勾选迁移后重试",
                    {"repos": legacy},
                )
            self._archive_legacy_repos(root, legacy)
        if (root / ".git").exists():
            if not self._managed(root):
                raise ApiError(409, "git_conflict", "目录中存在无法识别的 .git，请先手动处理")
            if remote:
                return self.set_remote(remote)
            return self.status()
        code, out, err = self._run(["init", "-b", branch], root, timeout=60)
        if code != 0:
            code, out, err = self._run(["init"], root, timeout=60)
            if code != 0:
                raise ApiError(500, "git_init_failed", f"初始化 Git 仓库失败：{_tail(err or out)}")
            self._run(["checkout", "-b", branch], root, timeout=30)
        self._run(["config", "core.quotepath", "false"], root, timeout=15)
        self._ensure_identity(root)
        ignore = root / ".gitignore"
        if not ignore.exists():
            ignore.write_text(".DS_Store\nThumbs.db\n*.tmp\n", encoding="utf-8")
        if remote:
            code, out, err = self._run(["remote", "add", "origin", remote], root, timeout=20)
            if code != 0:
                raise ApiError(502, "git_remote_failed", f"添加 remote 失败：{_tail(err or out)}")
        self._commit_all(root, "Initialize KnowledgeLib library")
        return self.status()

    def set_remote(self, url: str) -> dict[str, Any]:
        root = self.root()
        self._ensure_repo(root)
        remote = validate_remote_url(url)
        code, _, _ = self._run(["remote", "get-url", "origin"], root, timeout=15)
        if code == 0:
            code, out, err = self._run(["remote", "set-url", "origin", remote], root, timeout=20)
        else:
            code, out, err = self._run(["remote", "add", "origin", remote], root, timeout=20)
        if code != 0:
            raise ApiError(502, "git_remote_failed", f"保存 remote 失败：{_tail(err or out)}")
        # Saving the remote explicitly resets folder snapshots. This is also the
        # escape hatch after a repository is recreated or force-pushed, so the
        # folder-mode marker must be cleared here as well.
        self._clear_folder_refs(root)
        self.config.patch_git_state({"folderSyncUsed": False})
        return self.status()

    # ------------------------------------------------------------------- sync
    def sync(
        self,
        direction: str = "sync",
        strategy: str | None = None,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        if direction not in DIRECTIONS:
            raise ApiError(400, "invalid_request", "同步方向不合法")
        settings = self.settings()
        chosen = strategy or settings["strategy"]
        if chosen not in STRATEGIES:
            raise ApiError(400, "invalid_request", "冲突处理策略不合法")
        if not self._lock.acquire(blocking=False):
            raise ApiError(409, "git_busy", "文库集正在同步，请稍后再试")
        try:
            self._record_attempt(utc_now_iso(), direction)
            result = self._sync_locked(self.root(), direction, chosen, trigger, settings)
            self._notify_change()
            self._record_result("ok", result.get("message", "同步完成"), direction)
            return result
        except ApiError as exc:
            self._record_result("error", exc.message, direction)
            raise
        except Exception as exc:  # noqa: BLE001
            message = f"同步失败：{exc}"
            self._record_result("error", message, direction)
            raise ApiError(500, "git_sync_failed", message)
        finally:
            self._lock.release()

    def _sync_locked(
        self,
        kb_dir: Path,
        direction: str,
        strategy: str,
        trigger: str,
        settings: Mapping[str, Any],
    ) -> dict[str, Any]:
        if settings.get("remoteDirError"):
            raise ApiError(
                409,
                "git_settings_invalid",
                f"「仓库内目录」设置不合法：{settings['remoteDirError']}；请重新填写并保存同步设置",
            )
        self._ensure_repo(kb_dir)
        if self._legacy_repos(kb_dir):
            raise ApiError(
                409,
                "git_legacy_repos",
                "检测到知识库目录内仍有独立 Git 仓库；请先迁移为文库集仓库",
                {"repos": self._legacy_repos(kb_dir)},
            )
        remote = self._remote_url(kb_dir)
        if not remote:
            raise ApiError(409, "git_no_remote", "尚未添加 Git remote，请先填写远端地址")
        branch = self._branch(kb_dir)
        remote_dir = str(settings.get("remoteDir") or "")
        if remote_dir:
            # Persist the folder-mode marker on every folder sync. It is the
            # guard of last resort when the refs/kl snapshots are later cleared
            # (for example after switching remoteDir to a different folder and
            # back to root mode) but folder mode was definitely used before.
            self.config.patch_git_state({"folderSyncUsed": True})
        if not remote_dir and (
            self._has_folder_refs(kb_dir) or bool(settings.get("folderSyncUsed"))
        ):
            # Root mode would merge the whole remote repository (including the
            # old library folder) into the library; refuse instead.
            raise ApiError(409, "git_mode_switch", _MODE_SWITCH_WARNING)
        summary: list[str] = []
        commit_created = False
        conflicts_resolved = 0
        pulled = False

        if self._merge_in_progress(kb_dir) or self._conflicts(kb_dir):
            if strategy == "abort":
                self._abort_merge(kb_dir)
                raise ApiError(409, "git_conflict", "存在未解决的 Git 冲突，已中止合并并保留本地文件")
            if strategy == "manual":
                raise ApiError(
                    409,
                    "git_conflict",
                    "存在未解决的 Git 冲突，请手工处理后再同步",
                    {"conflicts": self._conflicts(kb_dir)},
                )
            conflicts_resolved += self._resolve_conflicts(kb_dir, strategy)
            summary.append(
                f"处理 {conflicts_resolved} 个冲突（优先{'本地' if strategy == 'local' else '远端'}）"
            )
            if remote_dir:
                # The resolved tree already contains the remote side; advance the
                # folder merge base so the following pull does not re-apply it.
                self._remember_resolved_remote(kb_dir, branch, remote_dir, remote)

        if self._has_changes(kb_dir):
            if not settings.get("autoCommit", True):
                raise ApiError(409, "git_dirty", "本地有未提交改动；请提交，或开启自动提交后再同步")
            commit_created = self._commit_all(kb_dir, f"KnowledgeLib: sync {utc_now_iso()}")
            if commit_created:
                summary.append("已提交本地改动")

        if direction in ("pull", "sync"):
            pull = self._pull(kb_dir, branch, strategy, remote, settings, remote_dir)
            pulled = bool(pull.get("pulled"))
            conflicts_resolved += int(pull.get("conflictsResolved") or 0)
            if pull.get("message"):
                summary.append(pull["message"])

        if direction in ("push", "sync"):
            # A custom pull command may not fetch, so only reuse the refs when
            # the built-in pull path already ran `git fetch`.
            can_reuse_fetch = direction == "sync" and not (
                (settings.get("pullCommand") or "").strip()
            )
            push = self._push(
                kb_dir, branch, settings, remote, remote_dir, skip_fetch=can_reuse_fetch
            )
            if push.get("message"):
                summary.append(push["message"])

        if not summary:
            summary.append("没有需要同步的改动")
        return {
            "ok": True,
            "direction": direction,
            "trigger": trigger,
            "branch": branch,
            "commitCreated": commit_created,
            "pulled": pulled,
            "conflictsResolved": conflicts_resolved,
            "message": "；".join(summary),
        }

    def _abort_merge(self, kb_dir: Path) -> None:
        try:
            git_dir = self._git_dir(kb_dir)
        except ApiError:
            return
        if (git_dir / "MERGE_HEAD").exists():
            self._run(["merge", "--abort"], kb_dir, timeout=60)
        elif (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
            self._run(["rebase", "--abort"], kb_dir, timeout=60)

    def _resolve_conflicts(self, kb_dir: Path, strategy: str) -> int:
        side = "ours" if strategy == "local" else "theirs"
        selected_stage = 2 if strategy == "local" else 3
        conflicts = self._conflicts(kb_dir)
        if not conflicts:
            return 0
        stages = self._unmerged_stages(kb_dir)
        if selected_stage == 3:
            # "Prefer remote" must not check out a path where the remote side
            # replaced a directory with a file: git would delete the local
            # directory and every local file below it before the per-path
            # guards in the loop can keep the conflict. Refuse up front, before
            # touching the index or working tree, so the fallback stays safe
            # even when the D/F probe was skipped or missed the clash.
            remote_files = [
                path
                for path in self._path_type_conflicts(stages)
                if 3 in stages.get(path, {})
            ]
            if remote_files:
                shown = "、".join(remote_files[:3])
                raise ApiError(
                    409,
                    "git_conflict",
                    f"远端与本地存在文件/目录互换（{shown}），已中止以避免误删；"
                    "请先调整后再同步；若远端没有变化，可直接使用「推送」",
                    {"conflicts": remote_files, "reason": "path_type"},
                )
        for path in conflicts:
            code, _, _ = self._run(["checkout", f"--{side}", "--", path], kb_dir, timeout=30)
            if code == 0:
                continue
            path_stages = stages.get(path, {})
            if selected_stage in path_stages:
                # The selected side still has content but git refused to write
                # it (for example a file/directory clash). Leave it unmerged so
                # the check below reports a conflict instead of losing data.
                continue
            if any(other.startswith(path + "/") for other in stages):
                # Other unmerged paths live below this one, so this is a
                # file/directory prefix clash. Removing `path` here would wipe
                # the local side and could cascade into deleting the whole
                # directory. Leave it unmerged and let the check below report
                # a conflict; this guard also covers a D/F probe that was
                # skipped or missed the clash.
                continue
            if (Path(kb_dir) / path).is_dir():
                # The selected side removed this file while the local side
                # turned it into a directory. Never delete the directory: the
                # following `git add -A` stages the local tree as-is.
                continue
            if 1 not in path_stages and 2 in path_stages:
                # The merge base had no file here but the local side has one,
                # which means the remote side has a directory at this path.
                # "Prefer remote" would have to delete the local file to make
                # room for it, and because the paths below it may already have
                # been resolved as one-sided deletions, the remote directory
                # would not even be restored. Refuse instead of silently
                # dropping local content; this is the fallback for a D/F
                # precheck that was skipped or missed the clash.
                raise ApiError(
                    409,
                    "git_conflict",
                    f"远端在 {path} 是目录、本地是文件，无法自动应用「优先远端」；"
                    "请先调整后再同步；若远端没有变化，可直接使用「推送」",
                    {"conflicts": [path], "reason": "path_type"},
                )
            self._run(["rm", "-f", "--ignore-unmatch", "--", path], kb_dir, timeout=30)
        self._run(["add", "-A"], kb_dir, timeout=60)
        remaining = self._conflicts(kb_dir)
        if remaining:
            raise ApiError(
                409,
                "git_conflict",
                "仍有冲突无法自动解决，请手工处理",
                {"conflicts": remaining},
            )
        self._ensure_identity(kb_dir)
        if self._git_path(kb_dir, "MERGE_HEAD").exists():
            code, out, err = self._run(
                [
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-m",
                    f"KnowledgeLib: resolve conflicts (prefer {side})",
                ],
                kb_dir,
                timeout=120,
            )
            if code != 0:
                raise ApiError(409, "git_conflict", f"提交冲突解决结果失败：{_tail(err or out)}")
        return len(conflicts)

    # -------------------------------------------------- remote folder layout
    def _rev(self, kb_dir: Path, spec: str, timeout: float = 15.0) -> str:
        code, out, _ = self._run(["rev-parse", "--verify", "--quiet", spec], kb_dir, timeout=timeout)
        return out.strip() if code == 0 else ""

    def _is_ancestor(self, kb_dir: Path, ancestor: str, descendant: str = "HEAD") -> bool:
        code, _, _ = self._run(
            ["merge-base", "--is-ancestor", ancestor, descendant], kb_dir, timeout=20
        )
        return code == 0

    def _path_type(self, kb_dir: Path, spec: str) -> str:
        code, out, _ = self._run(["cat-file", "-t", spec], kb_dir, timeout=15)
        return out.strip() if code == 0 else ""

    def _update_head_ref(self, kb_dir: Path, branch: str, commit: str) -> None:
        code, _, _ = self._run(["symbolic-ref", "-q", "HEAD"], kb_dir, timeout=10)
        target = f"refs/heads/{branch}" if code == 0 else "HEAD"
        code, _, err = self._run(["update-ref", target, commit], kb_dir, timeout=20)
        if code != 0:
            raise ApiError(500, "git_sync_failed", f"更新本地分支失败：{_tail(err)}")

    def _has_folder_refs(self, kb_dir: Path) -> bool:
        code, out, _ = self._run(
            ["for-each-ref", "--format=%(refname)", "refs/kl"], kb_dir, timeout=15
        )
        return code == 0 and bool(out.strip())

    def _clear_folder_refs(self, kb_dir: Path) -> None:
        code, out, _ = self._run(
            ["for-each-ref", "--format=%(refname)", "refs/kl"], kb_dir, timeout=15
        )
        if code != 0:
            return
        for ref in out.splitlines():
            ref = ref.strip()
            if ref:
                self._run(["update-ref", "-d", ref], kb_dir, timeout=15)

    def _remote_history_replaced(
        self,
        kb_dir: Path,
        branch: str,
        remote_dir: str,
        remote_url: str,
        remote_commit: str,
    ) -> bool:
        if not remote_commit:
            return False
        _, tip_ref, _ = self._folder_refs(branch, remote_dir, remote_url)
        old_tip = self._rev(kb_dir, tip_ref)
        if not old_tip:
            return False
        return not self._is_ancestor(kb_dir, old_tip, remote_commit)

    def _folder_refs(self, branch: str, remote_dir: str, remote_url: str) -> tuple[str, str, str]:
        key = hashlib.sha1(
            f"{remote_url}\n{branch}\n{remote_dir}".encode("utf-8")
        ).hexdigest()[:16]
        return (f"refs/kl/base/{key}", f"refs/kl/tip/{key}", f"refs/kl/local/{key}")

    def _update_ref(self, kb_dir: Path, name: str, value: str) -> None:
        code, _, err = self._run(["update-ref", name, value], kb_dir, timeout=20)
        if code != 0:
            raise ApiError(500, "git_sync_failed", f"更新 Git 同步标记失败：{_tail(err)}")

    def _empty_tree(self, kb_dir: Path) -> str:
        code, out, err = self._run(["mktree"], kb_dir, timeout=15, input_text="")
        if code != 0 or not out.strip():
            raise ApiError(500, "git_sync_failed", f"创建空 Git 树失败：{_tail(err or out)}")
        return out.strip()

    def _record_folder_state(
        self,
        kb_dir: Path,
        branch: str,
        remote_dir: str,
        remote_url: str,
        remote_commit: str,
        remote_tree: str,
        advance_local: bool = True,
    ) -> None:
        """Remember the remote tree root so the next pull has a merge base.

        `advance_local` must stay False on pull-only paths: the local content is
        not on the remote yet, so the local sync marker must not claim it is.
        A snapshot commit is reused when the remote tree did not change.
        """
        base_ref, tip_ref, local_ref = self._folder_refs(branch, remote_dir, remote_url)
        old_base = self._rev(kb_dir, base_ref)
        old_base_tree = self._rev(kb_dir, f"{base_ref}^{{tree}}") if old_base else ""
        if old_base and old_base_tree == remote_tree:
            snapshot = old_base
        else:
            args = ["commit-tree", remote_tree]
            if old_base:
                args += ["-p", old_base]
            args += ["-m", f"KnowledgeLib: remote snapshot {remote_dir}"]
            code, out, err = self._run(args, kb_dir, timeout=30)
            snapshot = out.strip() if code == 0 else ""
            if not snapshot:
                raise ApiError(500, "git_sync_failed", f"记录远端目录快照失败：{_tail(err or out)}")
            self._update_ref(kb_dir, base_ref, snapshot)
        if remote_commit and self._rev(kb_dir, tip_ref) != remote_commit:
            self._update_ref(kb_dir, tip_ref, remote_commit)
        if advance_local:
            head = self._rev(kb_dir, "HEAD")
            if head and self._rev(kb_dir, local_ref) != head:
                self._update_ref(kb_dir, local_ref, head)

    def _remember_resolved_remote(
        self, kb_dir: Path, branch: str, remote_dir: str, remote_url: str
    ) -> None:
        remote_ref = f"refs/remotes/origin/{branch}"
        remote_commit = self._rev(kb_dir, remote_ref)
        if not remote_commit:
            return
        remote_tree = self._rev(kb_dir, f"{remote_ref}:{remote_dir}") or self._empty_tree(kb_dir)
        self._record_folder_state(
            kb_dir,
            branch,
            remote_dir,
            remote_url,
            remote_commit,
            remote_tree,
            advance_local=self._rev(kb_dir, "HEAD^{tree}") == remote_tree,
        )

    def _build_remote_tree(self, kb_dir: Path, remote_commit: str, remote_dir: str) -> str:
        """Build a tree where the library root is placed at remote_dir/.

        The rest of the remote tree (README, other folders, ...) is preserved.
        """
        index_path = self._git_path(kb_dir, f"kl-sync-{os.getpid()}.index")
        index_env = {"GIT_INDEX_FILE": str(index_path)}
        try:
            self._discard_index(index_path)
            if remote_commit:
                code, out, err = self._run(["read-tree", remote_commit], kb_dir, timeout=60, extra_env=index_env)
            else:
                code, out, err = self._run(["read-tree", "--empty"], kb_dir, timeout=60, extra_env=index_env)
            if code != 0:
                raise ApiError(500, "git_sync_failed", f"读取远端目录树失败：{_tail(err or out)}")
            code, out, err = self._run(
                ["rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", remote_dir],
                kb_dir,
                timeout=60,
                extra_env=index_env,
            )
            if code != 0:
                raise ApiError(500, "git_sync_failed", f"替换远端目录失败：{_tail(err or out)}")
            local_tree = self._rev(kb_dir, "HEAD^{tree}")
            code, out, err = self._run(
                ["read-tree", f"--prefix={remote_dir}/", local_tree],
                kb_dir,
                timeout=60,
                extra_env=index_env,
            )
            if code != 0:
                raise ApiError(500, "git_sync_failed", f"写入远端目录失败：{_tail(err or out)}")
            code, out, err = self._run(["write-tree"], kb_dir, timeout=30, extra_env=index_env)
            if code != 0 or not out.strip():
                raise ApiError(500, "git_sync_failed", f"生成远端目录树失败：{_tail(err or out)}")
            return out.strip()
        finally:
            self._discard_index(index_path)

    def _write_merge_head(self, kb_dir: Path, commit: str) -> None:
        try:
            self._git_path(kb_dir, "MERGE_HEAD").write_text(commit + "\n", encoding="utf-8")
            self._git_path(kb_dir, "MERGE_MSG").write_text(
                "KnowledgeLib: merge remote directory\n", encoding="utf-8"
            )
        except OSError as exc:
            raise ApiError(500, "git_sync_failed", f"无法写入合并状态：{exc}")

    def _resolve_trivial_merges(self, kb_dir: Path) -> int:
        """Resolve conflicts where only one side changed.

        `git read-tree -m` keeps these as unmerged entries (unlike `git merge`),
        so deleting or changing a file on only one side would otherwise look like
        a conflict.
        """
        entries = self._unmerged_stages(kb_dir)
        if not entries:
            return 0
        resolved = 0
        for path, stages in entries.items():
            base = stages.get(1)
            ours = stages.get(2)
            theirs = stages.get(3)
            side = ""
            if base is not None:
                if ours == base:
                    side = "theirs"
                elif theirs == base:
                    side = "ours"
            elif ours is not None and ours == theirs:
                side = "ours"
            if not side:
                if ours is None and theirs is None and base is not None:
                    side = "ours"
                else:
                    continue
            desired = stages.get(3 if side == "theirs" else 2)
            if desired is None:
                code, _, _ = self._run(["rm", "-f", "--ignore-unmatch", "--", path], kb_dir, timeout=30)
                applied = code == 0
            else:
                # Type changes (blob <-> symlink) keep both stages, so the
                # chosen side has to be written explicitly here.
                code, _, _ = self._run(["checkout", f"--{side}", "--", path], kb_dir, timeout=30)
                applied = code == 0
                if applied:
                    code, _, _ = self._run(["add", "--", path], kb_dir, timeout=30)
                    applied = code == 0
            if applied:
                # A path that could not be written stays unmerged and is
                # reported by the conflict check that follows the pull.
                resolved += 1
        return resolved

    def _mark_conflicts(self, kb_dir: Path, conflicts: list[str]) -> None:
        for path in conflicts:
            # checkout --conflict=merge writes familiar conflict markers; blobs
            # that cannot be merged are left untouched.
            self._run(["checkout", "--conflict=merge", "--", path], kb_dir, timeout=30)

    @staticmethod
    def _path_type_conflicts(unmerged: Mapping[str, Any]) -> list[str]:
        """Return the paths that are a file/directory swap.

        A merge that replaces a file with a directory (or the reverse) leaves
        both the file path and the paths below the directory unmerged. Only the
        swapped node itself is reported: it is the path that is a prefix
        directory of another unmerged path. The nested paths are implied and
        would only add noise to the error details.
        """
        paths = sorted(unmerged)
        return sorted(
            path for path in paths
            if any(other.startswith(path + "/") for other in paths)
        )

    def _merge_probe_unmerged(
        self,
        kb_dir: Path,
        base_tree: str,
        remote_tree: str,
    ) -> dict[str, dict[int, tuple[str, str]]]:
        """Run the three-way merge in a throwaway index.

        The probe never passes `-u`, so neither the index nor the working tree
        of the repository is modified. Refresh is required because the next
        `read-tree -m` refuses to merge an index with stale stat information.

        The index name is unique per process: a leftover `*.index.lock` from a
        killed previous process therefore cannot permanently block later pulls.
        A failing `ls-files -u` fails the probe closed, because an unreadable
        conflict list must not be mistaken for "no conflicts".
        """
        index_path = self._git_path(kb_dir, f"kl-probe-{os.getpid()}.index")
        probe_env = {"GIT_INDEX_FILE": str(index_path)}
        try:
            self._discard_index(index_path)
            code, out, err = self._run(
                ["read-tree", "--reset", "HEAD"], kb_dir, timeout=60, extra_env=probe_env
            )
            if code != 0:
                raise ApiError(409, "git_pull_failed", f"预检合并结果失败：{_tail(err or out)}")
            self._run(["update-index", "--refresh"], kb_dir, timeout=60, extra_env=probe_env)
            code, out, err = self._run(
                ["read-tree", "-m", base_tree, "HEAD", remote_tree],
                kb_dir,
                timeout=120,
                extra_env=probe_env,
            )
            if code != 0:
                raise ApiError(409, "git_pull_failed", f"预检合并结果失败：{_tail(err or out)}")
            return self._unmerged_stages(kb_dir, extra_env=probe_env, strict=True)
        finally:
            self._discard_index(index_path)

    def _unset_upstream(self, kb_dir: Path, branch: str) -> None:
        """Drop the branch upstream that root mode left behind via `push -u`.

        Folder mode never pushes the local branch: it builds a synthetic commit
        from the remote tree and pushes that, so a leftover upstream would make
        a bare `git push` in the library directory send local `main` (root
        layout) over the remote branch and silently overwrite the folder
        layout. Removing the upstream makes such accidental pushes fail. The
        command is best-effort: the branch may have no upstream configured.
        """
        self._run(["branch", "--unset-upstream", branch], kb_dir, timeout=15)

    def _pull_folder(
        self,
        kb_dir: Path,
        branch: str,
        strategy: str,
        remote_url: str,
        remote_dir: str,
    ) -> dict[str, Any]:
        self._unset_upstream(kb_dir, branch)
        code, out, err = self._run(["fetch", "origin", "--prune"], kb_dir, timeout=180)
        if code != 0:
            raise self._remote_error("git_fetch_failed", "拉取远端失败", out, err, remote_url)
        remote_ref = f"refs/remotes/origin/{branch}"
        remote_commit = self._rev(kb_dir, remote_ref)
        base_ref, tip_ref, _ = self._folder_refs(branch, remote_dir, remote_url)
        if not remote_commit:
            if self._rev(kb_dir, tip_ref):
                # The folder was synced before but the remote branch is gone:
                # treat it as replaced history instead of re-importing.
                raise ApiError(409, "git_remote_rewritten", _REMOTE_BRANCH_GONE)
            return {"pulled": False, "message": "远端还没有该分支，已跳过拉取"}
        empty_tree = self._empty_tree(kb_dir)
        base_commit = self._rev(kb_dir, base_ref)
        base_tree = self._rev(kb_dir, f"{base_ref}^{{tree}}") if base_commit else empty_tree
        if self._remote_history_replaced(
            kb_dir, branch, remote_dir, remote_url, remote_commit
        ):
            raise ApiError(
                409,
                "git_remote_rewritten",
                "远端历史已变化（仓库可能被重建或强推）；请确认后重新保存 remote 以重置同步快照，或更换仓库内目录",
            )
        remote_tree = self._rev(kb_dir, f"{remote_ref}:{remote_dir}")
        removed = False
        if not remote_tree:
            # An unresolvable `<remote_dir>` is normally a remotely deleted
            # folder. It can also mean a file now sits at one of its ancestor
            # paths (for example `kb` replaced by a blob while `kb/sub` is the
            # configured folder); treating that as a deletion would wipe the
            # local library, so fail closed instead.
            parts = remote_dir.split("/")
            for index in range(1, len(parts)):
                prefix = "/".join(parts[:index])
                kind = self._path_type(kb_dir, f"{remote_ref}:{prefix}")
                if kind and kind != "tree":
                    raise ApiError(
                        409,
                        "git_pull_failed",
                        f"远端的 {prefix} 不是文件夹，无法拉取 {remote_dir}/",
                    )
            if not base_commit:
                return {"pulled": False, "message": f"远端还没有 {remote_dir}/ 目录，已跳过拉取"}
            if base_tree == empty_tree:
                return {"pulled": False, "message": f"远端 {remote_dir}/ 为空（无变化）"}
            # The folder existed at the last sync and was deleted remotely.
            remote_tree = empty_tree
            removed = True
        code, out, _ = self._run(["cat-file", "-t", remote_tree], kb_dir, timeout=15)
        if out.strip() != "tree":
            raise ApiError(409, "git_pull_failed", f"远端的 {remote_dir} 不是文件夹，无法拉取")
        if base_commit and self._is_ancestor(kb_dir, remote_commit):
            # The user resolved a conflict manually, or this is our own merge
            # commit; the remote side is already part of HEAD.
            self._record_folder_state(
                kb_dir,
                branch,
                remote_dir,
                remote_url,
                remote_commit,
                remote_tree,
                advance_local=self._rev(kb_dir, "HEAD^{tree}") == remote_tree,
            )
            return {"pulled": False, "message": f"远端目录已合并到本地（origin/{branch}:{remote_dir}/）"}
        self._ensure_identity(kb_dir)
        if not self._rev(kb_dir, "HEAD"):
            code, out, err = self._run(
                [
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "--allow-empty",
                    "-m",
                    "KnowledgeLib: initialize repository",
                ],
                kb_dir,
                timeout=60,
            )
            if code != 0:
                raise ApiError(
                    500,
                    "git_sync_failed",
                    f"初始化空仓库提交失败：{_tail(err or out)}",
                )
        head_tree = self._rev(kb_dir, "HEAD^{tree}")
        if base_tree != remote_tree:
            # Probe the merge in a throwaway index before anything touches the
            # working tree: a remote-driven file <-> directory swap cannot be
            # resolved safely and applying it would delete local content for
            # every strategy. When the remote tree is identical to the merge
            # base the swap is a single-sided local change: the three-way merge
            # below keeps it, and `_resolve_conflicts` refuses to delete it.
            probe_unmerged = self._merge_probe_unmerged(kb_dir, base_tree, remote_tree)
            path_type_conflicts = self._path_type_conflicts(probe_unmerged)
            if path_type_conflicts:
                shown = "、".join(path_type_conflicts[:3])
                raise ApiError(
                    409,
                    "git_conflict",
                    f"远端与本地存在文件/目录互换（{shown}），已中止以避免误删；"
                    "请先调整后再同步；若远端没有变化，可直接使用「推送」",
                    {"conflicts": path_type_conflicts, "reason": "path_type"},
                )
        code, out, err = self._run(["read-tree", "--reset", "-u", "HEAD"], kb_dir, timeout=60)
        if code != 0:
            raise ApiError(409, "git_pull_failed", f"重置工作区失败：{_tail(err or out)}")
        code, out, err = self._run(
            ["read-tree", "-m", "-u", base_tree, "HEAD", remote_tree],
            kb_dir,
            timeout=120,
        )
        if code != 0:
            self._run(["read-tree", "--reset", "-u", "HEAD"], kb_dir, timeout=60)
            raise ApiError(409, "git_pull_failed", f"合并远端目录失败：{_tail(err or out, 300)}")
        self._resolve_trivial_merges(kb_dir)
        conflicts = self._conflicts(kb_dir)
        pulled = False
        resolved = 0
        if conflicts:
            if strategy == "abort":
                self._run(["read-tree", "--reset", "-u", "HEAD"], kb_dir, timeout=60)
                raise ApiError(
                    409,
                    "git_conflict",
                    f"拉取 {remote_dir}/ 产生冲突，已中止合并并保留本地文件",
                    {"conflicts": conflicts},
                )
            self._write_merge_head(kb_dir, remote_commit)
            if strategy == "manual":
                self._mark_conflicts(kb_dir, conflicts)
                raise ApiError(
                    409,
                    "git_conflict",
                    f"拉取 {remote_dir}/ 产生冲突，请手工处理后再次同步",
                    {"conflicts": conflicts},
                )
            resolved = self._resolve_conflicts(kb_dir, strategy)
            pulled = True
        else:
            code, out, err = self._run(["write-tree"], kb_dir, timeout=30)
            merged_tree = out.strip() if code == 0 else ""
            if not merged_tree:
                raise ApiError(500, "git_sync_failed", f"生成合并结果失败：{_tail(err or out)}")
            if merged_tree != head_tree:
                code, out, err = self._run(
                    [
                        "commit-tree",
                        merged_tree,
                        "-p",
                        "HEAD",
                        "-p",
                        remote_commit,
                        "-m",
                        f"KnowledgeLib: merge {remote_dir}/ from origin/{branch}",
                    ],
                    kb_dir,
                    timeout=60,
                )
                merge_commit = out.strip() if code == 0 else ""
                if not merge_commit:
                    raise ApiError(500, "git_sync_failed", f"创建合并提交失败：{_tail(err or out)}")
                self._update_head_ref(kb_dir, branch, merge_commit)
                self._run(["update-index", "--refresh"], kb_dir, timeout=30)
                pulled = True
        self._record_folder_state(
            kb_dir,
            branch,
            remote_dir,
            remote_url,
            remote_commit,
            remote_tree,
            advance_local=self._rev(kb_dir, "HEAD^{tree}") == remote_tree,
        )
        message = f"已从 origin/{branch}:{remote_dir}/ 同步"
        if removed:
            message = f"远端已删除 {remote_dir}/"
            if resolved:
                message += f"，已按冲突策略处理 {resolved} 个冲突"
            else:
                message += "，本地对应文件已同步删除"
        elif resolved:
            message += f"并处理 {resolved} 个冲突"
        elif not pulled:
            message += "（无变化）"
        return {"pulled": pulled, "conflictsResolved": resolved, "message": message}

    def _push_folder(
        self,
        kb_dir: Path,
        branch: str,
        remote_url: str,
        remote_dir: str,
        skip_fetch: bool = False,
    ) -> dict[str, Any]:
        self._unset_upstream(kb_dir, branch)
        self._ensure_identity(kb_dir)
        if not skip_fetch:
            code, out, err = self._run(["fetch", "origin", "--prune"], kb_dir, timeout=180)
            if code != 0:
                raise self._remote_error("git_fetch_failed", "拉取远端失败", out, err, remote_url)
        remote_ref = f"refs/remotes/origin/{branch}"
        remote_commit = self._rev(kb_dir, remote_ref)
        base_ref, tip_ref, _ = self._folder_refs(branch, remote_dir, remote_url)
        if not remote_commit and self._rev(kb_dir, tip_ref):
            raise ApiError(409, "git_remote_rewritten", _REMOTE_BRANCH_GONE)
        empty_tree = self._empty_tree(kb_dir)
        remote_folder_tree = self._rev(kb_dir, f"{remote_ref}:{remote_dir}") or empty_tree
        base_commit = self._rev(kb_dir, base_ref)
        if self._remote_history_replaced(
            kb_dir, branch, remote_dir, remote_url, remote_commit
        ):
            raise ApiError(
                409,
                "git_remote_rewritten",
                "远端历史已变化（仓库可能被重建或强推）；请确认后重新保存 remote 以重置同步快照，或更换仓库内目录",
            )
        if remote_commit:
            # A file at <remoteDir> or at an ancestor path cannot hold the library.
            parts = remote_dir.split("/")
            for index in range(1, len(parts) + 1):
                prefix = "/".join(parts[:index])
                kind = self._path_type(kb_dir, f"{remote_ref}:{prefix}")
                if kind and kind != "tree":
                    raise ApiError(
                        409,
                        "git_path_conflict",
                        f"远端的 {prefix} 是文件，无法在它下面创建 {remote_dir}/",
                    )
        if not base_commit and remote_folder_tree != empty_tree:
            raise ApiError(
                409,
                "git_pull_required",
                f"远端目录 {remote_dir}/ 已有文件；请先「拉取」合并，或换一个目录名",
            )
        if base_commit and remote_commit:
            base_tree = self._rev(kb_dir, f"{base_ref}^{{tree}}") or empty_tree
            if base_tree != remote_folder_tree:
                detail = (
                    f"远端已删除 {remote_dir}/，请先拉取再推送"
                    if remote_folder_tree == empty_tree
                    else "远端目录有新提交，请先拉取再推送"
                )
                raise ApiError(409, "git_push_rejected", detail)
        local_tree = self._rev(kb_dir, "HEAD^{tree}")
        push_tree = self._build_remote_tree(kb_dir, remote_commit, remote_dir)
        remote_tip_tree = (
            self._rev(kb_dir, f"{remote_ref}^{{tree}}") if remote_commit else empty_tree
        )
        if remote_commit and push_tree == remote_tip_tree:
            self._record_folder_state(
                kb_dir, branch, remote_dir, remote_url, remote_commit, remote_folder_tree
            )
            return {"message": f"远端目录 {remote_dir}/ 已是最新"}
        args = ["commit-tree", push_tree]
        if remote_commit:
            args += ["-p", remote_commit]
        args += ["-m", f"KnowledgeLib: sync {remote_dir}/ at {utc_now_iso()}"]
        code, out, err = self._run(args, kb_dir, timeout=60)
        commit = out.strip() if code == 0 else ""
        if not commit:
            raise ApiError(500, "git_sync_failed", f"创建远端提交失败：{_tail(err or out)}")
        code, out, err = self._run(
            ["push", "origin", f"{commit}:refs/heads/{branch}"], kb_dir, timeout=180
        )
        if code != 0:
            text = (out + "\n" + err).lower()
            if "rejected" in text or "non-fast-forward" in text or "fetch first" in text:
                raise ApiError(409, "git_push_rejected", "推送被远端拒绝：远端有新提交，请先拉取再推送")
            raise self._remote_error("git_push_failed", "推送失败", out, err, remote_url)
        self._update_ref(kb_dir, remote_ref, commit)
        self._record_folder_state(
            kb_dir, branch, remote_dir, remote_url, commit, local_tree
        )
        return {"message": f"已推送到 origin/{branch}:{remote_dir}/"}

    def _pull(
        self,
        kb_dir: Path,
        branch: str,
        strategy: str,
        remote_url: str,
        settings: Mapping[str, Any],
        remote_dir: str = "",
    ) -> dict[str, Any]:
        custom = (settings.get("pullCommand") or "").strip()
        if custom:
            parts = self._command_parts(custom, branch, remote_url, kb_dir, remote_dir)
            code, out, err = self._run_custom(parts, kb_dir, timeout=180)
            conflicts = self._conflicts(kb_dir)
            if conflicts:
                if strategy == "abort":
                    self._abort_merge(kb_dir)
                    raise ApiError(
                        409,
                        "git_conflict",
                        "自定义拉取产生冲突，已中止并保留本地文件",
                        {"conflicts": conflicts},
                    )
                if strategy == "manual":
                    raise ApiError(
                        409,
                        "git_conflict",
                        "自定义拉取产生冲突，请手工处理",
                        {"conflicts": conflicts},
                    )
                resolved = self._resolve_conflicts(kb_dir, strategy)
                return {
                    "pulled": True,
                    "conflictsResolved": resolved,
                    "message": f"自定义拉取完成并处理 {resolved} 个冲突",
                }
            if code != 0:
                raise self._remote_error("git_pull_failed", "自定义拉取命令失败", out, err, remote_url)
            return {"pulled": True, "message": "已用自定义命令拉取"}

        if remote_dir:
            return self._pull_folder(kb_dir, branch, strategy, remote_url, remote_dir)

        code, out, err = self._run(["fetch", "origin", "--prune"], kb_dir, timeout=180)
        if code != 0:
            raise self._remote_error("git_fetch_failed", "拉取远端失败", out, err, remote_url)
        remote_ref = f"refs/remotes/origin/{branch}"
        code, _, _ = self._run(["rev-parse", "--verify", "--quiet", remote_ref], kb_dir, timeout=15)
        if code != 0:
            return {"pulled": False, "message": "远端还没有该分支，已跳过拉取"}
        merge_args = ["-c", "commit.gpgsign=false", "merge", "--no-edit", "--allow-unrelated-histories"]
        if strategy == "local":
            merge_args += ["-X", "ours"]
        elif strategy == "remote":
            merge_args += ["-X", "theirs"]
        self._ensure_identity(kb_dir)
        code, out, err = self._run(merge_args + [f"origin/{branch}"], kb_dir, timeout=180)
        if code == 0:
            self._set_upstream(kb_dir, branch)
            return {"pulled": True, "message": "已拉取并合并 origin/" + branch}
        conflicts = self._conflicts(kb_dir)
        if not conflicts:
            raise ApiError(
                409,
                "git_pull_failed",
                f"合并失败：{_tail(err or out, 300)}",
            )
        if strategy == "abort":
            self._abort_merge(kb_dir)
            raise ApiError(
                409,
                "git_conflict",
                "拉取产生冲突，已中止合并并保留本地文件",
                {"conflicts": conflicts},
            )
        if strategy == "manual":
            raise ApiError(
                409,
                "git_conflict",
                "拉取产生冲突，已保留冲突标记，请手工处理",
                {"conflicts": conflicts},
            )
        resolved = self._resolve_conflicts(kb_dir, strategy)
        self._set_upstream(kb_dir, branch)
        return {
            "pulled": True,
            "conflictsResolved": resolved,
            "message": f"已拉取并处理 {resolved} 个冲突",
        }

    def _set_upstream(self, kb_dir: Path, branch: str) -> None:
        self._run(["branch", "--set-upstream-to", f"origin/{branch}", branch], kb_dir, timeout=30)

    def _push(
        self,
        kb_dir: Path,
        branch: str,
        settings: Mapping[str, Any],
        remote_url: str,
        remote_dir: str = "",
        skip_fetch: bool = False,
    ) -> dict[str, Any]:
        custom = (settings.get("pushCommand") or "").strip()
        if custom:
            parts = self._command_parts(custom, branch, remote_url, kb_dir, remote_dir)
            code, out, err = self._run_custom(parts, kb_dir, timeout=180)
            if code != 0:
                raise self._remote_error("git_push_failed", "自定义推送命令失败", out, err, remote_url)
            return {"message": "已用自定义命令推送"}
        if remote_dir:
            return self._push_folder(
                kb_dir, branch, remote_url, remote_dir, skip_fetch=skip_fetch
            )
        code, out, err = self._run(["push", "-u", "origin", branch], kb_dir, timeout=180)
        if code != 0:
            text = (out + "\n" + err).lower()
            if "rejected" in text or "non-fast-forward" in text or "fetch first" in text:
                raise ApiError(
                    409,
                    "git_push_rejected",
                    "推送被远端拒绝：远端有新提交，请先拉取再推送",
                )
            raise self._remote_error("git_push_failed", "推送失败", out, err, remote_url)
        return {"message": "已推送到 origin/" + branch}

    def _command_parts(
        self,
        command: str,
        branch: str,
        remote_url: str,
        kb_dir: Path,
        remote_dir: str = "",
    ) -> list[str]:
        parts = shlex.split(command, posix=True)
        if not parts or Path(parts[0]).name.lower() not in ("git", "git.exe", "gh", "gh.exe"):
            raise ApiError(400, "invalid_request", "自定义命令必须以 git 或 gh 开头")
        variables = {
            "branch": branch,
            "remote": "origin",
            "url": remote_url,
            "kb": Path(kb_dir).name,
            "dir": remote_dir,
        }
        expanded: list[str] = []
        for token in parts:
            value = token
            for key, replacement in variables.items():
                value = value.replace("{" + key + "}", replacement)
            expanded.append(value)
        # _run_custom() resolves and prepends git or gh itself.
        return expanded

    def _remote_error(self, code: str, message: str, out: str, err: str, remote_url: str) -> ApiError:
        detail = _short((err or out).strip(), 1600)
        if remote_url:
            detail = detail.replace(remote_url, "<remote>")
        return ApiError(502, code, message + (f"：{_tail(detail, 300)}" if detail else ""))

    # -------------------------------------------------------------- scheduler
    def start_scheduler(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._scheduler_loop, name="kl-git-sync", daemon=True)
        self._thread.start()

    def stop_scheduler(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.scheduler_tick()
            except Exception:  # noqa: BLE001
                continue

    def scheduler_tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or _now_dt()
        root = self.root()
        settings = self.settings()
        if not settings.get("autoSync") or not (root / ".git").exists():
            return []
        base = _parse_time(settings.get("lastAttemptAt")) or _parse_time(settings.get("updatedAt"))
        if base is not None:
            due = base + timedelta(minutes=int(settings.get("intervalMinutes") or 15))
            if current < due:
                return []
        try:
            self.sync(
                direction=str(settings.get("autoDirection") or "sync"),
                strategy=str(settings.get("strategy") or "abort"),
                trigger="auto",
            )
            return [{"root": str(root), "ok": True}]
        except ApiError as exc:
            return [{"root": str(root), "ok": False, "message": exc.message}]
        except Exception as exc:  # noqa: BLE001
            return [{"root": str(root), "ok": False, "message": str(exc)}]
