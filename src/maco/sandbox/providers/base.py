"""Shared helpers for concrete sandbox providers."""

from __future__ import annotations

import shlex
from typing import Mapping

from ..core import SandboxContext, SandboxExec, normalize_context, posix_join


class BaseSandboxProvider:
    guest_workspace: str
    guest_scratch: str

    def __init__(self, context: SandboxContext) -> None:
        self.context = normalize_context(context)

    def python_script_command(self, guest_script_path: str, args: list[str]) -> str:
        command = self.context.python_command or f"uv run --project {shlex.quote(self.guest_workspace)} python"
        return " ".join([command, shlex.quote(guest_script_path), *[shlex.quote(arg) for arg in args]])

    def _timeout(self, request: SandboxExec) -> int:
        return request.timeout or self.context.timeout

    def _guest_env(self, request_env: Mapping[str, str], *, gateway_url: str) -> dict[str, str]:
        env = {
            "MACO_WORKSPACE": self.guest_workspace,
            "MACO_GATEWAY_FILE": posix_join(self.guest_workspace, "gateway.json"),
            "MACO_GATEWAY_URL": gateway_url,
            "PYTHONPATH": self.guest_workspace,
        }
        if self.context.gateway.token:
            env["MACO_GATEWAY_TOKEN"] = self.context.gateway.token
        env.update(request_env)
        return env
