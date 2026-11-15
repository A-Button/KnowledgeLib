"""Per-knowledge-base Git repository management and synchronization.

Git itself is the source of truth for remotes and commits. Only user-facing
automation preferences (auto-sync interval, conflict strategy and optional
custom commands) live in the per-user config directory, never inside the
knowledge base and never in the repository.
"""

from __future__ import annotations

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
from .storage import read_json, touch_private, utc_now_iso, write_json_atomic

STRATEGIES = ("abort", "local", "remote", "manual")
DIRECTIONS = ("sync", "pull", "push")
KB_META = ".kb.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "autoSync": False,
    "intervalMinutes": 15,
    "autoDirection": "sync",
    "strategy": "abort",
    "autoCommit": True,
    "pushCommand": "",
    "pullCommand": "",
    "updatedAt": "",
    "lastAttemptAt": "",
    "lastSyncAt": "",
    "lastStatus": "never",
    "lastMessage": "",
    "lastDirection": "",
}

_COMMAND_FORBIDDEN = (";", "|", ">", "<", chr(96), "$(")
_COMMAND_BLOCKED_ARGS = {
    "-C",
    "--git-dir",
    "--work-tree",
    "--exec-path",
    "--upload-pack",
    "--receive-pack",
}
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")


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
    if executable not in ("git", "git.exe"):
        raise ApiError(400, "invalid_request", f"{field}必须以 git 开头，不能执行其他程序")
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


