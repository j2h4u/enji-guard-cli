import json
from collections.abc import Callable
from typing import cast

import typer

from enji_guard_cli.cli_impl.durations import format_duration_seconds
from enji_guard_cli.cli_impl.rendering_support import object_dict, object_list

type JsonCommandAction = Callable[[], object]

MIN_TIMEZONE_DIVERGENCE_COUNT = 2


def echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, sort_keys=True))


def echo_table(headers: tuple[str, ...], rows: list[tuple[str, ...]], empty_message: str = "No rows.") -> None:
    if not rows:
        typer.echo(empty_message)
        return
    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row, strict=True)]
    typer.echo(table_line(headers, widths))
    typer.echo(table_line(tuple("-" * width for width in widths), widths))
    for row in rows:
        typer.echo(table_line(row, widths))


def table_line(cells: tuple[str, ...], widths: list[int]) -> str:
    return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))


def echo_repo_score_table(payload: object) -> None:
    headers = (
        "project",
        "repo",
        "state",
        "last_report",
        "overall",
        "grade",
        "weakest",
        "vulns",
        "ai",
        "tests",
        "tech",
        "deps",
        "cog",
        "dead",
    )
    echo_table(headers, [repo_score_row(project, repo) for project, repo in payload_repos(payload)], "No repositories.")


def echo_repo_status_table(payload: object) -> None:
    headers = (
        "project",
        "repo",
        "state",
        "overall",
        "weakest",
        "readable",
        "stale",
        "active",
        "queued",
        "running",
        "failed",
        "last_report",
        "current",
        "audited",
    )
    echo_table(
        headers, [repo_status_row(project, repo) for project, repo in payload_repos(payload)], "No repositories."
    )
    repo = single_status_repo(payload)
    if repo is not None:
        typer.echo("")
        echo_status_report_table(repo)


def echo_project_table(payload: object) -> None:
    headers = ("project", "id", "repos", "recon", "score_axes")
    echo_table(headers, [project_row(project) for project in payload_projects(payload)], "No projects.")


def echo_repo_resolve_table(payload: object) -> None:
    data = object_dict(payload)
    headers = ("selector", "resolved", "project", "repo", "repo_id", "state")
    rows = [repo_resolve_row(data, object_dict(match)) for match in object_list(data.get("matches"))]
    echo_table(headers, rows, "No repositories.")


def echo_status_report_table(repo: dict[str, object]) -> None:
    headers = ("repo", "audit", "report", "freshness", "task", "run", "report_done", "task_done", "current", "audited")
    reports = object_dict(repo.get("reports"))
    rows = [status_report_row(repo, object_dict(report)) for report in object_list(reports.get("items"))]
    echo_table(headers, rows, "No reports.")


def echo_email_preferences_table(payload: object) -> None:
    headers = ("project", "repo", "audit", "manual", "auto")
    rows = [
        (
            text_cell(row.get("project_name"), fallback=text_cell(row.get("project_id"))),
            text_cell(row.get("github_repo"), fallback=text_cell(row.get("repo_id"))),
            text_cell(row.get("audit")),
            text_cell(row.get("manual_run_completion")),
            text_cell(row.get("scheduled_run_completion")),
        )
        for row in (object_dict(item) for item in object_list(object_dict(payload).get("preferences")))
    ]
    echo_table(headers, rows, "No email preferences.")


def echo_schedule_settings_table(payload: object) -> None:
    schedule_rows = [object_dict(item) for item in object_list(object_dict(payload).get("schedules"))]
    headers = schedule_settings_headers(schedule_rows)
    rows = [schedule_settings_row(row, include_status="status" in headers) for row in schedule_rows]
    echo_table(headers, rows, "No schedules.")
    for warning in schedule_timezone_warnings(schedule_rows):
        typer.echo(warning)


def schedule_settings_headers(rows: list[dict[str, object]]) -> tuple[str, ...]:
    base = ("project", "repo", "audit", "enabled", "freq", "days", "at", "timezone")
    if any("status" in row for row in rows):
        return (*base, "status")
    return base


def schedule_settings_row(row: dict[str, object], *, include_status: bool) -> tuple[str, ...]:
    base = (
        text_cell(row.get("project_name"), fallback=text_cell(row.get("project_id"))),
        text_cell(row.get("github_repo"), fallback=text_cell(row.get("repo_id"))),
        text_cell(row.get("audit")),
        text_cell(row.get("enabled")),
        text_cell(row.get("frequency")),
        days_cell(row.get("days_of_week")),
        schedule_at_cell(row),
        text_cell(row.get("timezone")),
    )
    if include_status:
        return (*base, text_cell(row.get("status")))
    return base


