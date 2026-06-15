"""Configuration loading for maco."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when an MCP configuration file is invalid."""


@dataclass(frozen=True)
class OAuthConfig:
    """Optional OAuth hints for remote HTTP/SSE MCP servers."""

    client_id: str | None = None
    client_secret: str | None = None
    scopes: list[str] = field(default_factory=list)
    redirect_uri: str | None = None
    auth_server_metadata_url: str | None = None
    interactive: str | None = None
    open_browser: bool | None = None
    callback_timeout: float | None = None


@dataclass(frozen=True)
class ServerConfig:
    """Configuration for one MCP server."""

    name: str
    server_type: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    base_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    oauth: OAuthConfig | None = None
    tool_white_list: list[str] = field(default_factory=list)

    @property
    def is_stdio(self) -> bool:
        return self.server_type == "stdio"

    @property
    def is_streamable_http(self) -> bool:
        return self.server_type in {"http", "streamable_http", "streamable-http"}

    @property
    def is_sse(self) -> bool:
        return self.server_type == "sse"


@dataclass(frozen=True)
class MacoConfig:
    """Top-level maco configuration."""

    path: Path
    servers: dict[str, ServerConfig]


def load_config(path: str | os.PathLike[str] = "mcp.json") -> MacoConfig:
    """Load a Claude-style MCP config file.

    The expected shape is ``{"mcpServers": {"name": {...}}}``.
    Values in ``env`` and string fields are expanded with the environment of the
    process running ``maco``; for example ``"$GITHUB_TOKEN"`` or
    ``"${GITHUB_TOKEN}"`` becomes the current value of that variable.
    """

    config_path = Path(path).expanduser()
    if not config_path.exists():
        raise ConfigError(f"configuration file not found: {config_path}")

    data = _read_mapping(config_path)
    servers_data = _extract_servers(data)
    servers: dict[str, ServerConfig] = {}
    for name, raw_server in servers_data.items():
        if not isinstance(raw_server, dict):
            raise ConfigError(f"server {name!r} must be an object")
        servers[name] = _parse_server(name, raw_server)

    if not servers:
        raise ConfigError(f"no MCP servers configured in {config_path}")
    return MacoConfig(path=config_path.resolve(), servers=servers)


def _read_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except Exception as exc:  # pragma: no cover - parser-specific messages vary
        raise ConfigError(f"failed to parse {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"configuration root in {path} must be an object")
    return data


def _extract_servers(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("mcpServers"), dict):
        return data["mcpServers"]
    raise ConfigError("configuration must contain a Claude-style mcpServers object")


def _parse_server(name: str, raw: dict[str, Any]) -> ServerConfig:
    server_type = str(
        raw.get("server_type")
        or raw.get("type")
        or raw.get("transport")
        or _infer_server_type(raw)
    ).strip().lower()
    if server_type in {"streamablehttp", "streamable-http"}:
        server_type = "streamable_http"

    env = _string_map(raw.get("env") or {})
    headers = _string_map(raw.get("headers") or {})
    oauth = _parse_oauth(name, raw.get("oauth"))
    args = raw.get("args") or []
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ConfigError(f"server {name!r} args must be a list of strings")

    white_list = (
        raw.get("tool_white_list")
        or raw.get("tool_whitelist")
        or raw.get("tools")
        or []
    )
    if not isinstance(white_list, list) or not all(isinstance(tool, str) for tool in white_list):
        raise ConfigError(f"server {name!r} tool whitelist must be a list of strings")

    command = _optional_expanded(raw.get("command"))
    base_url = _optional_expanded(raw.get("base_url") or raw.get("url"))
    cwd = _optional_expanded(raw.get("cwd"))

    if server_type == "stdio" and not command:
        raise ConfigError(f"server {name!r} requires command for stdio transport")
    if server_type in {"http", "streamable_http", "sse"} and not base_url:
        raise ConfigError(f"server {name!r} requires base_url/url for {server_type} transport")
    if server_type not in {"stdio", "http", "streamable_http", "sse"}:
        raise ConfigError(f"server {name!r} has unsupported transport {server_type!r}")

    return ServerConfig(
        name=name,
        server_type=server_type,
        command=command,
        args=[_expand_value(arg) for arg in args],
        env=env,
        cwd=cwd,
        base_url=base_url,
        headers=headers,
        oauth=oauth,
        tool_white_list=white_list,
    )


def _parse_oauth(name: str, raw: Any) -> OAuthConfig | None:
    if raw is None or raw is False:
        return None
    if raw is True:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"server {name!r} oauth must be an object")

    scopes = raw.get("scopes") or []
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        raise ConfigError(f"server {name!r} oauth scopes must be a list of strings")

    interactive = _optional_expanded(raw.get("interactive"))
    if interactive is not None:
        interactive = interactive.strip().lower()

    return OAuthConfig(
        client_id=_optional_expanded(raw.get("client_id")),
        client_secret=_optional_expanded(raw.get("client_secret")),
        scopes=[_expand_value(scope) for scope in scopes],
        redirect_uri=_optional_expanded(raw.get("redirect_uri")),
        auth_server_metadata_url=_optional_expanded(raw.get("auth_server_metadata_url")),
        interactive=interactive,
        open_browser=_optional_bool(name, raw.get("open_browser")),
        callback_timeout=_optional_duration_seconds(name, raw.get("callback_timeout")),
    )


def _optional_bool(name: str, value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = _expand_value(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"server {name!r} oauth open_browser must be a boolean")


def _optional_duration_seconds(name: str, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        seconds = float(value)
    elif isinstance(value, str):
        seconds = _parse_duration_seconds(_expand_value(value).strip())
    else:
        raise ConfigError(
            f"server {name!r} oauth callback_timeout must be a number or duration string"
        )
    if seconds <= 0:
        raise ConfigError(f"server {name!r} oauth callback_timeout must be positive")
    return seconds


def _parse_duration_seconds(value: str) -> float:
    if not value:
        raise ConfigError("oauth callback_timeout must not be empty")
    multipliers = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    for suffix, multiplier in multipliers.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * multiplier
    return float(value)


def _infer_server_type(raw: dict[str, Any]) -> str:
    if raw.get("base_url") or raw.get("url"):
        return "http"
    return "stdio"


def _optional_expanded(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"expected string value, got {type(value).__name__}")
    return _expand_value(value)


def _string_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ConfigError("expected object with string keys and values")
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ConfigError("expected object with string keys")
        if value is None:
            result[key] = ""
        elif isinstance(value, str):
            result[key] = _expand_value(value)
        else:
            result[key] = _expand_value(str(value))
    return result


def _expand_value(value: str) -> str:
    """Expand ~/ and environment variables in a config value."""

    return os.path.expandvars(os.path.expanduser(value))
