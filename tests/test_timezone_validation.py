"""Timezone validation must stop mutation before a batch scope can expand."""

from typing import cast

import pytest

from enji_guard_cli.application.catalog import AuditCatalogService
from enji_guard_cli.application.subscriptions import (
    ImprovementJobWriteRequest,
    ScheduleWriteRequest,
    SubscriptionsFacade,
    SubscriptionWriteScope,
)
from enji_guard_cli.audit.ports import AuditGatewayPort
from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.portfolio.ports import PortfolioTargetService
from enji_guard_cli.settings import FanoutSettings

_INVALID_TIMEZONE = "Mars/Olympus_Mons"


class _RecordingTargets:
    def __init__(self) -> None:
        self.write_calls: list[object] = []

    def write_targets(self, *_: object, **__: object) -> tuple[object, ...]:
        self.write_calls.append(object())
        raise AssertionError("invalid timezone must not resolve mutation targets")


class _RecordingGateway:
    def __init__(self) -> None:
        self.schedule_writes: list[object] = []
        self.improvement_writes: list[object] = []

    def set_schedule(self, *_: object) -> None:
        self.schedule_writes.append(object())
        raise AssertionError("invalid timezone must not write an audit schedule")

    def set_improvement_job(self, *_: object) -> None:
        self.improvement_writes.append(object())
        raise AssertionError("invalid timezone must not write an improvement job")


def _subscriptions(targets: _RecordingTargets, gateway: _RecordingGateway) -> SubscriptionsFacade:
    return SubscriptionsFacade(
        catalog=cast(AuditCatalogService, object()),
        gateway=cast(AuditGatewayPort, gateway),
        targets=cast(PortfolioTargetService, targets),
        fanout=BoundedFanout(FanoutSettings(max_concurrency=1)),
    )


@pytest.mark.parametrize("operation", ["schedules", "improvement_jobs"])
def test_invalid_timezone_stops_all_project_mutation_before_target_resolution_and_writes(operation: str) -> None:
    targets = _RecordingTargets()
    gateway = _RecordingGateway()
    subscriptions = _subscriptions(targets, gateway)
    scope = SubscriptionWriteScope(all_projects=True)

    with pytest.raises(ValueError, match=f"unknown IANA timezone: {_INVALID_TIMEZONE}"):
        if operation == "schedules":
            subscriptions.set_schedules(
                ScheduleWriteRequest(None, None, enabled=True, timezone=_INVALID_TIMEZONE, scope=scope)
            )
        else:
            subscriptions.set_improvement_jobs(
                ImprovementJobWriteRequest(
                    None,
                    None,
                    selectors=("__all__",),
                    enabled=True,
                    timezone=_INVALID_TIMEZONE,
                    scope=scope,
                )
            )

    assert targets.write_calls == []
    assert gateway.schedule_writes == []
    assert gateway.improvement_writes == []
