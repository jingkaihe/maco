"""Command line interface for maco."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any

import click

from .codegen import generate, generate_sandbox_sdk_from_gateway
from .config import ConfigError, load_config
from .gateway import ServeOptions, serve
from .runner import RunnerError, exit_with_error, run_code
from .sandbox import DEFAULT_SANDBOX_IMAGE, SANDBOX_SDK_ROOT
from .serve_mcp import ServeMcpOptions, serve_mcp
from .service import (
    DEFAULT_MCP_PORT,
    ServiceError,
    SERVICE_ID_ENV,
    SERVICE_TOKEN_ENV,
    ensure_no_detached_service,
    find_available_port,
    list_services,
    show_status,
    start_detached,
    stop_detached,
)
from .version import get_version_info


_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
_INTERNAL_CONTEXT_SETTINGS = {"help_option_names": []}
_RUN_CONTEXT_SETTINGS = {
    **_CONTEXT_SETTINGS,
    "allow_extra_args": True,
    "allow_interspersed_args": False,
}


def _serve_mcp_options(*, port_default: int | None = DEFAULT_MCP_PORT) -> Any:
    port_help = (
        "HTTP MCP bind port (default: auto)"
        if port_default is None
        else f"HTTP MCP bind port (default: {port_default})"
    )

    def decorator(command: Any) -> Any:
        for option in reversed(
            [
                click.option("--config", default="mcp.json", help="MCP config path", show_default=True),
                click.option(
                    "--provider",
                    type=click.Choice(["local", "docker", "matchlock"]),
                    default="local",
                    help="sandbox provider to use",
                    show_default=True,
                ),
                click.option("--workspace", default=".maco", help="generated workspace directory", show_default=True),
                click.option("--clean", is_flag=True, help="remove the workspace before generating"),
                click.option("--scratch", help="writable scratch directory for sandbox code"),
                click.option("--gateway-host", help="host for the managed gateway started by maco up"),
                click.option(
                    "--gateway-port",
                    default=0,
                    type=int,
                    help="port for the managed gateway started by maco up",
                    show_default=True,
                ),
                click.option("--gateway-token", help="explicit bearer token for the managed gateway"),
                click.option(
                    "--no-gateway-token",
                    is_flag=True,
                    help="disable bearer-token protection for the managed gateway",
                ),
                click.option("--host", default="127.0.0.1", help="HTTP MCP bind host", show_default=True),
                click.option("--port", default=port_default, type=int, help=port_help),
                click.option("--timeout", default=60, type=int, help="default command timeout in seconds", show_default=True),
                click.option("--debug", is_flag=True, help="log provider command summaries to server stderr"),
                click.option(
                    "--image",
                    help=f"container image for docker/matchlock providers (default: {DEFAULT_SANDBOX_IMAGE})",
                ),
                click.option("--python-command", help="guest command prefix used by code_execute"),
                click.option("--docker-binary", default="docker", help="docker binary path/name", show_default=True),
                click.option("--docker-network", help="docker network passed to `docker run --network`"),
                click.option(
                    "--docker-gateway-host",
                    default="host.docker.internal",
                    help="hostname inside docker that reaches the host gateway",
                    show_default=True,
                ),
                click.option(
                    "--docker-gateway-ip",
                    help="explicit host gateway IP to map inside Docker; usually auto-detected",
                ),
                click.option(
                    "--matchlock-binary",
                    default="matchlock",
                    help="matchlock binary path/name",
                    show_default=True,
                ),
                click.option(
                    "--matchlock-gateway-host",
                    default="maco-gateway.internal",
                    help="hostname inside matchlock that reaches the host gateway",
                    show_default=True,
                ),
                click.option(
                    "--matchlock-gateway-ip",
                    help=(
                        "IP for --add-host <gateway-host>:<ip> inside matchlock "
                        "(managed default: 192.168.64.1 on macOS, 192.168.100.1 elsewhere)"
                    ),
                ),
                click.option(
                    "--matchlock-allow-host",
                    multiple=True,
                    help="extra host to allow from the matchlock sandbox (repeatable)",
                ),
            ]
        ):
            command = option(command)
        return command

    return decorator


@click.group(
    name="maco",
    context_settings=_CONTEXT_SETTINGS,
    help="Generate and execute Python code interfaces for MCP tools.",
    no_args_is_help=True,
)
def app() -> None:
    """maco command group."""


@app.command("version", help="print maco version and release metadata")
def version_command() -> int:
    return _cmd_version(_namespace(command="version"))


@app.command("gen", help="generate Python wrappers for configured MCP tools")
@click.option("--config", default="mcp.json", help="MCP config path", show_default=True)
@click.option("--workspace", default=".maco", help="generated workspace directory", show_default=True)
@click.option("--server", help="only generate wrappers for one configured server")
@click.option("--clean", is_flag=True, help="remove the workspace before generating")
def gen_command(config: str, workspace: str, server: str | None, clean: bool) -> int:
    return _cmd_gen(_namespace(command="gen", config=config, workspace=workspace, server=server, clean=clean))


@app.command("run", context_settings=_RUN_CONTEXT_SETTINGS, help="run a Python file with generated wrappers available")
@click.option("--workspace", help="generated workspace directory (default: auto-detect)")
@click.option("--cwd", help="working directory for the script")
@click.option("--python", help="Python version/interpreter to pass to uv run")
@click.argument("code_path")
@click.argument("script_args", nargs=-1, type=click.UNPROCESSED)
def run_command(
    workspace: str | None,
    cwd: str | None,
    python: str | None,
    code_path: str,
    script_args: tuple[str, ...],
) -> int:
    return _cmd_run(
        _namespace(
            command="run",
            workspace=workspace,
            cwd=cwd,
            python=python,
            code_path=code_path,
            script_args=list(script_args),
        )
    )


@app.command("up", help="start the sandboxed maco MCP server")
@_serve_mcp_options(port_default=None)
@click.option("-d", "--detach", is_flag=True, help="start maco in the background for this project")
def up_command(**kwargs: Any) -> int:
    return _cmd_up(_serve_mcp_namespace("up", kwargs))


@app.command("status", help="show detached maco status for this project")
@click.option("--workspace", default=".maco", help="generated workspace directory", show_default=True)
def status_command(workspace: str) -> int:
    return _cmd_status(_namespace(command="status", workspace=workspace))


@app.command("down", help="stop detached maco for this project")
@click.option("--workspace", default=".maco", help="generated workspace directory", show_default=True)
def down_command(workspace: str) -> int:
    return _cmd_down(_namespace(command="down", workspace=workspace))


@app.command("ls", help="list detached maco processes")
def ls_command() -> int:
    return _cmd_ls(_namespace(command="ls"))


@app.command("_mcp-server", hidden=True, add_help_option=False, context_settings=_INTERNAL_CONTEXT_SETTINGS)
@_serve_mcp_options()
def internal_mcp_server_command(**kwargs: Any) -> int:
    return _cmd_mcp_server(_serve_mcp_namespace("_mcp-server", kwargs))


@app.command("_gateway", hidden=True, add_help_option=False, context_settings=_INTERNAL_CONTEXT_SETTINGS)
@click.option("--config", default="mcp.json")
@click.option("--workspace", default=".maco")
@click.option("--clean", is_flag=True)
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=0, type=int)
@click.option("--token")
@click.option("--no-token", is_flag=True)
def internal_gateway_command(
    config: str,
    workspace: str,
    clean: bool,
    host: str,
    port: int,
    token: str | None,
    no_token: bool,
) -> int:
    return _cmd_serve(
        _namespace(
            command="_gateway",
            config=config,
            workspace=workspace,
            clean=clean,
            host=host,
            port=port,
            token=token,
            no_token=no_token,
        )
    )


@app.command("sandbox-bootstrap", hidden=True, add_help_option=False, context_settings=_INTERNAL_CONTEXT_SETTINGS)
@click.option("--gateway-url")
@click.option("--gateway-token")
@click.option("--workspace", default=SANDBOX_SDK_ROOT)
@click.option("--no-clean", is_flag=True)
def sandbox_bootstrap_command(
    gateway_url: str | None,
    gateway_token: str | None,
    workspace: str,
    no_clean: bool,
) -> int:
    return _cmd_sandbox_bootstrap(
        _namespace(
            command="sandbox-bootstrap",
            gateway_url=gateway_url,
            gateway_token=gateway_token,
            workspace=workspace,
            no_clean=no_clean,
        )
    )


def build_cli() -> click.Group:
    return app


def _namespace(**values: Any) -> SimpleNamespace:
    return SimpleNamespace(**values)


def _serve_mcp_namespace(command: str, values: dict[str, Any]) -> SimpleNamespace:
    normalized = dict(values)
    normalized["command"] = command
    normalized["matchlock_allow_host"] = list(normalized.get("matchlock_allow_host") or [])
    return _namespace(**normalized)


def main(argv: list[str] | None = None) -> int:
    try:
        result = app.main(args=argv, prog_name="maco", standalone_mode=False)
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return exc.exit_code
    except (ConfigError, RunnerError, ServiceError, OSError, ValueError) as exc:
        exit_with_error(exc)
    return int(result or 0)


def _cmd_version(args: Any) -> int:
    info = get_version_info()
    print(f"version: {info.version}")
    return 0


def _cmd_gen(args: Any) -> int:
    config = load_config(args.config)
    stats = generate(
        config,
        workspace=args.workspace,
        server_filter=args.server,
        clean=args.clean,
    )
    print(f"Generated {stats.tool_count} tools from {stats.server_count} servers")
    print(f"Workspace: {stats.workspace}")
    print("Explore: rg --files {}/maco_generated/servers".format(stats.workspace))
    return 0


def _cmd_serve(args: Any) -> int:
    config = load_config(args.config)
    stats = generate(config, workspace=args.workspace, clean=args.clean)
    print(f"Generated {stats.tool_count} tools from {stats.server_count} servers")
    print(f"Workspace: {stats.workspace}")
    serve(
        config,
        ServeOptions(
            host=args.host,
            port=args.port,
            workspace=args.workspace,
            token=args.token,
            use_token=not args.no_token,
        ),
    )
    return 0


def _cmd_run(args: Any) -> int:
    script_args = list(args.script_args or [])
    if script_args and script_args[0] == "--":
        script_args = script_args[1:]
    return run_code(
        args.code_path,
        script_args,
        workspace=args.workspace,
        cwd=args.cwd,
        python=args.python,
    )


def _cmd_up(args: Any) -> int:
    if args.detach:
        start_detached(args)
        return 0
    ensure_no_detached_service(args)
    if args.port is None:
        args.port = find_available_port(args.host, DEFAULT_MCP_PORT)
    return _cmd_mcp_server(args)


def _cmd_mcp_server(args: Any) -> int:
    if args.port is None:
        args.port = DEFAULT_MCP_PORT
    serve_mcp(
        ServeMcpOptions(
            config=args.config,
            provider=args.provider,
            workspace=args.workspace,
            clean=args.clean,
            scratch=args.scratch,
            gateway_host=args.gateway_host,
            gateway_port=args.gateway_port,
            gateway_token=args.gateway_token,
            gateway_use_token=not args.no_gateway_token,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
            debug=args.debug,
            image=args.image,
            python_command=args.python_command,
            docker_binary=args.docker_binary,
            docker_network=args.docker_network,
            docker_gateway_host=args.docker_gateway_host,
            docker_gateway_ip=args.docker_gateway_ip,
            matchlock_binary=args.matchlock_binary,
            matchlock_gateway_host=args.matchlock_gateway_host,
            matchlock_gateway_ip=args.matchlock_gateway_ip,
            matchlock_allow_host=tuple(args.matchlock_allow_host or []),
            detached_service_id=os.environ.get(SERVICE_ID_ENV),
            detached_service_token=os.environ.get(SERVICE_TOKEN_ENV),
        )
    )
    return 0


def _cmd_status(args: Any) -> int:
    show_status(args)
    return 0


def _cmd_down(args: Any) -> int:
    stop_detached(args)
    return 0


def _cmd_ls(args: Any) -> int:
    list_services()
    return 0


def _cmd_sandbox_bootstrap(args: Any) -> int:
    gateway_url = args.gateway_url or os.environ.get("MACO_GATEWAY_URL")
    if not gateway_url:
        raise ValueError("sandbox bootstrap requires --gateway-url or MACO_GATEWAY_URL")
    stats = generate_sandbox_sdk_from_gateway(
        gateway_url,
        token=args.gateway_token or os.environ.get("MACO_GATEWAY_TOKEN"),
        workspace=args.workspace,
        clean=not args.no_clean,
    )
    print(f"Generated sandbox SDK with {stats.tool_count} tools from {stats.server_count} servers")
    print(f"Workspace: {stats.workspace}")
    print(f"Explore: rg --files {stats.workspace}/tools")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
