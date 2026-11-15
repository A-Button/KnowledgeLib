"""Shared helpers for API routers."""

from __future__ import annotations

from typing import Any


def trim_sources(hits: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    used = 0
    for hit in hits:
        if used >= limit:
            break
        text = str(hit.get("text") or hit.get("summary") or hit.get("snippet") or "")
        remaining = limit - used
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining]
        item = dict(hit)
        item["text"] = text
        result.append(item)
        used += len(text)
    return result
