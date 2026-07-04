from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from enji_guard_cli.core_impl.models import RepoTargetPayload
from enji_guard_cli.json_types import JsonObjectPayload, JsonValue

type ResolveSingleProjectId = Callable[[str | None], str]
type ResolveSingleRepoTarget = Callable[[str, str | None], RepoTargetPayload]
type ValidateProjectName = Callable[[str], str]
type ParseGithubRepo = Callable[[str], tuple[str, str]]
type TransferScheduleReplacements = Callable[[JsonObjectPayload], JsonObjectPayload | None]
type CreateProject = Callable[[str], JsonObjectPayload]
type RenameProject = Callable[[str, str], JsonObjectPayload]
type ProjectDetail = Callable[[str], JsonObjectPayload]
type DeleteProject = Callable[[str], None]
type AddProjectRepo = Callable[[str, str, str], JsonObjectPayload]
type DeleteProjectRepo = Callable[[str, str], None]
type ConnectProjectRepo = Callable[[str, str], JsonObjectPayload]
type PreflightRepoMove = Callable[[str, str, str], JsonObjectPayload]
type MoveRepo[TRepoTransfer] = Callable[[TRepoTransfer], JsonObjectPayload]
type MakeRepoTransfer[TRepoTransfer] = Callable[[str, str, str, JsonObjectPayload | None], TRepoTransfer]


@dataclass(frozen=True, slots=True)
class MoveRepoDependencies[TRepoTransfer]:
    resolve_single_repo_target: ResolveSingleRepoTarget
    resolve_single_project_id: ResolveSingleProjectId
    preflight_repo_move: PreflightRepoMove
    transfer_schedule_replacements: TransferScheduleReplacements
    make_repo_transfer: MakeRepoTransfer[TRepoTransfer]
    move_repo: MoveRepo[TRepoTransfer]


def create_project_payload(
    name: str, *, validate_project_name: ValidateProjectName, create_project: CreateProject
) -> JsonObjectPayload:
    project_name = validate_project_name(name)
    return {
        "project_name": project_name,
        "response": create_project(project_name),
    }


def rename_project_payload(
    project: str,
    name: str,
    *,
    resolve_single_project_id: ResolveSingleProjectId,
    validate_project_name: ValidateProjectName,
    rename_project: RenameProject,
) -> JsonObjectPayload:
    project_id = resolve_single_project_id(project)
    project_name = validate_project_name(name)
    return {
        "project_id": project_id,
        "project_name": project_name,
        "response": rename_project(project_id, project_name),
    }


def delete_project_payload(
    project: str,
    *,
    resolve_single_project_id: ResolveSingleProjectId,
    project_detail: ProjectDetail,
    delete_project: DeleteProject,
) -> JsonObjectPayload:
    project_id = resolve_single_project_id(project)
    repo_count = _project_repo_count(project_detail(project_id))
    if repo_count > 0:
        raise ValueError(f"project is not empty: {repo_count} repo(s)")
    delete_project(project_id)
    return {"project_id": project_id, "deleted": True}


def _project_repo_count(project: JsonObjectPayload) -> int:
    repos = project.get("repos")
    if isinstance(repos, list):
        return len(repos)
    project_payload = project.get("project")
    if isinstance(project_payload, dict):
        repo_ids = project_payload.get("repoIds")
        if isinstance(repo_ids, list):
            return len(repo_ids)
    return 0


def add_repo_payload(
    github_repo: str,
    project: str | None,
    *,
    resolve_single_project_id: ResolveSingleProjectId,
    parse_github_repo: ParseGithubRepo,
    add_project_repo: AddProjectRepo,
) -> JsonObjectPayload:
    project_id = resolve_single_project_id(project)
    github_owner, github_name = parse_github_repo(github_repo)
    response = add_project_repo(project_id, github_owner, github_name)
    return {
        "added": True,
        "already_present": False,
        "connected": _repo_connected(response),
        "project_id": project_id,
        "github_repo": github_repo,
        "repo": response,
        "next_step": "status",
    }


def activate_existing_repo_payload(
    target: RepoTargetPayload,
    *,
    connect_project_repo: ConnectProjectRepo | None,
) -> JsonObjectPayload:
    response: JsonObjectPayload | None = None
    if target.get("connected") is not True and connect_project_repo is not None:
        response = connect_project_repo(target["project_id"], target["repo_id"])
    return {
        "added": False,
        "already_present": True,
        "connected": True if response is not None else target.get("connected"),
        "repo": cast(JsonValue, dict(target)),
        "response": response,
        "next_step": "status",
    }


def _repo_connected(payload: JsonObjectPayload) -> bool | None:
    repo = payload.get("repo")
    if isinstance(repo, dict):
        connected = repo.get("connected")
        if isinstance(connected, bool):
            return connected
    connected = payload.get("connected")
    if isinstance(connected, bool):
        return connected
    return None


def remove_repo_payload(
    repo: str,
    project: str | None,
    *,
    resolve_single_repo_target: ResolveSingleRepoTarget,
    delete_project_repo: DeleteProjectRepo,
) -> JsonObjectPayload:
    target = resolve_single_repo_target(repo, project)
    delete_project_repo(target["project_id"], target["repo_id"])
    return {
        "repo": cast(JsonValue, dict(target)),
        "project_id": target["project_id"],
        "repo_id": target["repo_id"],
        "removed": True,
    }


def move_repo_payload[TRepoTransfer](
    repo: str,
    source_project: str | None,
    target_project: str,
    *,
    dependencies: MoveRepoDependencies[TRepoTransfer],
) -> JsonObjectPayload:
    source = dependencies.resolve_single_repo_target(repo, source_project)
    target_project_id = dependencies.resolve_single_project_id(target_project)
    if source["project_id"] == target_project_id:
        raise ValueError("repo is already in target project")
    preflight = dependencies.preflight_repo_move(source["project_id"], source["repo_id"], target_project_id)
    response = dependencies.move_repo(
        dependencies.make_repo_transfer(
            source["project_id"],
            source["repo_id"],
            target_project_id,
            dependencies.transfer_schedule_replacements(preflight),
        )
    )
    return {
        "repo": cast(JsonValue, dict(source)),
        "source_project_id": source["project_id"],
        "target_project_id": target_project_id,
        "preflight": preflight,
        "response": response,
    }
