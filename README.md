# mcp-as-code

`mcp-as-code` (`maco`) exposes MCP tools as generated Python modules so agents can write and run ordinary Python code instead of loading every MCP tool definition into model context.

It is inspired by Cloudflare's [code-execution-with-MCP pattern](https://blog.cloudflare.com/code-mode/): keep live MCP sessions in a small local gateway, generate typed Python wrappers for the available tools, and let the agent solve multi-step work in code.

## Why use it?

Direct MCP tool calls are great for one-off actions, but they get awkward when the task needs loops, joins, filtering, retries, paging, local files, or lots of intermediate state. `maco` gives the agent a normal Python interface to MCP tools, so the model can inspect a small generated module, write a script, and let Python do the repetitive work.

The generated wrappers use Pydantic models derived from MCP JSON schemas, including nested objects and aliases for JSON keys that are not valid Python identifiers. This keeps generated code compact while still giving useful type hints and runtime validation.

## Benefits

- Batch operations: call the same MCP tool across many inputs, files, tickets, URLs, records, or search results.
- Progressive discovery: list generated modules with `rg --files`, inspect only the wrapper you need, and avoid loading every MCP schema into the model context.
- Data shaping: page, filter, sort, deduplicate, group, or join large MCP responses locally before presenting a small result.
- Cross-server workflows: combine outputs from multiple MCP servers in one Python script, such as search + fetch + summarize, issue tracker + git repo, or calendar + email.
- Reusable automation: keep helper functions, local caches, checkpoint files, and repeatable scripts around a task instead of doing everything as ad hoc tool calls.
- Safer long-running work: run the MCP gateway in a persistent terminal/tmux session while iterating on code separately with `maco run`.

For a single simple MCP call, direct MCP tool use is usually faster. `maco` is most useful once the task becomes a small program.

## Example use cases

- Research pipeline over search results: call a search MCP server, fetch the top matching pages, deduplicate URLs, extract relevant snippets, and write a cited summary from a small filtered set of sources.
- Issue and pull request triage: query GitHub/GitLab issues or PRs, group them by label/owner/status, cross-reference local repository state, and produce a prioritized report.
- Repository investigation: combine filesystem, git, and code-search MCP servers to inspect generated file lists, read only relevant source files, and build a focused map of how a feature works.
- Browser-based data collection: drive a browser MCP server through a list of pages, capture titles/screenshots/text, retry flaky pages, and store structured results locally.
- Personal information workflows: combine calendar, email, contacts, or task MCP servers to find scheduling conflicts, collect context for a meeting, or draft a follow-up plan.
- Local data enrichment: read rows from a CSV or database-backed MCP server, enrich each row via another MCP tool, validate the results with Python, and write a cleaned output file.
- Operational audits: enumerate cloud, CI, ticketing, or documentation resources through MCP tools, apply policy checks in Python, and emit only the exceptions that need human attention.

## How it works

```text
Claude-style mcp.json
        │
        ▼
  maco gen ───────▶ .maco/maco_generated/servers/... typed Python wrappers
        │
        ▼
  maco serve ─────▶ local gateway on 127.0.0.1:<ephemeral-port>
        │
        ▼
  maco run script.py ── imports generated wrappers ── calls gateway ── calls MCP servers
```

`maco serve` owns the MCP client sessions and writes connection details to `.maco/gateway.json`. `maco run` finds the generated workspace, sets `PYTHONPATH` and gateway environment variables, and runs your script with `uv run`.

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

If you are using the source checkout directly, the `scripts/` wrappers are also available:

```bash
./scripts/maco-gen --config mcp.json --workspace .maco --clean
./scripts/maco-serve --config mcp.json --workspace .maco
./scripts/maco-run --workspace .maco path/to/script.py
```

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

After `maco gen`, discover generated wrappers progressively:

```bash
rg --files .maco/maco_generated/servers
sed -n '1,160p' .maco/maco_generated/servers/<server>/__init__.py
sed -n '1,220p' .maco/maco_generated/servers/<server>/<tool>.py
```

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

The default gateway bind address is `127.0.0.1:0`, so the operating system chooses an ephemeral localhost port. By default, `maco serve` writes a random bearer token and gateway URL to `.maco/gateway.json`.

For agent workflows, run the gateway in a persistent session while iterating on scripts:

```bash
tmux -L llm-agent new-session -d -s maco-gateway './scripts/maco-serve --config mcp.json --workspace .maco 2>&1'
tmux -L llm-agent capture-pane -t maco-gateway -p -S -50
./scripts/maco-run --workspace .maco ./analysis.py
```

Stop it when done:

```bash
tmux -L llm-agent kill-session -t maco-gateway
```

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
