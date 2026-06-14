from __future__ import annotations

import asyncio
import json
from pathlib import Path

from maco.sandbox import GatewayInfo, SandboxContext, SandboxExec, SandboxRunResult
from maco.serve_mcp import _bash_description, _code_execute_description, _mcp_instructions, create_serve_mcp_app


def test_serve_mcp_code_execute_uses_provider_script_command(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    app = create_serve_mcp_app(provider, context)

    result = app.call_tool("code_execute", {"code": "print('hello')", "filename": "task.py"})
    if hasattr(result, "__await__"):
        asyncio.run(result)

    assert provider.requests[0].command == "python /workspace/task.py"


def test_serve_mcp_instructions_list_server_modules_and_rg_fd_discovery(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    (context.workspace / "maco_generated" / "servers" / "echoServer").mkdir(parents=True)
    (context.workspace / "maco_generated" / "servers" / "echoServer" / "__init__.py").write_text(
        "from .echo import echo\n",
        encoding="utf-8",
    )

    instructions = _mcp_instructions(provider, context)

    assert "Available generated server modules:" in instructions
    assert "- echoServer: /workspace/.maco/maco_generated/servers/echoServer" in instructions
    assert "rg --files /workspace/.maco/maco_generated/servers" in instructions
    assert "fd . /workspace/.maco/maco_generated/servers -t f" in instructions
    assert "rg \"^def \" /workspace/.maco/maco_generated/servers/<server>" in instructions
    assert "MACO_GATEWAY_URL" not in instructions
    assert "PYTHONPATH" not in instructions


def test_code_execute_description_lists_server_modules(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    (context.workspace / "maco_generated" / "servers" / "github").mkdir(parents=True)
    (context.workspace / "maco_generated" / "servers" / "github" / "__init__.py").write_text(
        "from .search import search\n",
        encoding="utf-8",
    )

    description = _code_execute_description(provider, context)

    assert "from maco_generated.servers.<server> import <tool>" in description
    assert "- github: /workspace/.maco/maco_generated/servers/github" in description
    assert "/workspace/.maco/maco_generated/servers/<server>/__init__.py" in description
    assert "MACO_GATEWAY_URL" not in description
    assert "PYTHONPATH" not in description


def test_bash_description_uses_concrete_wrapper_paths_without_gateway_details(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()

    description = _bash_description(provider, context)

    assert "rg --files /workspace/.maco/maco_generated/servers" in description
    assert "fd . /workspace/.maco/maco_generated/servers -t f" in description
    assert "rg \"^def \" /workspace/.maco/maco_generated/servers/<server>" in description
    assert "MACO_GATEWAY_URL" not in description
    assert "PYTHONPATH" not in description


def _context(tmp_path: Path) -> SandboxContext:
    workspace = tmp_path / ".maco"
    (workspace / "maco_generated").mkdir(parents=True)
    (workspace / "maco_generated" / "client.py").write_text("", encoding="utf-8")
    (workspace / "gateway.json").write_text(
        json.dumps({"url": "http://127.0.0.1:9/", "token": "secret-token"}),
        encoding="utf-8",
    )
    return SandboxContext(
        workspace=workspace.resolve(),
        scratch=(tmp_path / "scratch").resolve(),
        gateway=GatewayInfo.from_file(workspace / "gateway.json"),
    )


class RecordingProvider:
    guest_workspace = "/workspace/.maco"
    guest_scratch = "/workspace"

    def __init__(self) -> None:
        self.requests: list[SandboxExec] = []

    def run(self, request: SandboxExec) -> SandboxRunResult:
        self.requests.append(request)
        return SandboxRunResult(0, "", "", ["recorded"])

    def python_script_command(self, guest_script_path: str, args: list[str]) -> str:
        return " ".join(["python", guest_script_path, *args])
