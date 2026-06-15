from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Protocol, cast
from urllib.request import urlopen

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

from maco.config import OAuthConfig, ServerConfig
from maco.mcp_manager import _client_streams
from maco.oauth import (
    ConfigOverlayTokenStorage,
    FileTokenStorage,
    MacoOAuthClientProvider,
    OAuthCallbackServer,
    _ignore_intermediate_response_body,
    callback_timeout,
    credentials_path,
    make_oauth_auth,
)


class _Closeable(Protocol):
    def close(self) -> None: ...


def test_credentials_path_is_stable_and_safe(tmp_path):
    path = credentials_path(" My Server! ", "https://mcp.example/mcp", storage_root=tmp_path)

    assert path.parent == tmp_path
    assert path.name.startswith("my_server-")
    assert path.suffix == ".json"
    assert path == credentials_path(" My Server! ", "https://mcp.example/mcp", storage_root=tmp_path)


def test_file_token_storage_round_trips_token_and_client_info(tmp_path):
    async def run() -> None:
        storage = FileTokenStorage(tmp_path / "credentials.json")
        token = OAuthToken(
            access_token="access-token",
            token_type="Bearer",
            refresh_token="refresh-token",
            expires_in=3600,
        )
        client_info = OAuthClientInformationFull(
            client_id="client-id",
            client_secret="client-secret",
            redirect_uris=[AnyUrl("http://127.0.0.1:1234/callback")],
            token_endpoint_auth_method="client_secret_post",
        )

        await storage.set_tokens(token)
        await storage.set_client_info(client_info)

        assert await storage.get_tokens() == token
        assert await storage.get_client_info() == client_info

    asyncio.run(run())
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600


def test_file_token_storage_persists_token_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr("maco.oauth.time.time", lambda: 1000.0)

    async def run() -> None:
        storage = FileTokenStorage(tmp_path / "credentials.json")

        await storage.set_tokens(
            OAuthToken(
                access_token="access-token",
                token_type="Bearer",
                refresh_token="refresh-token",
                expires_in=3600,
            )
        )

        assert await storage.get_token_expiry_time() == 4600.0

    asyncio.run(run())


def test_file_token_storage_treats_old_expiring_cache_as_expired(tmp_path):
    path = tmp_path / "credentials.json"
    token = OAuthToken(
        access_token="access-token",
        token_type="Bearer",
        refresh_token="refresh-token",
        expires_in=3600,
    )
    path.write_text(
        json.dumps({"token": token.model_dump(mode="json", exclude_none=True)}),
        encoding="utf-8",
    )

    async def run() -> None:
        storage = FileTokenStorage(path)

        assert await storage.get_token_expiry_time() == 0.0

    asyncio.run(run())


def test_oauth_provider_restores_persisted_token_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr("maco.oauth.time.time", lambda: 1000.0)

    async def run() -> None:
        storage = FileTokenStorage(tmp_path / "credentials.json")
        await storage.set_tokens(
            OAuthToken(
                access_token="access-token",
                token_type="Bearer",
                refresh_token="refresh-token",
                expires_in=3600,
            )
        )
        await storage.set_client_info(
            OAuthClientInformationFull(
                client_id="client-id",
                redirect_uris=[AnyUrl("http://127.0.0.1:1234/callback")],
                token_endpoint_auth_method="none",
            )
        )
        provider = MacoOAuthClientProvider(
            server_url="https://mcp.example/mcp",
            client_metadata=OAuthClientMetadata(
                redirect_uris=[AnyUrl("http://127.0.0.1:1234/callback")],
                token_endpoint_auth_method="none",
            ),
            storage=storage,
            oauth_config=OAuthConfig(),
        )

        await provider._initialize()

        assert provider.context.token_expiry_time == 4600.0

    asyncio.run(run())


def test_config_overlay_supplies_preconfigured_client_id(tmp_path):
    async def run() -> None:
        storage = ConfigOverlayTokenStorage(
            FileTokenStorage(tmp_path / "credentials.json"),
            OAuthConfig(client_id="configured-client", client_secret="configured-secret"),
        )

        client_info = await storage.get_client_info()

        assert client_info is not None
        assert client_info.client_id == "configured-client"
        assert client_info.client_secret == "configured-secret"
        assert client_info.token_endpoint_auth_method == "client_secret_post"

    asyncio.run(run())


def test_callback_server_returns_code_and_state():
    async def run() -> None:
        callback = OAuthCallbackServer.start()
        try:
            with urlopen(callback.redirect_uri + "?code=oauth-code&state=state-123", timeout=2) as response:
                body = response.read().decode("utf-8")
            assert "Authorization complete" in body
            assert await callback.wait() == ("oauth-code", "state-123")
        finally:
            callback.close()

    asyncio.run(run())


