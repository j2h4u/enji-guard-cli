from typing import cast

from enji_guard_cli.core_impl.models import OWNER_NAME_SLUG_PARTS, ProjectRef, RepoTargetPayload
from enji_guard_cli.core_impl.payloads import json_bool, json_dict, json_str, required_str
from enji_guard_cli.core_impl.repo_status import score_grades, score_summary
from enji_guard_cli.enji_api import JsonObjectPayload, JsonValue


def owner_name_from_slug(slug: str) -> tuple[str | None, str | None]:
    parts = slug.split("/")
    if len(parts) != OWNER_NAME_SLUG_PARTS:
        return None, None
    owner = parts[0]
    name = parts[1]
    if not owner or not name:
        return None, None
    return owner, name


def project_ref_matches(project_ref: ProjectRef, selector: str) -> bool:
    name = project_ref["name"]
    return project_ref["id"] == selector or (name is not None and name.casefold() == selector.casefold())


def ambiguous_project_message(project_refs: list[ProjectRef]) -> str:
    return f"project selector is ambiguous; pass --project. candidates: {project_candidates(project_refs)}"


def project_candidates(project_refs: list[ProjectRef]) -> str:
    return ", ".join(project_candidate(project_ref) for project_ref in project_refs)


def project_candidate(project_ref: ProjectRef) -> str:
    name = project_ref["name"]
    if name is None:
        return str(project_ref["id"])
    return f"{name} ({project_ref['id']})"


def validated_project_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("project name must not be empty")
    return normalized


def transfer_schedule_replacements(preflight: JsonObjectPayload) -> JsonObjectPayload | None:
    replacements = preflight.get("scheduleReplacements")
    if isinstance(replacements, dict):
        return cast(JsonObjectPayload, replacements)
    return None


def parse_github_repo(github_repo: str) -> tuple[str, str]:
    owner, name = owner_name_from_slug(github_repo)
    if owner is None or name is None:
        raise ValueError("repo must be an owner/name GitHub slug")
    return owner, name


def validate_write_scope(
    repo: str | None,
    project: str | None,
    *,
    all_repos: bool,
    all_projects: bool,
    operation: str,
) -> None:
    if all_repos and all_projects:
        raise ValueError(f"{operation}: pass --all-repos or --all-projects, not both")
    if all_projects:
        if repo is not None:
            raise ValueError(f"{operation}: REPO cannot be combined with --all-projects")
        if project is not None:
            raise ValueError(f"{operation}: --project cannot be combined with --all-projects")
        return
    if all_repos:
        if repo is not None:
            raise ValueError(f"{operation}: REPO cannot be combined with --all-repos")
        if project is None:
            raise ValueError(f"{operation}: --all-repos requires --project")
        return
    if repo is None:
        raise ValueError(f"{operation}: pass REPO, --all-repos with --project, or --all-projects")


def repo_target_matches(target: RepoTargetPayload, selector: str) -> bool:
    return selector == target["repo_id"] or selector == target["github_repo"]


def ambiguous_repo_message(selector: str, matches: list[RepoTargetPayload]) -> str:
    candidates = ", ".join(repo_candidate(match) for match in matches)
    return f"repo selector is ambiguous: {selector}. candidates: {candidates}"


def repo_candidate(target: RepoTargetPayload) -> str:
    project_name = target["project_name"] or target["project_id"]
    github_repo = target["github_repo"] or target["repo_id"]
    return f"{github_repo} in {project_name} ({target['repo_id']})"


def repo_target(
    project_id: str,
    project_name: str | None,
    repo: dict[str, JsonValue],
) -> RepoTargetPayload:
    repo_id = required_str(repo, "id", "repo is missing id")
    owner = json_str(repo.get("githubOwner"))
    name = json_str(repo.get("githubName"))
    scores = json_dict(repo.get("scores"))
    return {
        "project_id": project_id,
        "project_name": project_name,
        "repo_id": repo_id,
        "github_owner": owner,
        "github_name": name,
        "github_repo": f"{owner}/{name}" if owner is not None and name is not None else None,
        "connected": json_bool(repo.get("connected")),
        "recon_done": json_bool(repo.get("reconDone")),
        "scores": scores,
        "score_grades": score_grades(scores),
        "score_summary": score_summary(scores),
    }


def targeted_run_payload(target: RepoTargetPayload, payload: object) -> dict[str, object]:
    if isinstance(payload, dict):
        result: dict[str, object] = {"target": target}
        result.update(payload)
        return result
    return {"target": target, "result": payload}
