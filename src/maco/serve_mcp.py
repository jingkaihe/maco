"""HTTP MCP server for running maco code inside a sandbox provider."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Annotated, Any

from jinja2 import Environment, PackageLoader, StrictUndefined
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .codegen import generate
from .config import load_config
from .gateway import GatewayServer, ServeOptions
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


_TEMPLATES = Environment(
    loader=PackageLoader("maco", "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class ServeMcpOptions:
    """Configuration for the sandbox-backed MCP server."""

    config: str | Path = "mcp.json"
    provider: str = "local"
    workspace: str | Path = ".maco"
    clean: bool = False
    scratch: str | Path | None = None
    gateway_file: str | Path | None = None
    gateway_host: str | None = None
    gateway_port: int = 0
    gateway_token: str | None = None
    gateway_use_token: bool = True
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

    config = load_config(options.config)
    workspace = Path(options.workspace).expanduser().resolve()
    stats = generate(config, workspace=workspace, clean=options.clean)
    print(f"Generated {stats.tool_count} tools from {stats.server_count} servers")
    print(f"Workspace: {workspace}")
    scratch = (
        Path(options.scratch).expanduser().resolve()
        if options.scratch is not None
        else workspace.parent / "maco-serve-mcp"
    )
    gateway_server: GatewayServer | None = None
    gateway_file = (
        Path(options.gateway_file).expanduser().resolve()
        if options.gateway_file is not None
        else workspace / "gateway.json"
    )
    if options.gateway_file is None:
        gateway_host = options.gateway_host or _default_gateway_host(options.provider)
        gateway_server = GatewayServer(
            config,
            ServeOptions(
                host=gateway_host,
                port=options.gateway_port,
                workspace=workspace,
                token=options.gateway_token,
                use_token=options.gateway_use_token,
            ),
        ).start()
        gateway_file = gateway_server.gateway_file
        print("maco gateway started")
        print(f"  URL: {gateway_server.url}")
        print(f"  gateway file: {gateway_file}")
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
    try:
        app.run("streamable-http")
    finally:
        if gateway_server is not None:
            gateway_server.stop()


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
        instructions=_mcp_instructions(provider, context),
        host=host,
        port=port,
        json_response=True,
        stateless_http=True,
    )

    @app.tool(description=_bash_description(provider, context))
    def bash(
        command: Annotated[
            str,
            Field(description="Non-interactive shell command to run in the sandbox scratch directory."),
        ],
        timeout: Annotated[
            int | None,
            Field(description="Optional command timeout in seconds. Omit to use the server default."),
        ] = None,
    ) -> dict[str, Any]:
        result = provider.run(SandboxExec(command=command, timeout=timeout))
        return _result_payload(result)

    @app.tool(description=_code_execute_description(provider, context))
    def code_execute(
        code: Annotated[
            str,
            Field(
                description=(
                    "Python source code to write into the sandbox scratch directory and execute. "
                    "Import generated MCP wrappers from maco_generated."
                )
            ),
        ],
        filename: Annotated[
            str | None,
            Field(
                description=(
                    "Optional relative .py path in scratch. Leave null to use <hash>.py."
                )
            ),
        ] = None,
        args: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional command-line arguments passed as sys.argv[1:]. "
                    "Leave null for no arguments."
                )
            ),
        ] = None,
        timeout: Annotated[
            int | None,
            Field(description="Optional script timeout in seconds. Omit to use the server default."),
        ] = None,
    ) -> dict[str, Any]:
        filename = filename if filename is not None else _content_addressed_script_filename(code)
        script = write_code_file(context.scratch, filename, code)
        guest_script = guest_path_for(script, context.scratch, provider.guest_scratch)
        command = provider.python_script_command(guest_script, args or [])
        result = provider.run(SandboxExec(command=command, timeout=timeout))
        payload = _result_payload(result)
        payload["script"] = str(script)
        payload["guest_script"] = guest_script
        return payload

    return app


def _mcp_instructions(provider: SandboxProvider, context: SandboxContext) -> str:
    return _render_model_text("serve_mcp_instructions.j2", provider, context)


def _bash_description(provider: SandboxProvider, context: SandboxContext) -> str:
    return _render_model_text("bash_description.j2", provider, context)


def _code_execute_description(provider: SandboxProvider, context: SandboxContext) -> str:
    return _render_model_text("code_execute_description.j2", provider, context)


def _render_model_text(template_name: str, provider: SandboxProvider, context: SandboxContext) -> str:
    wrapper_root = _guest_server_root(provider)
    return _TEMPLATES.get_template(template_name).render(
        server_modules=_server_modules(context.workspace),
        wrapper_root=wrapper_root,
    ).strip()


def _server_modules(workspace: Path) -> list[str]:
    server_root = workspace / "maco_generated" / "servers"
    return sorted(
        path.name
        for path in server_root.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    ) if server_root.exists() else []


def _guest_server_root(provider: SandboxProvider) -> str:
    return f"{provider.guest_workspace.rstrip('/')}/maco_generated/servers"


def _content_addressed_script_filename(code: str) -> str:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.py"


def _default_gateway_host(provider: str) -> str:
    return "0.0.0.0" if provider.replace("_", "-").lower() in {"docker", "matchlock"} else "127.0.0.1"


def _result_payload(result: SandboxRunResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": result.command,
    }
