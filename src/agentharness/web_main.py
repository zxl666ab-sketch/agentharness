"""Web-first Agent Harness launcher."""

from __future__ import annotations

import argparse
import ipaddress
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

from agentharness.api.server import create_app
from agentharness.config import load_project_env


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_bind(host: str, allow_remote_execution: bool) -> None:
    """Refuse to serve read/write procurement data on a non-loopback bind
    unless the operator explicitly opts in and accepts the auth-proxy duty."""
    if not _is_loopback(host) and not allow_remote_execution:
        raise SystemExit(
            "拒绝启动：非回环绑定必须显式使用 --allow-remote-execution，"
            "并在前面部署认证代理；否则所有接口（含报价原件与 SSE 事件）"
            "都会在无认证下被网络访问。"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentharness",
        description="启动采价台 Web 工作台。",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument(
        "--workspace",
        action="append",
        type=Path,
        help="Authorized workspace root; repeat to expose multiple roots.",
    )
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser.")
    parser.add_argument(
        "--allow-remote-execution",
        action="store_true",
        help="Explicitly allow run/approval endpoints on a non-loopback bind.",
    )
    return parser


def main() -> None:
    load_project_env()
    args = build_parser().parse_args()
    env_data_dir = os.environ.get("AGENTHARNESS_DATA_DIR", "").strip()
    data_dir = args.data_dir or (Path(env_data_dir) if env_data_dir else None)
    roots = [path.expanduser().resolve() for path in (args.workspace or [Path.cwd()])]
    for root in roots:
        if not root.is_dir():
            raise SystemExit(f"Workspace does not exist or is not a directory: {root}")
    validate_bind(args.host, args.allow_remote_execution)
    execution_enabled = _is_loopback(args.host) or args.allow_remote_execution
    url = f"http://{args.host}:{args.port}"
    print(f"采价台 Web: {url}")
    print("Workspaces: " + ", ".join(str(root) for root in roots))
    if not args.no_open and _is_loopback(args.host):
        timer = threading.Timer(0.8, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    app = create_app(
        data_dir=data_dir,
        workspace_roots=roots,
        execution_enabled=execution_enabled,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
