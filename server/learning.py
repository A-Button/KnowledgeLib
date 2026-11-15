"""Learning-path generation: planning, material writing and numbered files."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from . import library
from .errors import ApiError
from .llm import ChatClient, LLMError
from .prompts import module_messages, plan_system_prompt, plan_user_prompt, repair_messages
from .storage import write_text_atomic

FENCE = chr(96) * 3
MIN_BODY = 150


class PlanError(Exception):
    pass


class MaterialError(Exception):
    pass


def _strip_fence(text: str) -> str:
    value = text.strip()
    if value.startswith(FENCE):
        rest = value[len(FENCE) :]
        if rest.startswith("\r\n"):
            rest = rest[2:]
        elif rest.startswith("\n"):
            rest = rest[1:]
        elif "\n" in rest:
            rest = rest.split("\n", 1)[1]
        else:
            rest = ""
        value = rest
        if value.rstrip().endswith(FENCE):
            value = value.rstrip()[: -len(FENCE)].rstrip()
    return value


def parse_plan_json(raw: str) -> dict[str, Any]:
    text = _strip_fence(raw or "")
    start = text.find("{")
    if start < 0:
        raise PlanError("输出中没有 JSON 对象")
    depth = 0
    in_string = False
    escaped = False
    end = -1
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end < 0:
        raise PlanError("JSON 对象没有闭合")
    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise PlanError(f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）")
    if not isinstance(data, dict):
        raise PlanError("JSON 根节点必须是对象")
    return data


def _one_line(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def validate_plan(data: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    title = _one_line(data.get("title"), 80)
    summary = _one_line(data.get("summary"), 600)
    if not title:
        title = f"{spec['domain']}学习路径"
    modules_raw = data.get("modules")
    if not isinstance(modules_raw, list) or not modules_raw:
        raise PlanError("modules 必须是非空数组")
    expected = int(spec["moduleCount"])
    if len(modules_raw) != expected:
        raise PlanError(f"modules 数量应为 {expected}，实际为 {len(modules_raw)}")

    def sort_key(item: Any) -> Any:
        if isinstance(item, dict):
            try:
                return int(item.get("index", 0))
            except (TypeError, ValueError):
                return 0
        return 0

    ordered = sorted(modules_raw, key=sort_key)
    modules: list[dict[str, Any]] = []
    total_hours = float(spec["weeks"] * spec["hoursPerWeek"])
    default_hours = max(1.0, round(total_hours / expected, 1))
    for position, item in enumerate(ordered, start=1):
        if not isinstance(item, dict):
            raise PlanError(f"第 {position} 个模块不是对象")
        module_title = _one_line(item.get("title"), 40)
        if not module_title:
            raise PlanError(f"第 {position} 个模块缺少标题")
        objectives = [
            _one_line(value, 120)
            for value in (item.get("objectives") or [])
            if isinstance(value, (str, int, float)) and _one_line(value, 120)
        ][:4]
        if not objectives:
            objectives = [f"掌握「{module_title}」的核心概念并能复述要点"]
        try:
            hours = float(item.get("estimatedHours") or default_hours)
        except (TypeError, ValueError):
            hours = default_hours
        hours = max(0.5, min(12.0, round(hours, 1)))
        prerequisites: list[int] = []
        for value in item.get("prerequisites") or []:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= number < position and number not in prerequisites:
                prerequisites.append(number)
        topics = [
            _one_line(value, 40)
            for value in (item.get("topics") or [])
            if isinstance(value, (str, int, float)) and _one_line(value, 40)
        ][:8]
        modules.append(
            {
                "index": position,
                "title": module_title,
                "summary": _one_line(item.get("summary"), 300),
                "objectives": objectives,
                "prerequisites": prerequisites,
                "estimatedHours": hours,
                "topics": topics,
                "practice": _one_line(item.get("practice"), 300),
            }
        )

    total_planned = sum(module["estimatedHours"] for module in modules)
    return {
        "title": title,
        "summary": summary,
        "modules": modules,
        "plannedHours": round(total_planned, 1),
        "requestedHours": total_hours,
    }


def clean_body(raw: str) -> str:
    text = (raw or "").lstrip("\ufeff").strip()
    text = _strip_fence(text)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4 :].lstrip()
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("# ")):
        lines.pop(0)
    text = "\n".join(lines).strip()
    if not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text.startswith("#"):
        text = "## 核心概念\n\n" + text
    return text.strip()


async def plan_with_repair(
    client: ChatClient,
    spec: dict[str, Any],
    tools: list[dict[str, Any]] | None = None,
    thinking: str = "off",
    model: str | None = None,
) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": plan_system_prompt(spec)},
        {"role": "user", "content": plan_user_prompt(spec)},
    ]
    last_error = ""
    raw = ""
    for attempt in range(2):
        if attempt == 1:
            messages = repair_messages(spec, raw, last_error)
        raw = await client.complete(messages, retries=1, tools=tools, thinking=thinking, model=model)
        try:
            return validate_plan(parse_plan_json(raw), spec)
        except PlanError as exc:
            last_error = str(exc)
            if attempt == 1:
                raise PlanError(last_error)
    raise PlanError(last_error or "规划失败")


async def material_with_repair(
    client: ChatClient,
    spec: dict[str, Any],
    plan: dict[str, Any],
    module: dict[str, Any],
    previous_title: str | None,
    next_title: str | None,
    tools: list[dict[str, Any]] | None = None,
    thinking: str = "off",
    model: str | None = None,
) -> str:
    strict = False
    last = ""
    for attempt in range(2):
        messages = module_messages(spec, plan, module, previous_title, next_title, strict=strict)
        raw = await client.complete(messages, retries=1, tools=tools, thinking=thinking, model=model)
        body = clean_body(raw)
        last = body
        if len(body) >= MIN_BODY:
            return body
        strict = True
    raise MaterialError(f"第 {module['index']:02d} 讲内容过短（{len(last)} 字符）")


def make_file_name(index: int, title: str) -> str:
    if index == 0:
        return library.OVERVIEW_NAME
    slug = library.slugify_title(title) or f"module-{index:02d}"
    return f"{index:02d}-{slug}.md"


def _escape_cell(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def _goal_text(spec: dict[str, Any]) -> str:
    from .prompts import GOAL_LABELS

    labels = [GOAL_LABELS.get(item, item) for item in spec.get("goals") or []]
    return "、".join(labels) if labels else "系统掌握"


def _level_text(spec: dict[str, Any]) -> str:
    from .prompts import LEVEL_LABELS

    return LEVEL_LABELS.get(spec.get("level", "beginner"), spec.get("level", "入门"))


def _thinking_text(value: str) -> str:
    return {"off": "关闭", "low": "低", "high": "高", "max": "最高"}.get(value or "off", "关闭")


def build_overview(
    spec: dict[str, Any],
    plan: dict[str, Any],
    meta: dict[str, Any],
    status: str,
) -> str:
    total = spec["weeks"] * spec["hoursPerWeek"]
    modules = plan.get("modules") or []
    status_map = {item.get("index"): item.get("status") for item in meta.get("modules") or []}
    missing = [module["index"] for module in modules if status_map.get(module["index"]) != "done"]
    status_line = {
        "generating": "生成中：课程地图已就绪，正在逐讲撰写。",
        "complete": "已完成：全部讲次均已生成。",
        "partial": "部分完成：有讲次生成失败，可点击继续生成缺失章节。",
        "failed": "生成失败：规划或写入未完成，请重试。",
        "planning": "规划中。",
        "external": "外部目录：非本应用生成。",
    }.get(status, status)
    if status == "partial" and missing:
        status_line += " 缺少：" + "、".join(f"{number:02d}" for number in missing) + "。"

    rows = []
    for module in modules:
        prereq = "、".join(f"{number:02d}" for number in module.get("prerequisites") or []) or "无"
        objective = (module.get("objectives") or [""])[0]
        link = make_file_name(module["index"], module["title"])
        rows.append(
            f"| {module['index']:02d} | [{_escape_cell(module['title'])}]({link}) | "
            f"{module.get('estimatedHours', '')}h | {prereq} | {_escape_cell(objective)} |"
        )
    table = "\n".join(rows) if rows else "| 00 | 暂无模块 | - | - | - |"

    checklist = "\n".join(
        f"- [ ] {module['index']:02d} {module['title']}" for module in modules
    ) or "- [ ] 暂无模块"
    generated_at = meta.get("createdAt") or ""
    try:
        stamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        stamp = generated_at

    return f"""# 00 · 学习路径总览