class GitService:
    """Run local git commands for knowledge bases and track sync preferences."""

    def __init__(
        self,
        config_dir: Path,
        environ: Mapping[str, str] | None = None,
        poll_seconds: float = 30.0,
    ) -> None:
        self.config_dir = Path(config_dir).expanduser().resolve()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "git-sync.json"
        self.environ = dict(os.environ)
        if environ:
            self.environ.update({str(key): str(value) for key, value in environ.items()})
        self.poll_seconds = max(5.0, float(poll_seconds))
        self._config_lock = threading.RLock()
        self._repo_guard = threading.Lock()
        self._repo_locks: dict[str, threading.Lock] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._root_provider: Callable[[], Path] | None = None
        self._data = self._load()

    # ---------------------------------------------------------------- config
    def _load(self) -> dict[str, Any]:
        data: dict[str, Any] = {"version": 1, "kbs": {}}
        if not self.config_path.exists():
            self._save(data)
            return data
        try:
            loaded = read_json(self.config_path)
        except Exception:
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = self.config_path.with_name(f"git-sync.json.corrupt-{stamp}")
            try:
                shutil.move(str(self.config_path), str(backup))
            except OSError:
                pass
            self._save(data)
            return data
        if not isinstance(loaded, dict) or not isinstance(loaded.get("kbs"), dict):
            self._save(data)
            return data
        data["kbs"] = {
            str(key): value
            for key, value in loaded["kbs"].items()
            if isinstance(key, str) and isinstance(value, dict)
        }
        return data

    def _save(self, data: dict[str, Any] | None = None) -> None:
        write_json_atomic(self.config_path, self._data if data is None else data)
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass
        touch_private(self.config_path)

    def settings_for(self, kb_id: str) -> dict[str, Any]:
        with self._config_lock:
            stored = self._data["kbs"].get(str(kb_id), {})
            result = dict(DEFAULT_SETTINGS)
            if isinstance(stored, dict):
                for key in result:
                    if key in stored:
                        result[key] = stored[key]
            return result

    def update_settings(self, kb_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, Mapping):
            raise ApiError(400, "invalid_request", "请求体必须是 JSON 对象")
        next_settings = self.settings_for(kb_id)
        if "autoSync" in patch:
            if not isinstance(patch["autoSync"], bool):
                raise ApiError(400, "invalid_request", "autoSync 必须是布尔值")
            next_settings["autoSync"] = patch["autoSync"]
        if "autoCommit" in patch:
            if not isinstance(patch["autoCommit"], bool):
                raise ApiError(400, "invalid_request", "autoCommit 必须是布尔值")
            next_settings["autoCommit"] = patch["autoCommit"]
        if "intervalMinutes" in patch:
            try:
                interval = int(patch["intervalMinutes"])
            except (TypeError, ValueError):
                raise ApiError(400, "invalid_request", "同步间隔必须是整数分钟")
            if interval < 1 or interval > 1440:
                raise ApiError(400, "invalid_request", "同步间隔需要在 1..1440 分钟之间")
            next_settings["intervalMinutes"] = interval
        if "autoDirection" in patch:
            direction = str(patch["autoDirection"] or "")
            if direction not in DIRECTIONS:
                raise ApiError(400, "invalid_request", "自动同步方向不合法")
            next_settings["autoDirection"] = direction
        if "strategy" in patch:
            strategy = str(patch["strategy"] or "")
            if strategy not in STRATEGIES:
                raise ApiError(400, "invalid_request", "冲突处理策略不合法")
            next_settings["strategy"] = strategy
        if "pushCommand" in patch:
            next_settings["pushCommand"] = validate_command(patch["pushCommand"], "自定义推送命令")
        if "pullCommand" in patch:
            next_settings["pullCommand"] = validate_command(patch["pullCommand"], "自定义拉取命令")
        next_settings["updatedAt"] = utc_now_iso()
        with self._config_lock:
            self._data["kbs"][str(kb_id)] = dict(next_settings)
            self._save()
        return dict(next_settings)

    def _record_attempt(self, kb_id: str, at: str, direction: str) -> None:
        with self._config_lock:
            settings = self.settings_for(kb_id)
            settings["lastAttemptAt"] = at
            settings["lastDirection"] = direction
            self._data["kbs"][str(kb_id)] = settings
            self._save()

    def _record_result(self, kb_id: str, status: str, message: str, direction: str) -> None:
        with self._config_lock:
            settings = self.settings_for(kb_id)
            settings["lastDirection"] = direction
            settings["lastStatus"] = status
            settings["lastMessage"] = _tail(message, 600)
            if status == "ok":
                settings["lastSyncAt"] = utc_now_iso()
            self._data["kbs"][str(kb_id)] = settings
            self._save()

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
            }
        )
        return env

    def _run(self, args: list[str], cwd: Path, timeout: float = 120.0) -> tuple[int, str, str]:
        executable = self._git()
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.run(
                [executable, *args],
                cwd=str(cwd),
                env=self._env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                **options,
            )
        except subprocess.TimeoutExpired:
            raise ApiError(504, "git_timeout", f"Git 命令超过 {int(timeout)} 秒未完成")
        except OSError as exc:
            raise ApiError(400, "git_unavailable", f"无法运行 Git：{exc}")
        return process.returncode, _decode(process.stdout), _decode(process.stderr)

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

    def _merge_in_progress(self, kb_dir: Path) -> bool:
        git_dir = Path(kb_dir) / ".git"
        if not git_dir.is_dir():
            return False
        return any((git_dir / name).exists() for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply"))

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
    def status(self, kb_dir: Path, kb_id: str) -> dict[str, Any]:
        settings = self.settings_for(kb_id)
        result: dict[str, Any] = {
            "available": bool(shutil.which("git")),
            "managed": False,
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
        if not self._managed(kb_dir):
            return result
        result["managed"] = True
        try:
            branch = self._branch(kb_dir)
            result["branch"] = branch
            result["remoteUrl"] = self._remote_url(kb_dir)
            result["upstream"] = self._upstream(kb_dir)
            code, out, _ = self._run(
                ["status", "--porcelain=v1", "--untracked-files=all"],
                kb_dir,
                timeout=20,
            )
            if code == 0:
                lines = [line for line in out.splitlines() if line.strip()]
                result["changedFiles"] = len(lines)
                result["dirty"] = bool(lines)
                result["changed"] = [line[3:] if len(line) > 3 else line for line in lines[:20]]
            result["conflicts"] = self._conflicts(kb_dir)
            result["mergeInProgress"] = self._merge_in_progress(kb_dir)
            if result["upstream"]:
                code, out, _ = self._run(
                    ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                    kb_dir,
                    timeout=20,
                )
                if code == 0:
                    parts = out.split()
                    if len(parts) == 2:
                        result["ahead"] = int(parts[0])
                        result["behind"] = int(parts[1])
            code, out, _ = self._run(
                ["log", "-1", "--format=%H%x00%h%x00%s%x00%cI"],
                kb_dir,
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
    def init_repo(
        self,
        kb_dir: Path,
        kb_id: str,
        remote_url: str = "",
        branch: str = "main",
    ) -> dict[str, Any]:
        kb_dir = Path(kb_dir)
        branch = validate_branch(branch)
        remote = validate_remote_url(remote_url) if (remote_url or "").strip() else ""
        if (kb_dir / ".git").exists():
            if not self._managed(kb_dir):
                raise ApiError(409, "git_conflict", "目录中存在无法识别的 .git，请先手动处理")
            if remote:
                return self.set_remote(kb_dir, kb_id, remote)
            return self.status(kb_dir, kb_id)
        code, out, err = self._run(["init", "-b", branch], kb_dir, timeout=60)
        if code != 0:
            code, out, err = self._run(["init"], kb_dir, timeout=60)
            if code != 0:
                raise ApiError(500, "git_init_failed", f"初始化 Git 仓库失败：{_tail(err or out)}")
            self._run(["checkout", "-b", branch], kb_dir, timeout=30)
        self._run(["config", "core.quotepath", "false"], kb_dir, timeout=15)
        self._ensure_identity(kb_dir)
        ignore = kb_dir / ".gitignore"
        if not ignore.exists():
            ignore.write_text(".DS_Store\nThumbs.db\n*.tmp\n", encoding="utf-8")
        if remote:
            code, out, err = self._run(["remote", "add", "origin", remote], kb_dir, timeout=20)
            if code != 0:
                raise ApiError(502, "git_remote_failed", f"添加 remote 失败：{_tail(err or out)}")
        self._commit_all(kb_dir, "Initialize KnowledgeLib knowledge base")
        return self.status(kb_dir, kb_id)

    def set_remote(self, kb_dir: Path, kb_id: str, url: str) -> dict[str, Any]:
        self._ensure_repo(kb_dir)
        remote = validate_remote_url(url)
        code, _, _ = self._run(["remote", "get-url", "origin"], kb_dir, timeout=15)
        if code == 0:
            code, out, err = self._run(["remote", "set-url", "origin", remote], kb_dir, timeout=20)
        else:
            code, out, err = self._run(["remote", "add", "origin", remote], kb_dir, timeout=20)
        if code != 0:
            raise ApiError(502, "git_remote_failed", f"保存 remote 失败：{_tail(err or out)}")
        return self.status(kb_dir, kb_id)

    # ------------------------------------------------------------------- sync
    def _repo_lock(self, kb_id: str) -> threading.Lock:
        with self._repo_guard:
            lock = self._repo_locks.get(str(kb_id))
            if lock is None:
                lock = threading.Lock()
                self._repo_locks[str(kb_id)] = lock
            return lock

    def sync(
        self,
        kb_dir: Path,
        kb_id: str,
        direction: str = "sync",
        strategy: str | None = None,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        if direction not in DIRECTIONS:
            raise ApiError(400, "invalid_request", "同步方向不合法")
        settings = self.settings_for(kb_id)
        chosen = strategy or settings["strategy"]
        if chosen not in STRATEGIES:
            raise ApiError(400, "invalid_request", "冲突处理策略不合法")
        lock = self._repo_lock(kb_id)
        if not lock.acquire(blocking=False):
            raise ApiError(409, "git_busy", "该知识库正在同步，请稍后再试")
        try:
            self._record_attempt(kb_id, utc_now_iso(), direction)
            result = self._sync_locked(Path(kb_dir), kb_id, direction, chosen, trigger, settings)
            self._record_result(kb_id, "ok", result.get("message", "同步完成"), direction)
            return result
        except ApiError as exc:
            self._record_result(kb_id, "error", exc.message, direction)
            raise
        except Exception as exc:  # noqa: BLE001
            message = f"同步失败：{exc}"
            self._record_result(kb_id, "error", message, direction)
            raise ApiError(500, "git_sync_failed", message)
        finally:
            lock.release()

    def _sync_locked(
        self,
        kb_dir: Path,
        kb_id: str,
        direction: str,
        strategy: str,
        trigger: str,
        settings: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._ensure_repo(kb_dir)
        remote = self._remote_url(kb_dir)
        if not remote:
            raise ApiError(409, "git_no_remote", "尚未添加 Git remote，请先填写远端地址")
        branch = self._branch(kb_dir)
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

        if self._has_changes(kb_dir):
            if not settings.get("autoCommit", True):
                raise ApiError(409, "git_dirty", "本地有未提交改动；请提交，或开启自动提交后再同步")
            commit_created = self._commit_all(kb_dir, f"KnowledgeLib: sync {utc_now_iso()}")
            if commit_created:
                summary.append("已提交本地改动")

        if direction in ("pull", "sync"):
            pull = self._pull(kb_dir, branch, strategy, remote, settings)
            pulled = bool(pull.get("pulled"))
            conflicts_resolved += int(pull.get("conflictsResolved") or 0)
            if pull.get("message"):
                summary.append(pull["message"])

        if direction in ("push", "sync"):
            push = self._push(kb_dir, branch, settings, remote)
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
        git_dir = Path(kb_dir) / ".git"
        if (git_dir / "MERGE_HEAD").exists():
            self._run(["merge", "--abort"], kb_dir, timeout=60)
        elif (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
            self._run(["rebase", "--abort"], kb_dir, timeout=60)

    def _resolve_conflicts(self, kb_dir: Path, strategy: str) -> int:
        side = "ours" if strategy == "local" else "theirs"
        conflicts = self._conflicts(kb_dir)
        if not conflicts:
            return 0
        for path in conflicts:
            code, _, _ = self._run(["checkout", f"--{side}", "--", path], kb_dir, timeout=30)
            if code != 0:
                target = kb_dir / path
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif target.exists():
                        target.unlink()
                except OSError:
                    pass
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
        if (Path(kb_dir) / ".git" / "MERGE_HEAD").exists():
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

    def _pull(
        self,
        kb_dir: Path,
        branch: str,
        strategy: str,
        remote_url: str,
        settings: Mapping[str, Any],
    ) -> dict[str, Any]:
        custom = (settings.get("pullCommand") or "").strip()
        if custom:
            parts = self._command_parts(custom, branch, remote_url, kb_dir)
            code, out, err = self._run(parts, kb_dir, timeout=180)
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

        code, out, err = self._run(["fetch", "origin", "--prune"], kb_dir, timeout=180)
        if code != 0:
            raise self._remote_error("git_fetch_failed", "拉取远端失败", out, err, remote_url)
        remote_ref = f"refs/remotes/origin/{branch}"
        code, _, _ = self._run(["rev-parse", "--verify", "--quiet", remote_ref], kb_dir, timeout=15)
        if code != 0:
            return {"pulled": False, "message": "远端还没有该分支，已跳过拉取"}
        merge_args = ["merge", "--no-edit", "--allow-unrelated-histories"]
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
    ) -> dict[str, Any]:
        custom = (settings.get("pushCommand") or "").strip()
        if custom:
            parts = self._command_parts(custom, branch, remote_url, kb_dir)
            code, out, err = self._run(parts, kb_dir, timeout=180)
            if code != 0:
                raise self._remote_error("git_push_failed", "自定义推送命令失败", out, err, remote_url)
            return {"message": "已用自定义命令推送"}
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

    def _command_parts(self, command: str, branch: str, remote_url: str, kb_dir: Path) -> list[str]:
        parts = shlex.split(command, posix=True)
        if not parts or Path(parts[0]).name.lower() not in ("git", "git.exe"):
            raise ApiError(400, "invalid_request", "自定义命令必须以 git 开头")
        variables = {
            "branch": branch,
            "remote": "origin",
            "url": remote_url,
            "kb": Path(kb_dir).name,
        }
        expanded: list[str] = []
        for token in parts:
            value = token
            for key, replacement in variables.items():
                value = value.replace("{" + key + "}", replacement)
            expanded.append(value)
        # _run() already prepends the resolved git executable.
        return expanded[1:]

    def _remote_error(self, code: str, message: str, out: str, err: str, remote_url: str) -> ApiError:
        detail = _short((err or out).strip(), 1600)
        if remote_url:
            detail = detail.replace(remote_url, "<remote>")
        return ApiError(502, code, message + (f"：{_tail(detail, 300)}" if detail else ""))

    # -------------------------------------------------------------- scheduler
    def start_scheduler(self, root_provider: Callable[[], Path]) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._root_provider = root_provider
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
            provider = self._root_provider
            if not provider:
                continue
            try:
                self.scheduler_tick(provider())
            except Exception:  # noqa: BLE001
                continue

    def scheduler_tick(self, root: Path, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or _now_dt()
        results: list[dict[str, Any]] = []
        try:
            children = sorted(Path(root).iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return results
        for kb_dir in children:
            if not kb_dir.is_dir() or kb_dir.name.startswith("."):
                continue
            try:
                meta = read_json(kb_dir / KB_META)
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            kb_id = str(meta.get("id") or "")
            if not kb_id:
                continue
            settings = self.settings_for(kb_id)
            if not settings.get("autoSync") or not (kb_dir / ".git").exists():
                continue
            base = _parse_time(settings.get("lastAttemptAt")) or _parse_time(settings.get("updatedAt"))
            if base is not None:
                due = base + timedelta(minutes=int(settings.get("intervalMinutes") or 15))
                if current < due:
                    continue
            try:
                self.sync(
                    kb_dir,
                    kb_id,
                    direction=str(settings.get("autoDirection") or "sync"),
                    strategy=str(settings.get("strategy") or "abort"),
                    trigger="auto",
                )
                results.append({"kbId": kb_id, "ok": True})
            except ApiError as exc:
                results.append({"kbId": kb_id, "ok": False, "message": exc.message})
            except Exception as exc:  # noqa: BLE001
                results.append({"kbId": kb_id, "ok": False, "message": str(exc)})
        return results
