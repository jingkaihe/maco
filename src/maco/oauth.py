"""OAuth helpers for remote HTTP/SSE MCP servers."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time
from typing import Any, AsyncGenerator, Callable, Literal, Protocol, cast
from urllib.parse import parse_qs, urlparse
import webbrowser

import httpx
from jinja2 import Template
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_info_from_metadata_url,
    create_client_registration_request,
    create_oauth_metadata_request,
    extract_field_from_www_auth,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
    should_use_client_metadata_url,
)
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import MCP_PROTOCOL_VERSION
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from mcp.types import LATEST_PROTOCOL_VERSION
from pydantic import AnyUrl

from .config import OAuthConfig, ServerConfig


_OAUTH_AUTHORIZATION_TEMPLATE = Template(
    """\
{{ name }} requires OAuth authorization.{% if opening_browser %} Opening your browser...{% endif %}
{% if browser_failed %}Could not open browser automatically.
{% endif %}If your browser did not open, visit this URL:

  {{ auth_url }}
"""
)


CALLBACK_PATH = "/mcp/oauth/callback"
DEFAULT_CALLBACK_TIMEOUT = 120.0
TokenEndpointAuthMethod = Literal[
    "none",
    "client_secret_post",
    "client_secret_basic",
    "private_key_jwt",
]


class TokenExpiryStorage(Protocol):
    """Optional extension for token stores that persist absolute expiry."""

    async def get_token_expiry_time(self) -> float | None: ...


def make_oauth_auth(
    server: ServerConfig,
    *,
    storage_root: Path | None = None,
    callback_server_factory: Callable[[str | None], OAuthCallbackServer] | None = None,
) -> httpx.Auth | None:
    """Create an HTTPX OAuth auth provider for a remote MCP server.

    OAuth is challenge-discovered by the MCP SDK. Static Authorization headers
    keep their existing behavior and take precedence over OAuth.
    """

    if not (server.is_streamable_http or server.is_sse):
        return None
    if any(key.lower() == "authorization" for key in server.headers):
        return None
    if not server.base_url:
        return None
    oauth = server.oauth or OAuthConfig()
    credential_path = credentials_path(server.name, server.base_url, storage_root=storage_root)

    callback_server_factory = callback_server_factory or OAuthCallbackServer.start
    callback = callback_server_factory(oauth.redirect_uri)
    redirect_uri = callback.redirect_uri
    storage = FileTokenStorage(credential_path)
    client_metadata = _client_metadata(server, oauth, redirect_uri)
    timeout = callback_timeout(oauth)
    provider = MacoOAuthClientProvider(
        server_url=server.base_url,
        client_metadata=client_metadata,
        storage=ConfigOverlayTokenStorage(storage, oauth),
        redirect_handler=_redirect_handler(server.name, oauth),
        callback_handler=lambda: callback.wait(timeout=timeout),
        timeout=timeout,
        oauth_config=oauth,
    )
    return _ClosingOAuthAuth(provider, callback)


def credentials_path(server_name: str, server_url: str, *, storage_root: Path | None = None) -> Path:
    """Return the stable credentials path for one server URL."""

    root = storage_root or Path.home() / ".maco" / "mcp" / "oauth"
    digest = hashlib.sha256(server_url.encode("utf-8")).hexdigest()[:12]
    return root / f"{_safe_server_name(server_name)}-{digest}.json"


def _safe_server_name(server_name: str) -> str:
    safe = re.sub(r"[^a-z0-9_-]+", "_", server_name.strip().lower()).strip("_")
    return safe or "server"


def _client_metadata(server: ServerConfig, oauth: OAuthConfig, redirect_uri: str) -> OAuthClientMetadata:
    return OAuthClientMetadata(
        client_name=f"maco-{server.name}" if server.name else "maco",
        redirect_uris=[AnyUrl(redirect_uri)],
        token_endpoint_auth_method=_token_endpoint_auth_method(oauth),
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=" ".join(oauth.scopes) if oauth.scopes else None,
    )


def _token_endpoint_auth_method(oauth: OAuthConfig) -> TokenEndpointAuthMethod:
    if oauth.client_secret:
        return "client_secret_post"
    return "none"


def _redirect_handler(server_name: str, oauth: OAuthConfig) -> Callable[[str], Any]:
    async def redirect_handler(auth_url: str) -> None:
        if not interactive_allowed(oauth):
            raise RuntimeError(
                f"MCP server {server_name!r} requires OAuth authorization; "
                "set oauth.interactive to 'always' or run in an interactive terminal"
            )

        name = f"MCP server {server_name!r}" if server_name else "MCP server"
        opening_browser = open_browser(oauth)
        browser_failed = opening_browser and not webbrowser.open(auth_url)
        print(
            _OAUTH_AUTHORIZATION_TEMPLATE.render(
                name=name,
                opening_browser=opening_browser,
                browser_failed=browser_failed,
                auth_url=auth_url,
            ).strip(),
            file=sys.stderr,
        )

    return redirect_handler


def interactive_allowed(oauth: OAuthConfig) -> bool:
    mode = _env_or_config("MACO_MCP_OAUTH_INTERACTIVE", oauth.interactive, "auto").strip().lower()
    if mode in {"always", "true", "enabled", "on", "yes", "1"}:
        return True
    if mode in {"never", "false", "disabled", "off", "no", "0"}:
        return False
    return sys.stdin.isatty()


def open_browser(oauth: OAuthConfig) -> bool:
    configured = _env_or_config("MACO_MCP_OAUTH_OPEN_BROWSER", oauth.open_browser, True)
    if isinstance(configured, bool):
        return configured
    return str(configured).strip().lower() not in {"0", "false", "no", "off"}


def callback_timeout(oauth: OAuthConfig) -> float:
    value = _env_or_config("MACO_MCP_OAUTH_CALLBACK_TIMEOUT", oauth.callback_timeout, None)
    if value is None:
        return DEFAULT_CALLBACK_TIMEOUT
    return _duration_seconds(value)


def _env_or_config(env_name: str, configured: Any, default: Any) -> Any:
    value = os.environ.get(env_name)
    if value is not None:
        return value
    if configured is not None:
        return configured
    return default


def _duration_seconds(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    multipliers = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    for suffix, multiplier in multipliers.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * multiplier
    return float(text)


class FileTokenStorage(TokenStorage):
    """JSON-file OAuth token storage for the MCP SDK."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    async def get_tokens(self) -> OAuthToken | None:
        data = await self._load()
        token = data.get("token")
        return OAuthToken.model_validate(token) if token else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = await self._load(missing_ok=True)
        data["token"] = tokens.model_dump(mode="json", exclude_none=True)
        if tokens.expires_in is None:
            data.pop("token_expires_at", None)
        else:
            data["token_expires_at"] = time.time() + int(tokens.expires_in)
        await self._save(data)

    async def get_token_expiry_time(self) -> float | None:
        data = await self._load()
        expires_at = data.get("token_expires_at")
        if expires_at is not None:
            return float(expires_at)
        token = data.get("token")
        if token and token.get("expires_in") is not None:
            return 0.0
        return None

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = await self._load()
        client_info = data.get("client_info")
        return OAuthClientInformationFull.model_validate(client_info) if client_info else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = await self._load(missing_ok=True)
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        await self._save(data)

    async def _load(self, *, missing_ok: bool = False) -> dict[str, Any]:
        async with self._lock:
            if not self.path.exists():
                return {} if missing_ok else {}
            return json.loads(await asyncio.to_thread(self.path.read_text, encoding="utf-8"))

    async def _save(self, data: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(_atomic_write_json, self.path, data)


class ConfigOverlayTokenStorage(TokenStorage):
    """Token storage that overlays configured OAuth client credentials."""

    def __init__(self, inner: TokenStorage, config: OAuthConfig):
        self.inner = inner
        self.config = config

    async def get_tokens(self) -> OAuthToken | None:
        return await self.inner.get_tokens()

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self.inner.set_tokens(tokens)

    async def get_token_expiry_time(self) -> float | None:
        get_token_expiry_time = getattr(self.inner, "get_token_expiry_time", None)
        if get_token_expiry_time is None:
            return None
        return await get_token_expiry_time()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        stored = await self.inner.get_client_info()
        if not self.config.client_id:
            return stored
        if stored is None:
            data: dict[str, Any] = {}
        else:
            data = stored.model_dump(mode="json", exclude_none=True)
        data["client_id"] = self.config.client_id
        if self.config.client_secret:
            data["client_secret"] = self.config.client_secret
        data["token_endpoint_auth_method"] = _token_endpoint_auth_method(self.config)
        data.setdefault("redirect_uris", None)
        return OAuthClientInformationFull.model_validate(data)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        if self.config.client_id:
            data = client_info.model_dump(mode="json", exclude_none=True)
            data["client_id"] = self.config.client_id
            if self.config.client_secret:
                data["client_secret"] = self.config.client_secret
                data["token_endpoint_auth_method"] = _token_endpoint_auth_method(self.config)
            client_info = OAuthClientInformationFull.model_validate(data)
        await self.inner.set_client_info(client_info)


class MacoOAuthClientProvider(OAuthClientProvider):
    """MCP SDK OAuth provider adapted for streaming MCP responses."""

    requires_response_body = False

    def __init__(self, *args: Any, oauth_config: OAuthConfig, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.oauth_config = oauth_config

    async def _initialize(self) -> None:
        await super()._initialize()
        if not self.context.current_tokens:
            return
        expiry_time = await _get_token_expiry_time(self.context.storage)
        if expiry_time is not None:
            self.context.token_expiry_time = expiry_time
        elif self.context.current_tokens.expires_in is not None:
            self.context.token_expiry_time = 0.0

    def _discard_stale_client_info(self) -> None:
        if self.oauth_config.client_id or not self.context.client_info:
            return
        if _client_info_has_stale_redirect_uri(
            self.context.client_info,
            self.context.client_metadata,
        ):
            self.context.client_info = None

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        async with self.context.lock:
            if not self._initialized:
                await self._initialize()

            self.context.protocol_version = request.headers.get(MCP_PROTOCOL_VERSION) or LATEST_PROTOCOL_VERSION

            if not self.context.is_token_valid() and self.context.can_refresh_token():
                refresh_request = await self._refresh_token()
                refresh_response = yield refresh_request
                await refresh_response.aread()
                if not await self._handle_refresh_response(refresh_response):
                    self._initialized = False

            if self.context.is_token_valid():
                self._add_auth_header(request)

            response = yield request

            if response.status_code == 401 and _has_bearer_challenge(response):
                await _ignore_intermediate_response_body(response)
                flow = self._complete_oauth_flow(response)
                try:
                    next_request = await flow.__anext__()
                    while True:
                        next_response = yield next_request
                        try:
                            next_request = await flow.asend(next_response)
                        except StopAsyncIteration:
                            break
                finally:
                    await flow.aclose()
                self._add_auth_header(request)
                yield request
            elif response.status_code == 403:
                error = extract_field_from_www_auth(response, "error")
                if error == "insufficient_scope":
                    await response.aread()
                    self.context.client_metadata.scope = get_client_metadata_scopes(
                        extract_scope_from_www_auth(response),
                        self.context.protected_resource_metadata,
                    )
                    token_response = yield await self._perform_authorization()
                    await token_response.aread()
                    await self._handle_token_response(token_response)
                    self._add_auth_header(request)
                    yield request

    async def _complete_oauth_flow(
        self, response: httpx.Response
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        www_auth_resource_metadata_url = extract_resource_metadata_from_www_auth(response)

        if self.oauth_config.auth_server_metadata_url:
            oauth_metadata_request = create_oauth_metadata_request(
                self.oauth_config.auth_server_metadata_url
            )
            oauth_metadata_response = yield oauth_metadata_request
            await oauth_metadata_response.aread()
            ok, asm = await handle_auth_metadata_response(oauth_metadata_response)
            if ok and asm:
                self.context.oauth_metadata = asm
        else:
            prm_discovery_urls = build_protected_resource_metadata_discovery_urls(
                www_auth_resource_metadata_url,
                self.context.server_url,
            )

            for url in prm_discovery_urls:
                discovery_request = create_oauth_metadata_request(url)
                discovery_response = yield discovery_request
                await discovery_response.aread()
                prm = await handle_protected_resource_response(discovery_response)
                if prm:
                    await self._validate_resource_match(prm)
                    self.context.protected_resource_metadata = prm
                    self.context.auth_server_url = str(prm.authorization_servers[0])
                    break

            asm_discovery_urls = build_oauth_authorization_server_metadata_discovery_urls(
                self.context.auth_server_url,
                self.context.server_url,
            )

            for url in asm_discovery_urls:
                oauth_metadata_request = create_oauth_metadata_request(url)
                oauth_metadata_response = yield oauth_metadata_request
                await oauth_metadata_response.aread()
                ok, asm = await handle_auth_metadata_response(oauth_metadata_response)
                if not ok:
                    break
                if asm:
                    self.context.oauth_metadata = asm
                    break

        self.context.client_metadata.scope = _configured_or_discovered_scope(
            self.oauth_config,
            extract_scope_from_www_auth(response),
            self.context.protected_resource_metadata,
            self.context.oauth_metadata,
        )

        self._discard_stale_client_info()
        if not self.context.client_info:
            if should_use_client_metadata_url(
                self.context.oauth_metadata,
                self.context.client_metadata_url,
            ):
                client_metadata_url = cast(str, self.context.client_metadata_url)
                client_information = create_client_info_from_metadata_url(
                    client_metadata_url,
                    redirect_uris=self.context.client_metadata.redirect_uris,
                )
                self.context.client_info = client_information
                await self.context.storage.set_client_info(client_information)
            else:
                registration_request = create_client_registration_request(
                    self.context.oauth_metadata,
                    self.context.client_metadata,
                    self.context.get_authorization_base_url(self.context.server_url),
                )
                registration_response = yield registration_request
                await registration_response.aread()
                client_information = await handle_registration_response(registration_response)
                self.context.client_info = client_information
                await self.context.storage.set_client_info(client_information)

        token_response = yield await self._perform_authorization()
        await token_response.aread()
        await self._handle_token_response(token_response)


def _configured_or_discovered_scope(
    oauth: OAuthConfig,
    www_authenticate_scope: str | None,
    protected_resource_metadata: Any,
    authorization_server_metadata: Any,
) -> str | None:
    if oauth.scopes:
        return " ".join(oauth.scopes)
    return get_client_metadata_scopes(
        www_authenticate_scope,
        protected_resource_metadata,
        authorization_server_metadata,
    )


async def _get_token_expiry_time(storage: TokenStorage) -> float | None:
    get_token_expiry_time = getattr(storage, "get_token_expiry_time", None)
    if get_token_expiry_time is None:
        return None
    return await get_token_expiry_time()


def _client_info_has_stale_redirect_uri(
    client_info: OAuthClientInformationFull,
    client_metadata: OAuthClientMetadata,
) -> bool:
    if not client_info.redirect_uris or not client_metadata.redirect_uris:
        return False
    registered_redirect_uris = {str(uri) for uri in client_info.redirect_uris}
    current_redirect_uris = {str(uri) for uri in client_metadata.redirect_uris}
    return not current_redirect_uris.issubset(registered_redirect_uris)


def _has_bearer_challenge(response: httpx.Response) -> bool:
    return any(
        "bearer" in value.lower()
        for value in response.headers.get_list("www-authenticate")
    )


async def _ignore_intermediate_response_body(response: httpx.Response) -> None:
    # HTTPX drains intermediate auth responses after this auth flow yields the
    # next request. The OAuth challenge is header-only, so avoid depending on
    # response-body framing for streamed MCP responses.
    response._content = b""
    await response.aclose()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.write("\n")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


@dataclass
class OAuthCallbackResult:
    code: str
    state: str | None
    error: str | None = None


class OAuthCallbackServer:
    """Small loopback server used for browser OAuth redirects."""

    def __init__(self, server: Any, thread: threading.Thread, redirect_uri: str, result: queue.Queue):
        self._server = server
        self._thread = thread
        self.redirect_uri = redirect_uri
        self._result = result

    @classmethod
    def start(cls, configured_redirect_uri: str | None = None) -> OAuthCallbackServer:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        path = CALLBACK_PATH
        listen_host = "127.0.0.1"
        listen_port = 0
        redirect_uri = ""

        if configured_redirect_uri:
            parsed = urlparse(configured_redirect_uri)
            if parsed.scheme != "http" or not parsed.hostname:
                raise ValueError("MCP OAuth redirect_uri must be an http loopback URL")
            if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("MCP OAuth redirect_uri must use a loopback host")
            listen_host = parsed.hostname
            listen_port = parsed.port if parsed.port is not None else 80
            path = parsed.path or CALLBACK_PATH
            redirect_uri = configured_redirect_uri

        result: queue.Queue[OAuthCallbackResult] = queue.Queue(maxsize=1)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                parsed = urlparse(self.path)
                if parsed.path != path:
                    self.send_error(404)
                    return
                query = parse_qs(parsed.query)
                callback_result = OAuthCallbackResult(
                    code=query.get("code", [""])[0],
                    state=query.get("state", [None])[0],
                    error=query.get("error", [None])[0],
                )
                with contextlib.suppress(queue.Full):
                    result.put_nowait(callback_result)
                body = (
                    "<!doctype html><html><body><h1>Authorization complete</h1>"
                    "<p>You can close this window and return to maco.</p>"
                    "<script>window.close();</script></body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return

        server = ThreadingHTTPServer((listen_host, listen_port), Handler)
        host, port = server.server_address[:2]
        if host == "0.0.0.0":
            host = "127.0.0.1"
        if not redirect_uri:
            redirect_uri = f"http://{host}:{port}{path}"
        elif listen_port == 0:
            redirect_uri = f"http://{host}:{port}{path}"
        thread = threading.Thread(target=server.serve_forever, name="maco-oauth-callback", daemon=True)
        thread.start()
        return cls(server, thread, redirect_uri, result)

    async def wait(self, *, timeout: float = DEFAULT_CALLBACK_TIMEOUT) -> tuple[str, str | None]:
        try:
            result = await asyncio.to_thread(self._result.get, True, timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"Timed out waiting {timeout:g} seconds for OAuth callback") from exc
        if result.error:
            raise RuntimeError(f"OAuth authorization failed: {html.escape(result.error)}")
        return result.code, result.state

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _ClosingOAuthAuth(httpx.Auth):
    """HTTPX auth wrapper that owns the callback server lifecycle."""

    requires_response_body = False

    def __init__(self, inner: MacoOAuthClientProvider, callback: OAuthCallbackServer):
        self.inner = inner
        self.callback = callback

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        flow = self.inner.async_auth_flow(request)
        try:
            next_request = await flow.__anext__()
            while True:
                response = yield next_request
                try:
                    next_request = await flow.asend(response)
                except StopAsyncIteration:
                    break
        finally:
            await flow.aclose()

    def auth_flow(self, request: httpx.Request):
        raise RuntimeError("maco MCP OAuth only supports async HTTP clients")

    def close(self) -> None:
        self.callback.close()
