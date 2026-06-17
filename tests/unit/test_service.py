from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import maco.service as service
from maco.service import ServiceError, ServiceSpec, find_available_port, service_id, start_detached


def test_service_id_is_project_scoped_and_stable():
    project = Path("/tmp/example-app")
    workspace = project / ".maco"

    assert service_id(project, workspace) == service_id(project, workspace)
    assert service_id(project, workspace).startswith("example-app-")
    assert service_id(Path("/other/example-app"), Path("/other/example-app/.maco")) != service_id(
        project,
        workspace,
    )


def test_find_available_port_skips_excluded(monkeypatch):
    unavailable = {8789, 8790}
    monkeypatch.setattr(service, "_is_port_available", lambda _host, port: port not in unavailable)

    assert find_available_port("127.0.0.1", excluded={8791}) == 8792


def test_start_detached_auto_assigns_port_and_writes_spec(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "mcp.json").write_text('{"mcpServers":{"echo":{"command":"echo"}}}', encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(service, "_is_port_available", lambda _host, port: port != 8789)

    spawned = {}

    def fake_spawn(spec: ServiceSpec):
        spawned["command"] = spec.command
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(service, "_spawn_detached", fake_spawn)

    spec = start_detached(_args())

    assert spec.port == 8790
    assert spec.pid == 12345
    assert spec.url == "http://127.0.0.1:8790/mcp"
    port_flag = spawned["command"].index("--port")
    assert spawned["command"][port_flag : port_flag + 2] == ["--port", "8790"]

    data = json.loads((tmp_path / "home" / ".maco" / "state" / "instances" / spec.id / "spec.json").read_text())
    assert data["pid"] == 12345
    assert data["port"] == 8790


def test_start_detached_is_idempotent_when_existing_process_matches(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "mcp.json").write_text('{"mcpServers":{"echo":{"command":"echo"}}}', encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(service, "_is_port_available", lambda _host, _port: True)
    monkeypatch.setattr(service.os, "kill", lambda _pid, _signal: None)
    monkeypatch.setattr(service, "_pid_matches_spec", lambda _spec: True)

    spawn_count = 0

    def fake_spawn(_spec: ServiceSpec):
        nonlocal spawn_count
        spawn_count += 1
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(service, "_spawn_detached", fake_spawn)

    first = start_detached(_args())
    second = start_detached(_args())

    assert first == second
    assert spawn_count == 1


def test_start_detached_restarts_when_existing_options_change(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "mcp.json").write_text('{"mcpServers":{"echo":{"command":"echo"}}}', encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(service, "_is_port_available", lambda _host, _port: True)
    monkeypatch.setattr(service, "_process_state", lambda spec: "running" if spec and spec.pid else "stopped")

    stopped = []

    def fake_stop(spec: ServiceSpec):
        stopped.append(spec.pid)

    pids = iter([111, 222])

    monkeypatch.setattr(service, "_stop_process", fake_stop)
    monkeypatch.setattr(service, "_spawn_detached", lambda _spec: SimpleNamespace(pid=next(pids)))

    first = start_detached(_args(provider="local"))
    second = start_detached(_args(provider="docker"))

    assert first.pid == 111
    assert second.pid == 222
    assert second.provider == "docker"
    assert stopped == [111]


def test_stop_detached_removes_registry_and_sends_sigterm(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    spec = _spec(project, pid=12345)
    spec_dir = tmp_path / "home" / ".maco" / "state" / "instances" / spec.id
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.json").write_text(spec.model_dump_json(), encoding="utf-8")

    signals = []

    def fake_kill(pid: int, signum: int):
        signals.append((pid, signum))
        if signum == 0 and len(signals) > 2:
            raise ProcessLookupError

    monkeypatch.setattr(service.os, "kill", fake_kill)
    monkeypatch.setattr(service, "_pid_matches_spec", lambda _spec: True)
    monkeypatch.setattr(service.time, "sleep", lambda _seconds: None)

    service.stop_detached(_args())

    assert (12345, service.signal.SIGTERM) in signals
    assert not spec_dir.exists()


def test_list_services_prints_project_rows(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    spec = _spec(project, pid=None)
    spec_dir = tmp_path / "home" / ".maco" / "state" / "instances" / spec.id
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.json").write_text(spec.model_dump_json(), encoding="utf-8")

    rows = service.list_services()

    assert rows == [(spec, "stopped")]
    out = capsys.readouterr().out
    assert "NAME" in out
    assert spec.id in out
    assert str(project) in out


def test_process_state_marks_reused_pid_as_stale(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    spec = _spec(project, pid=12345)
    monkeypatch.setattr(service.os, "kill", lambda _pid, _signal: None)

    completed = SimpleNamespace(returncode=0, stdout="python -m something.else\n")
    monkeypatch.setattr(service.subprocess, "run", lambda *_args, **_kwargs: completed)

    assert service._process_state(spec) == "stale"


def test_process_state_accepts_maco_internal_mcp_server_pid(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    spec = _spec(project, pid=12345)
    monkeypatch.setattr(service.os, "kill", lambda _pid, _signal: None)

    completed = SimpleNamespace(returncode=0, stdout="python -m maco.cli _mcp-server --port 8789\n")
    monkeypatch.setattr(service.subprocess, "run", lambda *_args, **_kwargs: completed)

    assert service._process_state(spec) == "running"


def test_explicit_busy_port_is_rejected(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "mcp.json").write_text('{"mcpServers":{"echo":{"command":"echo"}}}', encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(service, "_is_port_available", lambda _host, _port: False)

    with pytest.raises(ServiceError, match="port 9000 is already in use"):
        start_detached(_args(port=9000))


def _args(**overrides):
    values = {
        "config": "mcp.json",
        "provider": "local",
        "workspace": ".maco",
        "clean": False,
        "scratch": None,
        "gateway_file": None,
        "gateway_host": None,
        "gateway_port": 0,
        "gateway_token": None,
        "no_gateway_token": False,
        "host": "127.0.0.1",
        "port": None,
        "timeout": 60,
        "debug": False,
        "image": None,
        "python_command": None,
        "docker_binary": "docker",
        "docker_network": None,
        "docker_gateway_host": "host.docker.internal",
        "docker_gateway_ip": None,
        "matchlock_binary": "matchlock",
        "matchlock_gateway_host": "maco-gateway.internal",
        "matchlock_gateway_ip": None,
        "matchlock_allow_host": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _spec(project: Path, *, pid: int | None) -> ServiceSpec:
    instance_id = service_id(project.resolve(), (project / ".maco").resolve())
    return ServiceSpec(
        id=instance_id,
        service_name=f"maco-{instance_id}",
        project_dir=str(project.resolve()),
        config=str((project / "mcp.json").resolve()),
        workspace=str((project / ".maco").resolve()),
        host="127.0.0.1",
        port=8789,
        url="http://127.0.0.1:8789/mcp",
        provider="local",
        command=["python", "-m", "maco.cli", "_mcp-server"],
        pid=pid,
        stdout_log=str((project / "out.log").resolve()),
        stderr_log=str((project / "err.log").resolve()),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
