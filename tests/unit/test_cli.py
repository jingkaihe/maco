from __future__ import annotations

from maco.cli import build_parser


def test_serve_mcp_is_primary_sandboxed_mcp_command():
    args = build_parser().parse_args(
        [
            "serve-mcp",
            "--config",
            "mcp.json",
            "--provider",
            "docker",
            "--workspace",
            ".maco",
            "--gateway-host",
            "0.0.0.0",
        ]
    )

    assert args.command == "serve-mcp"
    assert args.config == "mcp.json"
    assert args.provider == "docker"
    assert args.workspace == ".maco"
    assert args.gateway_host == "0.0.0.0"
