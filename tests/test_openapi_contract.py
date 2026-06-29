import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from enji_guard_cli.auth import import_bearer_token
from enji_guard_cli.enji_api import access, reports_list
from enji_guard_cli.transport import EnjiHttpRequest, EnjiHttpResponse

CONTRACT_PATH = Path("contracts/enji-openapi.json")
HTTP_METHODS = frozenset({"get", "put", "post", "patch", "delete", "head", "options", "trace"})


@dataclass
class FakeEnjiHttpClient:
    responses: list[EnjiHttpResponse]
    requests: list[EnjiHttpRequest] = field(default_factory=list)

    async def request(self, request: EnjiHttpRequest) -> EnjiHttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def test_implemented_enji_api_paths_exist_in_openapi_contract(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    import_bearer_token("token-123", auth_file)
    client = FakeEnjiHttpClient(
        [
            json_response({"limits": {}}),
            json_response({"projects": []}),
        ]
    )

    access(auth_file, client)
    reports_list(auth_file, client)

    contract = cast(object, json.loads(CONTRACT_PATH.read_text(encoding="utf-8")))
    assert isinstance(contract, dict)
    paths = contract.get("paths")
    assert isinstance(paths, dict)
    contract_operations = {
        (method.upper(), path)
        for path, raw_path_item in paths.items()
        if isinstance(path, str) and isinstance(raw_path_item, dict)
        for method in raw_path_item
        if method in HTTP_METHODS
    }
    requested_operations = {(request.method.upper(), urlsplit(request.url).path) for request in client.requests}

    assert requested_operations <= contract_operations


def json_response(payload: object) -> EnjiHttpResponse:
    return EnjiHttpResponse(
        status_code=200,
        headers={},
        content=json.dumps(payload).encode("utf-8"),
    )
