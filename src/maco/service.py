"""Project-scoped background process management for ``maco up -d``."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Any

from pydantic import BaseModel, ConfigDict


DEFAULT_MCP_PORT = 8789
_MCP_SERVER_COMMAND = "_mcp-server"


class ServiceError(ValueError):
    """Raised when maco cannot manage a detached service."""


class ServiceSpec(BaseModel):
    """Persisted description of one project-scoped detached maco process."""

    model_config = ConfigDict(frozen=True)

    id: str
    service_name: str
    project_dir: str
    config: str
    workspace: str
    host: str
    port: int
    url: str
    provider: str
    command: list[str]
    pid: int | None
    stdout_log: str
    stderr_log: str
    created_at: str
    updated_at: str


def start_detached(args: Any) -> ServiceSpec:
    """Start the detached maco process for the current project."""

    project_dir = Path.cwd().resolve()
    config = _resolve_under_project(project_dir, getattr(args, "config", "mcp.json"))
    workspace = _resolve_under_project(project_dir, getattr(args, "workspace", ".maco"))
    if not config.exists():
        raise ServiceError(f"configuration file not found: {config}")

    instance_id = service_id(project_dir, workspace)
    existing = _load_spec(instance_id)
    existing_state = _process_state(existing) if existing is not None else "stopped"
    port = _select_detached_port(args, existing=existing, existing_state=existing_state, instance_id=instance_id)
    command = _serve_mcp_command(args, config=config, workspace=workspace, port=port)
    now = _now()
    spec = ServiceSpec(
        id=instance_id,
        service_name=f"maco-{instance_id}",
        project_dir=str(project_dir),
        config=str(config),
        workspace=str(workspace),
        host=str(getattr(args, "host", "127.0.0.1")),
        port=port,
        url=f"http://{getattr(args, 'host', '127.0.0.1')}:{port}/mcp",
        provider=str(getattr(args, "provider", "local")),
        command=command,
        pid=existing.pid if existing is not None and existing_state == "running" else None,
        stdout_log=str(_logs_root() / f"{instance_id}.out.log"),
        stderr_log=str(_logs_root() / f"{instance_id}.err.log"),
        created_at=existing.created_at if existing is not None else now,
        updated_at=now,
    )

    if existing is not None and existing_state == "running":
        if _spec_equivalent(existing, spec):
            print("maco is already running for this project")
            _print_spec_details(existing, state="running")
            return existing
        _stop_process(existing)

    _instance_dir(spec.id).mkdir(parents=True, exist_ok=True)
    _logs_root().mkdir(parents=True, exist_ok=True)
    process = _spawn_detached(spec)
    spec = _replace_pid(spec, process.pid)
    _write_spec(spec)
    print("Started maco detached process")
    _print_spec_details(spec, state="running")
    return spec


def stop_detached(args: Any) -> ServiceSpec | None:
    """Stop the detached maco process for the current project."""

    spec = _load_current_spec(args)
    if spec is None:
        print("maco is not running for this project")
        return None
    state = _process_state(spec)
    if state == "running":
        _stop_process(spec)
    shutil.rmtree(_instance_dir(spec.id), ignore_errors=True)
    print("Stopped maco detached process")
    _print_spec_details(spec, state="stopped")
    return spec


def show_status(args: Any) -> ServiceSpec | None:
    """Print detached process status for the current project."""

    spec = _load_current_spec(args)
    if spec is None:
        print("maco is not running for this project")
        print("Use `maco up -d` to start it, or `maco ls` to list other maco processes.")
        return None
    state = _process_state(spec)
    if state == "running":
        print("maco is running")
    else:
        print(f"maco is {state}")
    _print_spec_details(spec, state=state)
    return spec


def list_services() -> list[tuple[ServiceSpec, str]]:
    """Print all known detached maco processes."""

    specs = _load_all_specs()
    if not specs:
        print("No detached maco processes found")
        return []

    rows = [(spec, _process_state(spec)) for spec in specs]
    headers = ("NAME", "STATE", "URL", "PROJECT")
    table = [headers, *((spec.id, state, spec.url, _project_display(spec)) for spec, state in rows)]
    widths = [max(len(str(row[index])) for row in table) for index in range(len(headers))]
    for row_index, row in enumerate(table):
        print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))
        if row_index == 0:
            print("  ".join("-" * width for width in widths))
    return rows


def ensure_no_detached_service(args: Any) -> None:
    """Reject foreground ``maco up`` when this project already has a running daemon."""

    spec = _load_current_spec(args)
    if spec is None:
        return
    if _process_state(spec) != "running":
        return
    raise ServiceError(
        "maco is already running in detached mode for this project. "
        f"Use `maco status` to inspect {spec.url}, or `maco down` to stop it."
    )


def service_id(project_dir: Path, workspace: Path) -> str:
    """Return the stable project-scoped instance id for a project/workspace pair."""

    slug = _slug(project_dir.name or "project")
    digest = hashlib.sha256(f"{project_dir}\0{workspace}".encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def find_available_port(host: str, start: int = DEFAULT_MCP_PORT, *, excluded: set[int] | None = None) -> int:
    """Find an available TCP port, starting at ``start`` and skipping ``excluded``."""

    excluded = excluded or set()
    for port in range(max(1, start), 65536):
        if port in excluded:
            continue
        if _is_port_available(host, port):
            return port
    raise ServiceError(f"could not find an available port on {host!r} starting at {start}")


def _select_detached_port(
    args: Any,
    *,
    existing: ServiceSpec | None,
    existing_state: str = "stopped",
    instance_id: str,
) -> int:
    requested = getattr(args, "port", None)
    if requested and requested > 0:
        if existing is None or existing_state != "running" or requested != existing.port:
            _ensure_port_available(str(getattr(args, "host", "127.0.0.1")), requested)
        return int(requested)
    if existing is not None:
        if existing_state == "running" or _is_port_available(str(getattr(args, "host", "127.0.0.1")), existing.port):
            return existing.port
    used_ports = {spec.port for spec in _load_all_specs() if spec.id != instance_id and _process_state(spec) == "running"}
    return find_available_port(str(getattr(args, "host", "127.0.0.1")), DEFAULT_MCP_PORT, excluded=used_ports)


def _ensure_port_available(host: str, port: int) -> None:
    if not _is_port_available(host, port):
        raise ServiceError(f"port {port} is already in use on {host}")


def _is_port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
    except OSError:
        return False
    return True


def _serve_mcp_command(args: Any, *, config: Path, workspace: Path, port: int) -> list[str]:
    command = [sys.executable, "-m", "maco.cli", "_mcp-server"]
    values = vars(args) | {"config": config, "workspace": workspace, "port": port}
    for key, value in values.items():
        if key in {"command", "func", "detach"} or value is None or value is False or value == []:
            continue
        flag = f"--{key.replace('_', '-')}"
        if value is True:
            command.append(flag)
        elif isinstance(value, list):
            for item in value:
                command.extend([flag, str(item)])
        else:
            command.extend([flag, str(value)])
    return command


def _spawn_detached(spec: ServiceSpec) -> subprocess.Popen[Any]:
    stdout = Path(spec.stdout_log).open("ab")
    stderr = Path(spec.stderr_log).open("ab")
    try:
        kwargs: dict[str, Any] = {
            "cwd": spec.project_dir,
            "stdin": subprocess.DEVNULL,
            "stdout": stdout,
            "stderr": stderr,
            "close_fds": True,
            "start_new_session": True,
        }
        if sys.platform == "win32":  # pragma: no cover - detached mode unsupported in product, harmless fallback
            kwargs.pop("start_new_session")
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        return subprocess.Popen(spec.command, **kwargs)
    finally:
        stdout.close()
        stderr.close()


def _stop_process(spec: ServiceSpec) -> None:
    if spec.pid is None:
        return
    try:
        os.kill(spec.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _process_state(spec) != "running":
            return
        time.sleep(0.1)
    try:
        os.kill(spec.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _process_state(spec: ServiceSpec | None) -> str:
    if spec is None or spec.pid is None:
        return "stopped"
    try:
        os.kill(spec.pid, 0)
    except ProcessLookupError:
        return "stopped"
    except PermissionError:
        return "unknown"
    if not _pid_matches_spec(spec):
        return "stale"
    return "running"


def _pid_matches_spec(spec: ServiceSpec) -> bool:
    if spec.pid is None:
        return False
    proc_cmdline = Path(f"/proc/{spec.pid}/cmdline")
    if proc_cmdline.exists():
        try:
            parts = [part.decode() for part in proc_cmdline.read_bytes().split(b"\0") if part]
        except OSError:
            parts = []
        if parts:
            return _command_parts_match(parts, spec.command)

    try:
        completed = subprocess.run(
            ["ps", "-p", str(spec.pid), "-o", "command="],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return True
    if completed.returncode != 0:
        return False
    command_text = completed.stdout or ""
    return "maco.cli" in command_text and _MCP_SERVER_COMMAND in command_text


def _command_parts_match(actual: list[str], expected: list[str]) -> bool:
    if actual == expected:
        return True
    return "-m" in actual and "maco.cli" in actual and _MCP_SERVER_COMMAND in actual


def _write_spec(spec: ServiceSpec) -> None:
    path = _spec_path(spec.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _replace_pid(spec: ServiceSpec, pid: int) -> ServiceSpec:
    return spec.model_copy(update={"pid": pid, "updated_at": _now()})


def _load_current_spec(args: Any) -> ServiceSpec | None:
    project_dir = Path.cwd().resolve()
    workspace = _resolve_under_project(project_dir, getattr(args, "workspace", ".maco"))
    return _load_spec(service_id(project_dir, workspace))


def _load_spec(instance_id: str) -> ServiceSpec | None:
    path = _spec_path(instance_id)
    if not path.exists():
        return None
    try:
        return ServiceSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ServiceError(f"failed to read maco service spec {path}: {exc}") from exc


def _load_all_specs() -> list[ServiceSpec]:
    root = _instances_root()
    if not root.exists():
        return []
    specs: list[ServiceSpec] = []
    for path in sorted(root.glob("*/spec.json")):
        specs.append(ServiceSpec.model_validate_json(path.read_text(encoding="utf-8")))
    return specs


def _spec_equivalent(left: ServiceSpec, right: ServiceSpec) -> bool:
    return (
        left.service_name == right.service_name
        and left.project_dir == right.project_dir
        and left.config == right.config
        and left.workspace == right.workspace
        and left.host == right.host
        and left.port == right.port
        and left.url == right.url
        and left.provider == right.provider
        and left.command == right.command
    )


def _resolve_under_project(project_dir: Path, value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_dir / path
    return path.resolve()


def _state_root() -> Path:
    return Path.home() / ".maco" / "state"


def _instances_root() -> Path:
    return _state_root() / "instances"


def _instance_dir(instance_id: str) -> Path:
    return _instances_root() / instance_id


def _spec_path(instance_id: str) -> Path:
    return _instance_dir(instance_id) / "spec.json"


def _logs_root() -> Path:
    return _state_root() / "logs"


def _project_display(spec: ServiceSpec) -> str:
    suffix = "" if Path(spec.project_dir).exists() else " (missing)"
    return f"{spec.project_dir}{suffix}"


def _print_spec_details(spec: ServiceSpec, *, state: str) -> None:
    print(f"  project:    {spec.project_dir}")
    print(f"  service:    {spec.service_name}")
    print(f"  state:      {state}")
    print(f"  pid:        {spec.pid if spec.pid is not None else '-'}")
    print(f"  URL:        {spec.url}")
    print(f"  provider:   {spec.provider}")
    print(f"  config:     {spec.config}")
    print(f"  workspace:  {spec.workspace}")
    print(f"  stdout log: {spec.stdout_log}")
    print(f"  stderr log: {spec.stderr_log}")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:32] or "project"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def format_command(command: list[str]) -> str:
    """Return a shell-display version of a service command."""

    return " ".join(shlex.quote(part) for part in command)
