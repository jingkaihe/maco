from __future__ import annotations

import asyncio
from collections.abc import Iterator
import contextlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from typing import Any
from urllib.request import urlopen

import httpx
import pytest

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def test_serve_mcp_local_tools_call_real_backend_with_generated_wrappers(tmp_path):
    repo = _repo_root()
    config_path, workspace = _generate_echo_workspace(repo, tmp_path)

    with _maco_gateway(repo, config_path, workspace, host="127.0.0.1"):
        with _serve_mcp(repo, workspace, tmp_path, provider="local") as mcp_url:
            payloads = asyncio.run(_call_wrapper_tool_values(mcp_url))

    bash_payload = payloads["bash"]
    code_payload = payloads["code_execute"]
    _assert_successful_command(bash_payload)
    _assert_successful_command(code_payload)
    assert _last_json_line(bash_payload["stdout"]) == {"echo": "bash-wrapper", "total": 42}
    assert _last_json_line(code_payload["stdout"]) == {"echo": "code-wrapper", "total": 13}


def test_serve_mcp_local_bash_returns_exit_status_stdout_and_stderr(tmp_path):
    repo = _repo_root()
    config_path, workspace = _generate_echo_workspace(repo, tmp_path)

    with _maco_gateway(repo, config_path, workspace, host="127.0.0.1"):
        with _serve_mcp(repo, workspace, tmp_path, provider="local") as mcp_url:
            payload = asyncio.run(
                _call_tool_payload(
                    mcp_url,
                    "bash",
                    {
                        "command": "printf 'stdout-value\\n'; printf 'stderr-value\\n' >&2; exit 7",
                        "timeout": 30,
                    },
                )
            )

    assert payload["ok"] is False
    assert payload["exit_code"] == 7
    assert payload["stdout"] == "stdout-value\n"
    assert payload["stderr"] == "stderr-value\n"


@pytest.mark.parametrize("provider", ["local", "docker", "matchlock"])
def test_serve_mcp_providers_call_real_backend_with_generated_client(tmp_path, provider):
    _require_provider(provider)
    repo = _repo_root()
    config_path, workspace = _generate_echo_workspace(repo, tmp_path)
    gateway_host = "0.0.0.0" if provider in {"docker", "matchlock"} else "127.0.0.1"

    with _maco_gateway(repo, config_path, workspace, host=gateway_host):
        with _serve_mcp(
            repo,
            workspace,
            tmp_path,
            provider=provider,
            extra_args=_provider_args(provider),
        ) as mcp_url:
            try:
                payloads = asyncio.run(_call_generated_client_tool_values(mcp_url, provider))
            except Exception as exc:  # pragma: no cover - Matchlock host/runtime dependent
                if provider == "matchlock":
                    pytest.skip(f"matchlock serve-mcp integration unavailable on this host: {exc}")
                raise

    for payload in payloads.values():
        _assert_successful_command(payload)

    assert _last_json_line(payloads["bash"]["stdout"]) == {
        "echo": f"bash-client:{provider}",
        "total": 34,
    }
    assert _last_json_line(payloads["code_execute"]["stdout"]) == {
        "echo": f"code-client:{provider}",
        "total": 56,
    }


async def _call_wrapper_tool_values(mcp_url: str) -> dict[str, dict[str, Any]]:
    bash_script = r'''
import json
from maco_generated.client import call_mcp_tool

echo = call_mcp_tool("echo-server", "echo", {"message": "bash-wrapper"})["result"]
total = call_mcp_tool("echo-server", "add", {"a": 20, "b": 22})["result"]
print(json.dumps({"echo": echo, "total": total}, sort_keys=True))
'''
    code = r'''
import json
from maco_generated.servers.echoServer import add, echo

message = echo(message="code-wrapper")
total = add(a=6, b=7)
print(json.dumps({"echo": message.result, "total": total.result}, sort_keys=True))
'''
    return await _call_bash_and_code_execute(
        mcp_url,
        bash_command=_python_heredoc(bash_script),
        code=code,
        filename="wrapper_task.py",
    )


async def _call_generated_client_tool_values(mcp_url: str, provider: str) -> dict[str, dict[str, Any]]:
    bash_script = f'''
import json
from maco_generated.client import call_mcp_tool

echo = call_mcp_tool("echo-server", "echo", {{"message": "bash-client:{provider}"}})["result"]
total = call_mcp_tool("echo-server", "add", {{"a": 11, "b": 23}})["result"]
print(json.dumps({{"echo": echo, "total": total}}, sort_keys=True))
'''
    code = r'''
import argparse
import json
from maco_generated.client import call_mcp_tool

parser = argparse.ArgumentParser()
parser.add_argument("--label", required=True)
args = parser.parse_args()

echo = call_mcp_tool("echo-server", "echo", {"message": f"code-client:{args.label}"})["result"]
total = call_mcp_tool("echo-server", "add", {"a": 50, "b": 6})["result"]
print(json.dumps({"echo": echo, "total": total}, sort_keys=True))
'''
    return await _call_bash_and_code_execute(
        mcp_url,
        bash_command=_python_heredoc(bash_script),
        code=code,
        filename=f"{provider}/client_task.py",
        args=["--label", provider],
    )


