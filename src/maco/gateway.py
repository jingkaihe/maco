"""Local HTTP gateway for generated MCP code."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import time
from typing import Any
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import MacoConfig
from .mcp_manager import MCPManager


@dataclass(frozen=True)
class ServeOptions:
    host: str = "127.0.0.1"
    port: int = 0
    workspace: str | Path = ".maco"
    token: str | None = None
    use_token: bool = True


class ManagerLoop:
    """Runs the async MCP manager on a private event loop."""

    def __init__(self, config: MacoConfig):
        self.manager = MCPManager(config)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="maco-mcp-loop", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self.run(self.manager.start())

    def stop(self) -> None:
        try:
            self.run(self.manager.aclose(), timeout=10)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout=10)
            self.loop.close()

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.run(self.manager.call_tool(server, tool, arguments))

    def list_tools(self) -> dict[str, list[dict[str, Any]]]:
        return self.run(self.manager.list_tools())

    def run(self, coro: Any, timeout: float | None = None) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()


def serve(config: MacoConfig, options: ServeOptions) -> None:
    """Run the gateway until interrupted."""

    workspace = Path(options.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    token = options.token if options.use_token else None
    if options.use_token and not token:
        token = secrets.token_urlsafe(32)

    manager_loop = ManagerLoop(config)
    manager_loop.start()

    handler_cls = _make_handler(manager_loop, token)
    httpd = ThreadingHTTPServer((options.host, options.port), handler_cls)
    actual_host, actual_port = httpd.server_address[:2]
    display_host = "127.0.0.1" if actual_host in {"0.0.0.0", ""} else actual_host
    url = f"http://{display_host}:{actual_port}/"
    _write_gateway_file(workspace / "gateway.json", url, token, config.path)

    stop_event = threading.Event()

    def _request_shutdown(signum: int, _frame: Any) -> None:
        print(f"\nreceived signal {signum}; stopping maco gateway", file=sys.stderr)
        stop_event.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    old_sigint = signal.signal(signal.SIGINT, _request_shutdown)
    old_sigterm = signal.signal(signal.SIGTERM, _request_shutdown)
    try:
        print("maco gateway started")
        print(f"  URL: {url}")
        print(f"  workspace: {workspace}")
        print(f"  gateway file: {workspace / 'gateway.json'}")
        print("  press Ctrl+C to stop")
        httpd.serve_forever()
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)
        httpd.server_close()
        manager_loop.stop()
        if stop_event.is_set():
            print("maco gateway stopped")


def _write_gateway_file(path: Path, url: str, token: str | None, config_path: Path) -> None:
    payload = {
        "url": url,
        "token": token,
        "pid": os.getpid(),
        "config": str(config_path),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _make_handler(manager_loop: ManagerLoop, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "maco-gateway/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            if self.path.rstrip("/") in {"", "/health"}:
                self._write_json({"ok": True, "servers": manager_loop.manager.server_names()})
                return
            if self.path.rstrip("/") == "/tools":
                try:
                    self._write_json({"servers": manager_loop.list_tools()})
                except Exception as exc:  # pragma: no cover - defensive gateway path
                    self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._write_error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            if self.path.rstrip("/") not in {"", "/call"}:
                self._write_error(HTTPStatus.NOT_FOUND, "not found")
                return
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                return
            try:
                request = self._read_json()
                server_name = _required_str(request, "server")
                tool_name = _required_str(request, "tool")
                arguments = request.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                response = manager_loop.call_tool(server_name, tool_name, arguments)
                self._write_json(response)
            except KeyError as exc:
                self._write_error(HTTPStatus.NOT_FOUND, str(exc))
            except ValueError as exc:
                self._write_error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def log_message(self, format: str, *args: Any) -> None:
            print(f"{self.address_string()} - {format % args}", file=sys.stderr)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8") if body else "{}")
            if not isinstance(data, dict):
                raise ValueError("request body must be a JSON object")
            return data

        def _write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_error(self, status: HTTPStatus, message: str) -> None:
            self._write_json({"error": message}, status=status)

    return Handler


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value
