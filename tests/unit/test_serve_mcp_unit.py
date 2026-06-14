from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re

from maco.sandbox import GatewayInfo, SandboxContext, SandboxExec, SandboxRunResult
from maco.serve_mcp import (
    _bash_description,
    _code_execute_description,
    _content_addressed_script_filename,
    _mcp_instructions,
    create_serve_mcp_app,
)


def test_serve_mcp_code_execute_uses_provider_script_command(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    app = create_serve_mcp_app(provider, context)

    result = app.call_tool("code_execute", {"code": "print('hello')", "filename": "task.py"})
    if hasattr(result, "__await__"):
        asyncio.run(result)

    assert provider.writes == [("task.py", "print('hello')")]
    assert provider.requests[0].command == "python /workspace/task.py"


def test_serve_mcp_code_execute_omitted_filename_uses_deterministic_path(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    app = create_serve_mcp_app(provider, context)
    code = "print('hello')"

    result = app.call_tool("code_execute", {"code": code})
    if hasattr(result, "__await__"):
        asyncio.run(result)

    relative = _content_addressed_script_filename(code)
    assert re.fullmatch(r"[0-9a-f]{16}\.py", relative)
    assert provider.writes == [(relative, code)]
    assert provider.requests[0].command == f"python /workspace/{relative}"


def test_serve_mcp_instructions_list_server_modules_and_rg_fd_discovery(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    (context.workspace / "tools" / "echoServer").mkdir(parents=True)
    (context.workspace / "tools" / "echoServer" / "__init__.py").write_text(
        "from .echo import echo\n",
        encoding="utf-8",
    )

    instructions = _mcp_instructions(provider, context)

    assert "Available generated server modules:" in instructions
    assert "- echoServer: /workspace/macosdk/tools/echoServer" in instructions
    assert "rg --files /workspace/macosdk/tools" in instructions
    assert "fd . /workspace/macosdk/tools -t f" in instructions
    assert "rg \"^def \" /workspace/macosdk/tools/<server>" in instructions
    assert "MACO_GATEWAY_URL" not in instructions
    assert "PYTHONPATH" not in instructions


def test_serve_mcp_instructions_include_all_server_modules(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    for index in range(55):
        server_dir = context.workspace / "tools" / f"server{index:02d}"
        server_dir.mkdir(parents=True)
        (server_dir / "__init__.py").write_text("", encoding="utf-8")

    instructions = _mcp_instructions(provider, context)

    assert "..." not in instructions
    assert "- server00: /workspace/macosdk/tools/server00" in instructions
    assert "- server54: /workspace/macosdk/tools/server54" in instructions


def test_code_execute_description_lists_server_modules(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    (context.workspace / "tools" / "github").mkdir(parents=True)
    (context.workspace / "tools" / "github" / "__init__.py").write_text(
        "from .search import search\n",
        encoding="utf-8",
    )

    description = _code_execute_description(provider, context)

    assert "from tools.<server> import <tool>" in description
    assert "For most tasks, pass only the code argument" in description
    assert "<hash>.py" in description
    assert "- github: /workspace/macosdk/tools/github" in description
    assert "/workspace/macosdk/tools/<server>/__init__.py" in description
    assert "MACO_GATEWAY_URL" not in description
    assert "PYTHONPATH" not in description


def test_code_execute_schema_describes_optional_arguments(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    app = create_serve_mcp_app(provider, context)
    tools = asyncio.run(app.list_tools())
    code_execute = next(tool for tool in tools if tool.name == "code_execute")

    schema = code_execute.inputSchema
    assert schema["required"] == ["code"]
    assert "Import generated tools" in schema["properties"]["code"]["description"]
    assert "<hash>.py" in schema["properties"]["filename"]["description"]
    assert "sys.argv[1:]" in schema["properties"]["args"]["description"]
    assert "server default" in schema["properties"]["timeout"]["description"]


def test_bash_description_uses_concrete_wrapper_paths_without_gateway_details(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()

    description = _bash_description(provider, context)

    assert "rg --files /workspace/macosdk/tools" in description
    assert "fd . /workspace/macosdk/tools -t f" in description
    assert "rg \"^def \" /workspace/macosdk/tools/<server>" in description
    assert "MACO_GATEWAY_URL" not in description
    assert "PYTHONPATH" not in description


def _context(tmp_path: Path) -> SandboxContext:
    workspace = tmp_path / ".maco"
    (workspace / "tools").mkdir(parents=True)
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
    guest_workspace = "/workspace/macosdk"
    guest_scratch = "/workspace"

    def __init__(self) -> None:
        self.requests: list[SandboxExec] = []
        self.writes: list[tuple[str, str]] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def write_file(self, relative_path: str, content: str) -> str:
        self.writes.append((relative_path, content))
        return f"/workspace/{relative_path}"

    def run(self, request: SandboxExec) -> SandboxRunResult:
        self.requests.append(request)
        return SandboxRunResult(0, "", "", ["recorded"])

    def python_script_command(self, guest_script_path: str, args: list[str]) -> str:
        return " ".join(["python", guest_script_path, *args])
