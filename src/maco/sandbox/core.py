"""Core sandbox types and provider factory."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit


class SandboxError(RuntimeError):
    """Raised when a sandbox provider cannot run a command."""


DEFAULT_SANDBOX_IMAGE = "ghcr.io/jingkaihe/maco:0.1.0-alpine"
DEFAULT_MATCHLOCK_GATEWAY_IP = "192.168.100.1"
SANDBOX_SDK_ROOT = "/workspace/macosdk"
SANDBOX_TOOLS_ROOT = f"{SANDBOX_SDK_ROOT}/tools"


@dataclass(frozen=True)
class GatewayInfo:
    """Host-side maco gateway coordinates."""

    url: str
    token: str | None = None

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> GatewayInfo:
        gateway_path = Path(path).expanduser()
        try:
            payload = json.loads(gateway_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SandboxError(f"gateway file not found: {gateway_path}") from exc
        except Exception as exc:
            raise SandboxError(f"failed to read gateway file {gateway_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SandboxError(f"gateway file {gateway_path} must contain a JSON object")
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            raise SandboxError(f"gateway file {gateway_path} must contain a non-empty url")
        token = payload.get("token")
        return cls(url=url, token=token if isinstance(token, str) and token else None)


@dataclass(frozen=True)
class SandboxRunResult:
    """Result from a sandbox command."""

    exit_code: int
    stdout: str
    stderr: str
    command: list[str]

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class SandboxContext:
    """Provider-independent paths and gateway details."""

    workspace: Path
    scratch: Path
    gateway: GatewayInfo
    timeout: int = 60
    python_command: str | None = None


@dataclass(frozen=True)
class SandboxExec:
    """Command request for a sandbox provider."""

    command: str
    timeout: int | None = None
    env: Mapping[str, str] = field(default_factory=dict)


class SandboxProvider(Protocol):
    """Common execution surface for local, Docker, and Matchlock sandboxes."""

    guest_workspace: str
    guest_scratch: str

    def start(self) -> None:
        """Start or bootstrap provider resources."""

    def stop(self) -> None:
        """Release provider resources."""

    def run(self, request: SandboxExec) -> SandboxRunResult:
        """Run a non-interactive command in the sandbox."""

    def write_file(self, relative_path: str, content: str) -> str:
        """Write a file inside sandbox scratch and return its guest path."""

    def python_script_command(self, guest_script_path: str, args: list[str]) -> str:
        """Build the shell command used by code_execute for a guest script."""


def provider_from_name(
    name: str,
    context: SandboxContext,
    *,
    image: str | None = None,
    docker_binary: str = "docker",
    docker_network: str | None = None,
    docker_gateway_host: str = "host.docker.internal",
    docker_gateway_ip: str | None = None,
    matchlock_binary: str = "matchlock",
    matchlock_gateway_host: str = "maco-gateway.internal",
    matchlock_gateway_ip: str | None = None,
    matchlock_extra_allow_hosts: list[str] | None = None,
) -> SandboxProvider:
    """Construct a sandbox provider from a CLI-friendly name."""

    # Imports are local to avoid importing provider subprocess dependencies when
    # callers only need the data types/helpers.
    from .providers.docker import DockerSandboxProvider
    from .providers.local import LocalSandboxProvider
    from .providers.matchlock import MatchlockSandboxProvider

    normalized = name.replace("_", "-").lower()
    if normalized == "local":
        return LocalSandboxProvider(context)
    if normalized == "docker":
        return DockerSandboxProvider(
            context,
            image=image or DEFAULT_SANDBOX_IMAGE,
            docker_binary=docker_binary,
            network=docker_network,
            gateway_host=docker_gateway_host,
            gateway_ip=docker_gateway_ip,
        )
    if normalized == "matchlock":
        return MatchlockSandboxProvider(
            context,
            image=image or DEFAULT_SANDBOX_IMAGE,
            matchlock_binary=matchlock_binary,
            gateway_host=matchlock_gateway_host,
            gateway_ip=matchlock_gateway_ip,
            extra_allow_hosts=matchlock_extra_allow_hosts or [],
        )
    raise SandboxError(f"unknown sandbox provider {name!r}; expected local, docker, or matchlock")


def write_code_file(scratch: Path, filename: str, code: str) -> Path:
    """Write code into the scratch directory after constraining the path."""

    if not filename.strip():
        raise SandboxError("filename must be non-empty")
    relative = Path(filename)
    if relative.is_absolute() or ".." in relative.parts:
        raise SandboxError("filename must be a relative path inside the sandbox scratch directory")
    path = scratch / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return path


def guest_path_for(local_path: Path, scratch: Path, guest_scratch: str) -> str:
    """Translate a local scratch path to the corresponding guest path."""

    try:
        relative = local_path.resolve().relative_to(scratch.resolve())
    except ValueError as exc:
        raise SandboxError(f"path {local_path} is not inside scratch directory {scratch}") from exc
    return posix_join(guest_scratch, relative.as_posix())


def translate_loopback_url(url: str, host: str) -> str:
    """Replace localhost in a gateway URL with a guest-reachable host alias."""

    parts = urlsplit(url)
    if parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return url
    netloc = host
    if parts.port is not None:
        netloc = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def normalize_context(context: SandboxContext) -> SandboxContext:
    workspace = context.workspace.expanduser().resolve()
    scratch = context.scratch.expanduser().resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    return SandboxContext(
        workspace=workspace,
        scratch=scratch,
        gateway=context.gateway,
        timeout=context.timeout,
        python_command=context.python_command,
    )


def posix_join(root: str, child: str) -> str:
    return f"{root.rstrip('/')}/{child.lstrip('/')}"
