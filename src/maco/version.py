"""Version and build metadata helpers."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
import subprocess

from . import _build_info


@dataclass(frozen=True)
class VersionInfo:
    """maco version and release metadata."""

    version: str
    commit_sha: str
    release_date: str


def get_version_info() -> VersionInfo:
    """Return package version, commit SHA, and release date metadata."""

    return VersionInfo(
        version=_package_version(),
        commit_sha=_build_value(_build_info.COMMIT_SHA) or _git_commit_sha() or "unknown",
        release_date=_build_value(_build_info.RELEASE_DATE) or "unreleased",
    )


def _package_version() -> str:
    try:
        return package_version("maco")
    except PackageNotFoundError:
        version_file = _find_ancestor_file("VERSION.txt")
        if version_file is not None:
            return version_file.read_text(encoding="utf-8").strip()
        return "unknown"


def _build_value(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _git_commit_sha() -> str | None:
    git_dir = _find_ancestor_file(".git")
    if git_dir is None:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=git_dir.parent,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _find_ancestor_file(name: str) -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / name
        if candidate.exists():
            return candidate
    return None
