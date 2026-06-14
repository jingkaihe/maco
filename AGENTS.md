## Project overview

`maco` is a Python implementation of the MCP code-execution pattern from Kodelet based on Anthropic's MCP code-execution article. It exposes many MCP tools through a compact code-execution interface backed by generated Python wrappers and a managed gateway.

## Common commands

```bash
make check
make test-unit
make test-integration
make build
make build-release
make image
make image-import
uv run maco --help
```

`make check` runs `ruff`, `ty`, and the full pytest suite. Use `make test-unit` for fast unit coverage and `make test-integration` for end-to-end tests that may start real MCP/gateway/sandbox processes. Use `make build-release` only when you intentionally want to embed the current commit/date into local build artifacts; it resets `src/maco/_build_info.py` afterwards. Use `python -m compileall` only as a targeted smoke test for generated code or unusual dynamic-code changes.

`VERSION.txt` is the single version source for the Python package and sandbox image tag. The Python distribution name is `mcp-as-code`; the import package and executable are `maco`. Python builds use Hatchling dynamic versioning from `VERSION.txt`; Docker image tags use `ghcr.io/<owner>/maco:<VERSION>-alpine`. Release builds call `scripts/write-build-info` before `uv build` so `maco version` reports the package version, commit SHA, and release date. Tag releases must use `v<VERSION>` and are handled by `.github/workflows/release.yml`, which publishes the Python package through PyPI trusted publishing/OIDC and pushes the GHCR image.

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
- `src/maco/serve_mcp.py` — HTTP MCP server exposing sandboxed `bash` and `code_execute`.
- `src/maco/cli.py` — `maco serve-mcp` plus lower-level `maco gen`, `maco serve`, and `maco run`.
- `tests/unit/` — fast unit tests.
- `tests/integration/` — end-to-end tests that may start real MCP/gateway/sandbox processes.
- `scripts/` — thin bash wrappers around the Python CLI.
- `SKILL.md` — agent-facing workflow and usage guide.
