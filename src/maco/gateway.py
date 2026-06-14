"""Local HTTP gateway for generated MCP code."""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import signal
import socket
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
    extra_hosts: tuple[str, ...] = ()
    freebind_hosts: tuple[str, ...] = ()


class ManagerLoop:
    """Runs the async MCP manager on a private event loop."""

    def __init__(self, config: MacoConfig):
        self.manager = MCPManager(config)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="maco-mcp-loop", daemon=True)
        self._ready: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._main_future: concurrent.futures.Future[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def start(self) -> None:
        self._thread.start()
        self._main_future = asyncio.run_coroutine_threadsafe(self._main(), self.loop)
        self._ready.result()

    def stop(self) -> None:
        try:
            if self._stop_event is not None:
                self.loop.call_soon_threadsafe(self._stop_event.set)
            if self._main_future is not None:
                self._main_future.result(timeout=10)
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

    async def _main(self) -> None:
        try:
            await self.manager.start()
            self._stop_event = asyncio.Event()
            self._ready.set_result(None)
            await self._stop_event.wait()
        except Exception as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
            raise
        finally:
            await self.manager.aclose()


class GatewayServer:
    """Managed maco gateway suitable for embedding or blocking CLI use."""

    def __init__(self, config: MacoConfig, options: ServeOptions) -> None:
        self.config = config
        self.options = options
        self.workspace = Path(options.workspace).expanduser().resolve()
        self.gateway_file = self.workspace / "gateway.json"
        self.token = options.token if options.use_token else None
        if options.use_token and not self.token:
            self.token = secrets.token_urlsafe(32)
        self.manager_loop = ManagerLoop(config)
        self.httpd: ThreadingHTTPServer | None = None
        self.extra_httpds: list[ThreadingHTTPServer] = []
        self.thread: threading.Thread | None = None
        self.extra_threads: list[threading.Thread] = []
        self.url = ""
        self.extra_urls: list[str] = []

    def start(self) -> GatewayServer:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.manager_loop.start()
        try:
            handler_cls = _make_handler(self.manager_loop, self.token)
            freebind_hosts = set(self.options.freebind_hosts)
            self.httpd = _make_http_server(
                self.options.host,
                self.options.port,
                handler_cls,
                freebind=self.options.host in freebind_hosts,
            )
            actual_host, actual_port = self.httpd.server_address[:2]
            display_host = "127.0.0.1" if actual_host in {"0.0.0.0", ""} else actual_host
            self.url = f"http://{display_host}:{actual_port}/"
            self.thread = threading.Thread(target=self.httpd.serve_forever, name="maco-gateway", daemon=True)
            self.thread.start()
            for index, host in enumerate(dict.fromkeys(self.options.extra_hosts)):
                if host == self.options.host:
                    continue
                extra_httpd = _make_http_server(
                    host,
                    actual_port,
                    handler_cls,
                    freebind=host in freebind_hosts,
                )
                extra_host, extra_port = extra_httpd.server_address[:2]
                extra_display_host = "127.0.0.1" if extra_host in {"0.0.0.0", ""} else extra_host
                self.extra_httpds.append(extra_httpd)
                self.extra_urls.append(f"http://{extra_display_host}:{extra_port}/")
                thread = threading.Thread(
                    target=extra_httpd.serve_forever,
                    name=f"maco-gateway-extra-{index}",
                    daemon=True,
                )
                thread.start()
                self.extra_threads.append(thread)
            _write_gateway_file(self.gateway_file, self.url, self.token, self.config.path)
        except Exception:
            for httpd, thread in [(self.httpd, self.thread), *zip(self.extra_httpds, self.extra_threads)]:
                if httpd is not None:
                    if thread is not None and thread.is_alive():
                        httpd.shutdown()
                        thread.join(timeout=10)
                    httpd.server_close()
            self.manager_loop.stop()
            raise
        return self

    def stop(self) -> None:
        for httpd in [self.httpd, *self.extra_httpds]:
            if httpd is not None:
                httpd.shutdown()
                httpd.server_close()
        for thread in [self.thread, *self.extra_threads]:
            if thread is not None:
                thread.join(timeout=10)
        self.manager_loop.stop()


def serve(config: MacoConfig, options: ServeOptions) -> None:
    """Run the gateway until interrupted."""

    gateway = GatewayServer(config, options).start()
    stop_event = threading.Event()

    def _request_shutdown(signum: int, _frame: Any) -> None:
        print(f"\nreceived signal {signum}; stopping maco gateway", file=sys.stderr)
        stop_event.set()

    old_sigint = signal.signal(signal.SIGINT, _request_shutdown)
    old_sigterm = signal.signal(signal.SIGTERM, _request_shutdown)
    try:
        print("maco gateway started")
        print(f"  URL: {gateway.url}")
        print(f"  workspace: {gateway.workspace}")
        print(f"  gateway file: {gateway.gateway_file}")
        print("  press Ctrl+C to stop")
        stop_event.wait()
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)
        gateway.stop()
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


def _make_http_server(
    host: str,
    port: int,
    handler_cls: type[BaseHTTPRequestHandler],
    *,
    freebind: bool = False,
) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), handler_cls, bind_and_activate=False)
    try:
        if freebind:
            _enable_freebind(httpd.socket)
        httpd.server_bind()
        httpd.server_activate()
    except Exception:
        httpd.server_close()
        raise
    return httpd


def _enable_freebind(sock: socket.socket) -> None:
    if not sys.platform.startswith("linux"):
        return
    ip_freebind = getattr(socket, "IP_FREEBIND", 15)
    sock.setsockopt(socket.SOL_IP, ip_freebind, 1)


def _make_handler(manager_loop: ManagerLoop, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "maco-gateway/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            if self.path.rstrip("/") in {"", "/health"}:
                self._write_json({"ok": True, "servers": manager_loop.manager.server_names()})
                return
            if self.path.rstrip("/") == "/tools":
                if token and self.headers.get("Authorization") != f"Bearer {token}":
                    self._write_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
                    return
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
