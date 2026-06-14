"""Docker sandbox provider."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

from ..core import (
    SANDBOX_SDK_ROOT,
    SANDBOX_USER,
    SandboxContext,
    SandboxError,
    SandboxExec,
    SandboxRunResult,
    translate_loopback_url,
)
from .base import RemoteSandboxProvider


class DockerSandboxProvider(RemoteSandboxProvider):
    """Run commands inside one long-lived Docker container."""

    def __init__(
        self,
        context: SandboxContext,
        *,
        image: str,
        docker_binary: str = "docker",
        network: str | None = None,
        gateway_host: str = "host.docker.internal",
        gateway_ip: str | None = None,
    ) -> None:
        super().__init__(context)
        self.image = image
        self.docker_binary = docker_binary
        self.network = network
        self.gateway_host = gateway_host
        self.gateway_ip = gateway_ip
        self.container_name = self._container_name()
        self.container_id: str | None = None
        self._start_attempted = False

    def start(self) -> None:
        if self.container_id is not None:
            return
        gateway_url = _docker_gateway_url(
            self.context.gateway.url,
            gateway_host=self.gateway_host,
            gateway_ip=self.gateway_ip,
        )
        env = self._guest_env({}, gateway_url=gateway_url)
        host_env = os.environ.copy()
        command = [
            self.docker_binary,
            "run",
            "-d",
            "--rm",
            "--user",
            SANDBOX_USER,
            "--name",
            self.container_name,
            "--label",
            "org.opencontainers.image.title=maco-sandbox",
            "--label",
            "maco.managed=true",
        ]
        if self.network:
            command.extend(["--network", self.network])
        if self.gateway_ip:
            command.extend(["--add-host", f"{self.gateway_host}:{self.gateway_ip}"])
        for key, value in sorted(env.items()):
            if key == "MACO_GATEWAY_TOKEN" and self.context.gateway.token:
                host_env[key] = self.context.gateway.token
                command.extend(["-e", key])
            else:
                command.extend(["-e", f"{key}={value}"])
        command.extend(["-w", self.guest_scratch, self.image])
        self._start_attempted = True
        try:
            completed = subprocess.run(
                command,
                env=host_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.context.timeout,
                check=False,
            )
            if completed.returncode != 0:
                raise SandboxError(f"failed to start Docker sandbox: {completed.stderr.strip()}")
            self.container_id = completed.stdout.strip()
            self._bootstrap_sdk()
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        if self.container_id is None and not self._start_attempted:
            return
        target = self.container_id or self.container_name
        try:
            subprocess.run(
                [self.docker_binary, "rm", "-f", target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except Exception:
            pass
        finally:
            self.container_id = None
            self._start_attempted = False

    def run(self, request: SandboxExec) -> SandboxRunResult:
        self.start()
        assert self.container_id is not None
        command = [
            self.docker_binary,
            "exec",
            "--user",
            SANDBOX_USER,
            "-w",
            self.guest_scratch,
            self.container_id,
            "sh",
            "-lc",
            request.command,
        ]
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self._timeout(request),
            check=False,
        )
        if self.context.debug:
            print(f"maco docker command: {self._command_summary(command)!r}", file=sys.stderr)
        return SandboxRunResult(completed.returncode, completed.stdout, completed.stderr)

    def write_file(self, relative_path: str, content: str) -> str:
        self.start()
        assert self.container_id is not None
        guest_path = self._guest_scratch_path(relative_path)
        parent = guest_path.rsplit("/", 1)[0]
        command = [
            self.docker_binary,
            "exec",
            "-i",
            "--user",
            SANDBOX_USER,
            self.container_id,
            "sh",
            "-lc",
            f"mkdir -p {shlex.quote(parent)} && cat > {shlex.quote(guest_path)}",
        ]
        completed = subprocess.run(
            command,
            input=content,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.context.timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise SandboxError(f"failed to write Docker sandbox file {guest_path}: {completed.stderr.strip()}")
        return guest_path

    def _bootstrap_sdk(self) -> None:
        assert self.container_id is not None
        command = [
            self.docker_binary,
            "exec",
            "--user",
            SANDBOX_USER,
            self.container_id,
            "maco",
            "sandbox-bootstrap",
            "--workspace",
            SANDBOX_SDK_ROOT,
        ]
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.context.timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise SandboxError(f"failed to bootstrap Docker sandbox SDK: {completed.stderr.strip()}")

    def _command_summary(self, command: list[str]) -> list[str]:
        redacted: list[str] = []
        for part in command:
            if self.context.gateway.token and part == self.context.gateway.token:
                redacted.append("<redacted>")
            else:
                redacted.append(part)
        return redacted

    def _container_name(self) -> str:
        return f"maco-sandbox-{uuid.uuid4().hex[:12]}"


def _docker_gateway_url(url: str, *, gateway_host: str, gateway_ip: str | None) -> str:
    translated = translate_loopback_url(url, gateway_host)
    if gateway_ip and _url_host(translated) in {gateway_ip, "0.0.0.0"}:
        return _replace_url_host(translated, gateway_host)
    return translated


def _url_host(url: str) -> str | None:
    return urlsplit(url).hostname


def _replace_url_host(url: str, host: str) -> str:
    parts = urlsplit(url)
    netloc = host
    if parts.port is not None:
        netloc = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
