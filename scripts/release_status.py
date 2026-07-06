#!/usr/bin/env python3
import json
import subprocess
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True, slots=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str


def main() -> int:
    print_section("Git")
    print(_git_status())
    print(_git_describe())

    print_section("Open PRs")
    prs = _gh_open_prs()
    if not isinstance(prs, list) or not prs:
        print("none")
    else:
        for pr in prs:
            print(f"#{pr['number']} {pr['title']} [{pr['headRefName']}]")
            print(f"  {pr['url']}")

    print_section("Latest Release")
    release = _gh_latest_release()
    if isinstance(release, dict):
        tag = str(release["tagName"])
        print(f"{tag} published {release['publishedAt']}")
        print(f"  {release['url']}")
    else:
        print("no release")

    print_section("Recent Runs")
    runs = _gh_recent_runs()
    if isinstance(runs, list):
        for run in runs:
            conclusion = run["conclusion"] or run["status"]
            print(f"{conclusion:16} {run['workflowName']}: {run['displayTitle']} ({run['event']}/{run['headBranch']})")
    return 0


def print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def _git_status() -> str:
    result = _run_git_status()
    return _command_text(result, "git")


def _git_describe() -> str:
    result = _run_git_describe()
    return _command_text(result, "git")


def _command_text(result: CommandResult, command_name: str) -> str:
    if result.code != 0:
        return result.stderr.strip() or f"{command_name} exited {result.code}"
    return result.stdout.strip()


def _gh_open_prs() -> object:
    result = _run_gh_pr_list()
    return _json_result(result)


def _gh_latest_release() -> object:
    result = _run_gh_release_view()
    return _json_result(result)


def _gh_recent_runs() -> object:
    result = _run_gh_run_list()
    return _json_result(result)


def _json_result(result: CommandResult) -> object:
    if result.code != 0:
        return None
    return cast(object, json.loads(result.stdout))


def _command_result(completed: subprocess.CompletedProcess[str]) -> CommandResult:
    return CommandResult(code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def _run_git_status() -> CommandResult:
    completed = subprocess.run(
        ["/usr/bin/git", "status", "--short", "--branch"],
        capture_output=True,
        check=False,
        text=True,
    )
    return _command_result(completed)


def _run_git_describe() -> CommandResult:
    completed = subprocess.run(
        ["/usr/bin/git", "describe", "--tags", "--always", "--dirty"],
        capture_output=True,
        check=False,
        text=True,
    )
    return _command_result(completed)


def _run_gh_pr_list() -> CommandResult:
    completed = subprocess.run(
        ["/usr/bin/gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName,url"],
        capture_output=True,
        check=False,
        text=True,
    )
    return _command_result(completed)


def _run_gh_release_view() -> CommandResult:
    completed = subprocess.run(
        ["/usr/bin/gh", "release", "view", "--json", "tagName,name,publishedAt,url"],
        capture_output=True,
        check=False,
        text=True,
    )
    return _command_result(completed)


def _run_gh_run_list() -> CommandResult:
    completed = subprocess.run(
        [
            "/usr/bin/gh",
            "run",
            "list",
            "--limit",
            "8",
            "--json",
            "workflowName,displayTitle,status,conclusion,headBranch,event,url",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    return _command_result(completed)


if __name__ == "__main__":
    raise SystemExit(main())
