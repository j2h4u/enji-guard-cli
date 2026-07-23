"""Runtime adapter for Auth Session lifecycle capabilities."""

from collections.abc import AsyncGenerator
from pathlib import Path

from enji_guard_cli.auth_session import api as auth_api
from enji_guard_cli.auth_session.credential_changes import credential_changes
from enji_guard_cli.auth_session.ports import AuthEventSink, AuthOutcomeSink
from enji_guard_cli.runtime_observability.ports import (
    BackendReadinessObservation,
)
from enji_guard_cli.runtime_observability.ports import (
    RuntimeAuthCoordinator as RuntimeAuthCoordinatorPort,
)
from enji_guard_cli.settings import EnjiGuardSettings, default_settings


class RuntimeAuthCoordinatorAdapter(RuntimeAuthCoordinatorPort):
    """Translate Auth Session lifecycle results for the runtime supervisor."""

    def __init__(
        self,
        auth_file: Path | None = None,
        *,
        settings: EnjiGuardSettings | None = None,
        event_sink: AuthEventSink,
        outcome_sink: AuthOutcomeSink,
    ) -> None:
        self.settings = settings if settings is not None else default_settings()
        self.auth_file = auth_file if auth_file is not None else self.settings.auth.auth_file
        self.event_sink = event_sink
        self.outcome_sink = outcome_sink

    async def reconcile_startup(self) -> None:
        await auth_api.reconcile_auth_startup(self.auth_file, outcome_sink=self.outcome_sink)

    async def observe_backend_readiness(self) -> BackendReadinessObservation:
        result = await auth_api.backend_readiness_probe_async(self.auth_file)
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
        return auth_api.start_auto_refresh_task(
            self.auth_file,
            settings=self.settings,
            event_sink=self.event_sink,
            outcome_sink=self.outcome_sink,
        )


__all__ = ["RuntimeAuthCoordinatorAdapter"]
