"""DeepSeek Search and Fetch API clients."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

import httpx

from .errors import ApiError

USER_AGENT = "KnowledgeLib/0.1"


class WebToolError(Exception):
    def __init__(self, status_code: int, code: str, message: str, retryable: bool = False) -> None:
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


def join_path(base: str, path: str) -> str:
    return (base or "").rstrip("/") + "/" + (path or "").lstrip("/")


def validate_fetch_url(url: Any) -> str:
    if not isinstance(url, str):
        raise WebToolError(400, "invalid_request", "url 必须是字符串")
    value = url.strip()
    if not value or len(value) > 8192:
        raise WebToolError(400, "invalid_request", "url 不能为空且不超过 8192 字符")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise WebToolError(400, "invalid_request", "url 必须以 http:// 或 https:// 开头")
    if not parts.hostname:
        raise WebToolError(400, "invalid_request", "url 必须包含主机名")
    if parts.username or parts.password:
        raise WebToolError(400, "invalid_request", "url 不能包含用户名或密码")
    return value


def _detail(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:400]
        if isinstance(body.get("message"), str):
            return body["message"][:400]
    if isinstance(body, str):
        return body[:400]
    return ""


def _map_http_error(status: int, body: Any) -> WebToolError:
    detail = _detail(body)
    suffix = f"：{detail}" if detail else ""
    if status == 400:
        return WebToolError(400, "invalid_request", "联网接口参数不合法（400）" + suffix)
    if status == 401:
        return WebToolError(401, "upstream_auth", "联网接口 API Key 无效（401）" + suffix)
    if status == 402:
        return WebToolError(402, "upstream_balance", "账户余额不足（402）" + suffix)
    if status == 403:
        return WebToolError(403, "fetch_forbidden", "目标网页被安全策略拒绝（403）" + suffix)
    if status == 404:
        return WebToolError(404, "fetch_unavailable", "未能获取网页正文（404）" + suffix)
    if status == 408:
        return WebToolError(408, "upstream_timeout", "联网接口读取超时（408）" + suffix, True)
    if status == 413:
        return WebToolError(413, "upstream_too_large", "请求体超过限制（413）" + suffix)
    if status == 429:
        return WebToolError(429, "upstream_rate", "联网接口限流（429），请稍后重试" + suffix, True)
    if status >= 500:
        return WebToolError(502, "upstream_error", f"联网服务暂时不可用（{status}）" + suffix, True)
    return WebToolError(502, "upstream_error", f"联网接口返回错误（{status}）" + suffix)


class WebTools:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        search_path: str = "/tools/search",
        fetch_path: str = "/tools/fetch",
        timeout: float = 45,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.search_path = search_path
        self.fetch_path = fetch_path
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _post(self, path: str, payload: dict[str, Any], retry: bool = True) -> dict[str, Any]:
        url = join_path(self.base_url, path)
        attempt = 0
        while True:
            attempt += 1
            try:
                timeout = httpx.Timeout(connect=10.0, read=float(self.timeout), write=30.0, pool=10.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, json=payload, headers=self._headers())
                if response.status_code >= 400:
                    try:
                        body = response.json()
                    except Exception:
                        body = response.text
                    error = _map_http_error(response.status_code, body)
                    if retry and error.retryable and attempt == 1:
                        await asyncio.sleep(0.4)
                        continue
                    raise error
                try:
                    data = response.json()
                except ValueError:
                    raise WebToolError(502, "upstream_error", "联网接口返回了非 JSON 响应")
                if not isinstance(data, dict):
                    raise WebToolError(502, "upstream_error", "联网接口返回了非对象 JSON")
                return data
            except httpx.TimeoutException:
                error = WebToolError(504, "upstream_timeout", "连接联网接口超时", True)
                if retry and attempt == 1:
                    await asyncio.sleep(0.4)
                    continue
                raise error
            except httpx.HTTPError as exc:
                error = WebToolError(502, "upstream_unreachable", f"无法连接联网接口：{exc}", True)
                if retry and attempt == 1:
                    await asyncio.sleep(0.4)
                    continue
                raise error

    async def search(
        self,
        query: str,
        count: int = 5,
        ori_question: str | None = None,
        need_content: bool = False,
    ) -> list[dict[str, Any]]:
        value = (query or "").strip()
        if not value:
            raise WebToolError(400, "invalid_request", "搜索词不能为空")
        if len(value) > 128:
            raise WebToolError(400, "invalid_request", "搜索词不能超过 128 字符")
        if not isinstance(count, int) or count < 1 or count > 50:
            raise WebToolError(400, "invalid_request", "count 必须在 1..50 之间")
        payload: dict[str, Any] = {
            "query": value,
            "count": count,
            "need_content": bool(need_content),
        }
        if ori_question:
            payload["ori_question"] = str(ori_question).strip()[:128]
        data = await self._post(self.search_path, payload)
        results = data.get("results")
        if not isinstance(results, list):
            raise WebToolError(502, "upstream_error", "联网接口响应缺少 results 数组")
        cleaned: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            cleaned.append(
                {
                    "rerank_score": item.get("rerank_score"),
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "snippet": item.get("snippet") or "",
                    "summary": item.get("summary") or "",
                    "content": item.get("content") or "",
                    "site_name": item.get("site_name"),
                    "site_icon": item.get("site_icon"),
                    "date": item.get("date"),
                    "authority": item.get("authority"),
                }
            )
        return cleaned

    async def fetch(self, url: str) -> dict[str, Any]:
        value = validate_fetch_url(url)
        data = await self._post(self.fetch_path, {"url": value})
        return {
            "title": data.get("title") or "",
            "content": data.get("content") or "",
            "url": value,
        }


def webtools_from_config(config: Any) -> WebTools:
    return WebTools(
        config.web_base_url(),
        config.web_key(),
        config.web_search_path(),
        config.web_fetch_path(),
        config.web_timeout(),
    )
