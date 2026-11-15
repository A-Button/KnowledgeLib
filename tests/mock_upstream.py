"""A local Python mock of DeepSeek / OpenAI-compatible APIs.

Run standalone with:
    uv run python -m tests.mock_upstream --port 8766
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MODULE_TITLES = [
    "概念地图",
    "核心机制",
    "动手实践",
    "常见误区",
    "综合项目",
    "性能优化",
    "工程化",
    "复盘与迁移",
    "进阶专题",
    "综合测验",
    "项目拆解",
    "发布实践",
    "协作流程",
    "质量保障",
    "性能剖析",
    "架构演进",
    "安全基础",
    "调试技巧",
    "文档写作",
    "总结",
]


class MockState:
    def __init__(self) -> None:
        self.key = "test-key"
        self.require_auth = True
        self.requests: list[dict[str, Any]] = []
        self.plan_invalid_once = False
        self.fail_module_once: set[int] = set()
        self.fail_module_times: dict[int, int] = {}
        self.chat_status = 200
        self.search_status = 200
        self.fetch_status = 200
        self.delay = 0.0
        self.tool_mode = ""
        self.server_web = False

    def reset(self) -> None:
        self.requests = []
        self.plan_invalid_once = False
        self.fail_module_once = set()
        self.fail_module_times = {}
        self.chat_status = 200
        self.search_status = 200
        self.fetch_status = 200
        self.delay = 0.0
        self.tool_mode = ""
        self.server_web = False

    def auth_ok(self, request: Request) -> bool:
        if not self.require_auth:
            return True
        return request.headers.get("authorization", "") == f"Bearer {self.key}"

    def record(self, request: Request, body: Any) -> None:
        self.requests.append(
            {
                "path": request.url.path,
                "body": copy.deepcopy(body),
                "auth_present": bool(request.headers.get("authorization")),
                "auth_ok": self.auth_ok(request),
            }
        )


def _error(status: int, message: str, code: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": code, "param": None, "code": code}},
    )


def _completion(content: str) -> dict[str, Any]:
    return {
        "id": "mock-completion",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def _plan_content(user_text: str) -> str:
    match = re.search(r'"moduleCount"\s*:\s*(\d+)', user_text)
    count = int(match.group(1)) if match else 5
    count = max(1, min(count, len(MODULE_TITLES)))
    modules = []
    for index in range(1, count + 1):
        modules.append(
            {
                "index": index,
                "title": MODULE_TITLES[index - 1],
                "summary": f"第 {index} 讲摘要：理解并练习 {MODULE_TITLES[index - 1]}。",
                "objectives": [f"能说明 {MODULE_TITLES[index - 1]} 的要点", "能完成本讲练习"],
                "prerequisites": [index - 1] if index > 1 else [],
                "estimatedHours": 2.0,
                "topics": [MODULE_TITLES[index - 1], "练习"],
                "practice": "完成一个最小示例并记录结果",
            }
        )
    return json.dumps(
        {
            "title": "MOCK 学习路径",
            "summary": "由本地 Mock 生成的学习路径。",
            "modules": modules,
        },
        ensure_ascii=False,
    )


def _module_content(index: int) -> str:
    body = (
        "# 这个一级标题应该被服务端移除\n\n"
        "## 核心概念\n\n"
        f"MOCK-MATERIAL-{index}：这一讲围绕核心机制展开，先解释概念，再给出步骤。"
        + "这是用于测试的占位讲义内容，长度需要超过阈值，因此继续补充说明。"
        * 2
        + "\n\n## 分步讲解\n\n第一步，观察现象；第二步，建立模型；第三步，动手验证。\n\n"
        + "## 示例\n\n给出一个最小可运行示例，并解释每一行的作用。\n\n"
        + "## 常见误区\n\n不要跳过基础概念，也不要把示例当成全部。\n\n"
        + "## 练习\n\n完成一个 30 分钟的小练习。\n\n"
        + "## 自测清单\n\n- [ ] 能复述核心概念\n- [ ] 能独立完成示例\n\n"
        + "## 延伸阅读\n\n继续阅读相关主题的官方文档与教程。"
    )
    return body


def _module_index(user_text: str) -> int:
    match = re.search(r"模块索引：(\d+)", user_text)
    return int(match.group(1)) if match else 0


def _tool_calls(mode: str) -> list[dict[str, Any]]:
    if mode == "read":
        return [
            {
                "id": "call_read",
                "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"file": "02-核心机制.md"})},
            }
        ]
    if mode == "edit":
        return [
            {
                "id": "call_edit",
                "type": "function",
                "function": {
                    "name": "edit",
                    "arguments": json.dumps(
                        {
                            "file": "02-核心机制.md",
                            "old_text": "MOCK-MATERIAL-2",
                            "new_text": "MOCK-EDITED",
                            "reason": "测试编辑",
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        ]
    if mode == "write":
        body = (
            "# 04 · 工具生成\n\n## 本节目标\n\n由工具创建的新讲义。\n\n"
            "## 核心概念\n\n这是通过 write 工具写入的测试内容。"
        )
        return [
            {
                "id": "call_write",
                "type": "function",
                "function": {
                    "name": "write",
                    "arguments": json.dumps(
                        {"file": "04-工具生成.md", "content": body, "reason": "测试新建"},
                        ensure_ascii=False,
                    ),
                },
            }
        ]
    if mode == "highlight":
        return [
            {
                "id": "call_highlight",
                "type": "function",
                "function": {
                    "name": "highlight",
                    "arguments": json.dumps(
                        {"quote": "MOCK-MATERIAL-2", "note": "测试高亮"},
                        ensure_ascii=False,
                    ),
                },
            }
        ]
    return []


def _server_web_calls() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calls = [
        {
            "index": 0,
            "reasoning_content": "",
            "calls": [
                {
                    "index": 0,
                    "id": "server_search_1",
                    "type": "deepseek_web_search",
                    "kind": "search",
                    "status": "completed",
                    "action": {"type": "search", "queries": ["mock"]},
                    "sources": [
                        {
                            "type": "url",
                            "url": "https://example.com/server-1",
                            "title": "MOCK 服务端搜索 1",
                            "snippet": "服务端搜索摘要 1",
                            "site_name": "Example",
                            "published_at": "2026-11-01",
                        }
                    ],
                }
            ],
        }
    ]
    annotations = [
        {
            "type": "url_citation",
            "url_citation": {"url": "https://example.com/server-1", "title": "MOCK 服务端搜索 1"},
        }
    ]
    return calls, annotations


def create_mock_app(state: MockState | None = None) -> FastAPI:
    app = FastAPI(title="Mock Upstream", docs_url=None, redoc_url=None, openapi_url=None)
    mock = state or MockState()
    app.state.mock = mock

    @app.get("/v1/models")
    async def models(request: Request) -> Any:
        if not mock.auth_ok(request):
            return _error(401, "invalid api key")
        return {"object": "list", "data": [{"id": "mock-model", "object": "model"}]}

    @app.post("/chat/completions")
    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Any:
        body = await request.json()
        mock.record(request, body)
        if not mock.auth_ok(request):
            return _error(401, "invalid api key")
        if mock.chat_status != 200:
            return _error(mock.chat_status, f"mock chat error {mock.chat_status}")
        if mock.delay:
            await asyncio.sleep(mock.delay)
        messages = body.get("messages") or []
        system = "\n".join(str(item.get("content") or "") for item in messages if item.get("role") == "system")
        all_user_text = "\n".join(
            str(item.get("content") or "") for item in messages if item.get("role") == "user"
        )
        user_text = ""
        for item in reversed(messages):
            if item.get("role") == "user":
                user_text = str(item.get("content") or "")
                break

        if "LEARNING_PATH_PLAN" in system:
            content = _plan_content(all_user_text)
            if mock.plan_invalid_once:
                mock.plan_invalid_once = False
                content = "这不是 JSON，只是一段说明文字。"
            return JSONResponse(_completion(content))

        if "LEARNING_MODULE_MATERIAL" in system:
            index = _module_index(user_text)
            if index in mock.fail_module_once:
                mock.fail_module_once.discard(index)
                return _error(500, f"mock module {index} failed")
            remaining = mock.fail_module_times.get(index, 0)
            if remaining > 0:
                mock.fail_module_times[index] = remaining - 1
                return _error(500, f"mock module {index} failed")
            return JSONResponse(_completion(_module_content(index)))

        answer = f"MOCK::{user_text}"
        if "<知识库资料>" in system:
            answer += " 引用 [K1]。"
        if "<联网资料>" in system:
            answer += " 引用 [W1]。"
        tools = body.get("tools") or []
        has_tool_result = any(item.get("role") == "tool" for item in messages)
        has_function_tool = any(item.get("type") == "function" for item in tools)
        has_server_web = any(item.get("type") == "deepseek_web_search" for item in tools)
        tool_calls = _tool_calls(mock.tool_mode) if (mock.tool_mode and has_function_tool and not has_tool_result) else []
        server_calls: list[dict[str, Any]] = []
        annotations: list[dict[str, Any]] = []
        if mock.server_web and has_server_web and not tool_calls:
            server_calls, annotations = _server_web_calls()
            answer = "MOCK-SERVER-WEB 回答，引用 [W1]。"
        if tool_calls:
            answer = ""

        if not body.get("stream"):
            message: dict[str, Any] = {"role": "assistant", "content": answer}
            if tool_calls:
                message["tool_calls"] = tool_calls
            if server_calls:
                message["server_tool_calls"] = server_calls
            if annotations:
                message["annotations"] = annotations
            return JSONResponse(
                {
                    "id": "mock-completion",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": body.get("model") or "mock-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "tool_calls" if tool_calls else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                }
            )

        async def stream():
            yield "data: " + json.dumps(
                {
                    "id": "mock",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"reasoning_content": "MOCK-REASONING "}, "finish_reason": None}],
                },
                ensure_ascii=False,
            ) + "\n\n"
            for i in range(0, len(answer), 6):
                chunk = {
                    "id": "mock",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": answer[i : i + 6]}, "finish_reason": None}],
                }
                yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                await asyncio.sleep(0.002)
            final_delta: dict[str, Any] = {}
            if tool_calls:
                final_delta["tool_calls"] = [
                    {
                        "index": index,
                        "id": call["id"],
                        "type": "function",
                        "function": call["function"],
                    }
                    for index, call in enumerate(tool_calls)
                ]
            if server_calls:
                final_delta["server_tool_calls"] = server_calls
            if annotations:
                final_delta["annotations"] = annotations
            final = {
                "id": "mock",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": final_delta,
                        "finish_reason": "tool_calls" if tool_calls else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }
            yield "data: " + json.dumps(final, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/v1/tools/search")
    async def search(request: Request) -> Any:
        body = await request.json()
        mock.record(request, body)
        if not mock.auth_ok(request):
            return _error(401, "invalid api key")
        if mock.search_status != 200:
            return _error(mock.search_status, f"mock search error {mock.search_status}")
        query = str(body.get("query") or "")
        count = int(body.get("count") or 10)
        if not query or len(query) > 128:
            return _error(400, "query must be 1..128 characters")
        if count < 1 or count > 50:
            return _error(400, "'count' must be between 1 and 50")
        results = []
        for index in range(1, min(count, 5) + 1):
            results.append(
                {
                    "rerank_score": round(2.4 - index * 0.2, 2),
                    "title": f"MOCK 搜索结果 {index}",
                    "url": f"https://example.com/page-{index}",
                    "snippet": f"这是第 {index} 条搜索结果的短摘要。",
                    "summary": f"这是第 {index} 条搜索结果的长摘要，用于给模型阅读。" * 3,
                    "content": f"网页 {index} 的正文内容。" * 20,
                    "site_name": "Example",
                    "site_icon": "https://example.com/favicon.ico",
                    "date": "2026-11-01T00:00:00+00:00",
                    "authority": 3,
                }
            )
        return {"results": results}

    @app.post("/v1/tools/fetch")
    async def fetch(request: Request) -> Any:
        body = await request.json()
        mock.record(request, body)
        if not mock.auth_ok(request):
            return _error(401, "invalid api key")
        if mock.fetch_status != 200:
            return _error(mock.fetch_status, f"mock fetch error {mock.fetch_status}")
        url = str(body.get("url") or "")
        match = re.match(r"^https://example\.com/page-(\d+)$", url)
        if not match:
            return _error(404, "mock page not found")
        number = match.group(1)
        return {
            "title": f"MOCK 页面 {number}",
            "content": f"# MOCK 页面 {number}\n\n" + ("抓取到的正文内容。" * 30),
        }

    @app.get("/__mock/stats")
    async def stats() -> Any:
        return {
            "requests": mock.requests,
            "count": len(mock.requests),
            "chat_calls": sum(1 for item in mock.requests if item["path"].endswith("/chat/completions")),
            "search_calls": sum(1 for item in mock.requests if item["path"].endswith("/tools/search")),
            "fetch_calls": sum(1 for item in mock.requests if item["path"].endswith("/tools/fetch")),
        }

    @app.post("/__mock/reset")
    async def reset() -> Any:
        mock.reset()
        return {"ok": True}

    @app.post("/__mock/config")
    async def config(request: Request) -> Any:
        body = await request.json()
        for key in (
            "plan_invalid_once",
            "chat_status",
            "search_status",
            "fetch_status",
            "delay",
            "tool_mode",
            "server_web",
        ):
            if key in body:
                setattr(mock, key, body[key])
        if "fail_module_once" in body:
            mock.fail_module_once = set(int(item) for item in body["fail_module_once"])
        if "fail_module_times" in body:
            mock.fail_module_times = {
                int(key): int(value) for key, value in body["fail_module_times"].items()
            }
        return {"ok": True}

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Local mock of DeepSeek/OpenAI APIs")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(create_mock_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