def test_callback_server_wait_times_out():
    async def run() -> None:
        callback = OAuthCallbackServer.start()
        try:
            with pytest.raises(TimeoutError, match="Timed out waiting 0.01 seconds for OAuth callback"):
                await callback.wait(timeout=0.01)
        finally:
            callback.close()

    asyncio.run(run())


def test_oauth_provider_discards_stale_dynamic_client_registration(tmp_path):
    provider = MacoOAuthClientProvider(
        server_url="https://mcp.example/mcp",
        client_metadata=OAuthClientMetadata(
            redirect_uris=[AnyUrl("http://127.0.0.1:2222/callback")],
            token_endpoint_auth_method="none",
        ),
        storage=FileTokenStorage(tmp_path / "credentials.json"),
        oauth_config=OAuthConfig(),
    )
    provider.context.client_info = OAuthClientInformationFull(
        client_id="registered-client",
        redirect_uris=[AnyUrl("http://127.0.0.1:1111/callback")],
        token_endpoint_auth_method="none",
    )

    provider._discard_stale_client_info()

    assert provider.context.client_info is None


def test_oauth_provider_keeps_configured_client_registration(tmp_path):
    provider = MacoOAuthClientProvider(
        server_url="https://mcp.example/mcp",
        client_metadata=OAuthClientMetadata(
            redirect_uris=[AnyUrl("http://127.0.0.1:2222/callback")],
            token_endpoint_auth_method="none",
        ),
        storage=FileTokenStorage(tmp_path / "credentials.json"),
        oauth_config=OAuthConfig(client_id="configured-client"),
    )
    client_info = OAuthClientInformationFull(
        client_id="configured-client",
        redirect_uris=[AnyUrl("http://127.0.0.1:1111/callback")],
        token_endpoint_auth_method="none",
    )
    provider.context.client_info = client_info

    provider._discard_stale_client_info()

    assert provider.context.client_info == client_info


def test_oauth_challenge_body_is_not_drained():
    class UndrainableStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self):
            raise AssertionError("OAuth challenge body should not be read")
            yield b""

        async def aclose(self) -> None:
            self.closed = True

    async def run() -> None:
        stream = UndrainableStream()
        response = httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer realm="mcp", scope="mcp.read"'},
            stream=stream,
            request=httpx.Request("POST", "https://mcp.example/mcp"),
        )

        await _ignore_intermediate_response_body(response)

        assert stream.closed
        assert await response.aread() == b""

    asyncio.run(run())


def test_make_oauth_auth_skips_static_authorization_header(tmp_path):
    server = ServerConfig(
        name="remote",
        server_type="http",
        base_url="https://mcp.example/mcp",
        headers={"Authorization": "Bearer static-token"},
        oauth=OAuthConfig(interactive="always"),
    )

    assert make_oauth_auth(server, storage_root=tmp_path) is None


def test_streamable_http_with_oauth_uses_mcp_http_client_factory(monkeypatch):
    captured: dict[str, object] = {}
    auth = object()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def create_client(*, headers=None, auth=None):
        captured["headers"] = headers
        captured["auth"] = auth
        return FakeClient()

    @contextlib.asynccontextmanager
    async def streamable_client(url, *, http_client):
        captured["url"] = url
        captured["http_client"] = http_client
        yield "read", "write", lambda: None

    monkeypatch.setattr("maco.mcp_manager.create_mcp_http_client", create_client)
    monkeypatch.setattr("maco.mcp_manager.streamable_http_client", streamable_client)
    monkeypatch.setattr("maco.mcp_manager.make_oauth_auth", lambda server: auth)

    async def run() -> None:
        server = ServerConfig(
            name="remote",
            server_type="http",
            base_url="https://mcp.example/mcp",
        )
        async with _client_streams(server):
            pass

    asyncio.run(run())

    assert captured["headers"] is None
    assert captured["auth"] is auth
    assert captured["url"] == "https://mcp.example/mcp"
    assert isinstance(captured["http_client"], FakeClient)


def test_make_oauth_auth_is_available_for_remote_servers_without_static_auth(tmp_path):
    server = ServerConfig(
        name="remote",
        server_type="http",
        base_url="https://mcp.example/mcp",
    )

    auth = make_oauth_auth(server, storage_root=tmp_path)
    try:
        assert auth is not None
    finally:
        if hasattr(auth, "close"):
            cast(_Closeable, auth).close()


def test_callback_timeout_env_override(monkeypatch):
    monkeypatch.setenv("MACO_MCP_OAUTH_CALLBACK_TIMEOUT", "3m")

    assert callback_timeout(OAuthConfig(callback_timeout=1)) == 180


def test_callback_server_rejects_non_loopback_redirect_uri():
    with pytest.raises(ValueError, match="loopback"):
        OAuthCallbackServer.start("http://example.com/callback")


@contextlib.contextmanager
def closing_auth(auth):
    try:
        yield auth
    finally:
        if hasattr(auth, "close"):
            cast(_Closeable, auth).close()
