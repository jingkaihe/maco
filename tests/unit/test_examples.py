from __future__ import annotations

import json
from pathlib import Path


def test_serve_mcp_example_configs_are_valid_json():
    example = Path("examples/serve-mcp")

    mcp_config = json.loads((example / "mcp.json").read_text(encoding="utf-8"))
    client_config = json.loads((example / "mcp-client.json").read_text(encoding="utf-8"))

    assert set(mcp_config["mcpServers"]) == {"github", "playwright"}
    assert client_config["mcpServers"]["maco"] == {
        "type": "http",
        "url": "http://127.0.0.1:8789/mcp",
    }


def test_serve_mcp_example_readme_mentions_configs_and_servers():
    readme = Path("examples/serve-mcp/README.md").read_text(encoding="utf-8")

    assert "examples/serve-mcp/mcp.json" in readme
    assert "examples/serve-mcp/mcp-client.json" in readme
    assert "@playwright/mcp@latest" in readme
    assert "ghcr.io/github/github-mcp-server" in readme
    assert "gh auth token" in readme
    assert ".env" not in readme
