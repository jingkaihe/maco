from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from maco.sandbox import (
    DockerSandboxProvider,
    GatewayInfo,
    LocalSandboxProvider,
    MatchlockSandboxProvider,
    SandboxContext,
    SandboxError,
    SandboxExec,
    SandboxRunResult,
    guest_path_for,
    translate_loopback_url,
    write_code_file,
)
import maco.sandbox.providers.docker as docker_provider
import maco.sandbox.providers.matchlock as matchlock_provider


def test_translate_loopback_url_for_guest_hosts():
    assert (
        translate_loopback_url("http://127.0.0.1:4789/", "host.docker.internal")
        == "http://host.docker.internal:4789/"
    )
    assert (
        translate_loopback_url("http://localhost:4789/path?x=1", "maco-gateway.internal")
        == "http://maco-gateway.internal:4789/path?x=1"
    )
    assert translate_loopback_url("http://gateway.example:4789/", "ignored") == "http://gateway.example:4789/"


def test_local_provider_injects_gateway_and_pythonpath(tmp_path):
    context = _context(tmp_path)
    provider = LocalSandboxProvider(context)

    result = provider.run(
        SandboxExec(
            command="python - <<'PY'\nimport os\nprint(os.environ['MACO_GATEWAY_URL'])\nprint(os.environ['MACO_GATEWAY_TOKEN'])\nprint(os.environ['MACO_WORKSPACE'])\nPY"
        )
    )

    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines() == [
        "http://127.0.0.1:9/",
        "secret-token",
        str(context.workspace),
    ]


def test_docker_provider_builds_guest_reachable_gateway_command(tmp_path, monkeypatch):
    context = _context(tmp_path)
    provider = DockerSandboxProvider(
        context,
        image="maco-test:latest",
        docker_binary="docker-test",
        gateway_host="host.docker.internal",
    )
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(docker_provider.subprocess, "run", fake_run)

    result = provider.run(SandboxExec(command="echo hi", timeout=7))

    assert result.ok
    command = captured["command"]
    assert command[:3] == ["docker-test", "run", "--rm"]
    assert ["--add-host", "host.docker.internal:host-gateway"] == command[3:5]
    assert "-e" in command
    assert "MACO_GATEWAY_URL=http://host.docker.internal:9/" in command
    assert "MACO_GATEWAY_TOKEN" in command
    assert "MACO_GATEWAY_TOKEN=secret-token" not in command
    assert captured["kwargs"]["env"]["MACO_GATEWAY_TOKEN"] == "secret-token"
    assert f"{context.scratch}:/workspace" in command
    assert f"{context.workspace}:/workspace/.maco:ro" in command
    assert command[-4:] == ["maco-test:latest", "sh", "-lc", "echo hi"]
    assert captured["kwargs"]["timeout"] == 7


def test_matchlock_provider_uses_sdk_builder_without_leaking_token(tmp_path, monkeypatch):
    context = _context(tmp_path)
    provider = MatchlockSandboxProvider(
        context,
        image="maco-test:latest",
        matchlock_binary="matchlock-test",
        gateway_host="maco-gateway.internal",
        extra_allow_hosts=["api.example.com"],
    )
    captured: dict[str, Any] = {}

    class FakeSandbox:
        def __init__(self, image: str) -> None:
            captured["image"] = image
            self.env: dict[str, str] = {}
            self.allowed: list[str] = []
            self.added_hosts: list[tuple[str, str]] = []
            self.secrets: list[tuple[str, str, str, tuple[str, ...]]] = []
            self.mounts: list[tuple[str, str, str, bool]] = []

        def with_workspace(self, path: str) -> FakeSandbox:
            captured["workspace"] = path
            return self

        def with_env_map(self, env: dict[str, str]) -> FakeSandbox:
            self.env.update(env)
            return self

        def with_env(self, name: str, value: str) -> FakeSandbox:
            self.env[name] = value
            return self

        def allow_host(self, host: str) -> FakeSandbox:
            self.allowed.append(host)
            return self

        def add_host(self, host: str, ip: str) -> FakeSandbox:
            self.added_hosts.append((host, ip))
            return self

        def add_secret_with_placeholder(
            self, name: str, value: str, placeholder: str, *hosts: str
        ) -> FakeSandbox:
            self.secrets.append((name, value, placeholder, hosts))
            return self

        def mount_host_dir_readonly(self, guest_path: str, host_path: str) -> FakeSandbox:
            self.mounts.append((guest_path, host_path, "host_fs", True))
            return self

        def mount_host_dir(self, guest_path: str, host_path: str) -> FakeSandbox:
            self.mounts.append((guest_path, host_path, "host_fs", False))
            return self

    class FakeConfig:
        def __init__(self, binary_path: str) -> None:
            captured["binary_path"] = binary_path

    class FakeClient:
        def __init__(self, config: FakeConfig) -> None:
            captured["config"] = config

        def start(self) -> None:
            captured["started"] = True

        def close(self) -> None:
            captured["closed"] = True

        def launch(self, spec: FakeSandbox) -> str:
            captured["spec"] = spec
            return "vm-test"

        def exec(self, command: str, *, working_dir: str, timeout: int) -> SandboxRunResult:
            captured.setdefault("execs", []).append((command, working_dir, timeout))
            return SandboxRunResult(0, "ok", "", ["inner"])

        def remove(self) -> None:
            captured["removed"] = True

    monkeypatch.setattr(matchlock_provider, "_load_matchlock_sdk", lambda: (FakeClient, FakeConfig, FakeSandbox))

    result = provider.run(SandboxExec(command="python task.py", timeout=11))

    assert result.ok
    spec = captured["spec"]
    assert captured["image"] == "maco-test:latest"
    assert captured["binary_path"] == "matchlock-test"
    assert captured["workspace"] == "/workspace"
    assert spec.allowed == ["api.example.com", "maco-gateway.internal"]
    assert spec.added_hosts == []
    assert spec.env["MACO_GATEWAY_URL"] == "http://maco-gateway.internal:9/"
    assert spec.env["MACO_GATEWAY_TOKEN"] == "MACO_GATEWAY_TOKEN_PLACEHOLDER"
    assert spec.secrets == [
        (
            "MACO_GATEWAY_TOKEN",
            "secret-token",
            "MACO_GATEWAY_TOKEN_PLACEHOLDER",
            ("maco-gateway.internal",),
        )
    ]
    assert ("/workspace/.maco", str(context.workspace), "host_fs", True) in spec.mounts
    assert ("/workspace", str(context.scratch), "host_fs", False) in spec.mounts
    assert captured["execs"] == [("python task.py", "/workspace", 11)]
    assert captured["removed"] is True
    assert not any("secret-token" in part for part in result.command)


