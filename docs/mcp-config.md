# MCP configuration

`maco` reads Claude-style MCP configuration files with a top-level `mcpServers` object. The same config is used by `maco gen` and `maco up`.

```json
{
  "mcpServers": {
    "server-name": {
      "command": "..."
    }
  }
}
```

## Supported transports

| Transport | When to use it | Required fields |
| --- | --- | --- |
| `stdio` | Local subprocess MCP servers | `command` |
| `http` / `streamable_http` | Remote Streamable HTTP MCP servers | `url` or `base_url` |
| `sse` | Deprecated SSE MCP servers | `url` or `base_url` |

If `type`, `server_type`, or `transport` is omitted, maco infers `http` when `url`/`base_url` is present and `stdio` otherwise.

## Common fields

| Field | Type | Applies to | Description |
| --- | --- | --- | --- |
| `type`, `server_type`, `transport` | string | all | Transport name. `streamable-http` and `streamablehttp` are normalized to `streamable_http`. |
| `command` | string | stdio | Executable used to start the MCP server. |
| `args` | string array | stdio | Arguments passed to `command`. |
| `env` | object | stdio | Environment variables passed to the subprocess. Values expand `$VAR`, `${VAR}`, and `~`. |
| `cwd` | string | stdio | Working directory for the subprocess. |
| `url`, `base_url` | string | HTTP/SSE | MCP endpoint URL. Values expand `$VAR`, `${VAR}`, and `~`. |
| `headers` | object | HTTP/SSE | Static HTTP headers. Values expand environment variables. |
| `oauth` | object | HTTP/SSE | Optional OAuth hints and interaction settings. See [OAuth](#oauth). |
| `tools`, `tool_white_list`, `tool_whitelist` | string array | all | Optional allow-list of upstream tool names to expose. |

## Stdio servers

Use stdio for MCP servers that run as local subprocesses.

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

### Environment variables

Prefer listing required environment variables under `env`. maco expands `$VAR` and `${VAR}` using the environment of the maco process, then passes the resolved values to the upstream MCP server.

```json
{
  "mcpServers": {
    "github": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "ghcr.io/github/github-mcp-server"
      ],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

Arguments and string fields are expanded too, so this is also valid:

```json
{
  "mcpServers": {
    "custom": {
      "command": "uv",
      "args": ["run", "server.py", "--token", "$TOKEN"],
      "env": {"TOKEN": "$TOKEN"}
    }
  }
}
```

## Streamable HTTP servers

Use `type: "http"` or `type: "streamable_http"` for remote Streamable HTTP MCP servers.

```json
{
  "mcpServers": {
    "remote": {
      "type": "http",
      "url": "https://example.com/mcp"
    }
  }
}
```

### Static headers

Use `headers` for API-key or static bearer-token servers. Static `Authorization` headers take precedence over OAuth and disable OAuth for that server.

```json
{
  "mcpServers": {
    "remote": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_TOKEN}"
      }
    }
  }
}
```

## SSE servers

SSE is supported for compatibility with older MCP servers. Set `type: "sse"` explicitly.

```json
{
  "mcpServers": {
    "legacy": {
      "type": "sse",
      "url": "https://example.com/sse"
    }
  }
}
```

## OAuth

For remote HTTP/SSE servers without a static `Authorization` header, maco can automatically perform OAuth when the upstream server returns an HTTP `401` with a Bearer challenge. The flow is:

1. maco sends the MCP request without a bearer token.
2. The upstream server responds with `WWW-Authenticate: Bearer ...`.
3. maco discovers protected-resource and authorization-server metadata.
4. maco uses configured client hints or dynamic client registration.
5. maco opens or prints an authorization URL.
6. maco receives the loopback callback, exchanges the code with PKCE, caches the token, and retries the MCP request.

You can omit `oauth` entirely when the provider supports standard discovery and dynamic client registration.

```json
{
  "mcpServers": {
    "remote": {
      "type": "http",
      "url": "https://example.com/mcp"
    }
  }
}
```

Add `oauth` when the provider needs hints such as a pre-registered client, scopes, a fixed callback URL, or non-standard metadata discovery.

```json
{
  "mcpServers": {
    "remote": {
      "type": "http",
      "url": "https://example.com/mcp",
      "oauth": {
        "client_id": "${MCP_CLIENT_ID}",
        "client_secret": "${MCP_CLIENT_SECRET}",
        "scopes": ["mcp.read", "mcp.write"],
        "redirect_uri": "http://127.0.0.1:1456/mcp/oauth/callback",
        "auth_server_metadata_url": "https://auth.example.com/.well-known/oauth-authorization-server",
        "interactive": "auto",
        "open_browser": true,
        "callback_timeout": "2m"
      }
    }
  }
}
```

### OAuth fields

| Field | Type | Description |
| --- | --- | --- |
| `client_id` | string | Pre-registered OAuth client ID. Omit when dynamic client registration is supported. |
| `client_secret` | string | Optional client secret. When present, token requests use `client_secret_post`. |
| `scopes` | string array | Requested OAuth scopes. If omitted, maco uses scopes from the Bearer challenge or protected-resource metadata. |
| `redirect_uri` | string | Loopback callback URI. Defaults to an ephemeral `http://127.0.0.1:<port>/mcp/oauth/callback`. `:0` is allowed and is replaced with the actual bound port. |
| `auth_server_metadata_url` | string | Optional direct authorization-server metadata URL for providers with non-standard discovery. |
| `interactive` | string | `auto`, `always`, or `never`. `auto` allows browser authorization only in an interactive terminal. |
| `open_browser` | boolean | Whether to attempt opening the default browser. The URL is always printed as a fallback. |
| `callback_timeout` | number or string | Time to wait for the callback. Supports seconds as a number or strings like `30s`, `2m`, `1h`. |

### OAuth cache and environment overrides

OAuth tokens and client registration metadata are cached under:

```text
~/.maco/mcp/oauth/
```

Credential files are written with `0600` permissions.

Environment variables can override interaction behavior:

| Variable | Description |
| --- | --- |
| `MACO_MCP_OAUTH_INTERACTIVE` | Overrides `oauth.interactive`. Use `never` in CI/headless runs to fail fast. |
| `MACO_MCP_OAUTH_OPEN_BROWSER` | Overrides `oauth.open_browser`. Accepts values like `true`, `false`, `1`, `0`. |
| `MACO_MCP_OAUTH_CALLBACK_TIMEOUT` | Overrides `oauth.callback_timeout`. Accepts seconds or duration strings like `2m`. |

## Tool filtering

Use `tools`, `tool_white_list`, or `tool_whitelist` to expose only selected upstream tools through generated wrappers and the maco gateway.

```json
{
  "mcpServers": {
    "remote": {
      "type": "http",
      "url": "https://example.com/mcp",
      "tools": ["search", "fetch"]
    }
  }
}
```

Filtering uses the upstream MCP tool names, not generated Python function names.

## Complete mixed example

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "github": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "ghcr.io/github/github-mcp-server"
      ],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
      }
    },
    "remote": {
      "type": "http",
      "url": "https://example.com/mcp",
      "oauth": {
        "scopes": ["mcp.read"]
      },
      "tools": ["search", "fetch"]
    }
  }
}
```