> 学习领域：{spec['domain']} ｜ 周期：{spec['weeks']} 周 ｜ 每周 {spec['hoursPerWeek']} 小时 ｜ 总计 {total} 小时 ｜ 水平：{_level_text(spec)}

## 学习目标

{plan.get('summary') or '按模块循序推进，完成每一讲的练习与自测。'}

目标标签：{_goal_text(spec)}。偏好：{spec.get('preferences') or '无特别偏好'}。

## 路径参数

| 项目 | 值 |
| --- | --- |
| 领域 | {_escape_cell(spec['domain'])} |
| 周期 | {spec['weeks']} 周 × {spec['hoursPerWeek']} 小时 = {total} 小时 |
| 模块数 | {len(modules)} |
| 生成模型 | {meta.get('model') or '未知'} |
| 联网生成 | {'是（DeepSeek web_tools）' if spec.get('webEnabled') else '否'} |
| 思考强度 | {_thinking_text(spec.get('thinking', 'off'))} |
| 生成时间 | {stamp} |
| 当前状态 | {status_line} |

## 模块地图

| 编号 | 模块 | 预计时长 | 前置 | 本节目标 |
| --- | --- | --- | --- | --- |
{table}

## 学习节奏建议

建议每周安排 2..4 次学习，每次 60..120 分钟；先读本讲正文，再完成练习，最后用自测清单检查。每完成一讲，把对应文件开头的编号记到自己的进度表里，避免跳读。遇到卡点先回到前置模块，或在阅读助手里针对这一讲提问。

