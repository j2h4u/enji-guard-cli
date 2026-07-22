"""Composition adapters exposing narrow auth capabilities to other contexts."""

from collections.abc import AsyncGenerator
from pathlib import Path

from enji_guard_cli.auth_session import api as _api
from enji_guard_cli.auth_session.credential_changes import credential_changes
from enji_guard_cli.auth_session.models import AuthBackendReadinessResult, StoredAuth
from enji_guard_cli.auth_session.ports import AuthOutcomeSink
from enji_guard_cli.auth_session.projection import AuthProjectionError, network_credential, project_auth
from enji_guard_cli.auth_session.store import load_auth, load_journal
from enji_guard_cli.enji_gateway.ports import (
    GatewayCredentialError,
)
from enji_guard_cli.enji_gateway.ports import (
    GatewayCredentialReader as GatewayCredentialReaderPort,
)
from enji_guard_cli.runtime_observability.ports import (
    BackendReadinessObservation,
)
from enji_guard_cli.runtime_observability.ports import (
    RuntimeAuthCoordinator as RuntimeAuthCoordinatorPort,
)
from enji_guard_cli.runtime_observability.telemetry import log_event, persist_event
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


class RuntimeAuthCoordinator(RuntimeAuthCoordinatorPort):
    """Runtime-owned adapter for refresh coordination and readiness observation."""

    def __init__(
        self,
        auth_file: Path | None = None,
        *,
        settings: EnjiGuardSettings | None = None,
        outcome_sink: AuthOutcomeSink = persist_event,
    ) -> None:
        self.settings = settings if settings is not None else default_settings()
        self.auth_file = auth_file if auth_file is not None else self.settings.auth.auth_file
        self.outcome_sink = outcome_sink

    async def reconcile_startup(self) -> None:
        await _api.reconcile_auth_startup(self.auth_file, outcome_sink=self.outcome_sink)

    async def observe_backend_readiness(self) -> BackendReadinessObservation:
        result: AuthBackendReadinessResult = await _api.backend_readiness_probe_async(self.auth_file)
        return BackendReadinessObservation(
            ready=result.ready,
            failure_kind=result.failure_kind,
            failure_code=result.failure_code,
            failure_message=result.failure_message,
            failure_status_code=result.failure_status_code,
            credential_type=result.credential_type,
            elapsed_ms=result.elapsed_ms,
            bypass_grace=result.bypass_grace,
        )

    async def credential_changes(self) -> AsyncGenerator[None]:
        async for _ in credential_changes(self.auth_file):
            yield None

    def start_background_refresh_task(self):
        return _api.start_auto_refresh_task(
            self.auth_file,
            settings=self.settings,
            event_sink=log_event,
            outcome_sink=self.outcome_sink,
        )


__all__ = ["GatewayCredentialReader", "RuntimeAuthCoordinator"]
