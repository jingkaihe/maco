from __future__ import annotations

import asyncio
import json
from pathlib import Path

from maco.sandbox import GatewayInfo, SandboxContext, SandboxExec, SandboxRunResult
from maco.serve_mcp import create_serve_mcp_app


def test_serve_mcp_code_executor_uses_provider_script_command(tmp_path):
    context = _context(tmp_path)
    provider = RecordingProvider()
    app = create_serve_mcp_app(provider, context)

    result = app.call_tool("code_executor", {"code": "print('hello')", "filename": "task.py"})
    if hasattr(result, "__await__"):
        asyncio.run(result)

    assert provider.requests[0].command == "python /workspace/task.py"


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
    guest_workspace = "/maco"
    guest_scratch = "/workspace"

    def __init__(self) -> None:
        self.requests: list[SandboxExec] = []

    def run(self, request: SandboxExec) -> SandboxRunResult:
        self.requests.append(request)
        return SandboxRunResult(0, "", "", ["recorded"])

    def python_script_command(self, guest_script_path: str, args: list[str]) -> str:
        return " ".join(["python", guest_script_path, *args])
