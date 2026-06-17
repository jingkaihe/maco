from __future__ import annotations

import pytest

from maco import __version__
import maco.cli as cli
from maco.cli import build_parser, main


def test_up_is_primary_sandboxed_mcp_command():
    args = build_parser().parse_args(
        [
            "up",
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

    assert args.command == "up"
    assert args.config == "mcp.json"
    assert args.provider == "docker"
    assert args.workspace == ".maco"
    assert args.gateway_host == "0.0.0.0"


def test_up_foreground_uses_auto_marker_until_dispatch():
    args = build_parser().parse_args(["up"])

    assert args.command == "up"
    assert args.detach is False
    assert args.port is None


def test_up_detached_parses_mcp_server_options():
    args = build_parser().parse_args(
        [
            "up",
            "-d",
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

    assert args.command == "up"
    assert args.detach is True
    assert args.config == "mcp.json"
    assert args.provider == "docker"
    assert args.workspace == ".maco"
    assert args.gateway_host == "0.0.0.0"
    assert args.port is None


def test_status_down_and_ls_commands_parse():
    status = build_parser().parse_args(["status", "--workspace", "custom"])
    down = build_parser().parse_args(["down", "--workspace", "custom"])
    ls = build_parser().parse_args(["ls"])

    assert status.command == "status"
    assert status.workspace == "custom"
    assert down.command == "down"
    assert down.workspace == "custom"
    assert ls.command == "ls"


def test_help_does_not_show_internal_commands(capsys):
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--help"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "_mcp-server" not in out
    assert "_gateway" not in out
    assert "sandbox-bootstrap" not in out
    assert "serve               " not in out


def test_up_detached_dispatches_to_service_manager(monkeypatch):
    captured = {}

    def fake_start(args):
        captured["args"] = args

    monkeypatch.setattr(cli, "start_detached", fake_start)

    assert main(["up", "-d"]) == 0
    assert captured["args"].detach is True


def test_up_foreground_uses_default_port_before_serving(monkeypatch):
    captured = {}

    def fake_ensure(args):
        captured["checked"] = args.port

    def fake_serve(args):
        captured["served"] = args.port
        return 0

    monkeypatch.setattr(cli, "ensure_no_detached_service", fake_ensure)
    monkeypatch.setattr(cli, "find_available_port", lambda host, start: 8790)
    monkeypatch.setattr(cli, "_cmd_mcp_server", fake_serve)

    assert main(["up"]) == 0
    assert captured == {"checked": None, "served": 8790}


def test_internal_mcp_server_dispatch_stays_available(monkeypatch):
    captured = {}

    def fake_serve(args):
        captured["port"] = args.port
        return 0

    monkeypatch.setattr(cli, "_cmd_mcp_server", fake_serve)

    assert main(["_mcp-server", "--port", "9000"]) == 0
    assert captured == {"port": 9000}


def test_internal_gateway_dispatch_stays_available(monkeypatch):
    captured = {}

    def fake_serve(args):
        captured["port"] = args.port
        return 0

    monkeypatch.setattr(cli, "_cmd_serve", fake_serve)

    assert main(["_gateway", "--port", "9001"]) == 0
    assert captured == {"port": 9001}


def test_version_command_prints_version_metadata(capsys):
    assert main(["version"]) == 0

    out = capsys.readouterr().out
    assert out == f"version: {__version__}\n"
