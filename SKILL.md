---
name: mcp-as-code
description: Generate and use Python code interfaces for MCP servers with maco. Use this skill whenever a user wants to access MCP tools through generated code, set up maco-gen/maco-serve/maco-run, run multi-step MCP workflows in Python, filter or join large MCP responses locally, or reduce direct MCP tool-call context by using code execution.
---

# MCP as Code (`maco`)

`maco` exposes MCP servers as generated Python modules. This lets agents write ordinary Python scripts for loops, filtering, joins, retries, and reusable helper logic while a local gateway owns the live MCP client sessions.

## What maco does

`maco` turns configured MCP tools into Python modules:

```text
.maco/
  gateway.json                  # written by maco serve
  maco_generated/
    client.py                   # low-level gateway client
    servers/
      <server>/
        <tool>.py               # one Python function per MCP tool
        __init__.py             # exports generated functions
```

Generated tool functions call a local gateway process. The gateway owns the MCP client sessions and forwards calls to the real MCP servers.

## Commands

Use the script wrappers from this repository/skill directory:

```bash
./scripts/maco-gen --config mcp.json --workspace .maco --clean
./scripts/maco-serve --config mcp.json --workspace .maco
./scripts/maco-run --workspace .maco path/to/code.py
```

If installed as a Python package, use the same subcommands through `uv run maco ...`.

Defaults:

- Config: `mcp.json`
- Generated workspace: `.maco`
- Gateway bind address: `127.0.0.1:0` (ephemeral localhost port)
- Gateway auth: random bearer token written to `.maco/gateway.json`

## Config format

`maco` expects Claude-style JSON with a top-level `mcpServers` object:

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

Environment variables can be populated in either of two ways:

1. Prefer listing variables under `env`. `maco` expands `$VAR` and `${VAR}` using the environment of the `maco gen` / `maco serve` process, then passes those resolved values to the MCP server subprocess.

```json
{
  "mcpServers": {
    "github": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

2. Or rely on subprocess inheritance for stdio servers: the MCP process also receives the default environment from the Python MCP SDK, so already-exported variables may be visible even when omitted from `env`. Listing them in `env` is more explicit and portable.

Streamable HTTP and SSE server entries can use Claude-style URL fields:

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

Supported transports: `stdio`, `http`/`streamable_http`, and `sse`.

## Recommended agent workflow

1. Generate interfaces:

   ```bash
   ./scripts/maco-gen --clean
   ```

   The command prints the generated workspace and a suggested `rg --files ...` command. Treat that as the starting point for discovery.

2. Discover available wrappers progressively. List generated modules first, then inspect only the server `__init__.py` and specific tool wrappers needed for the task. Avoid reading `.maco/manifest.json` by default because it can pull every tool/schema into context at once.

   ```bash
   rg --files .maco/maco_generated/servers
   sed -n '1,160p' .maco/maco_generated/servers/<server>/__init__.py
   sed -n '1,220p' .maco/maco_generated/servers/<server>/<tool>.py
   ```

   Use `rg --files ... | rg '<keyword>'` when you have a likely tool name, for example `rg --files .maco/maco_generated/servers | rg 'screenshot|navigate'`. `manifest.json` is only for broad audits or automation that needs the full generated index.

3. Start the gateway in tmux so it stays alive:

   ```bash
   tmux -L llm-agent new-session -d -s maco-gateway './scripts/maco-serve 2>&1'
   tmux -L llm-agent capture-pane -t maco-gateway -p -S -50
   ```

   To stop it later:

   ```bash
   tmux -L llm-agent kill-session -t maco-gateway
   ```

4. Write ordinary Python code that imports generated tools:

   ```python
   from maco_generated.servers.filesystem import listDirectory, readFile

   listing = listDirectory(path="/tmp")
   print(listing)
   content = readFile(path="/tmp/example.txt")
   print(content)
   ```

   Tool functions accept either a Pydantic input model, a single dict, or keyword arguments:

   ```python
   result = someTool({"query": "hello"})
   result = someTool(query="hello")
   ```

5. Run the code:

   ```bash
   ./scripts/maco-run ./analysis.py
   ```

   `maco-run` sets `PYTHONPATH`, `MACO_WORKSPACE`, `MACO_GATEWAY_FILE`, `MACO_GATEWAY_URL`, and `MACO_GATEWAY_TOKEN` from `.maco/gateway.json`.

## Return values

Generated functions return Pydantic output models when an output schema is available. Access fields as attributes:

```python
out = someTool(query="hello")
print(out.result)
```

For JSON keys that are not valid Python identifiers, use the generated field name shown in the wrapper; Pydantic aliases preserve the original MCP key when calling the server.

At the low-level client boundary, generated functions normalize MCP responses in this order:

1. MCP `structuredContent` when present;
2. otherwise joined text content parsed as JSON if possible;
3. otherwise plain text.

## When to use this instead of direct MCP tools

Use generated code when you need to:

- call several MCP tools in a loop;
- page/filter/sort large responses locally;
- merge data across MCP servers;
- avoid loading every tool schema into model context;
- persist local intermediate files or reusable helpers.

For a single simple MCP call, direct tool use may still be faster.

## Safety notes

- The gateway binds to localhost by default and uses a random bearer token by default.
- Do not commit `.maco/gateway.json`; it contains an access token for the live gateway.
- Generated wrappers are code; inspect them before using unfamiliar MCP servers.
- The MCP servers still enforce their own permissions and side effects. Treat generated function calls exactly like direct MCP tool calls.
