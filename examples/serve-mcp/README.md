# `maco serve-mcp` example

This example shows how to expose several upstream MCP servers through one compact
`maco serve-mcp` endpoint. The upstream servers here are:

- [Playwright MCP](https://playwright.dev/mcp/introduction), launched with `npx -y @playwright/mcp@latest`
- [GitHub MCP server](https://github.com/github/github-mcp-server), launched with the official `ghcr.io/github/github-mcp-server` Docker image

`maco` first generates Python wrappers for those upstream tools, then runs a
local gateway that owns the upstream MCP sessions. Finally, `maco serve-mcp`
starts a Streamable HTTP MCP server with two tools:

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

- `mcp.json` — upstream MCP servers that `maco gen` and `maco serve` connect to.
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

## 1. Generate wrappers

From the repository root:

```bash
uv run maco gen \
  --config examples/serve-mcp/mcp.json \
  --workspace examples/serve-mcp/.maco \
  --clean
```

Inspect the generated wrappers progressively:

```bash
rg --files examples/serve-mcp/.maco/maco_generated/servers
sed -n '1,160p' examples/serve-mcp/.maco/maco_generated/servers/playwright/__init__.py
sed -n '1,160p' examples/serve-mcp/.maco/maco_generated/servers/github/__init__.py
```

## 2. Start the maco gateway

Keep this running in one terminal:

```bash
uv run maco serve \
  --config examples/serve-mcp/mcp.json \
  --workspace examples/serve-mcp/.maco
```

The gateway writes `examples/serve-mcp/.maco/gateway.json`. Generated wrappers
call this gateway; the gateway calls the real Playwright and GitHub MCP servers.

## 3. Start `maco serve-mcp`

In another terminal, start the downstream MCP server. For local execution:

```bash
uv run maco serve-mcp \
  --workspace examples/serve-mcp/.maco \
  --scratch examples/serve-mcp/scratch \
  --provider local \
  --port 8789
```

For Docker sandbox execution, first make the gateway reachable from containers:

```bash
uv run maco serve \
  --config examples/serve-mcp/mcp.json \
  --workspace examples/serve-mcp/.maco \
  --host 0.0.0.0
```

Then run:

```bash
uv run maco serve-mcp \
  --workspace examples/serve-mcp/.maco \
  --scratch examples/serve-mcp/scratch \
  --provider docker \
  --port 8789
```

For Matchlock, import the sandbox image once:

```bash
docker save ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine \
  | matchlock image import ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine
```

Then run `maco serve-mcp` with the Matchlock provider. The gateway must be bound
to a host address reachable by Matchlock:

```bash
uv run maco serve-mcp \
  --workspace examples/serve-mcp/.maco \
  --scratch examples/serve-mcp/scratch \
  --provider matchlock \
  --matchlock-gateway-ip 192.168.100.1 \
  --port 8789
```

## 4. Connect an MCP client

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

The client will see only two tools, `bash` and `code_execute`, but those tools can
use all generated wrappers for Playwright and GitHub.

## Example prompts for the MCP client

Ask the client to inspect available generated wrappers before writing code:

```text
Use the maco bash tool to list generated server modules and tool exports.
Then use code_execute to open example.com with Playwright and summarize the page title.
```

A more explicit code-oriented prompt:

```text
Use maco code_execute. Import generated tools from maco_generated.servers.playwright.
Navigate to https://example.com, capture the page title or visible text, and print JSON.
```

A GitHub-oriented prompt:

```text
Use maco bash to inspect the GitHub generated wrapper exports. Then use
code_execute to query open issues in jingkaihe/mcp-as-code and summarize the top
five by title and URL as JSON.
```

## Notes

- Prefer calling `code_execute` with only the `code` argument. If `filename` is
  omitted, maco writes the script to a deterministic `<hash>.py` file in scratch.
- The Docker and Matchlock providers use the default sandbox image
  `ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine`, which includes Python 3.12,
  `uv`, `pydantic`, `rg`, and `fd`.
- `examples/serve-mcp/.maco/` and `examples/serve-mcp/scratch/` are local
  runtime files and should not be committed.
