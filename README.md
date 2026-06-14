# mcp-as-code

`mcp-as-code` (`maco`) turns MCP tools into generated Python modules.

It follows Anthropic's [code-execution-with-MCP pattern](https://www.anthropic.com/engineering/code-execution-with-mcp): keep live MCP sessions in a local gateway, then let agents use normal Python for multi-step work.

## At a glance

- `maco gen` generates typed Python wrappers from a Claude-style `mcp.json` when you want wrapper-only discovery.
- `maco serve` generates wrappers, then runs a localhost gateway that owns the MCP sessions.
- `maco run` executes your Python script with the generated wrappers on `PYTHONPATH`.
- `maco serve-mcp` generates wrappers, starts a managed gateway, and exposes sandboxed `bash` and `code_execute` tools over HTTP MCP.

Use it when the task is easier as a small program than as a sequence of direct tool calls.

## Good fits

- Research workflows: search, fetch, deduplicate, filter, then summarize from a smaller source set.
- Project triage: query issues or PRs, group them, and cross-check local repository state.
- Data collection: run browser, filesystem, API, or database tools over many inputs and write structured output.

## Why it helps

- Less context: inspect only the generated wrapper you need instead of loading every MCP schema.
- More leverage: use Python for loops, paging, filtering, joins, retries, caches, and local files.
- Typed boundary: generated Pydantic models provide type hints and runtime validation from MCP schemas.

## How it works

```text
Claude-style mcp.json
        │
        ▼
  maco serve ─────▶ .maco/maco_generated/servers/... typed Python wrappers
        │
        ▼
  local gateway on 127.0.0.1:<ephemeral-port>
        │
        ▼
  maco run script.py ── imports generated wrappers ── calls gateway ── calls MCP servers
```

`maco serve` refreshes the generated workspace, owns the MCP client sessions, and writes connection details to `.maco/gateway.json`. `maco gen` is still available when you only want to generate or inspect wrappers without starting the gateway. `maco run` finds the generated workspace, sets `PYTHONPATH` and gateway environment variables, and runs your script with `uv run`.

## Quick start

```bash
uv run maco serve --config mcp.json --workspace .maco --clean
# in another terminal, after the gateway has started:
uv run maco run --workspace .maco path/to/script.py
```

Generated code is written to a workspace (default: `.maco/`) with:

- `servers/<server>/<tool>.py` wrappers for each MCP tool
- `servers/<server>/__init__.py` exports
- `client.py` for low-level gateway calls
- `pyproject.toml` so scripts can be run with `uv`

See [`SKILL.md`](SKILL.md) for an agent-facing workflow.

If you are using the source checkout directly, the `scripts/` wrappers mirror the CLI subcommands: `./scripts/maco-gen`, `./scripts/maco-serve`, `./scripts/maco-run`, and `./scripts/maco-serve-mcp`.

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

For environment variables, put them under `env`. `maco` expands `$VAR` and `${VAR}` using the environment of the process running `maco`, then passes the resolved values to the MCP server subprocess.

HTTP-style servers can use URL and header fields:

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

Supported transports are `stdio`, `http`/`streamable_http`, and `sse`. Only Claude-style config is supported.

## Generated code

After `maco gen` or once `maco serve` has started, discover generated wrappers progressively:

```bash
rg --files .maco/maco_generated/servers
sed -n '1,160p' .maco/maco_generated/servers/<server>/__init__.py
sed -n '1,220p' .maco/maco_generated/servers/<server>/<tool>.py
```

Use `rg --files ... | rg '<keyword>'` when you have a likely tool name, for example `rg --files .maco/maco_generated/servers | rg 'screenshot|navigate'`.

Avoid reading `.maco/manifest.json` by default when working as an agent: it is useful for automation and broad audits, but inspecting every generated tool at once can waste context.

Generated functions accept a Pydantic input model, a dict, or keyword arguments:

```python
from maco_generated.servers.filesystem import listDirectory, readFile

listing = listDirectory(path="/tmp")
content = readFile({"path": "/tmp/example.txt"})

print(listing)
print(content)
```

When a tool has an output schema, the wrapper returns a Pydantic output model:

```python
result = search(query="mcp as code")
print(result.items)
```

For JSON keys that are not valid Python identifiers, use the generated Python field name shown in the wrapper. Pydantic aliases preserve the original MCP key when calling the server.

