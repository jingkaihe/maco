"""Docker sandbox provider."""

from __future__ import annotations

import os
import subprocess

from ..core import SandboxContext, SandboxExec, SandboxRunResult, translate_loopback_url
from .base import BaseSandboxProvider


class DockerSandboxProvider(BaseSandboxProvider):
    """Run commands in a one-shot Docker container."""

    guest_workspace = "/maco"
    guest_scratch = "/workspace"

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

    def run(self, request: SandboxExec) -> SandboxRunResult:
        self.context.scratch.mkdir(parents=True, exist_ok=True)
        gateway_url = translate_loopback_url(self.context.gateway.url, self.gateway_host)
        env = self._guest_env(request.env, gateway_url=gateway_url)
        host_env = os.environ.copy()
        command = [self.docker_binary, "run", "--rm"]
        if self.network:
            command.extend(["--network", self.network])
        command.extend(["--add-host", f"{self.gateway_host}:host-gateway"])
        for key, value in sorted(env.items()):
            if key == "MACO_GATEWAY_TOKEN" and self.context.gateway.token:
                # Keep gateway bearer tokens out of process argv. Docker's
                # `-e NAME` form copies the value from the docker client's
                # environment into the container.
                host_env[key] = self.context.gateway.token
                command.extend(["-e", key])
            else:
                command.extend(["-e", f"{key}={value}"])
        command.extend(
            [
                "-v",
                f"{self.context.workspace}:{self.guest_workspace}:ro",
                "-v",
                f"{self.context.scratch}:{self.guest_scratch}",
                "-w",
                self.guest_scratch,
                self.image,
                "sh",
                "-lc",
                request.command,
            ]
        )
        completed = subprocess.run(
            command,
            env=host_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self._timeout(request),
            check=False,
        )
        return SandboxRunResult(completed.returncode, completed.stdout, completed.stderr, command)
