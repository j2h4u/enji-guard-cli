"""Validate that a pull request title can drive release-please."""

from __future__ import annotations

import argparse
import re
import sys
from typing import cast

RELEASABLE_TYPES = frozenset(
    {
        "build",
        "chore",
        "ci",
        "docs",
        "feat",
        "fix",
        "perf",
        "refactor",
        "revert",
        "style",
        "test",
    }
)

TITLE_PATTERN = re.compile(r"^(?P<type>[a-z]+)(?:\([a-z0-9][a-z0-9._/-]*\))?(?P<breaking>!)?: (?P<description>\S.*)$")


def validate_pr_title(title: str) -> tuple[bool, str]:
    """Return whether a PR title is a releasable Conventional Commit subject."""

    normalized = title.strip()
    if not normalized:
        return False, "PR title is empty."

    match = TITLE_PATTERN.fullmatch(normalized)
    if match is None:
        return (
            False,
            "PR title must look like 'fix: short description', 'feat(scope): short description', "
            "or 'feat(scope)!: breaking description'.",
        )

    commit_type = match.group("type")
    if commit_type not in RELEASABLE_TYPES:
        allowed = ", ".join(sorted(RELEASABLE_TYPES))
        return False, f"Unsupported Conventional Commit type '{commit_type}'. Allowed types: {allowed}."

    return True, "PR title is releasable."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True, help="Pull request title to validate.")
    args = parser.parse_args(argv)

    title = cast("str", args.title)
    ok, message = validate_pr_title(title)
    if ok:
        print(message)
        return 0
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