def schedule_timezone_warnings(rows: list[dict[str, object]]) -> list[str]:
    by_repo: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        if row.get("enabled") is not True:
            continue
        timezone = row.get("timezone")
        audit = row.get("audit")
        if not isinstance(timezone, str) or not isinstance(audit, str):
            continue
        repo = text_cell(row.get("github_repo"), fallback=text_cell(row.get("repo_id")))
        by_repo.setdefault(repo, {}).setdefault(timezone, []).append(audit)
    warnings: list[str] = []
    for repo, audits_by_timezone in by_repo.items():
        if len(audits_by_timezone) < MIN_TIMEZONE_DIVERGENCE_COUNT:
            continue
        parts = [f"{timezone}: {', '.join(sorted(audits))}" for timezone, audits in sorted(audits_by_timezone.items())]
        warnings.append(f"timezone divergence: {repo}: {'; '.join(parts)}")
    return warnings


def echo_auth_status(payload: object) -> None:
    data = object_dict(payload)
    authenticated = "yes" if data.get("authenticated") is True else "no"
    typer.echo(f"authenticated: {authenticated}")
    for key in ("credential_type", "email", "name", "user_id", "auth_file", "code", "message"):
        value = text_cell(data.get(key))
        if value != "-":
            typer.echo(f"{key}: {value}")


def echo_audit_catalog(payload: object) -> None:
    headers = ("audit", "label", "job_kind", "route")
    rows = [
        (
            text_cell(audit.get("alias")),
            text_cell(audit.get("label")),
            text_cell(audit.get("job_kind")),
            text_cell(audit.get("route_slug")),
        )
        for audit in (object_dict(item) for item in object_list(payload))
    ]
    echo_table(headers, rows, "No audits.")


def echo_audit_start(payload: object) -> None:
    data = object_dict(payload)
    preflight = object_dict(data.get("preflight"))
    counts = object_dict(preflight.get("counts"))
    warning = object_dict(preflight.get("warning"))
    typer.echo(
        "preflight: "
        f"{text_cell(counts.get('ready'))} ready, "
        f"{text_cell(counts.get('running'))} running, "
        f"{text_cell(counts.get('stale'))} stale"
    )
    typer.echo(f"warning: {text_cell(warning.get('code'))} {text_cell(warning.get('message'))}")
    results = object_list(data.get("results"))
    state_counts: dict[str, int] = {
        "started": 0,
        "queued": 0,
        "already_running": 0,
        "up_to_date": 0,
        "failed": 0,
    }
    for result in (object_dict(item) for item in results):
        state = result.get("state")
        if isinstance(state, str):
            state_counts[state] = state_counts.get(state, 0) + 1
    results_summary = ", ".join(f"{state}={state_counts[state]}" for state in state_counts)
    typer.echo(f"results: {results_summary}")


def echo_wait_status(payload: object) -> None:
    data = object_dict(payload)
    complete = "yes" if data.get("complete") is True else "no"
    typer.echo(f"complete: {complete}")
    typer.echo(f"fresh: {text_cell(data.get('fresh'))}")
    for key in ("reason", "repo_id", "elapsed_seconds", "current_head_sha", "last_report_at"):
        typer.echo(f"{key}: {text_cell(data.get(key))}")
        if key == "elapsed_seconds":
            typer.echo(f"elapsed_human: {duration_cell(data.get(key))}")
    counts = object_dict(data.get("counts"))
    typer.echo(
        "reports: "
        f"{text_cell(counts.get('ready'))} ready, "
        f"{text_cell(counts.get('running'))} running, "
        f"{text_cell(counts.get('missing'))} missing, "
        f"{text_cell(counts.get('stale'))} stale"
    )
    echo_wait_list("missing", data)
    echo_wait_list("running", data)
    echo_wait_list("failed", data)
    echo_wait_list("stale", data)


def echo_wait_list(key: str, data: dict[str, object]) -> None:
    values = [value for value in object_list(data.get(key)) if isinstance(value, str)]
    if values:
        typer.echo(f"{key}: {', '.join(values)}")


