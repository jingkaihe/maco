"""Command line interface for maco."""

from __future__ import annotations

import argparse
import sys

from .codegen import generate
from .config import ConfigError, load_config
from .gateway import ServeOptions, serve
from .runner import RunnerError, exit_with_error, run_code
from .sandbox import DEFAULT_SANDBOX_IMAGE
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

    return parser


def _add_serve_mcp_options(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--provider",
        choices=["local", "docker", "matchlock"],
        default="local",
        help="sandbox provider to use (default: local)",
    )
    command.add_argument("--workspace", default=".maco", help="generated workspace directory")
    command.add_argument("--scratch", help="writable scratch directory for sandbox code")
    command.add_argument("--gateway-file", help="gateway.json written by `maco serve`")
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
    command.add_argument("--matchlock-binary", default="matchlock", help="matchlock binary path/name")
    command.add_argument(
        "--matchlock-gateway-host",
        default="maco-gateway.internal",
        help="hostname inside matchlock that reaches the host gateway",
    )
    command.add_argument(
        "--matchlock-gateway-ip",
        help="optional IP for --add-host <gateway-host>:<ip> inside matchlock",
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
            provider=args.provider,
            workspace=args.workspace,
            scratch=args.scratch,
            gateway_file=args.gateway_file,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
            image=args.image,
            python_command=args.python_command,
            docker_binary=args.docker_binary,
            docker_network=args.docker_network,
            docker_gateway_host=args.docker_gateway_host,
            matchlock_binary=args.matchlock_binary,
            matchlock_gateway_host=args.matchlock_gateway_host,
            matchlock_gateway_ip=args.matchlock_gateway_ip,
            matchlock_allow_host=tuple(args.matchlock_allow_host or []),
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
