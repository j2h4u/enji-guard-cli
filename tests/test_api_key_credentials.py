from pathlib import Path

import pytest

from enji_guard_cli.auth_session import CredentialError
from enji_guard_cli.auth_session.api_key import ApiKeyCredentialReader, api_key_from_environment
from enji_guard_cli.enji_gateway.client import EnjiApiSession


def test_inline_api_key_projects_neutral_bearer_credentials() -> None:
    key = api_key_from_environment({"ENJI_GUARD_API_KEY": "secret-value"})

    assert key is not None
    credentials = ApiKeyCredentialReader(key, "https://fleet.example.test").load()

    assert credentials.base_url == "https://fleet.example.test"
    assert credentials.credential_type == "api_key"
    assert credentials.headers == {"Authorization": "Bearer secret-value"}
    assert "secret-value" not in repr(key)
    assert "secret-value" not in repr(credentials)

    session = EnjiApiSession(credentials.base_url, dict(credentials.headers))
    assert "secret-value" not in repr(session)


def test_api_key_file_is_supported_without_exposing_its_value(tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("secret-from-file\n", encoding="utf-8")

    key = api_key_from_environment({"ENJI_GUARD_API_KEY_FILE": str(key_file)})

    assert key is not None
    assert "secret-from-file" not in repr(key)


def test_api_key_sources_are_mutually_exclusive() -> None:
    with pytest.raises(CredentialError, match="configure only one") as caught:
        api_key_from_environment({"ENJI_GUARD_API_KEY": "inline", "ENJI_GUARD_API_KEY_FILE": "/tmp/key"})

    assert caught.value.code == "API_KEY_CONFIG_CONFLICT"


def test_empty_api_key_sources_are_treated_as_unconfigured() -> None:
    assert api_key_from_environment({"ENJI_GUARD_API_KEY": " ", "ENJI_GUARD_API_KEY_FILE": ""}) is None


def test_api_key_rejects_internal_whitespace() -> None:
    with pytest.raises(CredentialError) as caught:
        api_key_from_environment({"ENJI_GUARD_API_KEY": "contains whitespace"})

    assert caught.value.code == "API_KEY_INVALID"
