"""Validate that every commit in a pull request is a parseable release input.

GitHub's squash body is built from the individual commit messages, and
release-please parses the resulting commit.  Two things can make it unusable:

* a subject that is not a Conventional Commit, which the changelog cannot
  classify; and
* a body line that release-please's grammar tries to read as a header and
  chokes on.  A Markdown bullet at column 0 does exactly that: the parser reads
  ``-`` as the commit type, hits the space, and reports
  ``unexpected token ' ' at 1:2``.  It then discards the **whole commit**, which
  vanishes from the changelog with no warning anywhere.

That second rule is not theoretical.  Commit c2249b6 shipped two column-0
bullets and was silently dropped from the v3.0.2 release notes -- while being
the very commit that added release-note enforcement.  Indent bullets by two
spaces and the parser treats them as body text.

Merge commits are exempt -- they are structure, not content.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import cast

from scripts.validate_pr_title import RELEASABLE_TYPES, TITLE_PATTERN

# Markdown list markers.  At column 0 these are read as a commit type by
# release-please's grammar; indented, they are ordinary body text.
BULLET_MARKERS = ("- ", "* ", "+ ")
# `%x00` is a git format escape: the argv entry stays plain text, and git
# emits the NUL byte into the output where it separates records.
RECORD_SEPARATOR_FORMAT = "%x00"
RECORD_SEPARATOR = "\x00"


def commit_messages(base_sha: str, head_sha: str) -> list[str]:
    """Return the full non-merge commit messages a PR adds on top of its base."""

    result = subprocess.run(  # noqa: S603 - argv is a fixed git invocation over two caller-supplied revisions.
        ["/usr/bin/git", "log", "--no-merges", f"--format=%B{RECORD_SEPARATOR_FORMAT}", f"{base_sha}..{head_sha}"],
        capture_output=True,
        check=True,
        text=True,
    )
    return [message.strip("\n") for message in result.stdout.split(RECORD_SEPARATOR) if message.strip()]


def _validate_message(message: str) -> list[str]:
    """Return the reasons release-please could not turn a commit into a release input."""

    problems: list[str] = []
    lines = message.splitlines()
    subject = lines[0].strip()

    match = TITLE_PATTERN.fullmatch(subject)
    if match is None:
        problems.append(f"'{subject}' is not a Conventional Commit subject.")
    else:
        commit_type = match.group("type")
        if commit_type not in RELEASABLE_TYPES:
            allowed = ", ".join(sorted(RELEASABLE_TYPES))
            problems.append(f"'{subject}' uses unsupported type '{commit_type}'. Allowed types: {allowed}.")

    bullets = [line for line in lines[1:] if line.startswith(BULLET_MARKERS)]
    if bullets:
        problems.append(
            f"'{subject}' has a Markdown bullet at column 0 ({bullets[0]!r}). "
            "Release-please reads the marker as a commit type, fails to parse the message, "
            "and drops the entire commit from the changelog without reporting anything. "
            "Indent the bullets by two spaces."
        )

    return problems


def validate_commit_messages(messages: list[str]) -> tuple[bool, list[str]]:
    """Return whether every commit message is a usable release input."""

    if not messages:
        return True, ["No non-merge commits to validate."]

    problems = [problem for message in messages for problem in _validate_message(message)]
    if problems:
        return False, problems
    return True, [f"All {len(messages)} commit message(s) are releasable."]


SCISSORS = "# ------------------------ >8 ------------------------"


def editable_message(raw: str) -> str:
    """Reduce a commit-msg file to what git will actually record.

    Two things in that file are not the message.  ``git commit -v`` appends the
    staged diff below a scissors line, and every deletion in it starts with
    ``-`` at column 0 -- checking those would reject a perfectly good commit for
    the diff it contains.  Comment lines are dropped for the same reason: they
    are stripped before the message is stored.
    """

    lines: list[str] = []
    for line in raw.split("\n"):
        if line.rstrip() == SCISSORS:
            break
        if not line.startswith("#"):
            lines.append(line)
    return "\n".join(lines).strip("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # A range is what CI has; a single message is what `git commit` has.  The
    # same rules applied at write time cost one amend instead of a rebase, so
    # the commit-msg hook uses the second form.
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--base-sha", help="Base commit the PR branches from.")
    source.add_argument("--message-file", help="File holding one commit message, as passed to a commit-msg hook.")
    parser.add_argument("--head-sha", help="Head commit of the PR. Required with --base-sha.")
    args = parser.parse_args(argv)

    message_file = cast("str | None", args.message_file)
    head_sha = cast("str | None", args.head_sha)
    if message_file is not None:
        messages = [editable_message(Path(message_file).read_text(encoding="utf-8"))]
    else:
        if head_sha is None:
            parser.error("--head-sha is required with --base-sha")
        messages = commit_messages(cast("str", args.base_sha), head_sha)
    ok, reported = validate_commit_messages(messages)
    stream = sys.stdout if ok else sys.stderr
    for message in reported:
        print(message, file=stream)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