def test_matchlock_provider_uses_explicit_gateway_ip_mapping(tmp_path, monkeypatch):
    context = _context(tmp_path)
    provider = MatchlockSandboxProvider(
        context,
        image="maco-test:latest",
        gateway_ip="192.0.2.10",
    )
    captured: dict[str, Any] = {}

    class FakeSandbox:
        def __init__(self, _image: str) -> None:
            self.added_hosts: list[tuple[str, str]] = []
            self.allowed: list[str] = []
            self.secrets: list[tuple[str, ...]] = []

        def with_workspace(self, _path: str) -> FakeSandbox:
            return self

        def with_env_map(self, _env: dict[str, str]) -> FakeSandbox:
            return self

        def with_env(self, _name: str, _value: str) -> FakeSandbox:
            return self

        def allow_host(self, host: str) -> FakeSandbox:
            self.allowed.append(host)
            return self

        def add_host(self, host: str, ip: str) -> FakeSandbox:
            self.added_hosts.append((host, ip))
            return self

        def add_secret_with_placeholder(self, *args: str) -> FakeSandbox:
            self.secrets.append(args)
            return self

        def mount_host_dir_readonly(self, _guest_path: str, _host_path: str) -> FakeSandbox:
            return self

        def mount_host_dir(self, _guest_path: str, _host_path: str) -> FakeSandbox:
            return self

    class FakeConfig:
        def __init__(self, binary_path: str) -> None:
            captured["binary_path"] = binary_path

    class FakeClient:
        def __init__(self, _config: FakeConfig) -> None:
            pass

        def start(self) -> None:
            pass

        def close(self) -> None:
            pass

        def launch(self, spec: FakeSandbox) -> str:
            captured["spec"] = spec
            return "vm-test"

        def exec(self, command: str, *, working_dir: str, timeout: int) -> SandboxRunResult:
            captured.setdefault("execs", []).append((command, working_dir, timeout))
            return SandboxRunResult(0, "ok", "", ["inner"])

        def remove(self) -> None:
            pass

    monkeypatch.setattr(matchlock_provider, "_load_matchlock_sdk", lambda: (FakeClient, FakeConfig, FakeSandbox))
    result = provider.run(SandboxExec(command="true"))

    assert captured["spec"].added_hosts == [("maco-gateway.internal", "192.0.2.10")]
    assert captured["spec"].allowed == []
    assert captured["spec"].secrets == []
    assert captured["execs"] == [("true", "/workspace", context.timeout)]
    assert "hosts=192.0.2.10:maco-gateway.internal" in result.command


def test_write_code_file_and_guest_path_are_constrained(tmp_path):
    scratch = tmp_path / "scratch"
    path = write_code_file(scratch, "nested/task.py", "print('ok')\n")

    assert path.read_text(encoding="utf-8") == "print('ok')\n"
    assert guest_path_for(path, scratch, "/workspace") == "/workspace/nested/task.py"
    with pytest.raises(SandboxError, match="relative path"):
        write_code_file(scratch, "../escape.py", "")


def _context(tmp_path: Path) -> SandboxContext:
    workspace = tmp_path / ".maco"
    (workspace / "maco_generated").mkdir(parents=True)
    (workspace / "maco_generated" / "client.py").write_text("", encoding="utf-8")
    (workspace / "gateway.json").write_text(
        json.dumps({"url": "http://127.0.0.1:9/", "token": "secret-token"}),
        encoding="utf-8",
    )
    return SandboxContext(
        workspace=workspace.resolve(),
        scratch=(tmp_path / "scratch").resolve(),
        gateway=GatewayInfo.from_file(workspace / "gateway.json"),
    )
