"""Knowledge base and learning-path directory management.

A knowledge base is a directory under the configured root. A learning path
is a directory inside a knowledge base containing a numbered set of Markdown
files (00 overview + 01..NN modules).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ApiError
from .storage import read_json, utc_now_iso, write_json_atomic

MAX_MD_BYTES = 2 * 1024 * 1024
FORBIDDEN_CHARS = set('<>:"/\\|?*')
RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
KB_META = ".kb.json"
PATH_META = ".path.json"
OVERVIEW_NAME = "00-学习路径总览.md"


def _now() -> str:
    return utc_now_iso()


def _is_reserved(stem: str) -> bool:
    return stem.split(".")[0].lower() in RESERVED_NAMES


def _clean_component(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip()
    chars: list[str] = []
    for ch in text:
        if ch in FORBIDDEN_CHARS or ord(ch) < 32:
            continue
        if ch.isspace():
            chars.append("-")
        else:
            chars.append(ch)
    text = "".join(chars)
    text = re.sub(r"-{2,}", "-", text)
    text = text.strip(" .-_")
    if _is_reserved(text):
        text = "_" + text
    return text


def slugify_dir(name: str, max_len: int = 60, fallback: str = "library") -> str:
    text = _clean_component(name)[:max_len].strip(" .-")
    if _is_reserved(text):
        text = "_" + text
    return text or fallback


def slugify_title(title: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKC", title or "").strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w\u4e00-\u9fff\-]", "", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-._ ")
    return text[:max_len].strip("-._ ") or ""


def unique_dir(parent: Path, stem: str) -> Path:
    candidate = parent / stem
    if not candidate.exists():
        return candidate
    for n in range(2, 1000):
        candidate = parent / f"{stem}-{n}"
        if not candidate.exists():
            return candidate
    raise ApiError(409, "conflict", "同名目录过多，请换一个名称")


def ensure_within(base: Path, target: Path) -> Path:
    base_r = base.resolve()
    try:
        target_r = target.resolve()
    except OSError:
        target_r = target.absolute()
    try:
        target_r.relative_to(base_r)
    except ValueError:
        raise ApiError(400, "invalid_path", "路径越界：目标不在允许的目录内")
    return target_r


def _read_meta(path: Path, kind: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = read_json(path)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("id") or not data.get("name"):
        return None
    data["_kind"] = kind
    return data


def read_kb_meta(kb_dir: Path) -> dict[str, Any]:
    meta = _read_meta(kb_dir / KB_META, "kb")
    if not meta:
        raise ApiError(404, "not_found", "知识库不存在或缺少元数据")
    return meta


def scan_kbs(root: Path) -> list[dict[str, Any]]:
    root = Path(root)
    if not root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        meta = _read_meta(child / KB_META, "kb")
        if not meta:
            continue
        paths = iter_paths(child)
        file_count = sum(len(ref.files) for ref in paths)
        total_bytes = sum(f["bytes"] for ref in paths for f in ref.files)
        updated = None
        for ref in paths:
            updated = max(updated, ref.updated_at) if updated else ref.updated_at
        result.append(
            {
                "id": meta["id"],
                "name": meta["name"],
                "slug": meta.get("slug", child.name),
                "description": meta.get("description", ""),
                "dirName": child.name,
                "absPath": str(child),
                "pathCount": len(paths),
                "fileCount": file_count,
                "totalBytes": total_bytes,
                "createdAt": meta.get("createdAt", ""),
                "updatedAt": updated or meta.get("createdAt", ""),
            }
        )
    return result


def find_kb(root: Path, kb_id: str) -> Path:
    root = Path(root)
    if root.is_dir():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            meta = _read_meta(child / KB_META, "kb")
            if meta and meta.get("id") == kb_id:
                return child
    raise ApiError(404, "not_found", "知识库不存在")


def create_kb(root: Path, name: str, description: str = "") -> dict[str, Any]:
    root = Path(root)
    if not root.is_dir():
        raise ApiError(400, "invalid_path", "知识库根目录不存在，请先在设置中配置")
    clean_name = (name or "").strip()
    if not clean_name or len(clean_name) > 60:
        raise ApiError(400, "invalid_request", "知识库名称不能为空且不超过 60 字符")
    slug = slugify_dir(clean_name, fallback="knowledge-base")
    if _is_reserved(slug):
        slug = "_" + slug
    kb_dir = unique_dir(root, slug)
    try:
        kb_dir.mkdir(parents=False)
    except OSError as exc:
        raise ApiError(400, "invalid_path", f"无法创建知识库目录：{exc}")
    meta = {
        "schemaVersion": 1,
        "id": hashlib.sha1(f"{kb_dir.name}:{_now()}".encode()).hexdigest()[:12],
        "name": clean_name,
        "slug": kb_dir.name,
        "description": (description or "").strip()[:200],
        "createdAt": _now(),
    }
    write_json_atomic(kb_dir / KB_META, meta)
    return meta


@dataclass
class PathRef:
    meta: dict[str, Any]
    directory: Path
    files: list[dict[str, Any]]

    @property
    def id(self) -> str:
        return str(self.meta["id"])

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or self.directory.name)

    @property
    def status(self) -> str:
        return str(self.meta.get("status") or "external")

    @property
    def updated_at(self) -> str:
        return str(self.meta.get("updatedAt") or self.meta.get("createdAt") or "")


def parse_index(filename: str) -> int | None:
    match = re.match(r"^(\d{1,3})[-_ ]", filename)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def list_md_files(path_dir: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    path_dir = Path(path_dir)
    if not path_dir.is_dir():
        return result
    for item in path_dir.rglob("*.md"):
        rel = item.relative_to(path_dir)
        if len(rel.parts) > 5:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        try:
            if item.is_symlink() or not item.is_file():
                continue
            stat = item.stat()
            if stat.st_size > MAX_MD_BYTES * 2:
                continue
        except OSError:
            continue
        result.append(
            {
                "name": rel.as_posix(),
                "index": parse_index(rel.name),
                "bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
    result.sort(key=lambda item: (item["index"] if item["index"] is not None else 9999, item["name"]))
    return result


def _synthetic_id(dir_name: str) -> str:
    return "p" + hashlib.sha1(dir_name.encode("utf-8")).hexdigest()[:11]


def iter_paths(kb_dir: Path) -> list[PathRef]:
    kb_dir = Path(kb_dir)
    if not kb_dir.is_dir():
        return []
    result: list[PathRef] = []
    for child in sorted(kb_dir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        meta = _read_meta(child / PATH_META, "path")
        files = list_md_files(child)
        if not meta and not files:
            continue
        if not meta:
            meta = {
                "schemaVersion": 1,
                "id": _synthetic_id(child.name),
                "name": child.name,
                "slug": child.name,
                "createdAt": "",
                "status": "external",
            }
        meta.setdefault("status", "complete")
        meta.setdefault("modules", [])
        result.append(PathRef(meta=meta, directory=child, files=files))
    result.sort(key=lambda ref: (ref.meta.get("createdAt") or "", ref.name.lower()))
    return result


def find_path(kb_dir: Path, path_id: str) -> PathRef:
    for ref in iter_paths(kb_dir):
        if ref.id == path_id:
            return ref
    raise ApiError(404, "not_found", "学习路径不存在")


def path_public(ref: PathRef) -> dict[str, Any]:
    meta = ref.meta
    plan = meta.get("plan")
    module_count = len(plan.get("modules") or []) if isinstance(plan, dict) else max(len(ref.files) - 1, 0)
    done = sum(1 for module in meta.get("modules") or [] if module.get("status") == "done")
    return {
        "id": ref.id,
        "name": ref.name,
        "slug": meta.get("slug", ref.directory.name),
        "dirName": ref.directory.name,
        "domain": meta.get("domain", ""),
        "status": ref.status,
        "createdAt": meta.get("createdAt", ""),
        "updatedAt": ref.updated_at,
        "spec": meta.get("spec"),
        "plan": plan,
        "modules": meta.get("modules") or [],
        "errors": meta.get("errors") or [],
        "moduleCount": module_count,
        "doneCount": done,
        "files": ref.files,
        "absPath": str(ref.directory),
        "model": meta.get("model") or "",
    }


def kb_detail(root: Path, kb_id: str) -> dict[str, Any]:
    kb_dir = find_kb(root, kb_id)
    meta = read_kb_meta(kb_dir)
    paths = [path_public(ref) for ref in iter_paths(kb_dir)]
    return {
        "id": meta["id"],
        "name": meta["name"],
        "slug": meta.get("slug", kb_dir.name),
        "description": meta.get("description", ""),
        "dirName": kb_dir.name,
        "absPath": str(kb_dir),
        "createdAt": meta.get("createdAt", ""),
        "paths": paths,
        "pathCount": len(paths),
    }


def create_generated_path(
    kb_dir: Path,
    spec: dict[str, Any],
    name_hint: str,
    model: str,
) -> PathRef:
    kb_dir = Path(kb_dir)
    base = name_hint.strip() or f"{spec.get('domain', '学习')}学习路径"
    slug = slugify_dir(base, fallback="learning-path")
    target = unique_dir(kb_dir, slug)
    try:
        target.mkdir(parents=False)
    except OSError as exc:
        raise ApiError(400, "invalid_path", f"无法创建学习路径目录：{exc}")
    now = _now()
    meta = {
        "schemaVersion": 1,
        "id": hashlib.sha1(f"{target.name}:{now}".encode()).hexdigest()[:12],
        "name": base[:60],
        "slug": target.name,
        "domain": spec.get("domain", ""),
        "createdAt": now,
        "updatedAt": now,
        "spec": spec,
        "plan": None,
        "status": "planning",
        "modules": [],
        "errors": [],
        "model": model,
    }
    write_json_atomic(target / PATH_META, meta)
    return PathRef(meta=meta, directory=target, files=[])


def update_path_meta(ref_or_dir: PathRef | Path, **changes: Any) -> dict[str, Any]:
    if isinstance(ref_or_dir, PathRef):
        ref = ref_or_dir
    else:
        directory = Path(ref_or_dir)
        meta = _read_meta(directory / PATH_META, "path")
        if not meta:
            raise ApiError(404, "not_found", "学习路径元数据不存在")
        ref = PathRef(meta=meta, directory=directory, files=list_md_files(directory))
    ref.meta.update(changes)
    ref.meta["updatedAt"] = _now()
    ref.meta.pop("_kind", None)
    write_json_atomic(ref.directory / PATH_META, ref.meta)
    if isinstance(ref_or_dir, PathRef):
        ref.files = list_md_files(ref.directory)
    return ref.meta


def write_path_meta(directory: Path, meta: dict[str, Any]) -> None:
    payload = {k: v for k, v in meta.items() if not k.startswith("_")}
    payload["updatedAt"] = _now()
    meta["updatedAt"] = payload["updatedAt"]
    write_json_atomic(directory / PATH_META, payload)


def read_md(kb_dir: Path, path_id: str, rel_name: str) -> dict[str, Any]:
    ref = find_path(kb_dir, path_id)
    rel = (rel_name or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise ApiError(400, "invalid_path", "文件名不合法")
    if not rel.lower().endswith(".md"):
        raise ApiError(400, "invalid_request", "只支持读取 .md 文件")
    target = ensure_within(ref.directory, ref.directory / rel)
    if not target.is_file():
        raise ApiError(404, "not_found", "文件不存在")
    try:
        size = target.stat().st_size
    except OSError:
        raise ApiError(404, "not_found", "文件不存在")
    if size > MAX_MD_BYTES * 2:
        raise ApiError(413, "too_large", "文件过大，无法预览")
    raw = target.read_bytes()
    warning = None
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
        warning = "文件不是合法 UTF-8，非法字节已替换为 U+FFFD"
    return {
        "name": rel,
        "content": content,
        "bytes": size,
        "warning": warning,
        "index": parse_index(Path(rel).name),
    }


def move_to_trash(target: Path, data_dir: Path) -> Path:
    target = Path(target)
    data_dir = Path(data_dir)
    trash = data_dir / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = unique_dir(trash, f"{stamp}-{target.name}")
    try:
        shutil.move(str(target), str(dest))
    except OSError as exc:
        raise ApiError(500, "internal_error", f"无法移动到回收站：{exc}")
    return dest


def delete_kb(root: Path, data_dir: Path, kb_id: str) -> dict[str, Any]:
    kb_dir = find_kb(root, kb_id)
    dest = move_to_trash(kb_dir, data_dir)
    return {"deleted": True, "trashedTo": str(dest)}


def delete_path(root: Path, data_dir: Path, kb_id: str, path_id: str) -> dict[str, Any]:
    kb_dir = find_kb(root, kb_id)
    ref = find_path(kb_dir, path_id)
    if ref.status in ("planning", "generating"):
        raise ApiError(409, "conflict", "该路径正在生成，暂不能删除")
    dest = move_to_trash(ref.directory, data_dir)
    return {"deleted": True, "trashedTo": str(dest)}
