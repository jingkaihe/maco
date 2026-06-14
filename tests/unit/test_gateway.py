from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from maco.gateway import _make_handler


class FakeManagerLoop:
    manager: FakeManagerLoop

    def __init__(self) -> None:
        self.manager = self

    def server_names(self) -> list[str]:
        return ["fake"]

    def list_tools(self) -> dict[str, list[dict[str, str]]]:
        return {"fake": [{"name": "echo"}]}

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "structuredContent": {
                "server": server,
                "tool": tool,
                "arguments": arguments,
            }
        }


def test_gateway_handler_checks_auth_and_forwards_calls():
    manager = FakeManagerLoop()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(cast(Any, manager), "secret-token"))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    try:
        with urlopen(url + "health", timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health == {"ok": True, "servers": ["fake"]}

        with pytest.raises(HTTPError) as exc_info:
            urlopen(url + "tools", timeout=2)
        assert exc_info.value.code == 401

        with urlopen(
            Request(url + "tools", headers={"Authorization": "Bearer secret-token"}), timeout=2
        ) as response:
            tools = json.loads(response.read().decode("utf-8"))
        assert tools == {"servers": {"fake": [{"name": "echo"}]}}

        with pytest.raises(HTTPError) as exc_info:
            urlopen(
                Request(
                    url,
                    data=json.dumps({"server": "fake", "tool": "echo", "arguments": {}}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=2,
            )
        assert exc_info.value.code == 401

        with urlopen(
            Request(
                url,
                data=json.dumps(
                    {"server": "fake", "tool": "echo", "arguments": {"message": "hello"}}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": "Bearer secret-token"},
                method="POST",
            ),
            timeout=2,
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
        assert result == {
            "structuredContent": {
                "server": "fake",
                "tool": "echo",
                "arguments": {"message": "hello"},
            }
        }
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
