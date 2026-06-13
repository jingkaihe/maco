## Project overview

`mcp-as-code` (`maco`) is a Python implementation of the MCP code-execution pattern from Kodelet based on Anthropic's MCP code-execution article. It generates Python wrappers for MCP tools, runs a local gateway that owns MCP client sessions, and runs user Python scripts through `uv` with the generated workspace on `PYTHONPATH`.

## Common commands

```bash
uv run pytest -q
uv run ruff check src tests
uv run ty check src tests
uv run maco --help
```

`ruff` plus `ty` covers source syntax and static typing for normal development. Use `python -m compileall` only as a targeted smoke test for generated code or unusual dynamic-code changes.

Script wrappers mirror the CLI subcommands for skill/drop-in usage:

```bash
./scripts/maco-gen --help
./scripts/maco-serve --help
./scripts/maco-run --help
./scripts/maco-serve-mcp --help
```

## Structure

- `src/maco/config.py` — load Claude-style `mcpServers` MCP config.
- `src/maco/mcp_manager.py` — async MCP client lifecycle and tool calls.
- `src/maco/codegen.py` — generates `.maco/maco_generated` Python wrappers.
- `src/maco/gateway.py` — localhost JSON/HTTP gateway used by generated wrappers.
- `src/maco/runner.py` — `uv run` execution helper that injects workspace/gateway env.
- `src/maco/sandbox/` — provider-based sandbox package with local, Docker, and Matchlock providers.
- `src/maco/serve_mcp.py` — experimental HTTP MCP server exposing sandboxed `bash` and `code_executor`.
- `src/maco/cli.py` — `maco gen`, `maco serve`, `maco run`.
- `tests/unit/` — fast unit tests.
- `tests/integration/` — end-to-end tests that may start real MCP/gateway/sandbox processes.
- `scripts/` — thin bash wrappers around the Python CLI.
- `SKILL.md` — agent-facing workflow and usage guide.
