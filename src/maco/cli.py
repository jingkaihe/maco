"""Command line interface for maco."""

from __future__ import annotations

import argparse
import os
import sys

from .codegen import generate, generate_sandbox_sdk_from_gateway
from .config import ConfigError, load_config
from .gateway import ServeOptions, serve
from .runner import RunnerError, exit_with_error, run_code
from .sandbox import DEFAULT_SANDBOX_IMAGE, SANDBOX_SDK_ROOT
from .serve_mcp import ServeMcpOptions, serve_mcp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maco",
        description="Generate and execute Python code interfaces for MCP tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("gen", help="generate Python wrappers for configured MCP tools")
    gen.add_argument("--config", default="mcp.json", help="MCP config path (default: mcp.json)")
    gen.add_argument("--workspace", default=".maco", help="generated workspace directory (default: .maco)")
    gen.add_argument("--server", help="only generate wrappers for one configured server")
    gen.add_argument("--clean", action="store_true", help="remove the workspace before generating")
    gen.set_defaults(func=_cmd_gen)

    serve_parser = subparsers.add_parser("serve", help="run the local maco gateway server")
    serve_parser.add_argument("--config", default="mcp.json", help="MCP config path (default: mcp.json)")
    serve_parser.add_argument("--workspace", default=".maco", help="generated workspace directory (default: .maco)")
    serve_parser.add_argument("--clean", action="store_true", help="remove the workspace before generating")
    serve_parser.add_argument("--host", default="127.0.0.1", help="host to bind (default: 127.0.0.1)")
    serve_parser.add_argument("--port", default=0, type=int, help="port to bind (default: 0, an ephemeral port)")
    serve_parser.add_argument("--token", help="explicit bearer token for generated code")
    serve_parser.add_argument("--no-token", action="store_true", help="disable gateway bearer-token protection")
    serve_parser.set_defaults(func=_cmd_serve)

    run = subparsers.add_parser("run", help="run a Python file with generated wrappers available")
    run.add_argument("--workspace", help="generated workspace directory (default: auto-detect)")
    run.add_argument("--cwd", help="working directory for the script")
    run.add_argument("--python", help="Python version/interpreter to pass to uv run")
    run.add_argument("code_path", help="Python file to execute")
    run.add_argument("script_args", nargs=argparse.REMAINDER, help="arguments passed to the Python file")
    run.set_defaults(func=_cmd_run)

    serve_mcp = subparsers.add_parser(
        "serve-mcp",
        help="run an HTTP MCP server exposing sandboxed bash and code execution",
    )
    _add_serve_mcp_options(serve_mcp)
    serve_mcp.set_defaults(func=_cmd_serve_mcp)

    sandbox_bootstrap = subparsers.add_parser(
        "sandbox-bootstrap",
        help=argparse.SUPPRESS,
    )
    sandbox_bootstrap.add_argument("--gateway-url", help=argparse.SUPPRESS)
    sandbox_bootstrap.add_argument("--gateway-token", help=argparse.SUPPRESS)
    sandbox_bootstrap.add_argument("--workspace", default=SANDBOX_SDK_ROOT, help=argparse.SUPPRESS)
    sandbox_bootstrap.add_argument("--no-clean", action="store_true", help=argparse.SUPPRESS)
    sandbox_bootstrap.set_defaults(func=_cmd_sandbox_bootstrap)

    return parser


def _add_serve_mcp_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--config", default="mcp.json", help="MCP config path (default: mcp.json)")
    command.add_argument(
        "--provider",
        choices=["local", "docker", "matchlock"],
        default="local",
        help="sandbox provider to use (default: local)",
    )
    command.add_argument("--workspace", default=".maco", help="generated workspace directory")
    command.add_argument("--clean", action="store_true", help="remove the workspace before generating")
    command.add_argument("--scratch", help="writable scratch directory for sandbox code")
    command.add_argument(
        "--gateway-file",
        help="connect to an existing gateway.json instead of starting a managed gateway",
    )
    command.add_argument(
        "--gateway-host",
        help="host for the managed gateway started by serve-mcp",
    )
    command.add_argument(
        "--gateway-port",
        default=0,
        type=int,
        help="port for the managed gateway started by serve-mcp (default: 0)",
    )
    command.add_argument("--gateway-token", help="explicit bearer token for the managed gateway")
    command.add_argument(
        "--no-gateway-token",
        action="store_true",
        help="disable bearer-token protection for the managed gateway",
    )
    command.add_argument("--host", default="127.0.0.1", help="HTTP MCP bind host")
    command.add_argument("--port", default=8789, type=int, help="HTTP MCP bind port")
    command.add_argument("--timeout", default=60, type=int, help="default command timeout in seconds")
    command.add_argument(
        "--image",
        help=f"container image for docker/matchlock providers (default: {DEFAULT_SANDBOX_IMAGE})",
    )
    command.add_argument("--python-command", help="guest command prefix used by code_execute")
    command.add_argument("--docker-binary", default="docker", help="docker binary path/name")
    command.add_argument("--docker-network", help="docker network passed to `docker run --network`")
    command.add_argument(
        "--docker-gateway-host",
        default="host.docker.internal",
        help="hostname inside docker that reaches the host gateway",
    )
    command.add_argument(
        "--docker-gateway-ip",
        help="explicit host gateway IP to map inside Docker; usually auto-detected",
    )
    command.add_argument("--matchlock-binary", default="matchlock", help="matchlock binary path/name")
    command.add_argument(
        "--matchlock-gateway-host",
        default="maco-gateway.internal",
        help="hostname inside matchlock that reaches the host gateway",
    )
    command.add_argument(
        "--matchlock-gateway-ip",
        help="IP for --add-host <gateway-host>:<ip> inside matchlock (default: 192.168.100.1 for managed gateways)",
    )
    command.add_argument(
        "--matchlock-allow-host",
        action="append",
        default=[],
        help="extra host to allow from the matchlock sandbox (repeatable)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, RunnerError, OSError, ValueError) as exc:
        exit_with_error(exc)
    return 0


def _cmd_gen(args: argparse.Namespace) -> int:
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


def _cmd_serve(args: argparse.Namespace) -> int:
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


def _cmd_run(args: argparse.Namespace) -> int:
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


def _cmd_serve_mcp(args: argparse.Namespace) -> int:
    serve_mcp(
        ServeMcpOptions(
            config=args.config,
            provider=args.provider,
            workspace=args.workspace,
            clean=args.clean,
            scratch=args.scratch,
            gateway_file=args.gateway_file,
            gateway_host=args.gateway_host,
            gateway_port=args.gateway_port,
            gateway_token=args.gateway_token,
            gateway_use_token=not args.no_gateway_token,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
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
        )
    )
    return 0


def _cmd_sandbox_bootstrap(args: argparse.Namespace) -> int:
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
