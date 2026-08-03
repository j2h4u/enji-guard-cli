"""Read-only API-key credentials for package-first CLI operation."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from enji_guard_cli.auth_session.ports import CredentialError, CredentialReader, RequestCredentials

API_KEY_ENV = "ENJI_GUARD_API_KEY"
API_KEY_FILE_ENV = "ENJI_GUARD_API_KEY_FILE"


@dataclass(frozen=True, slots=True)
class ApiKey:
    value: str = field(repr=False)

    @classmethod
    def parse(cls, raw: str) -> ApiKey:
        value = raw.strip()
        if not value or any(character.isspace() for character in value):
            raise CredentialError("API_KEY_INVALID", "the Enji API key is empty or contains whitespace")
        return cls(value)


@dataclass(frozen=True, slots=True)
class ApiKeyCredentialReader(CredentialReader):
    api_key: ApiKey
    base_url: str

    def load(self, auth_file: Path | None = None) -> RequestCredentials:
        del auth_file
        return RequestCredentials(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key.value}"},
            credential_type="api_key",
        )


@dataclass(frozen=True, slots=True)
class EnvironmentCredentialReader(CredentialReader):
    """Select API-key credentials lazily, falling back to the current store."""

    fallback: CredentialReader
    base_url: str

    def load(self, auth_file: Path | None = None) -> RequestCredentials:
        api_key = api_key_from_environment()
        if api_key is None:
            return self.fallback.load(auth_file)
        return ApiKeyCredentialReader(api_key, self.base_url).load()


def api_key_from_environment(environment: Mapping[str, str] | None = None) -> ApiKey | None:
    source = os.environ if environment is None else environment
    inline = source.get(API_KEY_ENV)
    file_name = source.get(API_KEY_FILE_ENV)
    if inline is not None and not inline.strip():
        inline = None
    if file_name is not None and not file_name.strip():
        file_name = None
    if inline is not None and file_name is not None:
        raise CredentialError(
            "API_KEY_CONFIG_CONFLICT",
            f"configure only one of {API_KEY_ENV} and {API_KEY_FILE_ENV}",
        )
    if inline is not None:
        return ApiKey.parse(inline)
    if file_name is None:
        return None
    path = Path(file_name).expanduser()
    try:
        return ApiKey.parse(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CredentialError(
            "API_KEY_FILE_UNREADABLE", f"cannot read the configured API-key file: {exc.strerror}"
        ) from exc


__all__ = [
    "API_KEY_ENV",
    "API_KEY_FILE_ENV",
    "ApiKey",
    "ApiKeyCredentialReader",
    "EnvironmentCredentialReader",
    "api_key_from_environment",
]
