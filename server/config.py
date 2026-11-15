"""Settings storage: defaults, masking, environment overrides and validation."""

from __future__ import annotations

import copy
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .errors import ApiError
from .git_sync import DEFAULT_SETTINGS as DEFAULT_GIT_SETTINGS, normalize_git_settings
from .storage import read_json, touch_private, write_json_atomic

DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_WEB_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

ENV_KEYS = {
    "api_key": "KL_API_KEY",
    "base_url": "KL_BASE_URL",
    "model": "KL_MODEL",
    "web_api_key": "KL_WEB_API_KEY",
    "web_base_url": "KL_WEB_BASE_URL",
    "kb_root": "KL_KB_ROOT",
}


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:3] + "****" + key[-4:]


def _validate_url(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "invalid_request", f"{field} 必须是字符串")
    value = value.strip()
    if not value or len(value) > 500:
        raise ApiError(400, "invalid_request", f"{field} 不能为空且不超过 500 字符")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ApiError(400, "invalid_request", f"{field} 必须是 http/https 地址并包含主机名")
    if any(ch.isspace() for ch in value):
        raise ApiError(400, "invalid_request", f"{field} 不能包含空白字符")
    return value


def _validate_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "invalid_request", f"{field} 不能为空")
    value = value.strip().strip('"')
    if len(value) > 240:
        raise ApiError(400, "invalid_request", f"{field} 过长")
    return value