## Running the gateway

The default gateway bind address is `127.0.0.1:0`, so the operating system chooses an ephemeral localhost port. By default, `maco serve` generates wrappers, writes a random bearer token and gateway URL to `.maco/gateway.json`, and keeps the gateway running until interrupted. Pass `--clean` to recreate the generated workspace before starting.

For agent workflows, run the gateway in a persistent session while iterating on scripts:

```bash
tmux -L llm-agent new-session -d -s maco-gateway 'uv run maco serve --config mcp.json --workspace .maco --clean 2>&1'
tmux -L llm-agent capture-pane -t maco-gateway -p -S -50
uv run maco run --workspace .maco ./analysis.py
```

Stop it when done:

```bash
tmux -L llm-agent kill-session -t maco-gateway
```

## Experimental Sandboxed MCP server

`maco serve-mcp` exposes a compact HTTP MCP server with two tools:

- `bash(command, timeout?)` — run a non-interactive shell command in the configured sandbox.
- `code_execute(code, filename?, args?, timeout?)` — write and run a Python script that imports generated wrappers.

The server advertises generated server modules with their sandbox paths. Docker and Matchlock use `/workspace/.maco/maco_generated/servers/<server>` for wrapper code and `/workspace` for writable files. The default sandbox image is `ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine`, which includes `uv`, Python 3.12, `pydantic`, `rg`, and `fd`; use those through `bash` to inspect wrapper exports before writing `code_execute` scripts.

The sandboxed MCP server is the one-command MCP-mode entrypoint: it generates wrappers, starts a managed maco gateway in the background, then serves HTTP MCP:

```bash
uv run maco serve-mcp --config mcp.json --workspace .maco --clean --provider local --port 8789
```

Providers are selected with `--provider local|docker|matchlock`.

- `local` runs commands as local subprocesses and uses the gateway URL from `.maco/gateway.json` directly.
- `docker` rewrites loopback gateway URLs to `http://host.docker.internal:<port>/`, mounts `.maco` read-only at `/workspace/.maco`, and mounts scratch at `/workspace`.
- `matchlock` uses the Matchlock Python SDK, rewrites loopback gateway URLs to `http://maco-gateway.internal:<port>/`, mounts `.maco` read-only at `/workspace/.maco`, mounts scratch at `/workspace`, allowlists the gateway host, and passes the gateway token through Matchlock secret placeholders instead of putting the real token in the VM environment.

When `serve-mcp` manages the gateway itself, it binds the gateway to `127.0.0.1` for the local provider and `0.0.0.0` for Docker/Matchlock so the sandbox can reach it through the provider-specific host alias. If you already run a separate `maco serve`, pass `--gateway-file path/to/gateway.json` to use that gateway instead.

The default Docker/Matchlock sandbox image is built from `images/sandbox/Dockerfile`:

```bash
docker build -t ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine images/sandbox
docker save ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine \
  | matchlock image import ghcr.io/jingkaihe/mcp-as-code:0.1.0-alpine
```

Treat the gateway bearer token as a scoped capability; policy enforcement belongs at the gateway, not in generated wrappers.

The sandbox providers live under `src/maco/sandbox/`, with concrete providers in `src/maco/sandbox/providers/`. Unit tests live under `tests/unit/`. Integration tests live under `tests/integration/`; they exercise the real echo MCP fixture through generated wrappers, the local gateway, and the sandboxed HTTP MCP server. Docker and Matchlock cases run when the host has the required runtime/gateway routing; otherwise those cases are skipped.

See [`examples/serve-mcp`](examples/serve-mcp) for a complete MCP-mode example that wraps Playwright MCP and GitHub MCP behind one `maco serve-mcp` endpoint.

## Development

```bash
uv run pytest -q
uv run ruff check src tests
uv run ty check src tests
uv run maco --help
```

`ruff` and `ty` cover source syntax and static typing for normal development. Use compile smoke tests only for generated-code edge cases where executing or importing the generated module gives extra confidence.

## Safety notes

- The gateway binds to localhost by default and uses a bearer token by default.
- Do not commit `.maco/gateway.json`; it contains connection details for the live gateway.
- Generated wrappers are code; inspect wrappers before using unfamiliar MCP servers.
- MCP servers still enforce their own permissions and side effects, so treat generated function calls exactly like direct MCP tool calls.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
