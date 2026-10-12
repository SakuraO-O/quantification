#!/usr/bin/env python3
"""Serve the same static dashboard bundle that GitHub Pages deploys.

This is a local preview helper, not a way to bypass browser access policy.
It binds to loopback only and does not fetch or publish production data.
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "codex"
PAGE_ASSETS = (
    ("dashboard_example.html", "index.html"),
    ("dashboard_config.js", "dashboard_config.js"),
    ("dashboard_auth.js", "dashboard_auth.js"),
    ("dashboard_runtime.js", "dashboard_runtime.js"),
)
LOOPBACK = "127.0.0.1"


def build_preview(destination: Path) -> None:
    """Copy exactly the files included in the Pages deployment."""
    destination.mkdir(parents=True, exist_ok=True)
    for source_name, deployed_name in PAGE_ASSETS:
        copy2(SOURCE_DIR / source_name, destination / deployed_name)


class PreviewHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def list_directory(self, path: str):  # type: ignore[override]
        self.send_error(404, "Directory listing disabled")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="本地预览趋势看板的 Pages 静态文件")
    parser.add_argument("--port", type=int, default=8766, help="本地监听端口，默认 8766")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0–65535 之间")

    with TemporaryDirectory(prefix="trend-dashboard-preview-") as temp_dir:
        site_dir = Path(temp_dir)
        build_preview(site_dir)
        handler = partial(PreviewHandler, directory=str(site_dir))
        with ThreadingHTTPServer((LOOPBACK, args.port), handler) as server:
            print(f"看板预览：http://{LOOPBACK}:{server.server_port}/", flush=True)
            print("页面使用仓库当前的 Supabase 配置；登录或编辑可能访问生产环境。按 Ctrl+C 停止。", flush=True)
            print("若此本地来源未列入 Edge 的 DASHBOARD_ALLOWED_ORIGINS，真实 API 请求会被 CORS 拒绝。", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
