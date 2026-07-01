from collections.abc import Callable
from typing import Never

from enji_guard_cli.core_impl.models import ProjectRef, RepoTargetPayload
from enji_guard_cli.core_impl.payloads import json_dict, json_object_list, json_str
from enji_guard_cli.core_impl.selectors import (
    ambiguous_project_message,
    ambiguous_repo_message,
    project_candidates,
    project_ref_matches,
    repo_target,
    repo_target_matches,
)
from enji_guard_cli.json_types import JsonObjectPayload

type ListProjects = Callable[[], JsonObjectPayload]
type ProjectDetail = Callable[[str], JsonObjectPayload]
type RaiseBadSelector = Callable[[str], Never]


def selected_project_ids(
    project: str | None,
    *,
    list_projects: ListProjects,
    raise_bad_selector: RaiseBadSelector,
) -> list[str]:
    if project is not None:
        return [resolve_single_project_id(project, list_projects=list_projects, raise_bad_selector=raise_bad_selector)]
    return [
        selected_id
        for project_payload in json_object_list(list_projects().get("projects"))
        if (selected_id := json_str(project_payload.get("id"))) is not None
    ]


def resolve_single_project_id(
    project: str | None,
    *,
    list_projects: ListProjects,
    raise_bad_selector: RaiseBadSelector,
) -> str:
    project_reference_list = project_refs(list_projects())
    if project is None:
        if len(project_reference_list) == 1:
            return project_reference_list[0]["id"]
        raise_bad_selector(ambiguous_project_message(project_reference_list))

    matches = [
        project_ref_payload
        for project_ref_payload in project_reference_list
        if project_ref_matches(project_ref_payload, project)
    ]
    if not matches:
        raise_bad_selector(
            f"project selector matched no projects: {project}. candidates: {project_candidates(project_reference_list)}"
        )
    if len(matches) > 1:
        raise_bad_selector(ambiguous_project_message(matches))
    return matches[0]["id"]


def project_refs(projects_payload: JsonObjectPayload) -> list[ProjectRef]:
    refs: list[ProjectRef] = []
    for project_payload in json_object_list(projects_payload.get("projects")):
        project_id = json_str(project_payload.get("id"))
        if project_id is None:
            continue
        refs.append({"id": project_id, "name": json_str(project_payload.get("name"))})
    return refs


def selected_repo_targets(
    repo: str | None,
    project: str | None,
    *,
    list_projects: ListProjects,
    project_detail: ProjectDetail,
    raise_bad_selector: RaiseBadSelector,
) -> list[RepoTargetPayload]:
    if repo is not None:
        return [
            resolve_single_repo_target(
                repo,
                project,
                list_projects=list_projects,
                project_detail=project_detail,
                raise_bad_selector=raise_bad_selector,
            )
        ]
    return [
        target
        for project_id in selected_project_ids(
            project, list_projects=list_projects, raise_bad_selector=raise_bad_selector
        )
        for target in project_repo_targets(project_id, project_detail=project_detail)
    ]


def project_repo_targets(project_id: str, *, project_detail: ProjectDetail) -> list[RepoTargetPayload]:
    project_payload = project_detail(project_id)
    project_metadata = json_dict(project_payload.get("project"))
    project_name = json_str(project_metadata.get("name"))
    return [
        repo_target(project_id, project_name, repo_payload)
        for repo_payload in json_object_list(project_payload.get("repos"))
        if json_str(repo_payload.get("id")) is not None
    ]


def matching_repo_targets(
    selector: str,
    project_ids: list[str],
    *,
    project_detail: ProjectDetail,
) -> list[RepoTargetPayload]:
    matches: list[RepoTargetPayload] = []
    for project_id in project_ids:
        matches.extend(
            target
            for target in project_repo_targets(project_id, project_detail=project_detail)
            if repo_target_matches(target, selector)
        )
    return matches


def resolve_single_repo_target(
    repo: str,
    project: str | None,
    *,
    list_projects: ListProjects,
    project_detail: ProjectDetail,
    raise_bad_selector: RaiseBadSelector,
) -> RepoTargetPayload:
    matches = matching_repo_targets(
        repo,
        selected_project_ids(project, list_projects=list_projects, raise_bad_selector=raise_bad_selector),
        project_detail=project_detail,
    )
    if not matches:
        raise_bad_selector(f"repo selector matched no repos: {repo}")
    if len(matches) > 1:
        raise_bad_selector(ambiguous_repo_message(repo, matches))
    return matches[0]
