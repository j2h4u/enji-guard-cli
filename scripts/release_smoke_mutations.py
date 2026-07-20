#!/usr/bin/env -S uv run --script
"""Opt-in, reversible project mutation smoke for a release candidate."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import uuid
from collections.abc import Mapping, Sequence
from typing import cast

from .release_smoke import EXIT_CONFIG, DockerSmokeSettings, Reporter, SmokeFailure, subprocess_runner

PROJECT_PREFIX = "__enji_guard_release_smoke__"
EXIT_INTERLOCK = 3
EXIT_CLEANUP = 4


def _project_name(value: str | None) -> str:
    if value:
        return value
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S")
    return f"{PROJECT_PREFIX}{stamp}-{uuid.uuid4().hex[:10]}"


def _cli(settings: DockerSmokeSettings, *command: str) -> list[str]:
    args = ["docker", "exec", "-i", settings.container, "enji-guard"]
    args.extend(command)
    return args


def _run(settings: DockerSmokeSettings, command: Sequence[str]) -> tuple[int, str, str]:
    result = subprocess_runner(_cli(settings, *command), timeout=settings.timeout_seconds)
    return result.returncode, result.stdout, result.stderr


def _assert_success(settings: DockerSmokeSettings, command: Sequence[str], label: str) -> None:
    code, _stdout, _stderr = _run(settings, command)
    if code != 0:
        raise SmokeFailure(f"{label} exited {code}")


def _project_mutation(settings: DockerSmokeSettings, command: Sequence[str], label: str) -> Mapping[str, object]:
    code, stdout, _stderr = _run(settings, command)
    if code != 0:
        raise SmokeFailure(f"{label} exited {code}")
    try:
        payload = cast(object, json.loads(stdout))
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{label} returned non-JSON output") from exc
    if not isinstance(payload, Mapping):
        raise SmokeFailure(f"{label} returned invalid result")
    return payload


def _project_is_listed(stdout: str, project_name: str) -> bool:
    """Check the structured project list without exposing its contents."""
    if project_name.casefold() in stdout.casefold():
        return True
    try:
        payload = cast(object, json.loads(stdout))
    except json.JSONDecodeError:
        return False
    return project_name.casefold() in _string_values(payload)


def _string_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(item for child in value.values() for item in _string_values(child))
    if isinstance(value, list):
        return tuple(item for child in value for item in _string_values(child))
    return ()


def _create_project(settings: DockerSmokeSettings, project_name: str) -> None:
    payload = _project_mutation(settings, ("project", "create", project_name, "--json"), "project create")
    if payload.get("state") != "created":
        raise SmokeFailure("project create did not report created")


def _run_mutation_body(settings: DockerSmokeSettings, name: str, reporter: Reporter, created: list[bool]) -> None:
    code, stdout, _stderr = _run(settings, ("project", "list", "--json"))
    if code != 0:
        raise SmokeFailure("project preflight list failed")
    if _project_is_listed(stdout, name):
        raise SmokeFailure("project name already exists; refusing unsafe cleanup")
    _create_project(settings, name)
    created[0] = True
    reporter.pass_("project create")
    repeated = _project_mutation(settings, ("project", "create", name, "--json"), "repeat project create")
    if repeated.get("state") not in {"already_present", "unchanged"}:
        raise SmokeFailure("repeat project create did not report an idempotent state")
    reporter.pass_("project create repeat (idempotent)")
    code, stdout, _stderr = _run(settings, ("project", "list", "--json"))
    if code != 0 or not _project_is_listed(stdout, name):
        raise SmokeFailure("project list did not contain the smoke project")
    reporter.pass_("project list contains smoke project")


def _cleanup_project(settings: DockerSmokeSettings, name: str, reporter: Reporter) -> str | None:
    cleanup_error: str | None = None
    try:
        deleted = _project_mutation(settings, ("project", "delete", name, "--json"), "project cleanup delete")
        if deleted.get("state") != "deleted":
            raise SmokeFailure("project cleanup delete did not report deleted")
        reporter.pass_("project cleanup delete")
    except (OSError, SmokeFailure) as exc:
        cleanup_error = str(exc) or "command failed"
        reporter.fail("project cleanup", cleanup_error)
    try:
        repeated = _project_mutation(settings, ("project", "delete", name, "--json"), "repeat cleanup delete")
        if repeated.get("state") != "already_absent":
            raise SmokeFailure("repeat cleanup delete did not report already_absent")
        reporter.pass_("project cleanup repeat (idempotent)")
    except (OSError, SmokeFailure) as exc:
        detail = str(exc) or "command failed"
        cleanup_error = cleanup_error or detail
        reporter.fail("project cleanup repeat", detail)
    try:
        code, stdout, _stderr = _run(settings, ("project", "list", "--json"))
        if code != 0:
            raise SmokeFailure("project cleanup list read-back failed")
        if _project_is_listed(stdout, name):
            raise SmokeFailure("project cleanup list still contains smoke project")
        reporter.pass_("project cleanup list read-back absent")
    except (OSError, SmokeFailure) as exc:
        detail = str(exc) or "command failed"
        cleanup_error = cleanup_error or detail
        reporter.fail("project cleanup list read-back", detail)
    return cleanup_error


def run_mutations(settings: DockerSmokeSettings, *, enabled: bool, project_name: str | None = None) -> int:
    if not enabled:
        print("FAIL safety interlock: pass --enable to run mutations", file=sys.stderr)
        return EXIT_INTERLOCK
    name = _project_name(project_name)
    if not name.startswith(PROJECT_PREFIX):
        print(f"FAIL safety interlock: project must start with {PROJECT_PREFIX}", file=sys.stderr)
        return EXIT_INTERLOCK
    reporter = Reporter()
    created = [False]
    cleanup_error: str | None = None
    try:
        _run_mutation_body(settings, name, reporter, created)
    except OSError:
        reporter.fail("mutation smoke", "command failed")
    except SmokeFailure as exc:
        reporter.fail("mutation smoke", str(exc))
    finally:
        if created[0]:
            cleanup_error = _cleanup_project(settings, name, reporter)
    if cleanup_error:
        return EXIT_CLEANUP
    return 1 if reporter.failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true", help="Required safety interlock.")
    parser.add_argument("--container", default="enji-guard-cli")
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timeout = cast(float, args.timeout)
    if timeout <= 0:
        print("FAIL configuration: --timeout must be positive", file=sys.stderr)
        return EXIT_CONFIG
    settings = DockerSmokeSettings(repo="unused", container=cast(str, args.container), timeout_seconds=timeout)
    return run_mutations(settings, enabled=cast(bool, args.enable))


if __name__ == "__main__":
    raise SystemExit(main())
