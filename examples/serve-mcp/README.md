# `maco serve-mcp` example

This example shows how to expose several upstream MCP servers through one compact `maco serve-mcp` endpoint. The upstream servers here are:

- [Playwright MCP](https://playwright.dev/mcp/introduction), launched with `npx -y @playwright/mcp@latest`
- [GitHub MCP server](https://github.com/github/github-mcp-server), launched with the official `ghcr.io/github/github-mcp-server` Docker image

`maco serve-mcp` generates Python wrappers for those upstream tools, starts a managed local gateway that owns the upstream MCP sessions, then starts a Streamable HTTP MCP server with two tools:

- `bash` — inspect generated wrappers or run shell probes in the sandbox
- `code_execute` — run Python code that imports generated wrappers

```text
MCP client ──HTTP──▶ maco serve-mcp ──sandbox──▶ generated Python wrappers
                                      │
                                      ▼
                                maco gateway
                                      │
                                      ▼
                         Playwright MCP + GitHub MCP
```

## Files

- `mcp.json` — upstream MCP servers that `maco serve-mcp` connects to.
- `mcp-client.json` — example downstream MCP client config that connects to `maco serve-mcp`.

## Prerequisites

- `uv`
- `node`/`npx`, for Playwright MCP
- Docker, for GitHub MCP and the Docker sandbox provider
- Optional: Matchlock, for the Matchlock sandbox provider
- A GitHub personal access token in `GITHUB_PERSONAL_ACCESS_TOKEN`

If you are already authenticated with the GitHub CLI, export a token directly:

```bash
export GITHUB_PERSONAL_ACCESS_TOKEN=$(gh auth token)
```

## 1. Start `maco serve-mcp`

The short path is to run from this example directory so the defaults line up with the local files:

```bash
cd examples/serve-mcp
uv run maco serve-mcp --provider local
```

This uses `mcp.json`, writes `.maco/gateway.json`, uses `maco-serve-mcp/` as scratch, starts the gateway, and serves HTTP MCP at `http://127.0.0.1:8789/mcp`. Add `--clean` only when you want to recreate the local generated SDK from scratch.

Inside the MCP client, use the `bash` tool to inspect the generated sandbox SDK progressively:

```bash
rg --files /workspace/macosdk/tools
sed -n '1,160p' /workspace/macosdk/tools/playwright/__init__.py
sed -n '1,160p' /workspace/macosdk/tools/github/__init__.py
```

To use a different sandbox provider, swap the provider name:

```bash
uv run maco serve-mcp --provider docker
uv run maco serve-mcp --provider matchlock
```

If you prefer to manage the maco gateway separately, run `uv run maco serve` yourself and pass `--gateway-file .maco/gateway.json` to `maco serve-mcp`.

## 2. Connect an MCP client

Configure your MCP client with `examples/serve-mcp/mcp-client.json`:

```json
{
  "mcpServers": {
    "maco": {
      "type": "http",
      "url": "http://127.0.0.1:8789/mcp"
    }
  }
}
```

The client will see only two tools, `bash` and `code_execute`, but those tools can use all generated wrappers for Playwright and GitHub. Code executed in the sandbox imports generated tools with `from tools.<server> import <tool>`.

## Notes

- Prefer calling `code_execute` with only the `code` argument. If `filename` is omitted, maco writes the script to a deterministic `<hash>.py` file in scratch.
- The Docker and Matchlock providers use the default sandbox image `ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine`, which includes Python 3.12, `uv`, `pydantic`, `rg`, and `fd`.
- `.maco/` and `maco-serve-mcp/` are local runtime files and should not be committed.
