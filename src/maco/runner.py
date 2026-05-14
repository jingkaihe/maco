"""Run Python scripts with generated MCP wrappers on PYTHONPATH."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


class RunnerError(RuntimeError):
    """Raised when maco cannot prepare a code execution run."""


def run_code(
    code_path: str | os.PathLike[str],
    script_args: list[str] | None = None,
    *,
    workspace: str | os.PathLike[str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    python: str | None = None,
) -> int:
    """Run a Python script with the generated maco workspace available."""

    script = Path(code_path).expanduser().resolve()
    if not script.exists():
        raise RunnerError(f"code file not found: {script}")
    workspace_path = find_workspace(script, workspace)
    gateway_file = workspace_path / "gateway.json"

    env = os.environ.copy()
    env["MACO_WORKSPACE"] = str(workspace_path)
    env["MACO_GATEWAY_FILE"] = str(gateway_file)
    gateway = _read_gateway(gateway_file)
    if gateway.get("url") and not env.get("MACO_GATEWAY_URL"):
        env["MACO_GATEWAY_URL"] = str(gateway["url"])
    if gateway.get("token") and not env.get("MACO_GATEWAY_TOKEN"):
        env["MACO_GATEWAY_TOKEN"] = str(gateway["token"])
    env["PYTHONPATH"] = _prepend_path(str(workspace_path), env.get("PYTHONPATH"))

    uv = shutil.which("uv")
    if uv is None:
        raise RunnerError("uv is required to run code; install uv or run the script manually with PYTHONPATH set")

    command = [uv, "run"]
    if python:
        command.extend(["--python", python])
    command.extend([str(script), *(script_args or [])])
    completed = subprocess.run(command, env=env, cwd=str(Path(cwd).resolve()) if cwd else None)
    return completed.returncode


def find_workspace(
    script: Path,
    explicit_workspace: str | os.PathLike[str] | None = None,
) -> Path:
    """Find the generated workspace for a script."""

    candidates: list[Path] = []
    if explicit_workspace:
        candidates.append(Path(explicit_workspace).expanduser())
    if os.environ.get("MACO_WORKSPACE"):
        candidates.append(Path(os.environ["MACO_WORKSPACE"]).expanduser())
    candidates.extend(parent / ".maco" for parent in [script.parent, *script.parents])
    cwd = Path.cwd().resolve()
    candidates.extend(parent / ".maco" for parent in [cwd, *cwd.parents])

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "maco_generated" / "client.py").exists():
            return resolved
    raise RunnerError(
        "could not find a generated maco workspace. Run `maco gen`, pass --workspace, "
        "or set MACO_WORKSPACE."
    )


def _read_gateway(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise RunnerError(f"failed to read gateway file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RunnerError(f"gateway file {path} must contain a JSON object")
    return data


def _prepend_path(prefix: str, existing: str | None) -> str:
    if not existing:
        return prefix
    return os.pathsep.join([prefix, existing])


def exit_with_error(exc: BaseException) -> None:
    print(f"maco: {exc}", file=sys.stderr)
    raise SystemExit(1)