def _validate_int(value: Any, field: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_request", f"{field} 必须是整数")
    if number < low or number > high:
        raise ApiError(400, "invalid_request", f"{field} 必须在 {low}..{high} 之间")
    return number


def default_config_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = dict(os.environ if environ is None else environ)
    override = (env.get("KL_CONFIG_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return (Path(base) / "KnowledgeLib").resolve()
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return (Path(base) / "knowledgelib").resolve()


class ConfigStore:
    """Thread-safe settings store in the per-user config directory."""

    def __init__(
        self,
        config_dir: Path,
        data_dir: Path,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir = Path(config_dir).expanduser().resolve()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.config_dir / "settings.json"
        self.environ = dict(os.environ if environ is None else environ)
        self._lock = threading.RLock()
        self.warnings: list[str] = []
        legacy = self.data_dir / "settings.json"
        if not self.path.exists() and legacy.exists():
            try:
                shutil.copy2(legacy, self.path)
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
                self.warnings.append(f"已将旧配置文件 {legacy} 迁移到用户目录 {self.path}。")
            except OSError:
                pass
        self._data = self._load()
        try:
            Path(self._data["kb"]["root"]).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------ load
    def _defaults(self) -> dict[str, Any]:
        return {
            "api": {
                "baseUrl": DEFAULT_API_BASE,
                "apiKey": "",
                "model": DEFAULT_MODEL,
                "timeoutSec": 120,
            },
            "web": {
                "baseUrl": DEFAULT_WEB_BASE,
                "apiKey": "",
                "searchPath": "/tools/search",
                "fetchPath": "/tools/fetch",
                "searchCount": 5,
                "fetchTopN": 2,
                "timeoutSec": 45,
            },
            "kb": {
                "root": str((self.data_dir / "library").resolve()),
                "topK": 6,
                "contextChars": 6000,
            },
            "git": copy.deepcopy(DEFAULT_GIT_SETTINGS),
        }

    def _load(self) -> dict[str, Any]:
        data = self._defaults()
        if not self.path.exists():
            self._save(data)
            return data
        try:
            loaded = read_json(self.path)
            if not isinstance(loaded, dict):
                raise ValueError("settings root is not an object")
        except Exception as exc:  # noqa: BLE001
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = self.path.with_name(f"settings.json.corrupt-{stamp}")
            try:
                shutil.move(str(self.path), str(backup))
                self.warnings.append(f"settings.json 无法解析（{exc}），已备份为 {backup.name} 并重建默认设置。")
            except OSError:
                self.warnings.append("settings.json 无法解析，已使用默认设置。")
            self._save(data)
            return data

        for section, defaults in data.items():
            if isinstance(defaults, dict) and isinstance(loaded.get(section), dict):
                for key, value in defaults.items():
                    loaded[section].setdefault(key, copy.deepcopy(value))
        if not all(isinstance(loaded.get(section), dict) for section in ("api", "web", "kb", "git")):
            self.warnings.append("settings.json 结构不完整，缺失部分已用默认值补齐。")
            merged = self._defaults()
            for section in ("api", "web", "kb", "git"):
                if isinstance(loaded.get(section), dict):
                    merged[section].update(loaded[section])
            loaded = merged
        return loaded

    # ------------------------------------------------------------------ save
    def _save(self, data: dict[str, Any] | None = None) -> None:
        write_json_atomic(self.path, self._data if data is None else data)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        touch_private(self.path)

    # ------------------------------------------------------------- effective
    def _env(self, name: str) -> str:
        return (self.environ.get(name) or "").strip()

    def api_key(self) -> str:
        env = self._env(ENV_KEYS["api_key"])
        return env or (self._data["api"].get("apiKey") or "")

    def web_key(self) -> str:
        env = self._env(ENV_KEYS["web_api_key"])
        if env:
            return env
        own = self._data["web"].get("apiKey") or ""
        return own or self.api_key()

    def base_url(self) -> str:
        return self._env(ENV_KEYS["base_url"]) or self._data["api"]["baseUrl"]

    def model(self) -> str:
        return self._env(ENV_KEYS["model"]) or self._data["api"]["model"]

    def web_base_url(self) -> str:
        return self._env(ENV_KEYS["web_base_url"]) or self._data["web"]["baseUrl"]

    def kb_root(self) -> Path:
        env = self._env(ENV_KEYS["kb_root"])
        raw = env or self._data["kb"]["root"]
        return Path(raw).expanduser().resolve()

    def timeout(self) -> float:
        return float(self._data["api"].get("timeoutSec") or 120)

    def web_search_count(self) -> int:
        return int(self._data["web"].get("searchCount") or 5)

    def web_fetch_top_n(self) -> int:
        return int(self._data["web"].get("fetchTopN") or 0)

    def web_timeout(self) -> float:
        return float(self._data["web"].get("timeoutSec") or 45)

    def is_deepseek(self) -> bool:
        from .llm import is_deepseek_endpoint

        return is_deepseek_endpoint(self.base_url(), self.model())

    def web_search_path(self) -> str:
        return str(self._data["web"].get("searchPath") or "/tools/search")

    def web_fetch_path(self) -> str:
        return str(self._data["web"].get("fetchPath") or "/tools/fetch")

    def top_k(self) -> int:
        return int(self._data["kb"].get("topK") or 6)

    def context_chars(self) -> int:
        return int(self._data["kb"].get("contextChars") or 6000)

    def git_settings(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data.get("git", DEFAULT_GIT_SETTINGS))

    def patch_git_state(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(fields, Mapping):
            raise ApiError(400, "invalid_request", "Git 状态必须是对象")
        with self._lock:
            next_data = copy.deepcopy(self._data)
            next_data.setdefault("git", copy.deepcopy(DEFAULT_GIT_SETTINGS)).update(fields)
            self._data = next_data
            self._save()
            return copy.deepcopy(self._data["git"])

    # ---------------------------------------------------------------- public
    def public(self) -> dict[str, Any]:
        with self._lock:
            api_stored = self._data["api"].get("apiKey") or ""
            web_stored = self._data["web"].get("apiKey") or ""
            api_env = bool(self._env(ENV_KEYS["api_key"]))
            web_env = bool(self._env(ENV_KEYS["web_api_key"]))
            api_key = self.api_key()
            web_key = self.web_key()

            if api_env:
                api_source = "env"
            elif api_stored:
                api_source = "settings"
            else:
                api_source = "none"

            if web_env:
                web_source = "env"
            elif web_stored:
                web_source = "settings"
            elif api_key:
                web_source = "inherit"
            else:
                web_source = "none"

            root = self.kb_root()
            return {
                "api": {
                    "baseUrl": self.base_url(),
                    "model": self.model(),
                    "timeoutSec": self._data["api"].get("timeoutSec", 120),
                    "hasApiKey": bool(api_key),
                    "apiKeyMasked": _mask(api_key),
                    "keySource": api_source,
                    "envBaseUrl": bool(self._env(ENV_KEYS["base_url"])),
                    "envModel": bool(self._env(ENV_KEYS["model"])),
                    "provider": "deepseek" if self.is_deepseek() else "generic",
                    "supportsServerTools": self.is_deepseek(),
                },
                "web": {
                    "baseUrl": self.web_base_url(),
                    "searchPath": self._data["web"].get("searchPath", "/tools/search"),
                    "fetchPath": self._data["web"].get("fetchPath", "/tools/fetch"),
                    "searchCount": self._data["web"].get("searchCount", 5),
                    "fetchTopN": self._data["web"].get("fetchTopN", 2),
                    "timeoutSec": self._data["web"].get("timeoutSec", 45),
                    "hasApiKey": bool(web_key),
                    "apiKeyMasked": _mask(web_key),
                    "keySource": web_source,
                },
                "kb": {
                    "root": str(root),
                    "rootExists": root.is_dir(),
                    "topK": self._data["kb"].get("topK", 6),
                    "contextChars": self._data["kb"].get("contextChars", 6000),
                },
                "git": self.git_settings(),
                "warnings": list(self.warnings),
                "configPath": str(self.path),
            }

    # ---------------------------------------------------------------- update
    def update(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, Mapping):
            raise ApiError(400, "invalid_request", "请求体必须是 JSON 对象")
        with self._lock:
            next_data = copy.deepcopy(self._data)
            api = patch.get("api")
            web = patch.get("web")
            kb = patch.get("kb")
            git = patch.get("git")
            if api is not None:
                if not isinstance(api, Mapping):
                    raise ApiError(400, "invalid_request", "api 必须是对象")
                target = next_data["api"]
                if api.get("clearApiKey") is True:
                    target["apiKey"] = ""
                elif "apiKey" in api and isinstance(api["apiKey"], str) and api["apiKey"] != "":
                    target["apiKey"] = api["apiKey"].strip()
                if "baseUrl" in api:
                    target["baseUrl"] = _validate_url(api["baseUrl"], "api.baseUrl").rstrip("/")
                if "model" in api:
                    model = api["model"]
                    if not isinstance(model, str) or not model.strip() or len(model.strip()) > 100:
                        raise ApiError(400, "invalid_request", "api.model 不能为空且不超过 100 字符")
                    target["model"] = model.strip()
                if "timeoutSec" in api:
                    target["timeoutSec"] = _validate_int(api["timeoutSec"], "api.timeoutSec", 5, 300)
            if web is not None:
                if not isinstance(web, Mapping):
                    raise ApiError(400, "invalid_request", "web 必须是对象")
                target = next_data["web"]
                if web.get("clearApiKey") is True:
                    target["apiKey"] = ""
                elif "apiKey" in web and isinstance(web["apiKey"], str) and web["apiKey"] != "":
                    target["apiKey"] = web["apiKey"].strip()
                if "baseUrl" in web:
                    target["baseUrl"] = _validate_url(web["baseUrl"], "web.baseUrl").rstrip("/")
                for key in ("searchPath", "fetchPath"):
                    if key in web:
                        value = web[key]
                        if not isinstance(value, str) or not value.startswith("/") or "://" in value or len(value) > 200:
                            raise ApiError(400, "invalid_request", f"web.{key} 必须以 / 开头")
                        target[key] = value
                if "searchCount" in web:
                    target["searchCount"] = _validate_int(web["searchCount"], "web.searchCount", 1, 50)
                if "fetchTopN" in web:
                    target["fetchTopN"] = _validate_int(web["fetchTopN"], "web.fetchTopN", 0, 5)
                if "timeoutSec" in web:
                    target["timeoutSec"] = _validate_int(web["timeoutSec"], "web.timeoutSec", 5, 300)
            if kb is not None:
                if not isinstance(kb, Mapping):
                    raise ApiError(400, "invalid_request", "kb 必须是对象")
                target = next_data["kb"]
                if "root" in kb:
                    raw = _validate_path(kb["root"], "kb.root")
                    root = Path(raw).expanduser()
                    if not root.is_absolute():
                        root = (Path.cwd() / root).resolve()
                    else:
                        root = root.resolve()
                    try:
                        root.mkdir(parents=True, exist_ok=True)
                        probe = root / ".kl-write-check"
                        probe.write_text("ok", encoding="utf-8")
                        probe.unlink()
                    except OSError as exc:
                        raise ApiError(400, "invalid_request", f"知识库根目录不可写：{exc}")
                    if len(str(root)) > 240:
                        raise ApiError(400, "invalid_request", "知识库根目录路径过长")
                    target["root"] = str(root)
                if "topK" in kb:
                    target["topK"] = _validate_int(kb["topK"], "kb.topK", 1, 20)
                if "contextChars" in kb:
                    target["contextChars"] = _validate_int(kb["contextChars"], "kb.contextChars", 1000, 20000)
            if git is not None:
                if not isinstance(git, Mapping):
                    raise ApiError(400, "invalid_request", "git 必须是对象")
                next_data["git"] = normalize_git_settings(next_data.get("git", {}), git)
            self._data = next_data
            self._save()
            return self.public()
