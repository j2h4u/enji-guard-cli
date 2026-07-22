#!/usr/bin/env -S uv run --script
"""Resolve the non-zero package version used by published container images."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from typing import cast

SEMVER = re.compile(r"^(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$")
SHA = re.compile(r"^[0-9a-fA-F]{12,40}$")


class VersionResolutionError(ValueError):
    """The release metadata cannot produce a valid package version."""


def resolve_package_version(release_tag: str, source_sha: str, base_tags: Sequence[str]) -> str:
    """Return X.Y.Z for releases or the latest reachable X.Y.Z+sha.N for builds."""
    sha = source_sha.strip()
    if SHA.fullmatch(sha) is None:
        raise VersionResolutionError("source SHA must be a hexadecimal Git object id")
    tag = release_tag.strip()
    if tag:
        match = re.fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)", tag)
        if match is None:
            raise VersionResolutionError(f"release tag is not a SemVer tag: {tag!r}")
        version = match.group(1)
    else:
        candidates: list[tuple[tuple[int, int, int], str]] = []
        for candidate in base_tags:
            match = re.fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)", candidate.strip())
            if match is None:
                continue
            version = match.group(1)
            parts = tuple(int(part) for part in version.split("."))
            candidates.append(((parts[0], parts[1], parts[2]), version))
        if not candidates:
            raise VersionResolutionError("no reachable SemVer release tag exists for an untagged build")
        version = max(candidates)[1]
        version = f"{version}+sha.{sha[:12].lower()}"
    if version.startswith("0.0.0"):
        raise VersionResolutionError("zero package version is not publishable")
    return version


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--base-tag", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        print(
            resolve_package_version(
                cast(str, args.release_tag), cast(str, args.source_sha), cast(list[str], args.base_tag)
            )
        )
    except VersionResolutionError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
