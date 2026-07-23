"""Adapters exposing narrow Auth Session capabilities to other contexts."""

from pathlib import Path

from enji_guard_cli.auth_session import api as _api
from enji_guard_cli.auth_session.models import StoredAuth
from enji_guard_cli.auth_session.projection import AuthProjectionError, network_credential, project_auth
from enji_guard_cli.auth_session.store import load_auth, load_journal
from enji_guard_cli.enji_gateway.ports import (
    GatewayCredentialError,
)
from enji_guard_cli.enji_gateway.ports import (
    GatewayCredentialReader as GatewayCredentialReaderPort,
)
from enji_guard_cli.settings import EnjiGuardSettings, default_settings


class GatewayCredentialReader(GatewayCredentialReaderPort):
    """Read-only credential adapter used by gateway requests."""

    def __init__(self, auth_file: Path | None = None, *, settings: EnjiGuardSettings | None = None) -> None:
        resolved_settings = settings if settings is not None else default_settings()
        self.auth_file = auth_file if auth_file is not None else resolved_settings.auth.auth_file

    def load(self, auth_file: Path | None = None) -> StoredAuth:
        target = auth_file if auth_file is not None else self.auth_file
        if target is None:
            target = _api.default_auth_file()
        try:
            return network_credential(project_auth(load_auth(target), load_journal(target)))
        except AuthProjectionError as exc:
            raise GatewayCredentialError(exc.code, exc.message) from exc

    def headers(self, stored_auth: StoredAuth) -> dict[str, str]:
        return _api.auth_headers(stored_auth)


__all__ = ["GatewayCredentialReader"]
