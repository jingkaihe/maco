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


def test_rejects_non_claude_style_config(tmp_path):
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({"servers": {"echo": {"command": "uv"}}}))

    with pytest.raises(ConfigError, match="mcpServers"):
        load_config(config_path)
