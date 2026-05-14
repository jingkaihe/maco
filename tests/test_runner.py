from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

import pytest

import maco.runner as runner
from maco.runner import find_workspace, run_code


def test_find_workspace_discovers_parent_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("MACO_WORKSPACE", raising=False)
    script_dir = tmp_path / "project" / "scripts"
    script_dir.mkdir(parents=True)
    script = script_dir / "analysis.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    workspace = tmp_path / "project" / ".maco"
    _write_generated_client(workspace)

    assert find_workspace(script) == workspace.resolve()


def test_run_code_injects_workspace_gateway_and_pythonpath(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "existing-path")
    monkeypatch.delenv("MACO_GATEWAY_URL", raising=False)
    monkeypatch.delenv("MACO_GATEWAY_TOKEN", raising=False)
    script = tmp_path / "analysis.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    workspace = tmp_path / ".maco"
    _write_generated_client(workspace)
    (workspace / "gateway.json").write_text(
        json.dumps({"url": "http://127.0.0.1:9/", "token": "test-token"}),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def fake_which(name: str) -> str | None:
        return "/usr/bin/uv" if name == "uv" else None

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[list[str]]:
        captured["command"] = command
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.shutil, "which", fake_which)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = run_code(
        script,
        ["--flag"],
        workspace=workspace,
        cwd=tmp_path,
        python="3.12",
    )

    assert result == 0
    assert captured["command"] == [
        "/usr/bin/uv",
        "run",
        "--python",
        "3.12",
        str(script.resolve()),
        "--flag",
    ]
    env = captured["env"]
    assert env["MACO_WORKSPACE"] == str(workspace.resolve())
    assert env["MACO_GATEWAY_FILE"] == str(workspace.resolve() / "gateway.json")
    assert env["MACO_GATEWAY_URL"] == "http://127.0.0.1:9/"
    assert env["MACO_GATEWAY_TOKEN"] == "test-token"
    assert env["PYTHONPATH"] == os.pathsep.join([str(workspace.resolve()), "existing-path"])
    assert captured["cwd"] == str(tmp_path.resolve())


def test_run_code_requires_generated_workspace(tmp_path):
    script = tmp_path / "analysis.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    with pytest.raises(runner.RunnerError, match="could not find"):
        run_code(script, workspace=tmp_path / "missing")


def _write_generated_client(workspace: Path) -> None:
    generated = workspace / "maco_generated"
    generated.mkdir(parents=True)
    (generated / "client.py").write_text("", encoding="utf-8")
