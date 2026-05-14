# mcp-as-code

`mcp-as-code` (`maco`) exposes MCP tools as generated Python modules so agents can write and run ordinary Python code instead of loading every MCP tool definition into model context.

## Quick start

```bash
uv run maco gen --config mcp.json
uv run maco serve --config mcp.json
uv run maco run path/to/script.py
```

Generated code is written to a workspace (default: `.maco/`) with:

- `servers/<server>/<tool>.py` wrappers for each MCP tool
- `servers/<server>/__init__.py` exports
- `client.py` for low-level gateway calls
- `pyproject.toml` so scripts can be run with `uv`

See [`SKILL.md`](SKILL.md) for an agent-facing workflow.

## MCP config

`maco` expects Claude-style `mcp.json` with a top-level `mcpServers` object:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  }
}
```

For environment variables, put them under `env`. `maco` expands `$VAR` and
`${VAR}` using the environment of the process running `maco`, then passes the
resolved values to the MCP server subprocess.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