def echo_wait_heartbeat(payload: dict[str, object]) -> None:
    data = object_dict(payload)
    counts = object_dict(data.get("counts"))
    typer.echo(
        "wait heartbeat: "
        f"elapsed_seconds={text_cell(data.get('elapsed_seconds'))} "
        f'elapsed_human="{duration_cell(data.get("elapsed_seconds"))}" '
        f"ready={text_cell(counts.get('ready'))} "
        f"running={text_cell(counts.get('running'))} "
        f"missing={text_cell(counts.get('missing'))} "
        f"stale={text_cell(counts.get('stale'))} "
        f"current_head_sha={text_cell(data.get('current_head_sha'))}",
        err=True,
    )


def echo_generic_payload(payload: object) -> None:
    echo_key_values(object_dict(payload))


def echo_access(payload: object) -> None:
    data = object_dict(payload)
    limits = object_dict(data.get("limits"))
    for key in ("group", "full_access"):
        typer.echo(f"{key}: {text_cell(data.get(key))}")
    for key in (
        "can_use_schedules",
        "can_add_repo",
        "can_create_project",
        "can_run_one_shot_autofix",
        "can_run_one_shot_pentest",
    ):
        typer.echo(f"{key}: {text_cell(limits.get(key))}")
    for key in ("audit_runs", "autofix_runs"):
        typer.echo(f"{key}: {value_cell(limits.get(key))}")


def duration_cell(value: object) -> str:
    if not isinstance(value, int):
        return "-"
    return format_duration_seconds(value)


def echo_key_values(payload: dict[str, object]) -> None:
    if not payload:
        typer.echo("No data.")
        return
    for key, value in payload.items():
        typer.echo(f"{key}: {value_cell(value)}")


def payload_projects(payload: object) -> list[dict[str, object]]:
    return [object_dict(project) for project in object_list(object_dict(payload).get("projects"))]


def payload_repos(payload: object) -> list[tuple[dict[str, object], dict[str, object]]]:
    repos: list[tuple[dict[str, object], dict[str, object]]] = []
    for project in payload_projects(payload):
        repos.extend((project, object_dict(repo_value)) for repo_value in object_list(project.get("repos")))
    return repos


def single_status_repo(payload: object) -> dict[str, object] | None:
    repo_pairs = payload_repos(payload)
    if len(repo_pairs) != 1:
        return None
    return repo_pairs[0][1]


def project_row(project: dict[str, object]) -> tuple[str, ...]:
    return (
        project_label(project),
        text_cell(project.get("id"), fallback=text_cell(project.get("project_id"))),
        str(repo_count(project)),
        project_recon_cell(project),
        str(len(object_dict(project.get("scores")))),
    )


def repo_resolve_row(data: dict[str, object], match: dict[str, object]) -> tuple[str, ...]:
    return (
        text_cell(data.get("selector")),
        text_cell(data.get("resolved")),
        project_label(match),
        repo_label(match),
        text_cell(match.get("repo_id")),
        repo_state(match),
    )


def repo_score_row(project: dict[str, object], repo: dict[str, object]) -> tuple[str, ...]:
    scores = object_dict(repo.get("scores"))
    score_summary = object_dict(repo.get("score_summary"))
    return (
        project_label(project),
        repo_label(repo),
        repo_state(repo),
        date_cell(repo.get("last_report_at")),
        score_cell(score_summary.get("overall_score")),
        text_cell(score_summary.get("overall_grade")),
        weakest_cell(score_summary),
        score_cell(scores.get("vulns")),
        score_cell(scores.get("ai-readiness")),
        score_cell(scores.get("tests")),
        score_cell(scores.get("tech-health")),
        score_cell(scores.get("dependency-hygiene")),
        score_cell(scores.get("cognitive-debt")),
        score_cell(scores.get("dead-code")),
    )


def status_report_row(repo: dict[str, object], report: dict[str, object]) -> tuple[str, ...]:
    report_state = object_dict(report.get("report"))
    task_state = object_dict(report.get("task"))
    return (
        repo_label(repo),
        text_cell(report.get("audit")),
        text_cell(report_state.get("readability_state")),
        text_cell(report_state.get("freshness_state")),
        text_cell(task_state.get("lifecycle_state")),
        text_cell(task_state.get("run_status")),
        date_cell(report_state.get("completed_at")),
        date_cell(task_state.get("completed_at")),
        sha_cell(report_state.get("current_head_sha")),
        sha_cell(report_state.get("audited_head_sha")),
    )


