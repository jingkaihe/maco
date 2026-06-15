# maco

`maco` (mcp-as-code) lets an MCP client interact with many upstream MCP tools through a small code-execution interface.

It follows Anthropic's [code-execution-with-MCP pattern](https://www.anthropic.com/engineering/code-execution-with-mcp): keep the large MCP surface area behind a gateway, then let agents write short Python programs for loops, filtering, joins, retries, and structured output. Instead of loading hundreds of tool schemas into the context window, the client gets a compact interface for shell discovery and Python execution.

## At a glance

- Point `maco` at a Claude-style `mcp.json` containing one or many MCP servers.
- Run `maco serve-mcp` to expose one Streamable HTTP MCP endpoint.
- Connect your MCP client to that endpoint; it sees only `bash` and `code_execute`.
- Agents thrive on discovery with `rg` and `fd`, so maco gives them `bash` access to navigate the tool interface as a real filesystem.
- Use `code_execute` to call upstream MCP tools from Python with `from tools.<server> import <tool>`.

## Why it helps

- **Small context footprint:** the client starts with two tools, not every upstream schema.
- **Programmatic leverage:** use Python for paging, filtering, joining, caching, retries, and local intermediate files.
- **Progressive discovery:** inspect only the generated wrappers relevant to the task.
- **Flexible isolation:** run code locally for fast iteration or inside Docker/Matchlock for stronger isolation.
- **Works with existing MCP servers:** stdio, Streamable HTTP, and SSE server configs are supported.

## How it works

```text
MCP client
    │ sees only bash + code_execute
    ▼
maco serve-mcp  ── sandbox ──▶ Python code imports generated tools
    │
    ▼
managed maco gateway
    │
    ▼
upstream MCP servers from mcp.json
```

`maco serve-mcp` starts a managed gateway for the upstream MCP servers, prepares a generated Python SDK for the sandbox, and serves a compact MCP endpoint for downstream clients.

## Installation

Install the Python package `mcp-as-code`; it provides the `maco` executable:

```bash
uv tool install mcp-as-code
```

Then verify the CLI:

```bash
maco version
```

## Quick start

Create a Claude-style `mcp.json`:

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

Start the `maco` MCP server:

```bash
maco serve-mcp --config mcp.json --provider docker
```

Use `--provider local` for a faster, non-isolated local feedback loop.

By default this serves Streamable HTTP MCP at `http://127.0.0.1:8789/mcp`.

Configure an MCP client to connect to that endpoint:

<details>
<summary>Codex</summary>

```bash
codex mcp add maco --url http://127.0.0.1:8789/mcp
```

</details>

<details>
<summary>Claude Code</summary>

```bash
claude mcp add --transport http maco http://127.0.0.1:8789/mcp
```

</details>

From the client, the agent uses the MCP `bash` tool for code navigation inside the sandbox:

```bash
rg --files /workspace/macosdk/tools
sed -n '1,160p' /workspace/macosdk/tools/filesystem/__init__.py
```

Then use `code_execute` to call tools in a context-efficient manner, using loops and conditions instead of traditional linear tool-call chaining:

```python
from tools.filesystem import listDirectory

for path in ["/tmp", "/var/tmp"]:
    listing = listDirectory(path=path)
    entries = listing if isinstance(listing, list) else getattr(listing, "entries", [])

    if not entries:
        print(f"{path}: empty")
    else:
        print(f"{path}: {len(entries)} entries")
```

See [`examples/serve-mcp`](examples/serve-mcp) for a complete example that wraps multiple upstream MCP servers behind one `maco` endpoint.

If you are using the source checkout directly, the script wrapper is equivalent:

```bash
./scripts/maco-serve-mcp --config mcp.json --provider docker
```

## MCP config

`maco` expects Claude-style JSON with a top-level `mcpServers` object. Supported upstream transports are `stdio`, `http`/`streamable_http`, and `sse`.

Minimal stdio example:

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

Minimal Streamable HTTP example:

```json
{
  "mcpServers": {
    "remote": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {"Authorization": "Bearer ${TOKEN}"}
    }
  }
}
```

For remote HTTP/SSE servers without a static `Authorization` header, maco can perform OAuth from the upstream server's HTTP `401 Bearer` challenge. See [`docs/mcp-config.md`](docs/mcp-config.md) for the full config reference, including environment expansion, headers, OAuth hints, token caching, and tool filtering.

## Sandbox providers

Choose the execution provider with `--provider`:

- `local` — fastest feedback loop; runs commands as local subprocesses.
- `docker` — runs commands in a long-lived Docker container.
- `matchlock` — runs commands in a long-lived Matchlock micro-VM.

The default Docker/Matchlock image is `ghcr.io/jingkaihe/maco:<VERSION>-alpine`, where `<VERSION>` comes from [`VERSION.txt`](VERSION.txt). It includes `maco`, Python 3.12, `uv`, `pydantic`, `rg`, and `fd`.

## Development

```bash
make check
make build
make image
```

## Safety notes

- `maco serve-mcp` exposes code execution to whatever can reach its HTTP MCP endpoint; bind and firewall it accordingly.
- The managed gateway uses a bearer token by default. Do not commit `.maco/gateway.json`.
- Sandbox providers change the isolation boundary, not the authority of the upstream MCP servers. Treat generated tool calls like direct MCP tool calls.
- Inspect unfamiliar generated wrappers before running code that calls them.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
