"""Command line entry point for KnowledgeLib."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="KnowledgeLib local learning-path studio")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("KL_PORT", "8765")), help="port (default 8765 or KL_PORT)")
    parser.add_argument("--data-dir", default=None, help="runtime data directory (default ./data)")
    parser.add_argument("--config-dir", default=None, help="per-user config directory (default APPDATA/KnowledgeLib or ~/.config/knowledgelib)")
    parser.add_argument("--open", action="store_true", help="open the browser after startup")
    parser.add_argument("--selftest", action="store_true", help="run an offline end-to-end self test")
    args = parser.parse_args()

    if args.selftest:
        from server.selftest import run_selftest

        return 0 if run_selftest() else 1

    import uvicorn

    from server.main import create_app

    data_dir = Path(args.data_dir) if args.data_dir else None
    config_dir = Path(args.config_dir) if args.config_dir else None
    application = create_app(data_dir, config_dir=config_dir)
    state = application.state.kl
    url_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{url_host}:{args.port}"
    key_note = "已配置" if state.config.public()["api"]["hasApiKey"] else "未配置"
    print("")
    print("  KnowledgeLib · 私人文库")
    print(f"  地址      {url}")
    print(f"  配置目录  {state.config_dir}")
    print(f"  数据目录  {state.data_dir}")
    print(f"  知识库根  {state.config.kb_root()}")
    print(f"  API Key   {key_note}")
    if args.host in ("0.0.0.0", "::"):
        print("  警告      当前监听所有网卡，局域网内其他设备可能访问本应用")
    print("")
    if args.open:
        timer = threading.Timer(1.0, _open_browser, args=[url])
        timer.daemon = True
        timer.start()
    uvicorn.run(application, host=args.host, port=args.port, log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
