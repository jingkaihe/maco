from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any
from urllib.parse import urlsplit

import pytest

from maco.sandbox import (
    DockerSandboxProvider,
    GatewayInfo,
    LocalSandboxProvider,
    MatchlockSandboxProvider,
    SandboxContext,
    SandboxExec,
)


TOKEN = "integration-token"


def test_local_provider_executes_with_gateway_and_generated_workspace(tmp_path):
    with fake_gateway() as gateway:
        context = _context(tmp_path, gateway.url, TOKEN)
        result = _run_smoke(LocalSandboxProvider(context))

    _assert_smoke(result, expected_gateway_url=gateway.url)


def test_docker_provider_executes_with_gateway_and_generated_workspace(tmp_path):
    _require_docker()
    _docker_pull_or_skip("python:3.12-alpine")
    with fake_gateway(host="0.0.0.0") as gateway:
        context = _context(tmp_path, gateway.url, TOKEN)
        provider = DockerSandboxProvider(context, image="python:3.12-alpine")
        result = _run_smoke(provider)

    _assert_smoke(result, expected_gateway_url=_guest_url(gateway.url, "host.docker.internal"))


def test_matchlock_provider_executes_with_gateway_and_generated_workspace(tmp_path):
    _require_matchlock()
    with fake_gateway(host="0.0.0.0") as gateway:
        context = _context(tmp_path, gateway.url, TOKEN)
        provider = MatchlockSandboxProvider(
            context,
            image="python:3.12-alpine",
            gateway_ip="192.168.100.1",
        )
        result = _run_smoke(provider)

    _assert_smoke(result, expected_gateway_url=_guest_url(gateway.url, "maco-gateway.internal"))


def _run_smoke(provider: LocalSandboxProvider | DockerSandboxProvider | MatchlockSandboxProvider):
    script = r'''
import json
import os
import urllib.request

import maco_generated

request = urllib.request.Request(
    os.environ["MACO_GATEWAY_URL"] + "health",
    headers={"Authorization": "Bearer " + os.environ["MACO_GATEWAY_TOKEN"]},
)
with urllib.request.urlopen(request, timeout=5) as response:
    health = json.loads(response.read().decode("utf-8"))

print(json.dumps({
    "gateway_url": os.environ["MACO_GATEWAY_URL"],
    "workspace": os.environ["MACO_WORKSPACE"],
    "py_path_ok": maco_generated.VALUE == "generated-ok",
    "health": health,
}, sort_keys=True))
'''
    return provider.run(SandboxExec(command=f"python - <<'PY'\n{script}\nPY", timeout=90))


def _assert_smoke(result: Any, *, expected_gateway_url: str) -> None:
    assert result.exit_code == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.stdout
    payload = json.loads(lines[-1])
    assert payload["gateway_url"] == expected_gateway_url
    assert payload["py_path_ok"] is True
    assert payload["health"] == {"ok": True}


def _context(tmp_path: Path, gateway_url: str, token: str) -> SandboxContext:
    workspace = tmp_path / ".maco"
    generated = workspace / "maco_generated"
    generated.mkdir(parents=True)
    (generated / "client.py").write_text("", encoding="utf-8")
    (generated / "__init__.py").write_text('VALUE = "generated-ok"\n', encoding="utf-8")
    (workspace / "gateway.json").write_text(
        json.dumps({"url": gateway_url, "token": token}),
        encoding="utf-8",
    )
    return SandboxContext(
        workspace=workspace.resolve(),
        scratch=(tmp_path / "scratch").resolve(),
        gateway=GatewayInfo.from_file(workspace / "gateway.json"),
        timeout=90,
    )


class fake_gateway:
    def __init__(self, host: str = "127.0.0.1") -> None:
        self._bind_host = host
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def __enter__(self) -> fake_gateway:
        httpd = ThreadingHTTPServer((self._bind_host, 0), _GatewayHandler)
        actual_host, actual_port = httpd.server_address[:2]
        display_host = "127.0.0.1" if actual_host in {"0.0.0.0", ""} else actual_host
        self.url = f"http://{display_host}:{actual_port}/"
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self._httpd = httpd
        self._thread = thread
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        assert self._httpd is not None
        assert self._thread is not None
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


class _GatewayHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.end_headers()
            return
        if self.path.rstrip("/") in {"", "/health"}:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def _guest_url(url: str, host: str) -> str:
    parts = urlsplit(url)
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return parts._replace(netloc=netloc).geturl()


def _require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker binary not available")
    result = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"docker daemon not available: {result.stderr.strip()}")


def _docker_pull_or_skip(image: str) -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    if result.returncode == 0:
        return
    pull = subprocess.run(
        ["docker", "pull", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=180,
        check=False,
    )
    if pull.returncode != 0:
        pytest.skip(f"could not pull {image}: {pull.stderr.strip()}")


def _require_matchlock() -> None:
    if shutil.which("matchlock") is None:
        pytest.skip("matchlock binary not available")
    result = subprocess.run(
        ["matchlock", "diagnose", "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"matchlock diagnose failed: {result.stderr.strip()}")
