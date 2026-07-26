"""Validate that every commit in a pull request is a parseable release input.

GitHub's squash body is built from the individual commit messages, and
release-please parses that body.  A commit whose subject is not a Conventional
Commit is not merely untidy: it is a line the changelog cannot classify, and it
degrades whatever the PR's override block is trying to say.

Merge commits are exempt -- they are structure, not content.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import cast

from scripts.validate_pr_title import RELEASABLE_TYPES, TITLE_PATTERN


def commit_subjects(base_sha: str, head_sha: str) -> list[str]:
    """Return the non-merge commit subjects a PR adds on top of its base."""

    result = subprocess.run(  # noqa: S603 - argv is a fixed git invocation over two caller-supplied revisions.
        ["/usr/bin/git", "log", "--no-merges", "--format=%s", f"{base_sha}..{head_sha}"],
        capture_output=True,
        check=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def validate_commit_subjects(subjects: list[str]) -> tuple[bool, list[str]]:
    """Return whether every subject is a releasable Conventional Commit."""

    if not subjects:
        return True, ["No non-merge commits to validate."]

    problems: list[str] = []
    for subject in subjects:
        match = TITLE_PATTERN.fullmatch(subject.strip())
        if match is None:
            problems.append(f"'{subject}' is not a Conventional Commit subject.")
            continue
        commit_type = match.group("type")
        if commit_type not in RELEASABLE_TYPES:
            allowed = ", ".join(sorted(RELEASABLE_TYPES))
            problems.append(f"'{subject}' uses unsupported type '{commit_type}'. Allowed types: {allowed}.")

    if problems:
        return False, problems
    return True, [f"All {len(subjects)} commit subject(s) are releasable."]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha", required=True, help="Base commit the PR branches from.")
    parser.add_argument("--head-sha", required=True, help="Head commit of the PR.")
    args = parser.parse_args(argv)

    subjects = commit_subjects(cast("str", args.base_sha), cast("str", args.head_sha))
    ok, messages = validate_commit_subjects(subjects)
    stream = sys.stdout if ok else sys.stderr
    for message in messages:
        print(message, file=stream)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
