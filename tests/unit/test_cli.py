from __future__ import annotations

from maco.cli import build_parser


def test_serve_mcp_is_primary_sandboxed_mcp_command():
    args = build_parser().parse_args(["serve-mcp", "--provider", "docker", "--workspace", ".maco"])

    assert args.command == "serve-mcp"
    assert args.provider == "docker"
    assert args.workspace == ".maco"
