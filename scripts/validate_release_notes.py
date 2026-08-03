"""Validate the release-note contract a pull request owes release-please.

PRs are squash-merged, so release-please sees exactly one commit per PR and, by
default, renders exactly one changelog line for it.  A PR that squashes several
commits therefore cannot describe itself through its title alone: whatever the
other commits did is lost the moment the merge button is pressed.

`BEGIN_COMMIT_OVERRIDE` / `END_COMMIT_OVERRIDE` in the PR body is the documented
escape hatch.  Release-please replaces the whole commit message with the block's
contents before parsing it, so the block is what the changelog is built from.

This validates that the block exists when it is owed, and that release-please
will actually parse what it contains.  Both failure modes it guards are silent
ones: a missing block loses the work, and a malformed block collapses back to a
single entry without any error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from scripts.validate_pr_title import RELEASABLE_TYPES, TITLE_PATTERN

BEGIN_MARKER = "BEGIN_COMMIT_OVERRIDE"
END_MARKER = "END_COMMIT_OVERRIDE"
BREAKING_PREFIX = "BREAKING CHANGE:"


def _extract_override(body: str) -> str | None:
    """Return the text between the override markers, or None when absent."""

    if BEGIN_MARKER not in body:
        return None
    after_begin = body.split(BEGIN_MARKER, 1)[1]
    if END_MARKER not in after_begin:
        return None
    return after_begin.split(END_MARKER, 1)[0].strip("\n")


def _split_messages(block: str) -> list[str]:
    """Split an override block the way release-please's splitMessages does.

    A nested message starts only where a Conventional Commit type sits at
    column 0 immediately after a blank line.  An indented or bulleted line is
    body text belonging to the message above it.
    """

    messages: list[str] = []
    current: list[str] = []
    previous_blank = True

    for line in block.splitlines():
        starts_message = (
            previous_blank
            and TITLE_PATTERN.fullmatch(line.strip()) is not None
            and not line.startswith((" ", "\t", "*", "-"))
        )
        if starts_message and current:
            messages.append("\n".join(current).strip("\n"))
            current = []
        current.append(line)
        previous_blank = not line.strip()

    if current:
        messages.append("\n".join(current).strip("\n"))
    return [message for message in messages if message.strip()]


def _validate_message(message: str) -> list[str]:
    """Return the problems that would make release-please mis-render a message."""

    problems: list[str] = []
    lines = message.splitlines()
    subject = lines[0].strip()

    match = TITLE_PATTERN.fullmatch(subject)
    if match is None:
        return [f"'{subject}' is not a Conventional Commit subject."]

    commit_type = match.group("type")
    if commit_type not in RELEASABLE_TYPES:
        allowed = ", ".join(sorted(RELEASABLE_TYPES))
        problems.append(f"'{subject}' uses unsupported type '{commit_type}'. Allowed types: {allowed}.")

    # release-please stops reading a BREAKING CHANGE note at the first blank
    # line, so a description followed by a blank line and then its bullet list
    # silently ships the description alone.  v3.0.0 lost eight bullets this way.
    for index, line in enumerate(lines):
        if not line.startswith(BREAKING_PREFIX):
            continue
        remainder = lines[index + 1 :]
        if remainder and not remainder[0].strip() and any(item.strip() for item in remainder):
            problems.append(
                f"'{subject}' has a blank line directly after '{BREAKING_PREFIX}'. "
                "Release-please ends the breaking-change note there, dropping everything below it. "
                "Put the bullets on the very next line."
            )

    return problems


def validate_release_notes(body: str, commit_count: int, require_above: int) -> tuple[bool, list[str]]:
    """Return whether a PR body satisfies the release-note contract."""

    block = _extract_override(body)

    if block is None:
        if commit_count > require_above:
            return False, [
                (
                    f"This PR squashes {commit_count} commits into one, so its title cannot describe all of them "
                    "and the changelog would render a single line."
                ),
                (
                    f"Add a {BEGIN_MARKER} / {END_MARKER} block to the PR description listing what shipped, "
                    "one Conventional Commit message per entry, separated by blank lines."
                ),
            ]
        return True, [f"No override block, and none owed for {commit_count} commit(s)."]

    messages = _split_messages(block)
    if not messages:
        return False, [f"The {BEGIN_MARKER} block is empty."]

    problems = [problem for message in messages for problem in _validate_message(message)]
    if problems:
        return False, problems

    return True, [f"Override block parses into {len(messages)} changelog entr(ies)."]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body-file", required=True, help="File holding the PR description ('-' for stdin).")
    parser.add_argument("--commit-count", required=True, type=int, help="Commits the merge will squash.")
    parser.add_argument(
        "--require-above",
        default=1,
        type=int,
        help="Demand an override block when the PR has more commits than this (default: 1).",
    )
    args = parser.parse_args(argv)

    body_file = cast("str", args.body_file)
    body = sys.stdin.read() if body_file == "-" else Path(body_file).read_text(encoding="utf-8")

    ok, messages = validate_release_notes(body, cast("int", args.commit_count), cast("int", args.require_above))
    stream = sys.stdout if ok else sys.stderr
    for message in messages:
        print(message, file=stream)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