async def _call_bash_and_code_execute(
    mcp_url: str,
    *,
    bash_command: str,
    code: str,
    filename: str,
    args: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    async with _streamable_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert {"bash", "code_execute"}.issubset(tool_names)

            bash_result = await session.call_tool("bash", {"command": bash_command, "timeout": 120})
            code_result = await session.call_tool(
                "code_execute",
                {
                    "code": code,
                    "filename": filename,
                    "args": args or [],
                    "timeout": 120,
                },
            )
    return {
        "bash": _tool_payload(bash_result),
        "code_execute": _tool_payload(code_result),
    }


async def _call_tool_payload(mcp_url: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with _streamable_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
    return _tool_payload(result)


@contextlib.asynccontextmanager
async def _streamable_client(mcp_url: str, *, timeout: float = 30):
    async with httpx.AsyncClient(timeout=timeout) as http_client:
        async with streamable_http_client(mcp_url, http_client=http_client) as streams:
            yield streams


def _tool_payload(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structuredContent", None)
    if payload is None:
        text = "".join(str(getattr(block, "text", "")) for block in getattr(result, "content", []) or [])
        payload = json.loads(text)
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        payload = payload["result"]
    assert isinstance(payload, dict), payload
    return payload


def _assert_successful_command(payload: dict[str, Any]) -> None:
    assert payload["ok"] is True, payload.get("stderr")
    assert payload["exit_code"] == 0, payload.get("stderr")


def _last_json_line(stdout: str) -> Any:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, stdout
    return json.loads(lines[-1])


def _python_heredoc(script: str) -> str:
    return f"python - <<'PY'\n{script.strip()}\nPY"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _generate_echo_workspace(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "mcp.json"
    workspace = tmp_path / ".maco"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "echo-server": {
                        "command": "uv",
                        "args": [
                            "run",
                            "--project",
                            str(repo),
                            "python",
                            str(repo / "tests" / "fixtures" / "echo_mcp_server.py"),
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(repo),
            "maco",
            "gen",
            "--config",
            str(config_path),
            "--workspace",
            str(workspace),
            "--clean",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return config_path, workspace


@contextlib.contextmanager
def _maco_gateway(repo: Path, config_path: Path, workspace: Path, *, host: str) -> Iterator[None]:
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "--project",
            str(repo),
            "maco",
            "serve",
            "--config",
            str(config_path),
            "--workspace",
            str(workspace),
            "--host",
            host,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_process_env(),
    )
    try:
        _wait_for_gateway(process, workspace / "gateway.json")
        yield
    finally:
        _terminate(process)


@contextlib.contextmanager
def _serve_mcp(
    repo: Path,
    workspace: Path,
    tmp_path: Path,
    *,
    provider: str,
    extra_args: list[str] | None = None,
) -> Iterator[str]:
    port = _free_port()
    scratch = tmp_path / f"scratch-{provider}"
    command = [
        "uv",
        "run",
        "--project",
        str(repo),
        "maco",
        "serve-mcp",
        "--workspace",
        str(workspace),
        "--scratch",
        str(scratch),
        "--provider",
        provider,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--timeout",
        "120",
    ]
    command.extend(extra_args or [])
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_process_env(),
    )
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    try:
        asyncio.run(_wait_for_mcp_server(process, mcp_url))
        yield mcp_url
    finally:
        _terminate(process)


def _wait_for_gateway(process: subprocess.Popen[str], gateway_file: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"maco serve exited early:\n{_read_process_output(process)}")
        if gateway_file.exists():
            gateway = json.loads(gateway_file.read_text(encoding="utf-8"))
            try:
                with urlopen(gateway["url"] + "health", timeout=1) as response:
                    if response.status == 200:
                        return
            except Exception:
                pass
        time.sleep(0.1)
    raise AssertionError(f"maco serve did not become healthy:\n{_read_process_output(process)}")


async def _wait_for_mcp_server(process: subprocess.Popen[str], mcp_url: str) -> None:
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"maco serve-mcp exited early:\n{_read_process_output(process)}")
        try:
            async with _streamable_client(mcp_url, timeout=3) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    tool_names = {tool.name for tool in tools.tools}
                    if {"bash", "code_execute"}.issubset(tool_names):
                        return
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(0.2)
    raise AssertionError(f"maco serve-mcp did not become ready: {last_error}\n{_read_process_output(process)}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _process_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    if process.stdout:
        process.stdout.close()


def _read_process_output(process: subprocess.Popen[str]) -> str:
    if not process.stdout:
        return ""
    try:
        return process.stdout.read()
    except Exception:
        return ""


def _require_provider(provider: str) -> None:
    if provider == "docker":
        _require_docker()
        _docker_pull_or_skip("python:3.12-alpine")
    elif provider == "matchlock":
        _require_matchlock()


def _provider_args(provider: str) -> list[str]:
    if provider == "docker":
        return ["--image", "python:3.12-alpine", "--python-command", "python"]
    if provider == "matchlock":
        return [
            "--image",
            "python:3.12-alpine",
            "--python-command",
            "python",
            "--matchlock-gateway-ip",
            "192.168.100.1",
        ]
    return ["--python-command", "python"]


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
