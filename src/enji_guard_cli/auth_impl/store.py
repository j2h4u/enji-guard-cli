import contextlib
import json
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from os import O_RDONLY, close, fsync
from os import open as os_open
from pathlib import Path
from typing import Literal, TypedDict, cast


class CredentialType(StrEnum):
    COOKIE = "cookie"
    BEARER_TOKEN = "bearer_token"


class CookieCredential(TypedDict):
    type: Literal["cookie"]
    cookie_header: str


class BearerTokenCredential(TypedDict):
    type: Literal["bearer_token"]
    token: str


Credential = CookieCredential | BearerTokenCredential


class StoredAuth(TypedDict):
    version: int
    base_url: str
    credential: Credential
    imported_at: str


def stored_auth(base_url: str, credential: Credential) -> StoredAuth:
    return {
        "version": 1,
        "base_url": base_url,
        "credential": credential,
        "imported_at": datetime.now(UTC).isoformat(),
    }


def write_auth_file(path: Path, payload: StoredAuth) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(serialized)
            temp_file.flush()
            fsync(temp_file.fileno())
        temp_path.chmod(0o600)
        temp_path.replace(path)
        _fsync_directory(path.parent)
    except OSError:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()
        raise
    path.chmod(0o600)


def load_auth_file(path: Path) -> StoredAuth | None:
    try:
        loaded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    if loaded.get("version") != 1:
        return None
    if not isinstance(loaded.get("base_url"), str):
        return None
    if not isinstance(loaded.get("imported_at"), str):
        return None
    return _load_stored_auth(loaded)


def replace_cookie_credential(path: Path, stored: StoredAuth, cookie_header: str) -> StoredAuth:
    credential = stored["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        raise ValueError("stored credential is not cookie based")
    updated_auth: StoredAuth = {
        "version": stored["version"],
        "base_url": stored["base_url"],
        "credential": {"type": "cookie", "cookie_header": cookie_header},
        "imported_at": stored["imported_at"],
    }
    write_auth_file(path, updated_auth)
    return updated_auth


def _load_stored_auth(loaded: dict[object, object]) -> StoredAuth | None:
    raw_credential = loaded.get("credential")
    if not isinstance(raw_credential, dict):
        return None
    credential = _load_credential(raw_credential)
    if credential is None:
        return None
    return {
        "version": 1,
        "base_url": cast(str, loaded["base_url"]),
        "credential": credential,
        "imported_at": cast(str, loaded["imported_at"]),
    }


def _load_credential(raw_credential: dict[object, object]) -> Credential | None:
    credential_type = raw_credential.get("type")
    cookie_header = raw_credential.get("cookie_header")
    if credential_type == CredentialType.COOKIE.value and isinstance(cookie_header, str):
        return {"type": "cookie", "cookie_header": cookie_header}
    token = raw_credential.get("token")
    if credential_type == CredentialType.BEARER_TOKEN.value and isinstance(token, str):
        return {"type": "bearer_token", "token": token}
    return None


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os_open(path, O_RDONLY)
    except OSError:
        return
    try:
        fsync(directory_fd)
    finally:
        close(directory_fd)