## 完成清单

{checklist}
"""


def render_module(
    spec: dict[str, Any],
    plan: dict[str, Any],
    module: dict[str, Any],
    body: str,
) -> str:
    lookup = {item["index"]: item for item in plan.get("modules") or []}
    prereq = "、".join(
        f"{number:02d} · {lookup[number]['title']}"
        for number in module.get("prerequisites") or []
        if number in lookup
    ) or "无"
    objectives = "\n".join(f"- {item}" for item in module.get("objectives") or [])
    return f"""# {module['index']:02d} · {module['title']}

> 预计学习时间：{module.get('estimatedHours')} 小时 ｜ 前置模块：{prereq} ｜ 学习领域：{spec['domain']}

## 本节目标

{objectives}

{body.strip()}
"""


def _module_entries(plan: dict[str, Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_index = {int(item.get("index", 0)): item for item in existing if item.get("index")}
    entries: list[dict[str, Any]] = []
    for module in plan.get("modules") or []:
        old = by_index.get(module["index"], {})
        entries.append(
            {
                "index": module["index"],
                "title": module["title"],
                "file": make_file_name(module["index"], module["title"]),
                "status": old.get("status", "pending"),
                "bytes": int(old.get("bytes") or 0),
                "error": old.get("error"),
            }
        )
    return entries


def _set_module(meta: dict[str, Any], index: int, **changes: Any) -> None:
    for item in meta.get("modules") or []:
        if int(item.get("index", 0)) == index:
            item.update(changes)
            return


def _overview_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


async def generation_events(
    state: Any,
    ref: library.PathRef,
    resume: bool = False,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    meta = ref.meta
    spec = dict(meta.get("spec") or {})
    provider_deepseek = state.config.is_deepseek()
    web_enabled = bool(spec.get("webEnabled")) and provider_deepseek
    thinking = str(spec.get("thinking") or "off")
    model = "deepseek-flash" if web_enabled else (meta.get("model") or state.config.model())
    meta["model"] = model
    meta["webEnabled"] = web_enabled
    tools: list[dict[str, Any]] = []
    if web_enabled:
        tools.append({"type": "deepseek_web_search"})
        if thinking == "off":
            tools.append({"type": "deepseek_read_url"})
    client = ChatClient(state.config.base_url(), state.config.api_key(), model, state.config.timeout())
    started = time.monotonic()
    finalized = False
    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
    workers: list[asyncio.Task[Any]] = []
    coordinator: asyncio.Task[Any] | None = None

    yield (
        "start",
        {
            "pathId": ref.id,
            "name": meta.get("name"),
            "spec": spec,
            "model": model,
            "webEnabled": web_enabled,
            "thinking": thinking,
            "moduleCount": int(spec.get("moduleCount") or 0),
            "resume": resume,
        },
    )

    try:
        plan = meta.get("plan")
        if not isinstance(plan, dict):
            meta["status"] = "planning"
            library.write_path_meta(ref.directory, meta)
            try:
                plan = await plan_with_repair(client, spec, tools=tools or None, thinking=thinking, model=model)
            except (PlanError, LLMError) as exc:
                message = exc.message if isinstance(exc, LLMError) else str(exc)
                meta["status"] = "failed"
                meta["errors"] = [{"index": 0, "message": message}]
                library.write_path_meta(ref.directory, meta)
                payload = {
                    "code": "plan_failed",
                    "message": message,
                    "status": getattr(exc, "status_code", None),
                    "retryable": getattr(exc, "retryable", False),
                }
                finalized = True
                yield ("error", payload)
                yield ("done", {"pathId": ref.id, "status": "failed", "files": len(ref.files), "elapsedMs": int((time.monotonic() - started) * 1000)})
                return
            meta["plan"] = plan
            meta["name_locked"] = bool(meta.get("name_locked"))
            if not meta.get("name_locked"):
                meta["name"] = str(plan["title"])[:60]
            meta["modules"] = _module_entries(plan, [])
            meta["status"] = "generating"
            meta["errors"] = []
            library.write_path_meta(ref.directory, meta)
            yield ("plan", {"plan": plan})
        else:
            meta["modules"] = _module_entries(plan, meta.get("modules") or [])
            meta["status"] = "generating"
            meta["errors"] = []
            library.write_path_meta(ref.directory, meta)
            yield ("plan", {"plan": plan})

        overview = build_overview(spec, plan, meta, "generating")
        overview_bytes = write_text_atomic(ref.directory / library.OVERVIEW_NAME, overview)
        yield ("file", {"index": 0, "name": library.OVERVIEW_NAME, "bytes": overview_bytes})

        modules = plan.get("modules") or []
        lookup = {item["index"]: item for item in modules}
        missing: list[dict[str, Any]] = []
        for module in modules:
            entry = next(
                (item for item in meta["modules"] if int(item.get("index", 0)) == module["index"]),
                None,
            )
            target = ref.directory / (entry or {}).get("file", make_file_name(module["index"], module["title"]))
            if entry and entry.get("status") == "done" and target.is_file():
                continue
            missing.append(module)
            if entry:
                entry["status"] = "pending"
                entry["error"] = None
        library.write_path_meta(ref.directory, meta)

        sem = asyncio.Semaphore(3)
        failed = False

        async def worker(module: dict[str, Any]) -> None:
            nonlocal failed
            async with sem:
                await queue.put(("module", {"index": module["index"], "title": module["title"], "status": "started"}))
                try:
                    previous_title = lookup[module["index"] - 1]["title"] if module["index"] > 1 and module["index"] - 1 in lookup else None
                    next_title = lookup[module["index"] + 1]["title"] if module["index"] + 1 in lookup else None
                    body = await material_with_repair(
                        client,
                        spec,
                        plan,
                        module,
                        previous_title,
                        next_title,
                        tools=tools or None,
                        thinking=thinking,
                        model=model,
                    )
                    content = render_module(spec, plan, module, body)
                    file_name = make_file_name(module["index"], module["title"])
                    wrote = write_text_atomic(ref.directory / file_name, content)
                    _set_module(meta, module["index"], status="done", bytes=wrote, error=None, file=file_name)
                    library.write_path_meta(ref.directory, meta)
                    await queue.put(("module", {"index": module["index"], "title": module["title"], "status": "done", "bytes": wrote}))
                    await queue.put(("file", {"index": module["index"], "name": file_name, "bytes": wrote}))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failed = True
                    message = exc.message if isinstance(exc, (LLMError, MaterialError)) else str(exc)
                    _set_module(meta, module["index"], status="failed", error=message)
                    meta.setdefault("errors", []).append({"index": module["index"], "message": message})
                    library.write_path_meta(ref.directory, meta)
                    await queue.put(("module", {"index": module["index"], "title": module["title"], "status": "failed", "error": message}))
                finally:
                    await queue.put(("__worker_done__", {}))

        async def coordinate() -> None:
            try:
                await asyncio.gather(*workers, return_exceptions=True)
            finally:
                await queue.put(("__all_done__", {}))

        workers = [asyncio.create_task(worker(module)) for module in missing]
        coordinator = asyncio.create_task(coordinate())
        while True:
            kind, payload = await queue.get()
            if kind == "__all_done__":
                break
            if kind == "__worker_done__":
                continue
            yield (kind, payload)

        status = "partial" if failed else "complete"
        meta["status"] = status
        library.write_path_meta(ref.directory, meta)
        overview = build_overview(spec, plan, meta, status)
        overview_bytes = write_text_atomic(ref.directory / library.OVERVIEW_NAME, overview)
        ref.files = library.list_md_files(ref.directory)
        finalized = True
        yield ("file", {"index": 0, "name": library.OVERVIEW_NAME, "bytes": overview_bytes})
        yield ("status", {"pathId": ref.id, "status": status})
        yield (
            "done",
            {
                "pathId": ref.id,
                "status": status,
                "files": len(ref.files),
                "elapsedMs": int((time.monotonic() - started) * 1000),
            },
        )
    except asyncio.CancelledError:
        if not finalized:
            meta["status"] = "partial" if meta.get("plan") else "failed"
            try:
                library.write_path_meta(ref.directory, meta)
            except OSError:
                pass
        raise
    except Exception as exc:  # noqa: BLE001
        meta["status"] = "failed"
        meta["errors"] = [{"index": 0, "message": str(exc)}]
        try:
            library.write_path_meta(ref.directory, meta)
        except OSError:
            pass
        finalized = True
        yield ("error", {"code": "generation_failed", "message": str(exc), "retryable": False})
        yield ("done", {"pathId": ref.id, "status": "failed", "files": len(ref.files), "elapsedMs": int((time.monotonic() - started) * 1000)})
    finally:
        for task in workers:
            if not task.done():
                task.cancel()
        if coordinator and not coordinator.done():
            coordinator.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        if coordinator:
            await asyncio.gather(coordinator, return_exceptions=True)
