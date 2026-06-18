# maco

**Connect every MCP server you need, keeping your agent's context lean.**

https://github.com/user-attachments/assets/4b91ea97-d48e-41c5-8189-0da8522ac459

As the number of MCP servers you connect grows, tool schemas and intermediate tool call results clutter your agent's context. `maco` (mcp-as-code) collapses them all into a single endpoint with a programmatic interface.

Instead of loading hundreds if not thousands of tool schemas upfront, `maco` reconstructs every MCP tool as Pydantic models and Python functions in a virtual filesystem and hands your agent just two of its favourite tools: `bash` to navigate, and `code_execute` to run. The agent discovers and composes tools as code, the thing frontier models do best.

## How it works

**Small context footprint:** the agent starts with two tools (`bash` and `code_execute`), not every MCP tool schema upfront.

**Progressive discovery:** frontier models excel at navigating filesystems. By representing the tool interface as code on a filesystem, the agent can leverage `rg`, `fd` and all the POSIX tools to discover and execute relevant MCP tools.

```bash
tools
├── playwright
│   ├── browserClick.py
│   ├── browserClose.py
│   ├── ... many other tools
│   └── __init__.py
└── github
    ├── addIssueComment.py
    └── __init__.py
```

**Programmatic leverage:** the agent is given a real programming language, Python, allowing it to orchestrate complex control flows with exceptional context-efficiency using loops, conditions, and state management.

```python
from collections import Counter
from tools.github import listCommits

owner, repo, page, counts = "openclaw", "openclaw", 1, Counter()

while True:
    commits = listCommits(owner=owner, repo=repo, perPage=100, page=page)
    for commit in commits:
        login = (commit.get("author") or {}).get("login")
        if login and "bot" not in login.lower():
            counts[login] += 1
    if len(commits) < 100 or page >= 20:
        break
    page += 1

total = sum(counts.values())
for login, count in counts.most_common():
    if count / total < 0.01:
        break
    print(f"@{login}: {count} commits ({count / total:.1%})")
```

The example above illustrates the MCP code that will be executed to find the top contributors to an open-source repository.

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

Create a `mcp.json`:

```json
{
    "mcpServers": {
        "playwright": {
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest"]
        },
        "github": {
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": { "Authorization": "Bearer ${GITHUB_TOKEN}" }
        }
    }
}
```

This config needs `npx` (for Playwright MCP), a GitHub token in `GITHUB_TOKEN`, and Docker if you use the `docker` provider.

Start the `maco` MCP server:

```bash
maco up --config mcp.json --provider docker
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

See [`examples/serve-mcp`](examples/serve-mcp) for a complete example that wraps multiple upstream MCP servers behind one `maco` endpoint.

## MCP config

See [`docs/mcp-config.md`](docs/mcp-config.md) for the full config reference, including environment expansion, headers, OAuth hints, token caching, and tool filtering.

## Sandbox providers

Choose the execution provider with `--provider`:

- `local`: fastest feedback loop; runs commands as local subprocesses.
- `docker`: runs commands in a long-lived Docker container.
- `matchlock`: runs commands in a long-lived Matchlock micro-VM.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
