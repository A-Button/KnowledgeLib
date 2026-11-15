"""Reading-assistant chat: RAG, DeepSeek server web tools and document tools."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import doc_tools, indexer, library
from .deps import SSE_HEADERS, get_state, sse_event
from .errors import ApiError
from .llm import LLMError, client_from_config, ensure_api_ready
from .prompts import chat_messages, chat_system_prompt, format_kb_context, format_web_context
from .routes_common import trim_sources
from .webtools import WebToolError, webtools_from_config

router = APIRouter(prefix="/api/chat", tags=["chat"])

WEB_LIMIT = 5000
MAX_TOOL_ROUNDS = 4
THINKING_LEVELS = ("off", "low", "high", "max")

# The web-tool model documented by DeepSeek.
DEEPSEEK_WEB_MODEL = "deepseek-flash"


class HistoryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str = ""
    content: str = ""
    reasoning: str = ""


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kbId: str | None = None
    pathId: str | None = None
    file: str | None = None
    message: str
    history: list[HistoryItem] = Field(default_factory=list)
    mode: Literal["kb", "kb_web", "web", "plain"] = "kb"
    thinking: Literal["off", "low", "high", "max"] = "off"
    enableTools: bool = True
    stream: bool = True


def _clean_history(items: list[HistoryItem], include_reasoning: bool) -> list[dict[str, str]]:
    kept: list[dict[str, str]] = []
    total = 0
    for item in reversed(items[-20:]):
        if item.role not in ("user", "assistant"):
            continue
        content = (item.content or "").strip()
        if not content:
            continue
        if len(content) > 8000:
            content = content[:8000]
        if total + len(content) > 12000:
            break
        entry: dict[str, str] = {"role": item.role, "content": content}
        if include_reasoning and item.role == "assistant" and item.reasoning:
            entry["reasoning_content"] = item.reasoning[:8000]
        kept.append(entry)
        total += len(content)
    kept.reverse()
    return kept


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or url
    except Exception:
        return url


async def _collect_web(
    state: Any,
    query: str,
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    tools = webtools_from_config(state.config)
    short = query.strip()[:128]
    events.append({"tool": "search", "status": "started", "query": short})
    try:
        results = await tools.search(
            short,
            count=state.config.web_search_count(),
            ori_question=short,
            need_content=False,
        )
    except WebToolError as exc:
        events.append({"tool": "search", "status": "error", "code": exc.code, "message": exc.message})
        return [], f"联网检索失败：{exc.message}"

    events.append({"tool": "search", "status": "done", "count": len(results)})
    hits: list[dict[str, Any]] = []
    for position, item in enumerate(results[:5], start=1):
        hits.append(
            {
                "ref": f"W{position}",
                "kind": "web",
                "title": item.get("title") or item.get("url") or "网页结果",
                "url": item.get("url") or "",
                "date": item.get("date"),
                "site": item.get("site_name"),
                "score": item.get("rerank_score"),
                "text": item.get("summary") or item.get("snippet") or item.get("content") or "",
            }
        )

    top_n = state.config.web_fetch_top_n()
    pairs = [(index, hit["url"]) for index, hit in enumerate(hits) if hit["url"]][:top_n]
    if pairs:
        events.append({"tool": "fetch", "status": "started", "urls": [_host(url) for _, url in pairs]})
        outcomes = await asyncio.gather(
            *(tools.fetch(url) for _, url in pairs),
            return_exceptions=True,
        )
        ok = 0
        failed = 0
        for (index, _), outcome in zip(pairs, outcomes):
            if isinstance(outcome, dict) and outcome.get("content"):
                hits[index]["text"] = str(outcome.get("content"))[:3000]
                if outcome.get("title"):
                    hits[index]["title"] = str(outcome["title"])
                ok += 1
            else:
                failed += 1
        events.append({"tool": "fetch", "status": "done", "ok": ok, "failed": failed})

    if not hits:
        return [], "本轮联网检索没有返回结果，只能依据知识库或通用知识回答。"
    return hits, ""


def _sources(kb_hits: list[dict[str, Any]], web_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for hit in kb_hits:
        result.append(
            {
                "ref": hit["ref"],
                "kind": "kb",
                "title": hit.get("heading") or hit.get("file"),
                "pathId": hit.get("pathId"),
                "pathName": hit.get("pathName"),
                "file": hit.get("file"),
                "fileIndex": hit.get("fileIndex"),
                "heading": hit.get("heading"),
                "text": hit.get("text"),
                "score": hit.get("score"),
            }
        )
    for hit in web_hits:
        result.append(
            {
                "ref": hit["ref"],
                "kind": "web",
                "title": hit.get("title"),
                "url": hit.get("url"),
                "date": hit.get("date"),
                "site": hit.get("site"),
                "text": hit.get("text"),
                "score": hit.get("score"),
            }
        )
    return result


def _server_web_hits(server_calls: list[dict[str, Any]], annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    hits: list[dict[str, Any]] = []

    def add(url: Any, title: Any, text: Any, date: Any = None, site: Any = None) -> None:
        value = str(url or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        hits.append(
            {
                "ref": f"W{len(hits) + 1}",
                "kind": "web",
                "title": str(title or value),
                "url": value,
                "date": date,
                "site": site,
                "score": None,
                "text": str(text or "")[:2000],
            }
        )

    for step in server_calls or []:
        for call in step.get("calls") or []:
            for source in call.get("sources") or []:
                add(
                    source.get("url"),
                    source.get("title"),
                    source.get("snippet") or source.get("summary"),
                    source.get("published_at"),
                    source.get("site_name"),
                )
            page = call.get("page")
            if isinstance(page, dict):
                add(page.get("url"), page.get("title"), "", page.get("published_at"), None)
    for annotation in annotations or []:
        citation = annotation.get("url_citation") if isinstance(annotation, dict) else None
        if isinstance(citation, dict):
            add(citation.get("url"), citation.get("title"), "")
    return hits


def _validate(req: ChatRequest) -> str:
    message = (req.message or "").strip()
    if not message:
        raise ApiError(400, "invalid_request", "问题不能为空")
    if len(message) > 4000:
        raise ApiError(400, "invalid_request", "问题不能超过 4000 字符")
    if req.thinking not in THINKING_LEVELS:
        raise ApiError(400, "invalid_request", "思考强度不合法")
    return message


def _kb_context(state: Any, kb_id: str | None) -> tuple[Any, str, Any]:
    if not kb_id:
        raise ApiError(400, "invalid_request", "该模式需要指定知识库")
    root = state.config.kb_root()
    kb_dir = library.find_kb(root, kb_id)
    meta = library.read_kb_meta(kb_dir)
    return kb_dir, meta.get("name", ""), meta


def _tool_specs(
    state: Any,
    req: ChatRequest,
    path_ref: Any,
    server_web: bool,
    suppress_client: bool = False,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if server_web:
        specs.append({"type": "deepseek_web_search"})
        if req.thinking == "off":
            specs.append({"type": "deepseek_read_url"})
    if req.enableTools and path_ref is not None and not suppress_client:
        specs.extend(doc_tools.tool_specs())
    return specs


def _assemble_tool_calls(deltas: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for index in sorted(deltas):
        item = deltas[index]
        if not item.get("name"):
            continue
        calls.append(
            {
                "id": item.get("id") or f"call_{index}",
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": item.get("arguments") or "{}",
                },
            }
        )
    return calls


def _tool_event(name: str, status: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"tool": name, "status": status}
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


@router.post("")
async def chat(req: ChatRequest, request: Request):
    state = get_state(request)
    message = _validate(req)
    ensure_api_ready(state.config)
    needs_kb = req.mode in ("kb", "kb_web")
    kb_dir = None
    kb_name = ""
    if needs_kb:
        kb_dir, kb_name, _ = _kb_context(state, req.kbId)

    path_ref = None
    if kb_dir is not None and req.pathId:
        try:
            path_ref = library.find_path(kb_dir, req.pathId)
        except ApiError:
            path_ref = None

    current_file = ""
    current_text = ""
    if path_ref is not None and req.file:
        try:
            payload = library.read_md(kb_dir, path_ref.id, req.file)
            current_file = payload["name"]
            current_text = (payload["content"] or "")[:5000]
        except ApiError:
            current_file = ""
            current_text = ""

    provider_deepseek = state.config.is_deepseek()
    wants_web = req.mode in ("web", "kb_web")
    client_tools = bool(req.enableTools and path_ref is not None)
    server_web = bool(provider_deepseek and wants_web)
    # DeepSeek accepts client tools in exactly two quadrants:
    #   web + thinking on  -> server web tool accompanies the function tools
    #   no web + thinking off -> function tools run without server tools
    # The other two combinations are rejected upstream, so drop the function
    # tools for this turn instead of failing the whole request.
    suppress_client = bool(
        provider_deepseek
        and client_tools
        and (
            (wants_web and req.thinking == "off")
            or (not wants_web and req.thinking != "off")
        )
    )
    suppress_reason = ("web" if wants_web else "thinking") if suppress_client else None
    model = DEEPSEEK_WEB_MODEL if server_web else state.config.model()
    tools = _tool_specs(state, req, path_ref, server_web, suppress_client=suppress_client)
    include_reasoning = provider_deepseek and req.thinking != "off" and bool(tools)
    history = _clean_history(req.history, include_reasoning)
    client = client_from_config(state.config, model=model)
    started = time.monotonic()
    current_files = [item["name"] for item in path_ref.files] if path_ref is not None else []

    if not req.stream:
        kb_hits: list[dict[str, Any]] = []
        if kb_dir is not None:
            kb_hits = await run_in_threadpool(
                indexer.search, kb_dir, message, state.config.top_k(), state.index_cache
            )
            kb_hits = trim_sources(kb_hits, state.config.context_chars())
        web_hits: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        web_note = ""
        if req.mode in ("web", "kb_web") and not server_web:
            web_hits, web_note = await _collect_web(state, message, events)
            web_hits = trim_sources(web_hits, WEB_LIMIT)
        note = web_note
        if needs_kb and not kb_hits:
            note = (note + " " if note else "") + "知识库中没有检索到直接相关的内容。"
        system = chat_system_prompt(
            kb_name,
            format_kb_context(kb_hits),
            format_web_context(web_hits),
            note,
            current_file,
            current_text,
            current_files,
        )
        messages = chat_messages(system, history, message)
        try:
            answer = await client.complete(
                messages,
                retries=0,
                tools=tools if server_web else None,
                thinking=req.thinking,
                model=model,
            )
        except LLMError as exc:
            raise ApiError(
                502 if exc.status_code >= 500 else exc.status_code,
                exc.code,
                exc.message,
                {"retryable": exc.retryable},
            )
        return {
            "answer": answer,
            "reasoning": "",
            "sources": _sources(kb_hits, web_hits),
            "toolEvents": events,
            "model": model,
            "thinking": req.thinking,
            "toolsSuppressed": suppress_client,
            "suppressReason": suppress_reason,
            "elapsedMs": int((time.monotonic() - started) * 1000),
        }

    async def stream():
        nonlocal current_file
        yield sse_event(
            "meta",
            {
                "mode": req.mode,
                "model": model,
                "thinking": req.thinking,
                "provider": "deepseek" if provider_deepseek else "generic",
                "webMode": "server" if server_web else ("direct" if wants_web else "off"),
                "suppressReason": suppress_reason,
                "tools": [
                    item.get("function", {}).get("name") if item.get("type") == "function" else item.get("type")
                    for item in (tools or [])
                ],
                "toolsSuppressed": suppress_client,
                "kb": {"id": req.kbId, "name": kb_name} if needs_kb else None,
            },
        )
        kb_hits: list[dict[str, Any]] = []
        if kb_dir is not None:
            kb_hits = await run_in_threadpool(
                indexer.search, kb_dir, message, state.config.top_k(), state.index_cache
            )
            kb_hits = trim_sources(kb_hits, state.config.context_chars())

        web_hits: list[dict[str, Any]] = []
        web_note = ""
        if req.mode in ("web", "kb_web") and not server_web:
            web_events: list[dict[str, Any]] = []
            web_hits, web_note = await _collect_web(state, message, web_events)
            web_hits = trim_sources(web_hits, WEB_LIMIT)
            for event in web_events:
                yield sse_event("tool", event)

        note = web_note
        if needs_kb and not kb_hits:
            note = (note + " " if note else "") + "知识库中没有检索到直接相关的内容。"
        system = chat_system_prompt(
            kb_name,
            format_kb_context(kb_hits),
            format_web_context(web_hits),
            note,
            current_file,
            current_text,
            current_files,
        )
        messages = chat_messages(system, history, message)
        sources = _sources(kb_hits, web_hits)
        yield sse_event("sources", {"sources": sources, "note": note})

        collected_server_calls: list[dict[str, Any]] = []
        collected_annotations: list[dict[str, Any]] = []
        usage = None
        try:
            for round_index in range(MAX_TOOL_ROUNDS + 1):
                round_content = ""
                round_reasoning = ""
                tool_deltas: dict[int, dict[str, Any]] = {}
                async for part in client.stream(
                    messages,
                    tools=tools or None,
                    thinking=req.thinking,
                    model=model,
                ):
                    if part["type"] == "content":
                        round_content += part["text"]
                        yield sse_event("delta", {"text": part["text"]})
                    elif part["type"] == "reasoning":
                        round_reasoning += part["text"]
                        yield sse_event("reasoning", {"text": part["text"]})
                    elif part["type"] == "usage":
                        usage = part["usage"]
                    elif part["type"] == "tool_call_delta":
                        index = int(part["index"])
                        entry = tool_deltas.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if part.get("id"):
                            entry["id"] = part["id"]
                        if part.get("name"):
                            entry["name"] = part["name"]
                        entry["arguments"] += str(part.get("arguments") or "")
                    elif part["type"] == "server_tool_calls":
                        collected_server_calls.extend(part.get("calls") or [])
                    elif part["type"] == "annotations":
                        collected_annotations.extend(part.get("annotations") or [])
                    elif part["type"] == "server_tool_event":
                        event = part.get("event") or {}
                        if event.get("type") == "call_started":
                            yield sse_event("tool", _tool_event("web", "started", detail=event.get("call", {}).get("kind")))
                        elif event.get("type") == "call_completed":
                            yield sse_event("tool", _tool_event("web", "done", detail=event.get("call", {}).get("kind")))

                tool_calls = _assemble_tool_calls(tool_deltas)
                if not tool_calls:
                    break
                if round_index >= MAX_TOOL_ROUNDS:
                    yield sse_event(
                        "error",
                        {"code": "tool_limit", "message": "工具调用次数过多，已停止本轮编辑。", "retryable": False},
                    )
                    break

                assistant_message: dict[str, Any] = {"role": "assistant", "content": round_content}
                if include_reasoning:
                    assistant_message["reasoning_content"] = round_reasoning
                assistant_message["tool_calls"] = tool_calls
                messages.append(assistant_message)

                for call in tool_calls:
                    call_id = call["id"]
                    name = call["function"]["name"]
                    raw_args = call["function"].get("arguments") or "{}"
                    try:
                        arguments = json.loads(raw_args)
                    except json.JSONDecodeError:
                        arguments = {}
                    yield sse_event(
                        "tool",
                        _tool_event(
                            name,
                            "started",
                            file=arguments.get("file") if isinstance(arguments, dict) else None,
                            reason=arguments.get("reason") if isinstance(arguments, dict) else None,
                        ),
                    )
                    if path_ref is None:
                        result = {"ok": False, "tool": name, "error": "当前没有打开学习路径，不能使用文档工具。"}
                    else:
                        result = doc_tools.execute(
                            name,
                            arguments if isinstance(arguments, dict) else {},
                            ref=path_ref,
                            current_file=current_file,
                            data_dir=state.data_dir,
                            index_cache=state.index_cache,
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": doc_tools.result_text(result),
                        }
                    )
                    if result.get("ok"):
                        yield sse_event(
                            "tool",
                            _tool_event(
                                name,
                                "done",
                                ok=True,
                                file=result.get("file"),
                                bytes=result.get("bytes"),
                            ),
                        )
                        if name in ("edit", "write"):
                            current_file = result.get("file") or current_file
                            yield sse_event(
                                "doc",
                                {
                                    "action": name,
                                    "file": result.get("file"),
                                    "pathId": path_ref.id,
                                },
                            )
                        elif name == "highlight":
                            yield sse_event(
                                "highlight",
                                {
                                    "file": result.get("file"),
                                    "quote": result.get("quote"),
                                    "note": result.get("note"),
                                },
                            )
                    else:
                        yield sse_event(
                            "tool",
                            _tool_event(name, "error", ok=False, message=result.get("error")),
                        )

            if server_web and (collected_server_calls or collected_annotations):
                server_hits = _server_web_hits(collected_server_calls, collected_annotations)
                if server_hits:
                    yield sse_event(
                        "sources",
                        {
                            "sources": _sources(kb_hits, server_hits),
                            "note": web_note,
                        },
                    )
            if usage:
                yield sse_event("usage", usage)
            yield sse_event(
                "done",
                {"finishReason": "stop", "elapsedMs": int((time.monotonic() - started) * 1000)},
            )
        except LLMError as exc:
            yield sse_event("error", exc.payload())
            yield sse_event(
                "done",
                {
                    "finishReason": "error",
                    "elapsedMs": int((time.monotonic() - started) * 1000),
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield sse_event(
                "error",
                {"code": "chat_failed", "message": str(exc), "retryable": False},
            )
            yield sse_event(
                "done",
                {
                    "finishReason": "error",
                    "elapsedMs": int((time.monotonic() - started) * 1000),
                },
            )

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
