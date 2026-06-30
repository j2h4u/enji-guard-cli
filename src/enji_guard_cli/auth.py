import asyncio
import base64
import binascii
import contextlib
import fcntl
import json
import logging
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from http.cookies import Morsel, SimpleCookie
from os import O_RDONLY, close, fsync
from os import open as os_open
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from enji_guard_cli.settings import DEFAULT_BASE_URL, AutoRefreshSettings, default_settings
from enji_guard_cli.telemetry import log_event
from enji_guard_cli.transport import (
    EnjiHttpClient,
    EnjiHttpError,
    EnjiHttpRequest,
    EnjiHttpResponse,
    HttpxEnjiHttpClient,
    raise_for_response_status,
)

AUTH_REFRESH_PATH = "/api/v1/auth/refresh"
AUTH_INVALID_CODE = "AUTH_INVALID"
AUTH_REFRESH_ORIGIN = "https://guard.enji.ai"
AUTH_REFRESH_REFERER = "https://guard.enji.ai/"
AUTH_REFRESH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_AUTH_FAILURE_CODES = frozenset({401, 403})
HTTP_TRANSIENT_REFRESH_ERROR_CODES = frozenset({429, 500, 502, 503, 504})
JWT_MIN_PART_COUNT = 2
AUTH_COOKIE_NAMES = frozenset({"access_token", "refresh_token"})
REFRESH_TOKEN_COOKIE_NAME = "refresh_token"
_LOGGER = logging.getLogger(__name__)
_COOKIE_REFRESH_LOCK = asyncio.Lock()


class CredentialType(StrEnum):
    COOKIE = "cookie"
    BEARER_TOKEN = "bearer_token"


class AuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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


class ImportCredentialPayload(TypedDict):
    ok: bool
    auth_file: str
    credential_type: str
    cookie_count: NotRequired[int]


class AuthStatusPayload(TypedDict):
    authenticated: bool
    code: str | None
    message: str | None
    auth_file: str
    credential_type: str | None
    email: str | None
    name: str | None
    user_id: str | None


class AuthRefreshPayload(TypedDict):
    ok: bool
    auth_file: str
    credential_type: str
    cookie_count: int
    access_expires_at: str | None


class AuthenticatedProfile(TypedDict):
    email: str | None
    name: str | None
    user_id: str | None


@dataclass(frozen=True, slots=True)
class CookieHeader:
    value: str
    count: int


def default_auth_file() -> Path:
    return default_settings().auth.auth_file


def normalize_cookie_header(raw_cookie: str) -> CookieHeader:
    cookie_line = _extract_cookie_line(raw_cookie)
    if not cookie_line:
        raise ValueError("cookie input is empty")

    cookie = SimpleCookie()
    cookie.load(cookie_line)
    if not cookie:
        raise ValueError("cookie input does not contain cookie pairs")

    normalized = "; ".join(f"{name}={morsel.coded_value}" for name, morsel in cookie.items())
    return CookieHeader(value=normalized, count=len(cookie))


def normalize_bearer_token(raw_token: str) -> str:
    token = raw_token.strip()
    if token.lower().startswith("authorization:"):
        token = token.split(":", 1)[1].strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    if not token:
        raise ValueError("token input is empty")
    return token


def import_cookie(
    raw_cookie: str, auth_file: Path | None = None, base_url: str = DEFAULT_BASE_URL
) -> ImportCredentialPayload:
    cookie_header = normalize_cookie_header(raw_cookie)
    target = auth_file if auth_file is not None else default_auth_file()
    _write_auth_file(
        target,
        _stored_auth(base_url, {"type": "cookie", "cookie_header": cookie_header.value}),
    )
    return {
        "ok": True,
        "auth_file": str(target),
        "credential_type": CredentialType.COOKIE.value,
        "cookie_count": cookie_header.count,
    }


