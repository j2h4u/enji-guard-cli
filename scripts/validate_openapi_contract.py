#!/usr/bin/env -S uv run python

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

CONTRACT_PATH = Path("contracts/enji-openapi.json")
HTTP_METHODS = frozenset({"get", "put", "post", "patch", "delete", "head", "options", "trace"})
REQUIRED_PATHS = frozenset(
    {
        "/api/v1/auth/me",
        "/api/v1/auth/refresh",
        "/api/ux/me/access",
        "/api/ux/projects",
        "/api/ux/repos/{repoId}/audit-auto-runs",
        "/api/ux/repos/{repoId}/audit-auto-runs/{actionKey}",
        "/api/ux/repos/{repoId}/audits/{actionKey}/email-preferences",
        "/api/v1/projects",
        "/api/v1/projects/{projectId}",
        "/api/ux/projects/{projectId}",
        "/api/ux/projects/{projectId}/repos",
        "/api/ux/projects/{projectId}/repos/{repoId}/connection",
        "/api/ux/repos/{repoId}/audit-runs",
        "/api/ux/github-installations",
        "/api/ux/projects/{sourceProjectId}/repos/{repoId}/transfer/preflight",
        "/api/ux/projects/{sourceProjectId}/repos/{repoId}/transfer",
        "/api/ux/repos/{repoId}/snapshots/upfront.audit.summary",
        "/api/ux/repos/{repoId}/audit-rerun-state",
        "/api/ux/runbook-freshness/{actionKey}",
        "/api/ux/repos/{repoId}/audit-history",
        "/api/v1/tasks/{taskId}",
        "/api/ux/feedback",
    }
)


def main() -> None:
    spec = _load_contract()
    paths = _as_object(spec.get("paths"), "paths")
    _validate_required_paths(paths)
    _validate_operations(paths)
    print(f"OpenAPI contract passed: {len(paths)} path(s)")


def _load_contract() -> dict[str, object]:
    try:
        loaded = cast(object, json.loads(CONTRACT_PATH.read_text(encoding="utf-8")))
    except OSError as exc:
        raise SystemExit(f"cannot read {CONTRACT_PATH}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{CONTRACT_PATH} is not valid JSON: {exc}") from exc
    spec = _as_object(loaded, "contract root")
    if spec.get("openapi") != "3.1.0":
        raise SystemExit("contract must use OpenAPI 3.1.0")
    return spec


def _validate_required_paths(paths: dict[str, object]) -> None:
    missing = REQUIRED_PATHS - set(paths)
    if missing:
        raise SystemExit(f"contract is missing required path(s): {', '.join(sorted(missing))}")


def _validate_operations(paths: dict[str, object]) -> None:
    operation_ids: set[str] = set()
    for path, raw_path_item in paths.items():
        if not path.startswith("/api/"):
            raise SystemExit(f"path outside Enji API namespace: {path}")
        path_item = _as_object(raw_path_item, f"path item {path}")
        operations = list(_iter_operations(path, path_item))
        if not operations:
            raise SystemExit(f"path has no HTTP operations: {path}")
        for method, operation in operations:
            _validate_operation(path, method, operation, operation_ids)


def _iter_operations(path: str, path_item: dict[str, object]) -> Iterator[tuple[str, dict[str, object]]]:
    for method, raw_operation in path_item.items():
        if method in HTTP_METHODS:
            yield method, _as_object(raw_operation, f"operation {method.upper()} {path}")


def _validate_operation(
    path: str,
    method: str,
    operation: dict[str, object],
    operation_ids: set[str],
) -> None:
    operation_id = operation.get("operationId")
    if not isinstance(operation_id, str) or not operation_id:
        raise SystemExit(f"{method.upper()} {path} must define operationId")
    if operation_id in operation_ids:
        raise SystemExit(f"duplicate operationId: {operation_id}")
    operation_ids.add(operation_id)
    responses = _as_object(operation.get("responses"), f"responses for {method.upper()} {path}")
    if not any(status_code.startswith("2") for status_code in responses):
        raise SystemExit(f"{method.upper()} {path} must define a success response")
    if "requestBody" in operation:
        request_body = _as_object(operation["requestBody"], f"requestBody for {method.upper()} {path}")
        if "$ref" in request_body:
            return
        if "content" not in request_body:
            raise SystemExit(f"requestBody for {method.upper()} {path} must define content")


def _as_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SystemExit(f"{context} must be an object")
    return cast(dict[str, object], value)


if __name__ == "__main__":
    main()
