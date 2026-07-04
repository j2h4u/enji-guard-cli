#!/usr/bin/env python3
import json
import subprocess
from dataclasses import dataclass
from typing import cast

REPO = "j2h4u/enji-guard-cli"
IMAGE = f"ghcr.io/{REPO}"


@dataclass(frozen=True, slots=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str


def main() -> int:
    print_section("Git")
    print(run_text(["git", "status", "--short", "--branch"]))
    print(run_text(["git", "describe", "--tags", "--always", "--dirty"]))

    print_section("Open PRs")
    prs = run_json(["gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName,url"])
    if not isinstance(prs, list) or not prs:
        print("none")
    else:
        for pr in prs:
            print(f"#{pr['number']} {pr['title']} [{pr['headRefName']}]")
            print(f"  {pr['url']}")

    print_section("Latest Release")
    release = run_json(["gh", "release", "view", "--json", "tagName,name,publishedAt,url"])
    if isinstance(release, dict):
        tag = str(release["tagName"])
        print(f"{tag} published {release['publishedAt']}")
        print(f"  {release['url']}")
        print_image_status(tag)
    else:
        print("no release")

    print_section("Recent Runs")
    runs = run_json(
        [
            "gh",
            "run",
            "list",
            "--limit",
            "8",
            "--json",
            "workflowName,displayTitle,status,conclusion,headBranch,event,url",
        ]
    )
    if isinstance(runs, list):
        for run in runs:
            conclusion = run["conclusion"] or run["status"]
            print(f"{conclusion:16} {run['workflowName']}: {run['displayTitle']} ({run['event']}/{run['headBranch']})")
    return 0


def print_image_status(tag: str) -> None:
    result = run(["docker", "buildx", "imagetools", "inspect", f"{IMAGE}:{tag}"])
    status = "published" if result.code == 0 else "missing"
    print(f"image {IMAGE}:{tag}: {status}")


def print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def run_text(command: list[str]) -> str:
    result = run(command)
    if result.code != 0:
        return result.stderr.strip() or f"{command[0]} exited {result.code}"
    return result.stdout.strip()


def run_json(command: list[str]) -> object:
    result = run(command)
    if result.code != 0:
        return None
    return cast(object, json.loads(result.stdout))


def run(command: list[str]) -> CommandResult:
    completed = subprocess.run(command, capture_output=True, check=False, text=True)  # noqa: S603
    return CommandResult(code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
