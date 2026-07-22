import base64
import binascii
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.cookies import Morsel, SimpleCookie
from typing import cast

AUTH_COOKIE_NAMES = frozenset({"access_token", "refresh_token"})
JWT_MIN_PART_COUNT = 2


@dataclass(frozen=True, slots=True)
class CookieHeader:
    value: str
    count: int


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


def merge_set_cookie_headers(cookie_header: str, set_cookie_headers: Iterable[str]) -> CookieHeader:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    for set_cookie_header in set_cookie_headers:
        updated_cookie = SimpleCookie()
        updated_cookie.load(set_cookie_header)
        for name, morsel in updated_cookie.items():
            validate_auth_cookie_update(name, morsel)
            cookie[name] = morsel.value

    if not cookie:
        raise ValueError("cookie input does not contain cookie pairs")
    normalized = "; ".join(f"{name}={morsel.coded_value}" for name, morsel in cookie.items())
    return CookieHeader(value=normalized, count=len(cookie))


def set_cookie_names(set_cookie_headers: Iterable[str]) -> tuple[str, ...]:
    cookie = SimpleCookie()
    for set_cookie_header in set_cookie_headers:
        cookie.load(set_cookie_header)
    return tuple(sorted(cookie))


def cookie_value(cookie_header: str, name: str) -> str | None:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    if name not in cookie:
        return None
    return cookie[name].value


def cookie_count(cookie_header: str) -> int:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    return len(cookie)


def jwt_expires_at(token: str) -> datetime | None:
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


def validate_auth_cookie_update(name: str, morsel: Morsel[str]) -> None:
    if name not in AUTH_COOKIE_NAMES:
        return
    if not is_persistable_auth_cookie_update(name, morsel):
        raise ValueError(f"auth refresh returned non-persistable {name} cookie")


def is_persistable_auth_cookie_update(name: str, morsel: Morsel[str]) -> bool:
    if name not in AUTH_COOKIE_NAMES:
        return True
    return bool(morsel.value) and not _morsel_deletes_cookie(morsel)


def _extract_cookie_line(raw_cookie: str) -> str:
    stripped = raw_cookie.strip()
    if not stripped:
        return ""

    for line in stripped.splitlines():
        line = line.strip()
        if line.lower().startswith("cookie:"):
            return line.split(":", 1)[1].strip()

    return stripped


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
