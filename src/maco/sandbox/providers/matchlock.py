"""Matchlock sandbox provider."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..core import SandboxContext, SandboxError, SandboxExec, SandboxRunResult, translate_loopback_url
from .base import BaseSandboxProvider


class MatchlockSandboxProvider(BaseSandboxProvider):
    """Run commands in a one-shot Matchlock micro-VM."""

    # Matchlock requires mounted guest paths to live under the configured
    # workspace, so place the generated maco package inside `/workspace` rather
    # than beside it.
    guest_workspace = "/workspace/.maco"
    guest_scratch = "/workspace"

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

    def run(self, request: SandboxExec) -> SandboxRunResult:
        Client, Config, Sandbox = _load_matchlock_sdk()

        self.context.scratch.mkdir(parents=True, exist_ok=True)
        gateway_url = translate_loopback_url(self.context.gateway.url, self.gateway_host)
        env = self._guest_env(request.env, gateway_url=gateway_url)
        gateway_policy_host = _url_host(gateway_url) or self.gateway_host
        gateway_mapping = (gateway_policy_host, self.gateway_ip) if self.gateway_ip else None
        if gateway_mapping is not None and self.extra_allow_hosts:
            raise SandboxError(
                "matchlock extra allow hosts cannot be combined with a mapped local gateway yet; "
                "Matchlock currently proxies all HTTP traffic when allow-hosts are configured"
            )

        spec = Sandbox(self.image).with_workspace(self.guest_scratch).with_env_map(env)
        if gateway_mapping is not None:
            spec.add_host(*gateway_mapping)
        allow_hosts = [*self.extra_allow_hosts]
        if gateway_mapping is None:
            allow_hosts.append(gateway_policy_host)
        for host in sorted(set(allow_hosts)):
            spec.allow_host(host)
        if self.context.gateway.token and gateway_mapping is None:
            # With Matchlock, prefer placeholder-based secret injection: the VM
            # sees only the placeholder, while the host-side network proxy
            # substitutes the real gateway token for requests to the gateway
            # host. This keeps the bearer token out of the guest environment and
            # out of process argv.
            placeholder = "MACO_GATEWAY_TOKEN_PLACEHOLDER"
            spec.with_env("MACO_GATEWAY_TOKEN", placeholder)
            spec.add_secret_with_placeholder(
                "MACO_GATEWAY_TOKEN",
                self.context.gateway.token,
                placeholder,
                gateway_policy_host,
            )

        spec.mount_host_dir_readonly(self.guest_workspace, str(self.context.workspace))
        spec.mount_host_dir(self.guest_scratch, str(self.context.scratch))

        config = Config(binary_path=self.matchlock_binary)
        client = Client(config)
        try:
            client.start()
            client.launch(spec)
            result = client.exec(
                request.command,
                working_dir=self.guest_scratch,
                timeout=self._timeout(request),
            )
        finally:
            client.close()
            try:
                client.remove()
            except Exception:
                # Cleanup failures should not mask the command result; Matchlock
                # GC/prune can reconcile leaked stopped state later.
                pass

        return SandboxRunResult(
            result.exit_code,
            result.stdout,
            result.stderr,
            _sdk_command_summary(
                self.matchlock_binary,
                self.image,
                request.command,
                gateway_url=gateway_url,
                allowed_hosts=sorted(set(allow_hosts)),
                gateway_mapping=gateway_mapping,
            ),
        )


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
