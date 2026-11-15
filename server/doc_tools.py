"""Client-side document tools exposed to the reading assistant.

The model may read, edit, create and highlight Markdown documents inside the
current learning path.  All paths are re-validated against the path directory;
the assistant never receives an unrestricted filesystem handle.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import library
from .errors import ApiError
from .storage import utc_now_iso, write_text_atomic

MAX_DOC_BYTES = 1024 * 1024
MAX_READ_CHARS = 12000


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": (
                    "读取当前学习路径中的一篇 Markdown 讲义。file 省略时读取当前正在阅读的文件。"
                    "返回文件名、首行编号标题和正文；正文过长时会截断。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {
                            "type": "string",
                            "description": "文件名，例如 02-核心机制.md；省略则读取当前文件。",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit",
                "description": (
                    "编辑当前学习路径中已有 Markdown 文件的一处内容。old_text 必须在文件中只出现一次，"
                    "new_text 为替换后的内容。修改后文件首行必须仍是与文件名一致的 '# NN · 标题'。"
                    "适合修订段落、补充例子、修正错误；大段重写请改用 write 新建文件。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "要编辑的文件名。"},
                        "old_text": {"type": "string", "description": "原文中只出现一次的精确文本。"},
                        "new_text": {"type": "string", "description": "替换后的文本，可以为空以删除该段。"},
                        "reason": {"type": "string", "description": "一句话说明修改原因。"},
                    },
                    "required": ["file", "old_text", "new_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write",
                "description": (
                    "在当前学习路径目录内创建一篇新的 Markdown 讲义；不能覆盖已有文件。"
                    "文件命名规范：必须为 'NN-标题.md'，NN 是两位数字 01..99，不能使用 00；"
                    "正文规范：content 第一行必须严格为 '# NN · 标题'（编号和文件名一致），"
                    "随后至少包含 '## 本节目标' 和 '## 核心概念' 两个二级标题，使用中文 Markdown，"
                    "不要输出 HTML 或整篇代码围栏。新文件会出现在当前阅读页的编号文件条中。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "新文件名，例如 04-性能调优.md。"},
                        "content": {"type": "string", "description": "完整 Markdown 内容，首行为 '# NN · 标题'。"},
                        "reason": {"type": "string", "description": "一句话说明新增目的。"},
                    },
                    "required": ["file", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "highlight",
                "description": (
                    "在当前阅读的 Markdown 文件中高亮一段原文，供用户回看。"
                    "quote 必须在文件中逐字出现；note 可选，用于解释高亮原因。"
                    "该工具只做展示，不修改文件。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "quote": {"type": "string", "description": "要标亮的原文片段。"},
                        "note": {"type": "string", "description": "可选的高亮说明。"},
                        "file": {"type": "string", "description": "可选的文件名，省略则使用当前文件。"},
                    },
                    "required": ["quote"],
                },
            },
        },
    ]


class ToolError(Exception):
    pass


def _resolve(ref: library.PathRef, name: str) -> Path:
    value = (name or "").replace("\\", "/").strip()
    if not value or value.startswith("/") or "/" in value or ".." in value:
        raise ToolError("文件名不合法；只能使用当前目录下的 .md 文件名")
    if not value.lower().endswith(".md"):
        raise ToolError("只允许操作 .md 文件")
    target = library.ensure_within(ref.directory, ref.directory / value)
    return target


def _read_target(ref: library.PathRef, name: str) -> tuple[Path, str]:
    target = _resolve(ref, name)
    if not target.is_file():
        raise ToolError(f"文件不存在：{name}")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise ToolError(f"无法读取文件：{exc}")
    if size > MAX_DOC_BYTES * 2:
        raise ToolError("文件过大，无法在助手中读取")
    return target, target.read_text(encoding="utf-8", errors="replace")


def _numbered_header(name: str, content: str) -> None:
    match = re.match(r"^(\d{2})-.+\.md$", name)
    if not match:
        raise ToolError("文件名必须为 NN-标题.md（NN 为两位数字）")
    if match.group(1) == "00":
        raise ToolError("00 预留给学习路径总览，新讲义请使用 01..99")
    prefix = f"# {match.group(1)} · "
    if not content.startswith(prefix):
        raise ToolError(f"正文第一行必须以 '{prefix}' 开头")


def _backup(data_dir: Path, path_name: str, file_name: str, content: str) -> Path | None:
    try:
        folder = Path(data_dir) / "trash" / "edits"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = utc_now_iso().replace(":", "").replace("-", "")[:15]
        safe_path = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", path_name)[:40] or "path"
        safe_file = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", file_name)[:80]
        target = folder / f"{stamp}-{safe_path}-{safe_file}"
        write_text_atomic(target, content)
        return target
    except OSError:
        return None


def execute(
    name: str,
    arguments: dict[str, Any],
    *,
    ref: library.PathRef,
    current_file: str,
    data_dir: Path,
    index_cache: Any,
) -> dict[str, Any]:
    try:
        if not isinstance(arguments, dict):
            arguments = {}
        if name == "read":
            file_name = str(arguments.get("file") or current_file or "").strip()
            if not file_name:
                names = [item["name"] for item in library.list_md_files(ref.directory)]
                return {"ok": False, "error": "当前没有打开文件；请指定 file。可用文件：" + "、".join(names[:30])}
            target, content = _read_target(ref, file_name)
            return {
                "ok": True,
                "tool": "read",
                "file": file_name,
                "bytes": target.stat().st_size,
                "first_line": content.splitlines()[0] if content else "",
                "content": content[:MAX_READ_CHARS],
                "truncated": len(content) > MAX_READ_CHARS,
            }
        if name == "edit":
            file_name = str(arguments.get("file") or current_file or "").strip()
            old_text = str(arguments.get("old_text") or "")
            new_text = str(arguments.get("new_text") or "")
            if not file_name:
                raise ToolError("edit 需要 file 参数")
            if not old_text:
                raise ToolError("old_text 不能为空")
            if len(old_text) > 100000 or len(new_text) > 100000:
                raise ToolError("old_text/new_text 过长")
            target, content = _read_target(ref, file_name)
            count = content.count(old_text)
            if count != 1:
                raise ToolError(f"old_text 在文件中出现了 {count} 次，必须恰好出现一次")
            updated = content.replace(old_text, new_text, 1)
            _numbered_header(file_name, updated)
            backup = _backup(data_dir, ref.directory.name, file_name, content)
            write_text_atomic(target, updated)
            index_cache.invalidate_dir(ref.directory)
            return {
                "ok": True,
                "tool": "edit",
                "file": file_name,
                "changed": len(updated) - len(content),
                "bytes": len(updated.encode("utf-8")),
                "backup": str(backup) if backup else None,
                "reason": str(arguments.get("reason") or "")[:200],
            }
        if name == "write":
            file_name = str(arguments.get("file") or "").strip()
            body = str(arguments.get("content") or "")
            if not file_name or not body:
                raise ToolError("write 需要 file 与 content 参数")
            if len(body.encode("utf-8")) > MAX_DOC_BYTES:
                raise ToolError("新文档超过 1 MiB 限制")
            target = _resolve(ref, file_name)
            if target.exists():
                raise ToolError(f"文件已存在，不能覆盖：{file_name}")
            _numbered_header(file_name, body)
            if "## 本节目标" not in body or "## 核心概念" not in body:
                raise ToolError("新文档必须包含 '## 本节目标' 与 '## 核心概念' 两个小节")
            write_text_atomic(target, body if body.endswith("\n") else body + "\n")
            index_cache.invalidate_dir(ref.directory)
            return {
                "ok": True,
                "tool": "write",
                "file": file_name,
                "bytes": len(body.encode("utf-8")),
                "first_line": body.splitlines()[0],
                "reason": str(arguments.get("reason") or "")[:200],
            }
        if name == "highlight":
            file_name = str(arguments.get("file") or current_file or "").strip()
            quote = str(arguments.get("quote") or "").strip()
            if not file_name or not quote:
                raise ToolError("highlight 需要 quote 参数（以及可选的 file）")
            if len(quote) > 800:
                raise ToolError("高亮片段不能超过 800 字符")
            _, content = _read_target(ref, file_name)
            position = content.find(quote)
            if position < 0:
                raise ToolError("quote 没有在文件中逐字出现，高亮失败")
            return {
                "ok": True,
                "tool": "highlight",
                "file": file_name,
                "quote": quote,
                "note": str(arguments.get("note") or "")[:300],
                "start": position,
            }
        raise ToolError(f"未知工具：{name}")
    except ToolError as exc:
        return {"ok": False, "tool": name, "error": str(exc)}
    except ApiError as exc:
        return {"ok": False, "tool": name, "error": exc.message}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}


def result_text(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)
