# `maco serve-mcp` example

This example shows how to expose several upstream MCP servers through one compact `maco serve-mcp` endpoint. The upstream servers here are:

- [Playwright MCP](https://playwright.dev/mcp/introduction), launched with `npx -y @playwright/mcp@latest`
- [GitHub MCP server](https://github.com/github/github-mcp-server), using GitHub's hosted Streamable HTTP endpoint at `https://api.githubcopilot.com/mcp/`

## Prerequisites

- `uv`
- `node`/`npx`, for Playwright MCP
- A GitHub personal access token in `GITHUB_TOKEN`, used by the hosted GitHub MCP server
- Docker, only if you use the Docker sandbox provider
- Optional: Matchlock, for the Matchlock sandbox provider

If you are already authenticated with the GitHub CLI, export a token directly:

```bash
export GITHUB_TOKEN=$(gh auth token)
```

## 1. Start `maco serve-mcp`

The short path is to run from this example directory so the defaults line up with the local files:

```bash
cd examples/serve-mcp
uv run maco serve-mcp --provider local
```

This uses `mcp.json`, writes `.maco/gateway.json`, uses `maco-serve-mcp/` as scratch, starts the gateway, and serves HTTP MCP at `http://127.0.0.1:8789/mcp`. Add `--clean` only when you want to recreate the local generated SDK from scratch.

## 2. Connect an agent to the MCP gateway

Configure your MCP client with `mcp-client.json` to connect to `maco serve-mcp`. For example, in your MCP client's settings, set the MCP server URL to `http://127.0.0.1:8789/mcp`.

If you are using codex you may connect to it via:

```bash
codex mcp add maco --url http://127.0.0.1:8789/mcp
```