def freshness_cell(value: object) -> str:
    if value is True:
        return "stale"
    if value is False:
        return "fresh"
    return "-"


def repo_status_row(project: dict[str, object], repo: dict[str, object]) -> tuple[str, ...]:
    score_summary = object_dict(repo.get("score_summary"))
    reports = object_dict(repo.get("reports"))
    counts = object_dict(reports.get("counts"))
    return (
        project_label(project),
        repo_label(repo),
        repo_state(repo),
        score_cell(score_summary.get("overall_score")),
        weakest_cell(score_summary),
        count_cell(counts.get("readable")),
        stale_audits_cell(reports),
        count_cell(counts.get("active")),
        count_cell(counts.get("queued")),
        count_cell(counts.get("running")),
        count_cell(counts.get("failed")),
        date_cell(repo.get("last_report_at")),
        sha_cell(repo.get("current_head_sha")),
        sha_cell(audited_head(reports)),
    )


def project_label(project: dict[str, object]) -> str:
    return text_cell(
        project.get("project_name"),
        fallback=text_cell(project.get("name"), fallback=text_cell(project.get("project_id"))),
    )


def repo_label(repo: dict[str, object]) -> str:
    return text_cell(repo.get("github_repo"), fallback=text_cell(repo.get("repo_id")))


def repo_state(repo: dict[str, object]) -> str:
    if repo.get("connected") is not True:
        return "disconnected"
    if repo.get("recon_done") is not True:
        return "uninitialized"
    if not object_dict(repo.get("scores")):
        return "unscored"
    return "scored"


def repo_count(project: dict[str, object]) -> int:
    repo_ids = object_list(project.get("repo_ids"))
    if repo_ids:
        return len(repo_ids)
    camel_repo_ids = object_list(project.get("repoIds"))
    if camel_repo_ids:
        return len(camel_repo_ids)
    return len(object_list(project.get("repos")))


def project_recon_cell(project: dict[str, object]) -> str:
    value = project.get("recon_pending")
    if value is None:
        value = project.get("reconPending")
    if value is True:
        return "pending"
    if value is False:
        return "done"
    return "-"


def weakest_cell(score_summary: dict[str, object]) -> str:
    axis = score_summary.get("weakest_axis")
    score = score_cell(score_summary.get("weakest_score"))
    if not isinstance(axis, str) or score == "-":
        return "-"
    return f"{axis}={score}"


def audited_head(reports: dict[str, object]) -> object | None:
    audited_heads: set[str] = set()
    for report_value in object_list(reports.get("items")):
        report = object_dict(report_value)
        audited_head = object_dict(report.get("report")).get("audited_head_sha")
        if isinstance(audited_head, str) and audited_head:
            audited_heads.add(audited_head)
    if not audited_heads:
        return None
    if len(audited_heads) > 1:
        return "mixed"
    return next(iter(audited_heads))


def stale_audits_cell(reports: dict[str, object]) -> str:
    stale = stale_audits(reports)
    return ", ".join(stale) if stale else "-"


def stale_audits(reports: dict[str, object]) -> list[str]:
    stale: list[str] = []
    for report_value in object_list(reports.get("items")):
        report = object_dict(report_value)
        report_state = object_dict(report.get("report"))
        if report_state.get("stale") is True and isinstance(report.get("audit"), str):
            stale.append(cast(str, report["audit"]))
    return stale


def count_cell(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        return "-"
    return str(value)


def sha_cell(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "-"
    return value[:8]


def date_cell(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "-"
    return value[:10]


def days_cell(value: object) -> str:
    days = [item for item in object_list(value) if isinstance(item, str)]
    if not days:
        return "-"
    return ",".join(days)


def schedule_at_cell(row: dict[str, object]) -> str:
    schedule_time = text_cell(row.get("schedule_time"))
    source = row.get("schedule_time_source")
    if source == "auto":
        if schedule_time == "-":
            return "auto"
        return f"{schedule_time} (auto)"
    if source == "user":
        if schedule_time == "-":
            return "manual"
        return f"{schedule_time} (manual)"
    return schedule_time


def score_cell(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.1f}".removesuffix(".0")
    return "-"


def text_cell(value: object, *, fallback: str = "-") -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    return fallback


def value_cell(value: object) -> str:
    if isinstance(value, str | int | float) or value is None or isinstance(value, bool):
        return text_cell(value)
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        return f"{len(value)} field(s)"
    return str(value)
