"""HTTP MCP server for running maco code inside a sandbox provider."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .sandbox import (
    GatewayInfo,
    SandboxContext,
    SandboxExec,
    SandboxProvider,
    SandboxRunResult,
    guest_path_for,
    provider_from_name,
    write_code_file,
)
from mcp.server.fastmcp import FastMCP


@dataclass(frozen=True)
class ServeMcpOptions:
    """Configuration for the sandbox-backed MCP server."""

    provider: str = "local"
    workspace: str | Path = ".maco"
    scratch: str | Path | None = None
    gateway_file: str | Path | None = None
    host: str = "127.0.0.1"
    port: int = 8789
    timeout: int = 60
    image: str | None = None
    python_command: str | None = None
    docker_binary: str = "docker"
    docker_network: str | None = None
    docker_gateway_host: str = "host.docker.internal"
    matchlock_binary: str = "matchlock"
    matchlock_gateway_host: str = "maco-gateway.internal"
    matchlock_gateway_ip: str | None = None
    matchlock_allow_host: tuple[str, ...] = ()


def serve_mcp(options: ServeMcpOptions) -> None:
    """Run a streamable HTTP MCP server exposing sandboxed bash/code tools."""

    workspace = Path(options.workspace).expanduser().resolve()
    scratch = (
        Path(options.scratch).expanduser().resolve()
        if options.scratch is not None
        else workspace.parent / "maco-serve-mcp"
    )
    gateway_file = (
        Path(options.gateway_file).expanduser().resolve()
        if options.gateway_file is not None
        else workspace / "gateway.json"
    )
    context = SandboxContext(
        workspace=workspace,
        scratch=scratch,
        gateway=GatewayInfo.from_file(gateway_file),
        timeout=options.timeout,
        python_command=options.python_command,
    )
    provider = provider_from_name(
        options.provider,
        context,
        image=options.image,
        docker_binary=options.docker_binary,
        docker_network=options.docker_network,
        docker_gateway_host=options.docker_gateway_host,
        matchlock_binary=options.matchlock_binary,
        matchlock_gateway_host=options.matchlock_gateway_host,
        matchlock_gateway_ip=options.matchlock_gateway_ip,
        matchlock_extra_allow_hosts=list(options.matchlock_allow_host),
    )
    app = create_serve_mcp_app(provider, context, host=options.host, port=options.port)
    print("maco serve-mcp started")
    print(f"  URL: http://{options.host}:{options.port}/mcp")
    print(f"  provider: {options.provider}")
    print(f"  workspace: {workspace}")
    print(f"  scratch: {scratch}")
    app.run("streamable-http")


def create_serve_mcp_app(
    provider: SandboxProvider,
    context: SandboxContext,
    *,
    host: str = "127.0.0.1",
    port: int = 8789,
) -> FastMCP:
    """Create the MCP app used by ``serve_mcp``.

    Separated for tests and future embedding. Tools intentionally return plain
    JSON-serializable dictionaries so MCP clients can inspect exit status,
    stdout, stderr, and the provider command that was executed.
    """

    app = FastMCP(
        "maco-serve-mcp",
        instructions=(
            "Run shell and Python code in a sandbox with generated maco wrappers. "
            "Use bash for discovery (`fd`, `rg`, `sed`) and code_executor for "
            "Python scripts that import maco_generated wrappers."
        ),
        host=host,
        port=port,
        json_response=True,
        stateless_http=True,
    )

    @app.tool(
        description=(
            "Run a non-interactive shell command inside the configured sandbox. "
            "The generated maco workspace is available via MACO_WORKSPACE and "
            "PYTHONPATH; the MCP gateway is available via MACO_GATEWAY_URL."
        )
    )
    def bash(command: str, timeout: int | None = None) -> dict[str, Any]:
        result = provider.run(SandboxExec(command=command, timeout=timeout))
        return _result_payload(result)

    @app.tool(
        description=(
            "Write Python code into the sandbox scratch directory and run it with "
            "generated maco wrappers on PYTHONPATH. The script can import from "
            "maco_generated.servers.<server>."
        )
    )
    def code_executor(
        code: str,
        filename: str = "task.py",
        args: list[str] | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        script = write_code_file(context.scratch, filename, code)
        guest_script = guest_path_for(script, context.scratch, provider.guest_scratch)
        command = provider.python_script_command(guest_script, args or [])
        result = provider.run(SandboxExec(command=command, timeout=timeout))
        payload = _result_payload(result)
        payload["script"] = str(script)
        payload["guest_script"] = guest_script
        return payload

    return app


def _result_payload(result: SandboxRunResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": result.command,
    }
