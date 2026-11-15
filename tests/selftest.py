"""Offline end-to-end self test used by python app.py --selftest."""

from __future__ import annotations

import re
import tempfile
import traceback
from pathlib import Path

import httpx

from server.main import create_app
from tests.mock_upstream import create_mock_app
from tests.support import iter_events, parse_sse_text, serve


def run() -> bool:
    checks: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            raise AssertionError(f"{name}: {detail}")
        checks.append(name)
        print(f"[ok] {name}" + (f" · {detail}" if detail else ""))

    try:
        with tempfile.TemporaryDirectory(prefix="kl-selftest-") as tmp:
            data_dir = Path(tmp) / "data"
            with serve(create_mock_app()) as mock_url:
                with serve(create_app(data_dir, {"KL_QUIET": "1"}, config_dir=Path(tmp) / "config")) as app_url:
                    with httpx.Client(base_url=app_url, timeout=90) as client:
                        health = client.get("/api/health")
                        check("health", health.status_code == 200 and health.json()["status"] == "ok")

                        settings = client.put(
                            "/api/settings",
                            json={
                                "api": {
                                    "baseUrl": mock_url,
                                    "apiKey": "test-key",
                                    "model": "mock-model",
                                },
                                "web": {
                                    "baseUrl": mock_url + "/v1",
                                    "apiKey": "test-key",
                                    "searchCount": 2,
                                    "fetchTopN": 1,
                                },
                            },
                        )
                        check("settings", settings.status_code == 200)
                        public = client.get("/api/settings").json()
                        check("masked-key", "test-key" not in str(public) and public["api"]["hasApiKey"])

                        created = client.post("/api/kbs", json={"name": "自检知识库", "description": "selftest"})
                        check("create-kb", created.status_code == 200)
                        kb = created.json()

                        spec = {
                            "domain": "Rust 异步编程",
                            "weeks": 2,
                            "hoursPerWeek": 6,
                            "level": "zero",
                            "goals": ["project"],
                            "preferences": "偏实战",
                            "moduleCount": 3,
                        }
                        with client.stream("POST", f"/api/kbs/{kb['id']}/paths", json=spec) as response:
                            text = "".join(response.iter_text())
                            check("generate-http", response.status_code == 200)
                        events = parse_sse_text(text)
                        done = iter_events(events, "done")
                        check("generate-done", bool(done) and done[-1]["status"] == "complete")

                        detail = client.get(f"/api/kbs/{kb['id']}").json()
                        path = detail["paths"][0]
                        path_dir = Path(path["absPath"])
                        files = sorted(path_dir.glob("*.md"))
                        check("numbered-files", len(files) == 4)
                        for file in files:
                            first = file.read_text(encoding="utf-8").splitlines()[0]
                            check("number-" + file.name[:2], bool(re.match(r"^# \d{2} · ", first)), first)
                        overview = (path_dir / "00-学习路径总览.md").read_text(encoding="utf-8")
                        check("overview-links", "01-概念地图.md" in overview)

                        search = client.get(f"/api/kbs/{kb['id']}/search", params={"q": "核心概念", "topK": 4})
                        check("retrieval", search.status_code == 200 and bool(search.json()["hits"]))

                        with client.stream(
                            "POST",
                            "/api/chat",
                            json={"kbId": kb["id"], "message": "核心概念是什么", "mode": "kb"},
                        ) as response:
                            chat_text = "".join(response.iter_text())
                        chat_events = parse_sse_text(chat_text)
                        deltas = "".join(item["text"] for item in iter_events(chat_events, "delta"))
                        check("chat-stream", "MOCK::" in deltas)
                        check("chat-sources", bool(iter_events(chat_events, "sources")[0]["sources"]))

                        with client.stream(
                            "POST",
                            "/api/chat",
                            json={"kbId": kb["id"], "message": "最新进展", "mode": "kb_web"},
                        ) as response:
                            web_text = "".join(response.iter_text())
                        web_events = parse_sse_text(web_text)
                        tools = iter_events(web_events, "tool")
                        check("web-search", any(item.get("tool") == "search" for item in tools))
                        check("web-fetch", any(item.get("tool") == "fetch" for item in tools))
                        sources = iter_events(web_events, "sources")[0]["sources"]
                        check("web-sources", any(item["kind"] == "web" for item in sources))

                        page = client.post("/api/web/fetch", json={"url": "https://example.com/page-1"})
                        check("fetch-proxy", page.status_code == 200 and "正文" in page.json()["content"])

            print(f"SELFTEST OK ({len(checks)} checks)")
            return True
    except Exception:
        traceback.print_exc()
        print("SELFTEST FAILED")
        return False
