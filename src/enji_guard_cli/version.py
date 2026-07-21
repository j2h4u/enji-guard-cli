"""Installed package version and build provenance."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version

from enji_guard_cli._build_provenance import COMMIT_SHA

_DISTRIBUTION = "enji-guard-cli"
_VCS_COMMIT = re.compile(r"(?:^|[.+])g(?P<commit>[0-9a-f]{7,40})(?:$|[.])", re.IGNORECASE)


def _commit_from_vcs_version(package_version: str) -> str | None:
    match = _VCS_COMMIT.search(package_version)
    return match.group("commit") if match is not None else None


def package_version() -> str:
    """Return the installed distribution version."""
    try:
        return version(_DISTRIBUTION)
    except PackageNotFoundError:
        return "0.0.0+unknown"


def version_text() -> str:
    """Return the human-facing version with source provenance."""
    installed = package_version()
    commit = COMMIT_SHA if COMMIT_SHA != "unknown" else _commit_from_vcs_version(installed)
    short_commit = commit[:12] if commit is not None else "unknown"
    return f"{_DISTRIBUTION} {installed} (commit {short_commit})"