def import_bearer_token(
    raw_token: str,
    auth_file: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> ImportCredentialPayload:
    target = auth_file if auth_file is not None else default_auth_file()
    _write_auth_file(
        target,
        _stored_auth(base_url, {"type": "bearer_token", "token": normalize_bearer_token(raw_token)}),
    )
    return {
        "ok": True,
        "auth_file": str(target),
        "credential_type": CredentialType.BEARER_TOKEN.value,
    }


def auth_headers(stored_auth: StoredAuth) -> dict[str, str]:
    credential = stored_auth["credential"]
    if credential["type"] == CredentialType.COOKIE.value:
        return {"Cookie": credential["cookie_header"]}
    return {"Authorization": f"Bearer {credential['token']}"}


def load_stored_auth(path: Path) -> StoredAuth | None:
    return _load_auth_file(path)


def cookie_access_expires_at(stored_auth: StoredAuth) -> datetime | None:
    credential = stored_auth["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        return None
    access_token = _cookie_value(credential["cookie_header"], "access_token")
    if access_token is None:
        return None
    return _jwt_expires_at(access_token)


def cookie_refresh_sleep_seconds(
    stored_auth: StoredAuth,
    now: datetime,
    *,
    settings: AutoRefreshSettings | None = None,
) -> int:
    refresh_settings = settings if settings is not None else default_settings().auto_refresh
    expires_at = cookie_access_expires_at(stored_auth)
    if expires_at is None:
        return refresh_settings.fallback_seconds
    refresh_at_delta = int((expires_at - now).total_seconds()) - refresh_settings.lead_seconds
    return max(refresh_at_delta, 0)


def merge_set_cookie_headers(cookie_header: str, set_cookie_headers: Iterable[str]) -> CookieHeader:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    for set_cookie_header in set_cookie_headers:
        updated_cookie = SimpleCookie()
        updated_cookie.load(set_cookie_header)
        for name, morsel in updated_cookie.items():
            _validate_auth_cookie_update(name, morsel)
            cookie[name] = morsel.value

    if not cookie:
        raise ValueError("cookie input does not contain cookie pairs")
    normalized = "; ".join(f"{name}={morsel.coded_value}" for name, morsel in cookie.items())
    return CookieHeader(value=normalized, count=len(cookie))


def replace_cookie_credential(path: Path, stored_auth: StoredAuth, cookie_header: str) -> StoredAuth:
    credential = stored_auth["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        raise ValueError("stored credential is not cookie based")
    updated_auth: StoredAuth = {
        "version": stored_auth["version"],
        "base_url": stored_auth["base_url"],
        "credential": {"type": "cookie", "cookie_header": cookie_header},
        "imported_at": stored_auth["imported_at"],
    }
    _write_auth_file(path, updated_auth)
    return updated_auth


def auth_status(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AuthStatusPayload:
    return asyncio.run(auth_status_async(auth_file, client))


def refresh_auth(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AuthRefreshPayload:
    return asyncio.run(refresh_auth_async(auth_file, client))


async def auth_status_async(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AuthStatusPayload:
    target = auth_file if auth_file is not None else default_auth_file()
    if not target.exists():
        return _unauthenticated_payload(target, None, "AUTH_REQUIRED", "auth file does not exist")

    stored_auth = _load_auth_file(target)
    if stored_auth is None:
        return _unauthenticated_payload(target, None, "AUTH_REQUIRED", "auth file is invalid")

    if client is not None:
        return await _auth_status_with_client(target, stored_auth, client)

    async with HttpxEnjiHttpClient() as owned_client:
        return await _auth_status_with_client(target, stored_auth, owned_client)


async def refresh_auth_async(auth_file: Path | None = None, client: EnjiHttpClient | None = None) -> AuthRefreshPayload:
    target = auth_file if auth_file is not None else default_auth_file()
    try:
        if client is not None:
            refreshed_auth = await refresh_stored_cookie_auth(target, client)
            return _auth_refresh_payload(target, refreshed_auth)

        async with HttpxEnjiHttpClient() as owned_client:
            refreshed_auth = await refresh_stored_cookie_auth(target, owned_client)
            return _auth_refresh_payload(target, refreshed_auth)
    except EnjiHttpError as exc:
        raise AuthError(exc.code, exc.message) from exc


def start_auto_refresh_task() -> asyncio.Task[None] | None:
    settings = default_settings()
    if not settings.auto_refresh.enabled:
        return None
    return asyncio.create_task(
        _auto_refresh_loop(
            auth_file=settings.auth.auth_file,
            refresh_settings=settings.auto_refresh,
        ),
        name="enji-guard-auth-auto-refresh",
    )


async def _auto_refresh_loop(
    *,
    auth_file: Path,
    refresh_settings: AutoRefreshSettings,
) -> None:
    async with HttpxEnjiHttpClient() as client:
        while True:
            sleep_seconds = _auto_refresh_sleep_seconds(
                auth_file=auth_file,
                refresh_settings=refresh_settings,
            )
            log_event(
                _LOGGER,
                logging.INFO,
                "enji_auth_auto_refresh_scheduled",
                {"sleep_seconds": sleep_seconds, "auth_file": str(auth_file)},
            )
            await asyncio.sleep(sleep_seconds)
            try:
                refreshed_auth = await refresh_stored_cookie_auth(auth_file, client)
            except EnjiHttpError as exc:
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "enji_auth_auto_refresh_failed",
                    {
                        "code": exc.code,
                        "status_code": exc.status_code,
                        "retry_seconds": refresh_settings.retry_seconds,
                    },
                )
                await asyncio.sleep(refresh_settings.retry_seconds)
            else:
                expires_at = cookie_access_expires_at(refreshed_auth)
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "enji_auth_auto_refresh_succeeded",
                    {"access_expires_at": expires_at.isoformat() if expires_at is not None else None},
                )


def _auto_refresh_sleep_seconds(*, auth_file: Path, refresh_settings: AutoRefreshSettings) -> int:
    stored_auth = load_stored_auth(auth_file)
    if stored_auth is None:
        return refresh_settings.fallback_seconds
    return cookie_refresh_sleep_seconds(stored_auth, datetime.now(UTC), settings=refresh_settings)


def _stored_auth(base_url: str, credential: Credential) -> StoredAuth:
    return {
        "version": 1,
        "base_url": base_url,
        "credential": credential,
        "imported_at": datetime.now(UTC).isoformat(),
    }


def _extract_cookie_line(raw_cookie: str) -> str:
    stripped = raw_cookie.strip()
    if not stripped:
        return ""

    for line in stripped.splitlines():
        line = line.strip()
        if line.lower().startswith("cookie:"):
            return line.split(":", 1)[1].strip()

    return stripped


def _write_auth_file(path: Path, payload: StoredAuth) -> None:
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


def _load_auth_file(path: Path) -> StoredAuth | None:
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


async def _auth_status_with_client(
    target: Path,
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
) -> AuthStatusPayload:
    credential_type = stored_auth["credential"]["type"]
    try:
        response = await _request_auth_status(stored_auth, client)
    except EnjiHttpError as exc:
        return _unauthenticated_payload(target, credential_type, exc.code, exc.message)

    if response.status_code == HTTP_OK:
        return _authenticated_payload(target, credential_type, _profile_from_response(response))
    if is_auth_invalid_response(response) and credential_type == CredentialType.COOKIE.value:
        return await _auth_status_after_refresh(target, stored_auth, client)
    return _auth_status_payload_from_response(
        target,
        credential_type,
        response,
        auth_invalid_code="AUTH_REQUIRED",
        auth_invalid_message="stored credential is not authenticated",
    )


def _auth_status_payload_from_response(
    target: Path,
    credential_type: str,
    response: EnjiHttpResponse,
    *,
    auth_invalid_code: str,
    auth_invalid_message: str,
) -> AuthStatusPayload:
    if response.status_code == HTTP_OK:
        return _authenticated_payload(target, credential_type, _profile_from_response(response))
    if is_auth_invalid_response(response):
        return _unauthenticated_payload(target, credential_type, auth_invalid_code, auth_invalid_message)
    if response.status_code in HTTP_AUTH_FAILURE_CODES:
        return _unauthenticated_payload(
            target, credential_type, "AUTH_REQUIRED", "stored credential is not authenticated"
        )
    try:
        raise_for_response_status(
            response,
            operation="auth status",
            expected_statuses=HTTP_AUTH_FAILURE_CODES | {HTTP_OK},
        )
    except EnjiHttpError as exc:
        return _unauthenticated_payload(target, credential_type, exc.code, exc.message)
    return _unauthenticated_payload(target, credential_type, "UPSTREAM", "auth status failed")


async def _auth_status_after_refresh(
    target: Path,
    stored_auth: StoredAuth,
    client: EnjiHttpClient,
) -> AuthStatusPayload:
    try:
        refreshed_auth = await refresh_cookie_auth(target, stored_auth, client)
        response = await _request_auth_status(refreshed_auth, client)
    except EnjiHttpError as exc:
        return _unauthenticated_payload(target, CredentialType.COOKIE.value, exc.code, exc.message)
    return _auth_status_payload_from_response(
        target,
        CredentialType.COOKIE.value,
        response,
        auth_invalid_code=AUTH_INVALID_CODE,
        auth_invalid_message="invalid access token after refresh",
    )


async def refresh_cookie_auth(path: Path, stored_auth: StoredAuth, client: EnjiHttpClient) -> StoredAuth:
    async with _COOKIE_REFRESH_LOCK:
        with _cookie_refresh_file_lock(path):
            latest_auth = _latest_auth_for_refresh(path, stored_auth)
            if latest_auth is not stored_auth:
                return latest_auth
            return await _refresh_cookie_auth_unlocked(path, stored_auth, client)


async def refresh_stored_cookie_auth(path: Path, client: EnjiHttpClient) -> StoredAuth:
    stored_auth = load_stored_auth(path)
    if stored_auth is None:
        raise EnjiHttpError("AUTH_REQUIRED", "auth file is invalid")
    return await refresh_cookie_auth(path, stored_auth, client)


async def _refresh_cookie_auth_unlocked(path: Path, stored_auth: StoredAuth, client: EnjiHttpClient) -> StoredAuth:
    credential = stored_auth["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        raise EnjiHttpError("AUTH_REQUIRED", "stored credential is not cookie based")
    response = await client.request(
        EnjiHttpRequest(
            method="POST",
            url=f"{stored_auth['base_url']}{AUTH_REFRESH_PATH}",
            operation="auth refresh",
            headers=_auth_refresh_headers(stored_auth),
        )
    )
    if response.status_code in {HTTP_UNAUTHORIZED, HTTP_FORBIDDEN}:
        raise EnjiHttpError(
            "AUTH_REQUIRED", "stored refresh cookie is not authenticated", status_code=response.status_code
        )
    refreshed_auth = _persist_refresh_response_cookies(path, stored_auth, response)
    raise_for_response_status(response, operation="auth refresh", expected_statuses={HTTP_OK})
    if not response.set_cookie_headers:
        raise EnjiHttpError("UPSTREAM", "auth refresh did not return Set-Cookie")
    return refreshed_auth


def _persist_refresh_response_cookies(
    path: Path,
    stored_auth: StoredAuth,
    response: EnjiHttpResponse,
) -> StoredAuth:
    if not response.set_cookie_headers:
        return stored_auth
    if response.status_code != HTTP_OK and not _should_persist_transient_refresh_cookies(response):
        return stored_auth
    credential = stored_auth["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        raise EnjiHttpError("AUTH_REQUIRED", "stored credential is not cookie based")
    try:
        cookie_header = merge_set_cookie_headers(credential["cookie_header"], response.set_cookie_headers)
        return replace_cookie_credential(path, stored_auth, cookie_header.value)
    except OSError as exc:
        raise EnjiHttpError("STORAGE", f"failed to persist refreshed cookie: {exc}") from exc
    except ValueError as exc:
        raise EnjiHttpError("AUTH_REQUIRED", str(exc)) from exc


def _should_persist_transient_refresh_cookies(response: EnjiHttpResponse) -> bool:
    if response.status_code not in HTTP_TRANSIENT_REFRESH_ERROR_CODES:
        return False
    updated_cookie = SimpleCookie()
    for set_cookie_header in response.set_cookie_headers:
        updated_cookie.load(set_cookie_header)
    if REFRESH_TOKEN_COOKIE_NAME not in updated_cookie:
        return False
    return all(
        _is_persistable_auth_cookie_update(name, morsel)
        for name, morsel in updated_cookie.items()
        if name in AUTH_COOKIE_NAMES
    )


def _validate_auth_cookie_update(name: str, morsel: Morsel[str]) -> None:
    if name not in AUTH_COOKIE_NAMES:
        return
    if not _is_persistable_auth_cookie_update(name, morsel):
        raise ValueError(f"auth refresh returned non-persistable {name} cookie")


def _is_persistable_auth_cookie_update(name: str, morsel: Morsel[str]) -> bool:
    if name not in AUTH_COOKIE_NAMES:
        return True
    return bool(morsel.value) and not _morsel_deletes_cookie(morsel)


def _morsel_deletes_cookie(morsel: Morsel[str]) -> bool:
    max_age = _morsel_attribute(morsel, "max-age").strip()
    if max_age.startswith("-") or max_age == "0":
        return True
    expires = _morsel_attribute(morsel, "expires").strip()
    if not expires:
        return False
    try:
        expires_at = parsedate_to_datetime(expires)
    except TypeError, ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _morsel_attribute(morsel: Morsel[str], name: str) -> str:
    value = cast(object, morsel[name])
    return value if isinstance(value, str) else ""


def _latest_auth_for_refresh(path: Path, stored_auth: StoredAuth) -> StoredAuth:
    latest_auth = load_stored_auth(path)
    if latest_auth is None:
        return stored_auth

    latest_credential = latest_auth["credential"]
    stored_credential = stored_auth["credential"]
    if latest_credential["type"] != CredentialType.COOKIE.value:
        return latest_auth
    if stored_credential["type"] != CredentialType.COOKIE.value:
        return latest_auth
    if latest_credential["cookie_header"] != stored_credential["cookie_header"]:
        return latest_auth
    return stored_auth


@contextlib.contextmanager
def _cookie_refresh_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        lock_path.chmod(0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def is_auth_invalid_response(response: EnjiHttpResponse) -> bool:
    if response.status_code != HTTP_UNAUTHORIZED:
        return False
    try:
        payload = response.json(operation="auth invalid check")
    except EnjiHttpError:
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if isinstance(error, dict):
        return error.get("code") == AUTH_INVALID_CODE
    return payload.get("code") == AUTH_INVALID_CODE


def _auth_refresh_headers(stored_auth: StoredAuth) -> dict[str, str]:
    headers = auth_headers(stored_auth)
    headers.update(
        {
            "Origin": AUTH_REFRESH_ORIGIN,
            "Referer": AUTH_REFRESH_REFERER,
            "User-Agent": AUTH_REFRESH_USER_AGENT,
        }
    )
    return headers


def _auth_refresh_payload(auth_file: Path, stored_auth: StoredAuth) -> AuthRefreshPayload:
    credential = stored_auth["credential"]
    if credential["type"] != CredentialType.COOKIE.value:
        raise EnjiHttpError("AUTH_REQUIRED", "stored credential is not cookie based")
    expires_at = cookie_access_expires_at(stored_auth)
    cookie = SimpleCookie()
    cookie.load(credential["cookie_header"])
    return {
        "ok": True,
        "auth_file": str(auth_file),
        "credential_type": CredentialType.COOKIE.value,
        "cookie_count": len(cookie),
        "access_expires_at": expires_at.isoformat() if expires_at is not None else None,
    }


def _cookie_value(cookie_header: str, name: str) -> str | None:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    if name not in cookie:
        return None
    return cookie[name].value


def _jwt_expires_at(token: str) -> datetime | None:
    parts = token.split(".")
    if len(parts) < JWT_MIN_PART_COUNT:
        return None
    payload_segment = parts[1]
    padding = "=" * (-len(payload_segment) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode((payload_segment + padding).encode("ascii"))
        payload = cast(object, json.loads(payload_bytes.decode("utf-8")))
    except binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(exp, UTC)


async def _request_auth_status(stored_auth: StoredAuth, client: EnjiHttpClient) -> EnjiHttpResponse:
    return await client.request(
        EnjiHttpRequest(
            method="GET",
            url=f"{stored_auth['base_url']}/api/v1/auth/me",
            headers=auth_headers(stored_auth),
            timeout_seconds=20.0,
            operation="auth status",
        )
    )


def _profile_from_response(response: EnjiHttpResponse) -> AuthenticatedProfile:
    payload = response.json(operation="auth status")
    if not isinstance(payload, dict):
        return {"email": None, "name": None, "user_id": None}
    return {
        "email": _optional_str(payload.get("email")),
        "name": _optional_str(payload.get("name")),
        "user_id": _optional_str(payload.get("user_id")),
    }


def _authenticated_payload(
    auth_file: Path,
    credential_type: str,
    profile: AuthenticatedProfile,
) -> AuthStatusPayload:
    return {
        "authenticated": True,
        "code": None,
        "message": None,
        "auth_file": str(auth_file),
        "credential_type": credential_type,
        "email": profile["email"],
        "name": profile["name"],
        "user_id": profile["user_id"],
    }


def _unauthenticated_payload(
    auth_file: Path,
    credential_type: str | None,
    code: str,
    message: str,
) -> AuthStatusPayload:
    return {
        "authenticated": False,
        "code": code,
        "message": message,
        "auth_file": str(auth_file),
        "credential_type": credential_type,
        "email": None,
        "name": None,
        "user_id": None,
    }


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os_open(path, O_RDONLY)
    except OSError:
        return
    try:
        fsync(directory_fd)
    finally:
        close(directory_fd)
