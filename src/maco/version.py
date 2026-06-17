"""Version and build metadata helpers."""

from __future__ import annotations

from dataclasses import dataclass

from . import __version__


@dataclass(frozen=True)
class VersionInfo:
    """maco version metadata."""

    version: str


def get_version_info() -> VersionInfo:
    """Return package version metadata."""

    return VersionInfo(
        version=__version__ or "unknown",
    )
