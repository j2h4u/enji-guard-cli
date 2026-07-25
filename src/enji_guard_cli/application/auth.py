"""Credential import and status, translated into application failures."""

from dataclasses import dataclass

from enji_guard_cli.application.errors import ApplicationAuthError
from enji_guard_cli.auth_session import (
    AuthError,
    AuthSessionService,
    AuthSessionStatus,
    ImportCredentialPayload,
)
from enji_guard_cli.runtime_observability.ports import RuntimeAuthCoordinator


@dataclass(frozen=True, slots=True)
class AuthFacade:
    """Operator credential surface plus the coordinator the supervisor drives."""

    session: AuthSessionService
    runtime_auth: RuntimeAuthCoordinator

    def import_cookie(self, raw_cookie: str) -> ImportCredentialPayload:
        try:
            return self.session.import_cookie(raw_cookie)
        except AuthError as exc:
            raise ApplicationAuthError(exc.code, exc.message) from exc

    def import_bearer(self, raw_token: str) -> ImportCredentialPayload:
        try:
            return self.session.import_bearer_token(raw_token)
        except AuthError as exc:
            raise ApplicationAuthError(exc.code, exc.message) from exc

    def auth_status(self) -> AuthSessionStatus:
        try:
            return self.session.status()
        except AuthError as exc:
            raise ApplicationAuthError(exc.code, exc.message) from exc


__all__ = ["AuthFacade"]
