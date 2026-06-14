"""Matchlock sandbox provider."""

from __future__ import annotations

import shlex
from typing import Any
from urllib.parse import urlsplit

from ..core import SANDBOX_SDK_ROOT, SandboxContext, SandboxError, SandboxExec, SandboxRunResult, translate_loopback_url
from .base import RemoteSandboxProvider


class MatchlockSandboxProvider(RemoteSandboxProvider):
    """Run commands inside one long-lived Matchlock micro-VM."""

    def __init__(
        self,
        context: SandboxContext,
        *,
        image: str,
        matchlock_binary: str = "matchlock",
        gateway_host: str = "maco-gateway.internal",
        gateway_ip: str | None = None,
        extra_allow_hosts: list[str] | None = None,
    ) -> None:
        super().__init__(context)
        self.image = image
        self.matchlock_binary = matchlock_binary
        self.gateway_host = gateway_host
        self.gateway_ip = gateway_ip
        self.extra_allow_hosts = extra_allow_hosts or []
        self.client: Any | None = None
        self.gateway_url = ""
        self.allowed_hosts: list[str] = []
        self.gateway_mapping: tuple[str, str] | None = None

    def start(self) -> None:
        if self.client is not None:
            return
        Client, Config, Sandbox = _load_matchlock_sdk()
        self.gateway_url = translate_loopback_url(self.context.gateway.url, self.gateway_host)
        env = self._guest_env({}, gateway_url=self.gateway_url)
        gateway_policy_host = _url_host(self.gateway_url) or self.gateway_host
        self.gateway_mapping = (gateway_policy_host, self.gateway_ip) if self.gateway_ip else None
        if self.gateway_mapping is not None and self.extra_allow_hosts:
            raise SandboxError(
                "matchlock extra allow hosts cannot be combined with a mapped local gateway yet; "
                "Matchlock currently proxies all HTTP traffic when allow-hosts are configured"
            )

        spec = Sandbox(self.image).with_workspace(self.guest_scratch).with_env_map(env)
        if self.gateway_mapping is not None:
            spec.add_host(*self.gateway_mapping)
        self.allowed_hosts = [*self.extra_allow_hosts]
        if self.gateway_mapping is None:
            self.allowed_hosts.append(gateway_policy_host)
        for host in sorted(set(self.allowed_hosts)):
            spec.allow_host(host)
        if self.context.gateway.token and self.gateway_mapping is None:
            placeholder = "MACO_GATEWAY_TOKEN_PLACEHOLDER"
            spec.with_env("MACO_GATEWAY_TOKEN", placeholder)
            spec.add_secret_with_placeholder(
                "MACO_GATEWAY_TOKEN",
                self.context.gateway.token,
                placeholder,
                gateway_policy_host,
            )
        spec.mount_memory(self.guest_scratch)

        config = Config(binary_path=self.matchlock_binary)
        client = Client(config)
        try:
            client.start()
            client.launch(spec)
            self.client = client
            self._bootstrap_sdk()
        except Exception:
            client.close()
            try:
                client.remove()
            except Exception:
                pass
            self.client = None
            raise

    def stop(self) -> None:
        if self.client is None:
            return
        client = self.client
        self.client = None
        client.close()
        try:
            client.remove()
        except Exception:
            pass

    def run(self, request: SandboxExec) -> SandboxRunResult:
        self.start()
        assert self.client is not None
        result = self.client.exec(
            request.command,
            working_dir=self.guest_scratch,
            timeout=self._timeout(request),
        )
        return SandboxRunResult(
            result.exit_code,
            result.stdout,
            result.stderr,
            _sdk_command_summary(
                self.matchlock_binary,
                self.image,
                request.command,
                gateway_url=self.gateway_url,
                allowed_hosts=sorted(set(self.allowed_hosts)),
                gateway_mapping=self.gateway_mapping,
            ),
        )

    def write_file(self, relative_path: str, content: str) -> str:
        self.start()
        assert self.client is not None
        guest_path = self._guest_scratch_path(relative_path)
        parent = guest_path.rsplit("/", 1)[0]
        self.client.exec(
            f"mkdir -p {shlex.quote(parent)}",
            working_dir=self.guest_scratch,
            timeout=self.context.timeout,
        )
        self.client.write_file(guest_path, content)
        return guest_path

    def _bootstrap_sdk(self) -> None:
        assert self.client is not None
        result = self.client.exec(
            f"maco sandbox-bootstrap --workspace {shlex.quote(SANDBOX_SDK_ROOT)}",
            working_dir=self.guest_scratch,
            timeout=self.context.timeout,
        )
        if result.exit_code != 0:
            raise SandboxError(f"failed to bootstrap Matchlock sandbox SDK: {result.stderr.strip()}")


def _sdk_command_summary(
    binary: str,
    image: str,
    command: str,
    *,
    gateway_url: str,
    allowed_hosts: list[str],
    gateway_mapping: tuple[str, str] | None = None,
) -> list[str]:
    """Return a non-secret summary for debugging/API responses."""

    summary: list[str] = [
        binary,
        "rpc",
        "launch",
        image,
        "exec",
        command,
        f"MACO_GATEWAY_URL={gateway_url}",
    ]
    if gateway_mapping is not None:
        host, ip = gateway_mapping
        summary.append(f"hosts={ip}:{host}")
    for host in allowed_hosts:
        summary.append(f"allow_host={host}")
    return summary


def _url_host(url: str) -> str | None:
    return urlsplit(url).hostname


def _load_matchlock_sdk() -> tuple[type[Any], type[Any], type[Any]]:
    try:
        from matchlock import Client, Config, Sandbox
    except ImportError as exc:  # pragma: no cover - depends on optional package availability
        raise SandboxError(
            "matchlock provider requires the Matchlock Python SDK; "
            "install `maco-sandbox[matchlock]` or `matchlock`"
        ) from exc
    return Client, Config, Sandbox
