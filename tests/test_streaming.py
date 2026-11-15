"""Real-socket SSE test: chunking, event order and connection close."""

from __future__ import annotations

from pathlib import Path

import httpx

from server.main import create_app
from tests.support import iter_events, parse_sse_text, serve


def test_real_socket_generation_and_chat(tmp_path: Path, mock_url: str):
    with serve(create_app(tmp_path / "data", {"KL_QUIET": "1"}, config_dir=tmp_path / "config")) as app_url:
        with httpx.Client(base_url=app_url, timeout=90) as client:
            settings = client.put(
                "/api/settings",
                json={
                    "api": {"baseUrl": mock_url, "apiKey": "test-key", "model": "mock-model"},
                    "web": {"baseUrl": mock_url + "/v1", "apiKey": "test-key"},
                },
            )
            assert settings.status_code == 200
            kb = client.post("/api/kbs", json={"name": "真实连接测试"}).json()
            spec = {
                "domain": "Rust 异步编程",
                "weeks": 1,
                "hoursPerWeek": 6,
                "moduleCount": 3,
                "level": "zero",
                "goals": [],
                "preferences": "",
            }
            with client.stream("POST", f"/api/kbs/{kb['id']}/paths", json=spec) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                text = "".join(response.iter_text())
            events = parse_sse_text(text)
            assert iter_events(events, "done")[-1]["status"] == "complete"
            assert [name for name, _ in events][0] == "start"

            with client.stream(
                "POST",
                "/api/chat",
                json={"kbId": kb["id"], "message": "核心概念是什么", "mode": "kb"},
            ) as response:
                assert response.status_code == 200
                chat_text = "".join(response.iter_text())
            chat_events = parse_sse_text(chat_text)
            assert [name for name, _ in chat_events][0] == "meta"
            assert chat_events[-1][0] == "done"
            assert "MOCK::" in "".join(item["text"] for item in iter_events(chat_events, "delta"))


def test_cancel_generation_leaves_consistent_state(tmp_path: Path, mock_url: str):
    import time

    httpx.post(mock_url + "/__mock/config", json={"delay": 0.4}, timeout=5)
    with serve(create_app(tmp_path / "data", {"KL_QUIET": "1"}, config_dir=tmp_path / "config")) as app_url:
        with httpx.Client(base_url=app_url, timeout=60) as client:
            client.put(
                "/api/settings",
                json={
                    "api": {"baseUrl": mock_url, "apiKey": "test-key", "model": "mock-model"},
                    "web": {"baseUrl": mock_url + "/v1", "apiKey": "test-key"},
                },
            )
            kb = client.post("/api/kbs", json={"name": "取消测试"}).json()
            spec = {
                "domain": "取消测试领域",
                "weeks": 1,
                "hoursPerWeek": 6,
                "moduleCount": 4,
                "level": "zero",
                "goals": [],
                "preferences": "",
            }
            with client.stream("POST", f"/api/kbs/{kb['id']}/paths", json=spec) as response:
                seen_start = False
                for line in response.iter_lines():
                    if line.startswith("event: start"):
                        seen_start = True
                    if seen_start and not line.strip():
                        break
                response.close()
            assert seen_start
            time.sleep(1.2)
            detail = client.get(f"/api/kbs/{kb['id']}").json()
            assert detail["paths"]
            assert detail["paths"][0]["status"] in ("failed", "partial")
            assert client.get("/api/health").status_code == 200
