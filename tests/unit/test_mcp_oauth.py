from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from mcp.types import LATEST_PROTOCOL_VERSION

from maco.config import MacoConfig, OAuthConfig, ServerConfig
from maco.mcp_manager import MCPManager


def test_streamable_http_oauth_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "maco.oauth.webbrowser.open",
        lambda url: bool(urlopen(url, timeout=2).read()),
    )
    server = OAuthProtectedMCPServer()
    server.start()
    try:
        config = MacoConfig(
            path=tmp_path / "mcp.json",
            servers={
                "time": ServerConfig(
                    name="time",
                    server_type="http",
                    base_url=server.url + "/mcp",
                    oauth=OAuthConfig(interactive="always", open_browser=True),
                    tool_white_list=["get_current_time"],
                )
            },
        )

        async def run() -> dict[str, Any]:
            async with MCPManager(config) as manager:
                tools = await manager.list_tools()
                result = await manager.call_tool("time", "get_current_time")
                return {"tools": tools, "result": result}

        output = asyncio.run(run())

        assert [tool["name"] for tool in output["tools"]["time"]] == ["get_current_time"]
        assert output["result"]["content"][0]["text"] == "2024-01-01T00:00:00Z"
        assert server.token_requests == 1
        assert server.registration_requests == 1
        assert server.authorization_urls
    finally:
        server.close()


class OAuthProtectedMCPServer:
    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""
        self.access_token = "oauth-access-token"
        self.token_requests = 0
        self.registration_requests = 0
        self.authorization_urls: list[str] = []

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                owner.handle(self)

            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                owner.handle(self)

            def do_DELETE(self) -> None:  # noqa: N802 - stdlib callback name
                owner.handle(self)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        if parsed.path == "/.well-known/oauth-protected-resource":
            self._write_json(
                handler,
                {
                    "authorization_servers": [self.url],
                    "resource": self.url + "/mcp",
                    "scopes_supported": ["mcp.read"],
                },
            )
            return
        if parsed.path in {"/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"}:
            self._write_json(
                handler,
                {
                    "issuer": self.url,
                    "authorization_endpoint": self.url + "/authorize",
                    "token_endpoint": self.url + "/token",
                    "registration_endpoint": self.url + "/register",
                },
            )
            return
        if parsed.path == "/register":
            self.registration_requests += 1
            body = self._read_json(handler)
            assert body["client_name"] == "maco-time"
            assert body["scope"] == "mcp.read"
            self._write_json(
                handler,
                {
                    "client_id": "registered-client",
                    "redirect_uris": body["redirect_uris"],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "scope": body["scope"],
                },
                status=201,
            )
            return
        if parsed.path == "/authorize":
            query = parse_qs(parsed.query)
            self.authorization_urls.append(handler.path)
            redirect_uri = query["redirect_uri"][0]
            state = query["state"][0]
            assert query["resource"][0] == self.url + "/mcp"
            callback_url = f"{redirect_uri}?code=oauth-code&state={state}"
            with urlopen(callback_url, timeout=2) as response:
                response.read()
            self._write_text(handler, "authorized")
            return
        if parsed.path == "/token":
            self.token_requests += 1
            form = parse_qs(self._read_text(handler))
            assert form["grant_type"] == ["authorization_code"]
            assert form["code"] == ["oauth-code"]
            assert form["client_id"] == ["registered-client"]
            assert form["resource"] == [self.url + "/mcp"]
            self._write_json(
                handler,
                {
                    "access_token": self.access_token,
                    "token_type": "Bearer",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                },
            )
            return
        if parsed.path == "/mcp":
            self._handle_mcp(handler)
            return
        handler.send_error(404)

    def _handle_mcp(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.headers.get("Authorization") != f"Bearer {self.access_token}":
            handler.send_response(401)
            handler.send_header(
                "WWW-Authenticate",
                f'Bearer realm="mcp", resource_metadata="{self.url}/.well-known/oauth-protected-resource", scope="mcp.read"',
            )
            handler.end_headers()
            handler.wfile.write(b"authorization required")
            return
        if handler.command == "DELETE":
            handler.send_response(204)
            handler.end_headers()
            return

        request = self._read_json(handler)
        method = request.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "oauth-test", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "get_current_time",
                        "description": "Get the current time.",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        elif method == "tools/call":
            result = {
                "content": [{"type": "text", "text": "2024-01-01T00:00:00Z"}],
                "isError": False,
            }
        elif method == "notifications/initialized":
            handler.send_response(202)
            handler.end_headers()
            return
        else:
            handler.send_error(400, f"unknown method {method}")
            return
        self._write_json(handler, {"jsonrpc": "2.0", "id": request["id"], "result": result})

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        return json.loads(self._read_text(handler))

    def _read_text(self, handler: BaseHTTPRequestHandler) -> str:
        length = int(handler.headers.get("Content-Length") or "0")
        return handler.rfile.read(length).decode("utf-8")

    def _write_json(
        self,
        handler: BaseHTTPRequestHandler,
        payload: dict[str, Any],
        *,
        status: int = 200,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _write_text(self, handler: BaseHTTPRequestHandler, text: str) -> None:
        body = text.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
