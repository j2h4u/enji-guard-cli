#!/usr/bin/env python3

import argparse
import os
import sys
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

GitRunner = Callable[[Sequence[str]], str]

DOCUMENTATION_PREFIXES = (".planning/", "docs/")
DOCUMENTATION_FILES = frozenset({"README.md", "AGENTS.md", "CHANGELOG.md", "ROADMAP.md", "SECURITY.md"})


class ClassificationError(RuntimeError):
    """Raised when the command cannot determine the changed files."""


class _Arguments(Protocol):
    event_name: str
    base_sha: str
    head_sha: str
    force_events: list[str]
    output_key: str
    output_file: Path | None


@dataclass(frozen=True, slots=True)
class Classification:
    requires_checks: bool
    changed_files: tuple[str, ...]


def is_documentation_path(path: str) -> bool:
    """Return whether a changed path is covered by the repository docs-only policy."""
    return path.startswith(DOCUMENTATION_PREFIXES) or path in DOCUMENTATION_FILES or path.endswith(".md")


def classify_changed_files(
    event_name: str,
    changed_files: Sequence[str],
    force_events: Collection[str] = (),
) -> Classification:
    """Classify a diff, conservatively requiring checks for empty or mixed diffs."""
    files = tuple(changed_files)
    requires_checks = event_name in force_events or not files or any(not is_documentation_path(path) for path in files)
    return Classification(requires_checks=requires_checks, changed_files=files)


def has_usable_base_sha(base_sha: str) -> bool:
    """Return whether a workflow supplied a real commit to diff from."""
    normalized_base = base_sha.strip()
    return bool(normalized_base) and not all(character == "0" for character in normalized_base)


def _run_git(args: Sequence[str]) -> str:
    command = tuple(args)
    executable = "/usr/bin/git"
    match command:
        case ("diff", "--name-only", _, _):
            pass
        case _:
            raise ClassificationError(f"unsupported git command: {' '.join(command)}")

    read_fd, write_fd = os.pipe()
    try:
        process_id = os.posix_spawn(
            executable,
            [executable, *command],
            os.environ,
            file_actions=(
                (os.POSIX_SPAWN_DUP2, write_fd, 1),
                (os.POSIX_SPAWN_DUP2, write_fd, 2),
                (os.POSIX_SPAWN_CLOSE, read_fd),
                (os.POSIX_SPAWN_CLOSE, write_fd),
            ),
        )
    except OSError as exc:
        os.close(read_fd)
        os.close(write_fd)
        raise ClassificationError(f"could not start git: {exc}") from exc

    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as output:
        result = output.read().decode("utf-8", errors="replace")
    _, status = os.waitpid(process_id, 0)
    exit_code = os.waitstatus_to_exitcode(status)
    if exit_code != 0:
        detail = result.strip() or f"git exited with status {exit_code}"
        raise ClassificationError(detail)
    return result


def _output_key(value: str) -> str:
    valid_first = bool(value) and (value[0] == "_" or (value[0].isascii() and value[0].isalpha()))
    valid_rest = all(character in {"_", "-"} or (character.isascii() and character.isalnum()) for character in value)
    if not valid_first or not valid_rest:
        raise argparse.ArgumentTypeError("output key must contain only ASCII letters, digits, '_' or '-'")
    return value


def changed_files_between(
    head_sha: str,
    base_sha: str,
    git: GitRunner = _run_git,
) -> tuple[str, ...]:
    """Return the names in the selected commit range."""
    return tuple(line for line in git(("diff", "--name-only", base_sha.strip(), head_sha.strip())).splitlines() if line)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify a GitHub Actions commit diff as docs-only or requiring checks."
    )
    parser.add_argument("--event-name", default=os.environ.get("EVENT_NAME", ""))
    parser.add_argument("--base-sha", default=os.environ.get("BASE_SHA", ""))
    parser.add_argument("--head-sha", default=os.environ.get("HEAD_SHA", ""))
    parser.add_argument("--force-event", action="append", dest="force_events", default=[])
    parser.add_argument("--output-key", required=True, type=_output_key, help="GitHub Actions output name to write")
    parser.add_argument("--output-file", type=Path, help="GITHUB_OUTPUT path; defaults to the environment variable")
    return parser


def _parse_args(argv: list[str] | None) -> _Arguments:
    return cast(_Arguments, _build_parser().parse_args(argv))


def _output_path(argument: Path | None) -> Path:
    if argument is not None:
        return argument
    configured = os.environ.get("GITHUB_OUTPUT")
    if not configured:
        raise ClassificationError("GITHUB_OUTPUT is not set; pass --output-file")
    return Path(configured)


def _write_output(path: Path, key: str, value: bool) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(f"{key}={'true' if value else 'false'}\n")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        output_file = _output_path(args.output_file)
        force_events = frozenset(args.force_events)
        if args.event_name in force_events:
            classification = classify_changed_files(args.event_name, (), force_events)
        else:
            if not args.head_sha:
                raise ClassificationError("--head-sha or HEAD_SHA is required")
            changed_files = (
                changed_files_between(args.head_sha, args.base_sha) if has_usable_base_sha(args.base_sha) else ()
            )
            classification = classify_changed_files(args.event_name, changed_files, force_events)
        _write_output(output_file, args.output_key, classification.requires_checks)
    except ClassificationError as exc:
        print(f"changed-file classification failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
