"""Local subprocess sandbox provider."""

from __future__ import annotations

import os
import subprocess

from ..core import SandboxContext, SandboxExec, SandboxRunResult
from .base import BaseSandboxProvider


class LocalSandboxProvider(BaseSandboxProvider):
    """Run commands as local subprocesses with maco env injected."""

    default_python_command = "uv run python"
    guest_workspace: str
    guest_scratch: str

    def __init__(self, context: SandboxContext) -> None:
        super().__init__(context)
        self.guest_workspace = str(self.context.workspace)
        self.guest_scratch = str(self.context.scratch)

    def run(self, request: SandboxExec) -> SandboxRunResult:
        self.context.scratch.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        injected = self._guest_env(request.env, gateway_url=self.context.gateway.url)
        existing_pythonpath = env.get("PYTHONPATH")
        if existing_pythonpath:
            injected["PYTHONPATH"] = os.pathsep.join([self.guest_workspace, existing_pythonpath])
        env.update(injected)
        command = ["sh", "-lc", request.command]
        completed = subprocess.run(
            command,
            cwd=str(self.context.scratch),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self._timeout(request),
            check=False,
        )
        return SandboxRunResult(completed.returncode, completed.stdout, completed.stderr, command)
