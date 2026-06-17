## Project overview

`maco` is a Python implementation of the MCP code-execution pattern from Kodelet based on Anthropic's MCP code-execution article. It exposes many MCP tools through a compact code-execution interface backed by generated Python wrappers and a managed gateway.

## Common commands

```bash
make check             # Run ruff, ty, and the full pytest suite
make test-unit         # Run fast unit tests
make test-integration  # Run end-to-end tests; may start real MCP/gateway/sandbox processes
make build             # Build Python sdist/wheel
make build-release     # Build wheel with embedded commit/date metadata, then reset local build info
make clean-sandboxes   # Remove managed Docker containers and maco Matchlock sandboxes after interrupted runs
make image             # Build sandbox image using VERSION.txt
make image-import      # Build and import sandbox image into Matchlock
uv run maco --help
```

Use `uv run python -m ast path/to/file.py >/dev/null` as a targeted syntax-only smoke test for generated code or unusual dynamic-code changes.

`VERSION.txt` is the single version source for the Python package and sandbox image tag. The Python distribution name is `mcp-as-code`; the import package and executable are `maco`. Python builds use Hatchling dynamic versioning from `VERSION.txt`; Docker image tags use `ghcr.io/<owner>/maco:<VERSION>-alpine`. Release builds call `scripts/write-build-info` before `uv build` so `maco version` reports the package version, commit SHA, and release date. Tag releases must use `v<VERSION>` and are handled by `.github/workflows/release.yml`, which publishes the Python package through PyPI trusted publishing/OIDC and pushes the GHCR image.

## Structure

- `src/maco/config.py` — load Claude-style `mcpServers` MCP config.
- `src/maco/mcp_manager.py` — async MCP client lifecycle and tool calls.
- `src/maco/codegen.py` — generates `.maco/maco_generated` Python wrappers.
- `src/maco/gateway.py` — localhost JSON/HTTP gateway used by generated wrappers.
- `src/maco/runner.py` — `uv run` execution helper that injects workspace/gateway env.
- `src/maco/sandbox/` — provider-based sandbox package with local, Docker, and Matchlock providers.
- `src/maco/serve_mcp.py` — HTTP MCP server exposing sandboxed `bash` and `code_execute`.
- `src/maco/cli.py` — `maco up`, `maco status`, `maco down`, `maco ls`, plus lower-level `maco gen` and `maco run`.
- `tests/unit/` — fast unit tests.
- `tests/integration/` — end-to-end tests that may start real MCP/gateway/sandbox processes.
- `scripts/` — release/build helper scripts.
- `SKILL.md` — agent-facing workflow and usage guide.
