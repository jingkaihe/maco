"""Async MCP client manager used by generation and the gateway."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol, cast

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from .config import MacoConfig, ServerConfig
from .oauth import make_oauth_auth


class _Closeable(Protocol):
    def close(self) -> None: ...


@dataclass
class ServerState:
    """Runtime state for an initialized MCP server."""

    config: ServerConfig
    session: ClientSession
    tools: list[Any]


class MCPManager:
    """Owns MCP sessions for all configured servers."""

    def __init__(self, config: MacoConfig):
        self.config = config
        self._stack = contextlib.AsyncExitStack()
        self._servers: dict[str, ServerState] = {}
        self._started = False

    async def __aenter__(self) -> MCPManager:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def start(self) -> None:
        if self._started:
            return
        try:
            for server in self.config.servers.values():
                read_stream, write_stream = await self._stack.enter_async_context(
                    _client_streams(server)
                )
                session = await self._stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                list_result = await session.list_tools()
                tools = [
                    tool
                    for tool in list_result.tools
                    if not server.tool_white_list or tool.name in server.tool_white_list
                ]
                self._servers[server.name] = ServerState(
                    config=server,
                    session=session,
                    tools=tools,
                )
            self._started = True
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        self._servers.clear()
        self._started = False
        await self._stack.aclose()

    async def list_tools(self, server_filter: str | None = None) -> dict[str, list[dict[str, Any]]]:
        await self._ensure_started()
        result: dict[str, list[dict[str, Any]]] = {}
        for name, state in self._servers.items():
            if server_filter and name != server_filter:
                continue
            result[name] = [_model_to_jsonable(tool) for tool in state.tools]
        return result

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self._ensure_started()
        state = self._servers.get(server_name)
        if state is None:
            known = ", ".join(sorted(self._servers)) or "<none>"
            raise KeyError(f"unknown MCP server {server_name!r}; known servers: {known}")
        if not any(tool.name == tool_name for tool in state.tools):
            known_tools = ", ".join(sorted(tool.name for tool in state.tools)) or "<none>"
            raise KeyError(
                f"unknown MCP tool {server_name}.{tool_name}; known tools for {server_name}: {known_tools}"
            )
        result = await state.session.call_tool(tool_name, arguments=arguments or {})
        return _model_to_jsonable(result)

    def server_names(self) -> list[str]:
        return sorted(self._servers)


    async def _ensure_started(self) -> None:
        if not self._started:
            await self.start()


@contextlib.asynccontextmanager
async def _client_streams(server: ServerConfig) -> AsyncIterator[tuple[Any, Any]]:
    if server.is_stdio:
        params = StdioServerParameters(
            command=server.command or "",
            args=server.args,
            env=server.env or None,
            cwd=server.cwd,
        )
        async with stdio_client(params) as streams:
            yield streams
        return

    if server.is_streamable_http:
        auth = make_oauth_auth(server)
        if server.headers or auth:
            try:
                async with create_mcp_http_client(
                    headers=server.headers or None,
                    auth=auth,
                ) as http_client:
                    async with streamable_http_client(
                        server.base_url or "",
                        http_client=http_client,
                    ) as (read_stream, write_stream, _get_session_id):
                        yield read_stream, write_stream
            finally:
                if hasattr(auth, "close"):
                    cast(_Closeable, auth).close()
        else:
            async with streamable_http_client(server.base_url or "") as (
                read_stream,
                write_stream,
                _get_session_id,
            ):
                yield read_stream, write_stream
        return

    if server.is_sse:
        auth = make_oauth_auth(server)
        try:
            async with sse_client(
                server.base_url or "",
                headers=server.headers or None,
                auth=auth,
            ) as streams:
                yield streams
        finally:
            if hasattr(auth, "close"):
                cast(_Closeable, auth).close()
        return

    raise ValueError(f"unsupported MCP server transport for {server.name}: {server.server_type}")


def _model_to_jsonable(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True, mode="json")
    if isinstance(value, dict):
        return value
    raise TypeError(f"cannot serialize MCP object of type {type(value).__name__}")
