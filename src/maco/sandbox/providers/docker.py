"""Docker sandbox provider."""

from __future__ import annotations

import os
import shlex
import subprocess

from ..core import SANDBOX_SDK_ROOT, SandboxContext, SandboxError, SandboxExec, SandboxRunResult, translate_loopback_url
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
    ) -> None:
        super().__init__(context)
        self.image = image
        self.docker_binary = docker_binary
        self.network = network
        self.gateway_host = gateway_host
        self.container_id: str | None = None

    def start(self) -> None:
        if self.container_id is not None:
            return
        gateway_url = translate_loopback_url(self.context.gateway.url, self.gateway_host)
        env = self._guest_env({}, gateway_url=gateway_url)
        host_env = os.environ.copy()
        command = [self.docker_binary, "run", "-d", "--rm"]
        if self.network:
            command.extend(["--network", self.network])
        command.extend(["--add-host", f"{self.gateway_host}:host-gateway"])
        for key, value in sorted(env.items()):
            if key == "MACO_GATEWAY_TOKEN" and self.context.gateway.token:
                host_env[key] = self.context.gateway.token
                command.extend(["-e", key])
            else:
                command.extend(["-e", f"{key}={value}"])
        command.extend(["-w", self.guest_scratch, self.image])
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
        try:
            self._bootstrap_sdk()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        if self.container_id is None:
            return
        subprocess.run(
            [self.docker_binary, "rm", "-f", self.container_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        self.container_id = None

    def run(self, request: SandboxExec) -> SandboxRunResult:
        self.start()
        assert self.container_id is not None
        command = [
            self.docker_binary,
            "exec",
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
        return SandboxRunResult(completed.returncode, completed.stdout, completed.stderr, self._command_summary(command))

    def write_file(self, relative_path: str, content: str) -> str:
        self.start()
        assert self.container_id is not None
        guest_path = self._guest_scratch_path(relative_path)
        parent = guest_path.rsplit("/", 1)[0]
        command = [
            self.docker_binary,
            "exec",
            "-i",
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
