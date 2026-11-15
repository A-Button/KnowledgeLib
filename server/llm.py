"""OpenAI-compatible chat client with thinking and tool-call support."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx

from .errors import ApiError


class LLMError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status_code,
            "retryable": self.retryable,
        }


THINKING_LEVELS = ("off", "low", "high", "max")


def is_deepseek_endpoint(base_url: str, model: str) -> bool:
    base = (base_url or "").lower()
    name = (model or "").lower()
    return "deepseek" in base or name.startswith("deepseek")


def chat_endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _upstream_message(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:400]
        if isinstance(body.get("message"), str):
            return body["message"][:400]
    if isinstance(body, str):
        return body[:400]
    return ""


def _map_error(status: int, body: Any) -> LLMError:
    detail = _upstream_message(body)
    suffix = f"：{detail}" if detail else ""
    if status == 401:
        return LLMError(401, "upstream_auth", "API Key 无效或未授权（401）" + suffix, False)
    if status == 402:
        return LLMError(402, "upstream_balance", "账户余额不足（402）" + suffix, False)
    if status == 403:
        return LLMError(403, "upstream_forbidden", "请求被模型服务拒绝（403）" + suffix, False)
    if status == 404:
        return LLMError(404, "upstream_not_found", "模型接口不存在（404），请检查 Base URL" + suffix, False)
    if status == 408:
        return LLMError(408, "upstream_timeout", "模型服务读取超时（408）" + suffix, True)
    if status == 413:
        return LLMError(413, "upstream_too_large", "请求体超过模型服务限制（413）" + suffix, False)
    if status == 429:
        return LLMError(429, "upstream_rate", "模型服务限流（429），请稍后重试" + suffix, True)
    if status >= 500:
        return LLMError(502, "upstream_error", f"模型服务暂时不可用（{status}）" + suffix, True)
    return LLMError(502, "upstream_error", f"模型服务返回错误（{status}）" + suffix, False)


class ChatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.deepseek = is_deepseek_endpoint(base_url, model)

    def _headers(self, stream: bool = False) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": "KnowledgeLib/0.2",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(connect=10.0, read=float(self.timeout), write=30.0, pool=10.0)

    def _apply_options(
        self,
        payload: dict[str, Any],
        tools: list[dict[str, Any]] | None,
        thinking: str,
        model: str | None,
    ) -> None:
        if model:
            payload["model"] = model
        if tools:
            payload["tools"] = tools
        level = thinking if thinking in THINKING_LEVELS else "off"
        if self.deepseek:
            if level == "off":
                payload["thinking"] = {"type": "disabled"}
            else:
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = level
        elif level != "off":
            # OpenAI-compatible reasoning controls; non-supporting servers may ignore it.
            payload["reasoning_effort"] = level

    async def complete(
        self,
        messages: list[dict[str, Any]],
        retries: int = 1,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        thinking: str = "off",
        model: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        self._apply_options(payload, tools, thinking, model)
        attempt = 0
        last_error: LLMError | None = None
        while attempt <= retries:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=self._timeout()) as client:
                    response = await client.post(
                        chat_endpoint(self.base_url),
                        json=payload,
                        headers=self._headers(),
                    )
                if response.status_code >= 400:
                    try:
                        body = response.json()
                    except Exception:
                        body = response.text
                    error = _map_error(response.status_code, body)
                    if error.retryable and attempt <= retries:
                        last_error = error
                        await asyncio.sleep(0.4)
                        continue
                    raise error
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError(502, "upstream_error", "模型返回为空（choices 为空）", False)
                message = choices[0].get("message") or {}
                content = message.get("content")
                if not isinstance(content, str):
                    content = ""
                return content
            except httpx.TimeoutException:
                error = LLMError(504, "upstream_timeout", "连接模型服务超时", True)
                if attempt <= retries:
                    last_error = error
                    await asyncio.sleep(0.4)
                    continue
                raise error
            except httpx.HTTPError as exc:
                error = LLMError(502, "upstream_unreachable", f"无法连接模型服务：{exc}", True)
                if attempt <= retries:
                    last_error = error
                    await asyncio.sleep(0.4)
                    continue
                raise error
        raise last_error or LLMError(502, "upstream_error", "模型调用失败", False)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        thinking: str = "off",
        model: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        self._apply_options(payload, tools, thinking, model)
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                async with client.stream(
                    "POST",
                    chat_endpoint(self.base_url),
                    json=payload,
                    headers=self._headers(stream=True),
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        try:
                            parsed = json.loads(body.decode("utf-8", errors="replace"))
                        except Exception:
                            parsed = body.decode("utf-8", errors="replace")
                        raise _map_error(response.status_code, parsed)
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event.get("usage"), dict):
                            yield {"type": "usage", "usage": event["usage"]}
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if isinstance(reasoning, str) and reasoning:
                            yield {"type": "reasoning", "text": reasoning}
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            yield {"type": "content", "text": content}
                        for call in delta.get("tool_calls") or []:
                            function = call.get("function") or {}
                            yield {
                                "type": "tool_call_delta",
                                "index": int(call.get("index") or 0),
                                "id": call.get("id") or "",
                                "name": function.get("name") or "",
                                "arguments": function.get("arguments") or "",
                            }
                        server_calls = delta.get("server_tool_calls")
                        if isinstance(server_calls, list) and server_calls:
                            yield {"type": "server_tool_calls", "calls": server_calls}
                        annotations = delta.get("annotations")
                        if isinstance(annotations, list) and annotations:
                            yield {"type": "annotations", "annotations": annotations}
                        tool_event = choice.get("server_tool_event")
                        if isinstance(tool_event, dict):
                            yield {"type": "server_tool_event", "event": tool_event}
                        finish = choice.get("finish_reason")
                        if finish:
                            yield {"type": "finish", "reason": finish}
        except httpx.TimeoutException:
            raise LLMError(504, "upstream_timeout", "模型流式响应超时", True)
        except httpx.HTTPError as exc:
            raise LLMError(502, "upstream_unreachable", f"无法连接模型服务：{exc}", True)


def client_from_config(config: Any, timeout: float | None = None, model: str | None = None) -> ChatClient:
    return ChatClient(
        config.base_url(),
        config.api_key(),
        model or config.model(),
        timeout if timeout is not None else config.timeout(),
    )


def ensure_api_ready(config: Any) -> None:
    key = config.api_key()
    base = config.base_url()
    local = "127.0.0.1" in base or "localhost" in base or "0.0.0.0" in base
    if not key and not local:
        raise ApiError(400, "not_configured", "尚未配置 API Key，请先在设置中填写")
