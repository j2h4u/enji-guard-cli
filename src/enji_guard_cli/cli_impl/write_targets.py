from dataclasses import dataclass
from typing import Literal, cast

type PreferenceSwitch = Literal["on", "off"]
type ScheduleFrequencyOption = Literal["daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"]

WRITE_FLAG_OPTIONS = frozenset({"--all-repos", "--all-projects", "--json"})
SCHEDULE_SET_VALUE_OPTIONS = frozenset({"--enabled", "--frequency", "--timezone"})
EMAIL_SET_VALUE_OPTIONS = frozenset({"--manual", "--scheduled"})


@dataclass(frozen=True, slots=True)
class ParsedWriteArgs:
    repo: str | None
    all_repos: bool
    all_projects: bool
    json_output: bool
    values: dict[str, str]


@dataclass(frozen=True, slots=True)
class ScheduleSetCliArgs:
    repo: str | None
    all_repos: bool
    all_projects: bool
    json_output: bool
    enabled: PreferenceSwitch | None
    frequency: ScheduleFrequencyOption | None
    timezone: str | None


@dataclass(frozen=True, slots=True)
class EmailSetCliArgs:
    repo: str | None
    all_repos: bool
    all_projects: bool
    json_output: bool
    manual: PreferenceSwitch | None
    scheduled: PreferenceSwitch | None


def parse_schedule_set_args(raw_args: list[str]) -> ScheduleSetCliArgs:
    parsed = parse_write_args(raw_args, value_options=SCHEDULE_SET_VALUE_OPTIONS)
    return ScheduleSetCliArgs(
        repo=parsed.repo,
        all_repos=parsed.all_repos,
        all_projects=parsed.all_projects,
        json_output=parsed.json_output,
        enabled=optional_switch(parsed.values.get("--enabled"), "--enabled"),
        frequency=optional_schedule_frequency(parsed.values.get("--frequency")),
        timezone=parsed.values.get("--timezone"),
    )


def parse_email_set_args(raw_args: list[str]) -> EmailSetCliArgs:
    parsed = parse_write_args(raw_args, value_options=EMAIL_SET_VALUE_OPTIONS)
    return EmailSetCliArgs(
        repo=parsed.repo,
        all_repos=parsed.all_repos,
        all_projects=parsed.all_projects,
        json_output=parsed.json_output,
        manual=optional_switch(parsed.values.get("--manual"), "--manual"),
        scheduled=optional_switch(parsed.values.get("--scheduled"), "--scheduled"),
    )


def parse_write_args(raw_args: list[str], *, value_options: frozenset[str]) -> ParsedWriteArgs:
    positional: list[str] = []
    flags: set[str] = set()
    values: dict[str, str] = {}
    index = 0
    while index < len(raw_args):
        token = raw_args[index]
        if token in WRITE_FLAG_OPTIONS:
            add_write_flag(flags, token)
            index += 1
        elif token in value_options:
            values[token] = read_write_option_value(raw_args, index, values)
            index += 2
        elif token.startswith("--"):
            raise ValueError(f"unknown option: {token}")
        else:
            positional.append(token)
            index += 1
    if len(positional) > 1:
        raise ValueError("pass at most one REPO")
    return ParsedWriteArgs(
        repo=positional[0] if positional else None,
        all_repos="--all-repos" in flags,
        all_projects="--all-projects" in flags,
        json_output="--json" in flags,
        values=values,
    )


def add_write_flag(flags: set[str], option: str) -> None:
    if option in flags:
        raise ValueError(f"duplicate option: {option}")
    flags.add(option)


def read_write_option_value(raw_args: list[str], index: int, values: dict[str, str]) -> str:
    option = raw_args[index]
    if option in values:
        raise ValueError(f"duplicate option: {option}")
    value_index = index + 1
    if value_index >= len(raw_args) or raw_args[value_index].startswith("--"):
        raise ValueError(f"{option} requires a value")
    return raw_args[value_index]


def optional_switch(value: str | None, option: str) -> PreferenceSwitch | None:
    if value is None:
        return None
    if value in {"on", "off"}:
        return cast(PreferenceSwitch, value)
    raise ValueError(f"{option} must be on or off")


def optional_schedule_frequency(value: str | None) -> ScheduleFrequencyOption | None:
    if value is None:
        return None
    if value in {"daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"}:
        return cast(ScheduleFrequencyOption, value)
    raise ValueError("--frequency is invalid")
