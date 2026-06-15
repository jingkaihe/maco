from __future__ import annotations

import json

import pytest

from maco.config import ConfigError, load_config


def test_load_claude_style_config_expands_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN", "secret-token")
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "echo": {
                        "command": "uv",
                        "args": ["run", "server.py", "--token", "$TOKEN"],
                        "env": {"TOKEN": "$TOKEN", "OTHER": "${TOKEN}"},
                    }
                }
            }
        )
    )

    config = load_config(config_path)

    assert list(config.servers) == ["echo"]
    server = config.servers["echo"]
    assert server.server_type == "stdio"
    assert server.command == "uv"
    assert server.args == ["run", "server.py", "--token", "secret-token"]
    assert server.env == {"TOKEN": "secret-token", "OTHER": "secret-token"}


def test_load_http_config_expands_headers_and_tool_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN", "secret-token")
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "type": "http",
                        "url": "http://127.0.0.1:8000/mcp?token=$TOKEN",
                        "headers": {"Authorization": "Bearer ${TOKEN}"},
                        "tools": ["search", "fetch"],
                    }
                }
            }
        )
    )

    config = load_config(config_path)

    server = config.servers["remote"]
    assert server.server_type == "http"
    assert server.base_url == "http://127.0.0.1:8000/mcp?token=secret-token"
    assert server.headers == {"Authorization": "Bearer secret-token"}
    assert server.tool_white_list == ["search", "fetch"]


def test_load_http_config_parses_oauth_hints(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIENT_ID", "client-123")
    monkeypatch.setenv("CLIENT_SECRET", "secret-456")
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "type": "http",
                        "url": "https://mcp.example/mcp",
                        "oauth": {
                            "client_id": "${CLIENT_ID}",
                            "client_secret": "$CLIENT_SECRET",
                            "scopes": ["mcp.read", "mcp.write"],
                            "redirect_uri": "http://127.0.0.1:1456/callback",
                            "auth_server_metadata_url": "https://auth.example/.well-known/oauth-authorization-server",
                            "interactive": "ALWAYS",
                            "open_browser": "false",
                            "callback_timeout": "2m",
                        },
                    }
                }
            }
        )
    )

    config = load_config(config_path)

    oauth = config.servers["remote"].oauth
    assert oauth is not None
    assert oauth.client_id == "client-123"
    assert oauth.client_secret == "secret-456"
    assert oauth.scopes == ["mcp.read", "mcp.write"]
    assert oauth.redirect_uri == "http://127.0.0.1:1456/callback"
    assert (
        oauth.auth_server_metadata_url
        == "https://auth.example/.well-known/oauth-authorization-server"
    )
    assert oauth.interactive == "always"
    assert oauth.open_browser is False
    assert oauth.callback_timeout == 120


def test_rejects_invalid_oauth_scopes(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {
                        "type": "http",
                        "url": "https://mcp.example/mcp",
                        "oauth": {"scopes": "mcp.read"},
                    }
                }
            }
        )
    )

    with pytest.raises(ConfigError, match="oauth scopes"):
        load_config(config_path)


def test_rejects_non_claude_style_config(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({"servers": {"echo": {"command": "uv"}}}))

    with pytest.raises(ConfigError, match="mcpServers"):
        load_config(config_path)
