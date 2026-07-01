from enji_guard_cli.core_impl.models import AUDIT_REPORT_SCHEMA, RECON_REPORT_SCHEMA
from enji_guard_cli.core_impl.payloads import json_list, json_object_list, json_str, required_str
from enji_guard_cli.enji_api import JsonObjectPayload, JsonValue


def project_repo(project: JsonObjectPayload, repo_id: str) -> dict[str, JsonValue]:
    for repo in json_object_list(project.get("repos")):
        if json_str(repo.get("id")) == repo_id:
            ensure_repo_connected(repo)
            return repo
    raise ValueError(f"project does not contain repo id: {repo_id}")


def ensure_repo_connected(repo: dict[str, JsonValue]) -> None:
    if repo.get("connected") is not True:
        raise ValueError(f"repo is not connected: {repo_full_name(repo)}")


def catalog_action(catalog: JsonObjectPayload, action_key: str) -> dict[str, JsonValue]:
    for action in json_object_list(catalog.get("curatedActions")):
        if json_str(action.get("actionKey")) == action_key:
            return action
    raise ValueError(f"catalog does not contain action key: {action_key}")


def repo_full_name(repo: dict[str, JsonValue]) -> str:
    owner = required_str(repo, "githubOwner", "repo is missing githubOwner")
    name = required_str(repo, "githubName", "repo is missing githubName")
    return f"{owner}/{name}"


def action_title(action: dict[str, JsonValue]) -> str:
    return required_str(action, "title", "curated action is missing title")


def task_description(
    action: dict[str, JsonValue],
    repo: dict[str, JsonValue],
    web_resources: list[dict[str, JsonValue]],
) -> str:
    template = json_str(action.get("taskDescriptionTemplate")) or default_task_description_template()
    variables = task_description_variables(action, repo, web_resources)
    for name, value in variables.items():
        template = template.replace(f"{{{{{name}}}}}", value)
    return template


def task_description_variables(
    action: dict[str, JsonValue],
    repo: dict[str, JsonValue],
    web_resources: list[dict[str, JsonValue]],
) -> dict[str, str]:
    full_name = repo_full_name(repo)
    return {
        "recurringPrefix": f"Task created from {required_str(action, 'actionKey', 'actionKey is missing')} for {full_name}.",
        "repoFullName": full_name,
        "repoUrl": f"https://github.com/{full_name}",
        "linkedSites": linked_sites_markdown(web_resources),
        "artifactSchemaName": required_str(
            action, "artifactSchemaName", "curated action is missing artifactSchemaName"
        ),
        "artifactSchemaVersion": required_str(
            action, "artifactSchemaVersion", "curated action is missing artifactSchemaVersion"
        ),
        "reportSchemaName": report_schema_name(action),
        "constraintsSection": "- use task title/description only",
        "pentestSection": "",
        "autofixSection": "",
    }


def default_task_description_template() -> str:
    return (
        "{{recurringPrefix}}\n"
        "\n"
        "Repository:\n"
        "- full_name: {{repoFullName}}\n"
        "- url: {{repoUrl}}\n"
        "\n"
        "Linked websites:\n"
        "{{linkedSites}}\n"
        "\n"
        "Artifact contract for this run:\n"
        "- structured artifact metadata.schema_name={{artifactSchemaName}}\n"
        "- structured artifact metadata.schema_version={{artifactSchemaVersion}}\n"
        "- markdown report metadata.schema_name={{reportSchemaName}}\n"
        "- artifacts must remain machine-readable and deterministic\n"
        "\n"
        "Constraints:\n"
        "{{constraintsSection}}"
    )


def report_schema_name(action: dict[str, JsonValue]) -> str:
    runbook_kind = json_str(action.get("runbookKind"))
    if runbook_kind == "recon":
        return RECON_REPORT_SCHEMA
    return AUDIT_REPORT_SCHEMA


def linked_web_resources(project: JsonObjectPayload, repo_id: str) -> list[dict[str, JsonValue]]:
    return [
        resource for resource in json_object_list(project.get("webResources")) if resource_links_repo(resource, repo_id)
    ]


def resource_links_repo(resource: dict[str, JsonValue], repo_id: str) -> bool:
    return repo_id in [item for item in json_list(resource.get("repoIds")) if isinstance(item, str)]


def linked_sites_markdown(web_resources: list[dict[str, JsonValue]]) -> str:
    urls = [json_str(resource.get("url")) for resource in web_resources]
    linked_urls = [url for url in urls if url is not None]
    if not linked_urls:
        return "- none linked yet"
    return "\n".join(f"- {url}" for url in linked_urls)
