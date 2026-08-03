"""Adapters exposing narrow Auth Session capabilities to other contexts."""

from pathlib import Path

from enji_guard_cli.auth_session import api as _api
from enji_guard_cli.auth_session.ports import CredentialError, CredentialReader, RequestCredentials
from enji_guard_cli.auth_session.projection import AuthProjectionError, network_credential, project_auth
from enji_guard_cli.auth_session.store import load_auth, load_journal
from enji_guard_cli.settings import EnjiGuardSettings, default_settings


class StoredCredentialReader(CredentialReader):
    """Read-only view of the credentials on disk.

    Nothing here is gateway-specific: it projects the auth file and its journal
    into a usable credential and never writes.  The gateway is simply its
    busiest caller.
    """

    def __init__(self, auth_file: Path | None = None, *, settings: EnjiGuardSettings | None = None) -> None:
        resolved_settings = settings if settings is not None else default_settings()
        self.settings = resolved_settings
        self.auth_file = auth_file if auth_file is not None else resolved_settings.auth.auth_file

    def load(self, auth_file: Path | None = None) -> RequestCredentials:
        target = auth_file if auth_file is not None else self.auth_file
        if target is None:
            target = _api.default_auth_file()
        try:
            stored_auth = network_credential(project_auth(load_auth(target), load_journal(target)))
        except AuthProjectionError as exc:
            raise CredentialError(exc.code, exc.message) from exc
        return RequestCredentials(
            base_url=stored_auth["base_url"],
            headers={**_api.auth_headers(stored_auth), "Origin": self.settings.auth.guard_origin},
            credential_type=stored_auth["credential"]["type"],
        )


__all__ = ["StoredCredentialReader"]
