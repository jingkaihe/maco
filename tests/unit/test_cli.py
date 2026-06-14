from __future__ import annotations

from maco.cli import build_parser, main


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


def test_version_command_prints_version_metadata(capsys):
    assert main(["version"]) == 0

    out = capsys.readouterr().out
    assert "version: " in out
    assert "commit: " in out
    assert "release date: " in out
