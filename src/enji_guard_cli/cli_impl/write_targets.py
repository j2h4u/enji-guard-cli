from dataclasses import dataclass
from typing import Literal, cast

type PreferenceSwitch = Literal["on", "off"]
type ScheduleCadenceOption = Literal["daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"]

WRITE_FLAG_OPTIONS = frozenset({"--all-repos", "--all-projects", "--json"})
AUTOFIX_SET_FLAG_OPTIONS = frozenset({*WRITE_FLAG_OPTIONS, "--all"})
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
    cadence: ScheduleCadenceOption | None
    timezone: str | None


@dataclass(frozen=True, slots=True)
class EmailSetCliArgs:
    repo: str | None
    all_repos: bool
    all_projects: bool
    json_output: bool
    manual: PreferenceSwitch | None
    scheduled: PreferenceSwitch | None


@dataclass(frozen=True, slots=True)
class AutofixSetCliArgs:
    repo: str | None
    selectors: list[str]
    all_autofixes: bool
    all_repos: bool
    all_projects: bool
    json_output: bool
    enabled: PreferenceSwitch | None
    frequency: ScheduleCadenceOption | None
    timezone: str | None


def parse_schedule_set_args(raw_args: list[str]) -> ScheduleSetCliArgs:
    parsed = parse_write_args(raw_args, value_options=SCHEDULE_SET_VALUE_OPTIONS)
    return ScheduleSetCliArgs(
        repo=parsed.repo,
        all_repos=parsed.all_repos,
        all_projects=parsed.all_projects,
        json_output=parsed.json_output,
        enabled=optional_switch(parsed.values.get("--enabled"), "--enabled"),
        cadence=optional_schedule_cadence(parsed.values.get("--frequency")),
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


def parse_autofix_set_args(raw_args: list[str]) -> AutofixSetCliArgs:
    positional, flags, values = parse_autofix_write_args(raw_args)
    batch_scope = "--all-repos" in flags or "--all-projects" in flags
    if batch_scope:
        repo = None
        selectors = positional
    elif not positional:
        repo = None
        selectors: list[str] = []
    else:
        repo, *selectors = positional
    if "--all" in flags and selectors:
        raise ValueError("pass AUTOFIXES or --all, not both")
    return AutofixSetCliArgs(
        repo=repo,
        selectors=selectors,
        all_autofixes="--all" in flags,
        all_repos="--all-repos" in flags,
        all_projects="--all-projects" in flags,
        json_output="--json" in flags,
        enabled=optional_switch(values.get("--enabled"), "--enabled"),
        frequency=optional_schedule_cadence(values.get("--frequency")),
        timezone=values.get("--timezone"),
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


def parse_autofix_write_args(raw_args: list[str]) -> tuple[list[str], set[str], dict[str, str]]:
    positional: list[str] = []
    flags: set[str] = set()
    values: dict[str, str] = {}
    index = 0
    while index < len(raw_args):
        token = raw_args[index]
        if token in AUTOFIX_SET_FLAG_OPTIONS:
            add_write_flag(flags, token)
            index += 1
        elif token in SCHEDULE_SET_VALUE_OPTIONS:
            values[token] = read_write_option_value(raw_args, index, values)
            index += 2
        elif token.startswith("--"):
            raise ValueError(f"unknown option: {token}")
        else:
            positional.append(token)
            index += 1
    if not positional and "--all" not in flags:
        raise ValueError("pass REPO and one or more AUTOFIXES, or --all")
    return positional, flags, values


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


def optional_schedule_cadence(value: str | None) -> ScheduleCadenceOption | None:
    if value is None:
        return None
    if value in {"daily", "workdays", "weekly-3x", "weekly-2x", "weekly", "monthly"}:
        return cast(ScheduleCadenceOption, value)
    raise ValueError("--frequency is invalid")
