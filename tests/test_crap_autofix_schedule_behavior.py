from dataclasses import dataclass

import pytest

import enji_guard_cli.enji_gateway.audit_gateway as gateway_module
from enji_guard_cli.audit.ports import AuditSchedule, AuditScheduleUpdate
from enji_guard_cli.audit.schedules import set_for_targets
from enji_guard_cli.auth_session.adapters import GatewayCredentialReader
from enji_guard_cli.enji_gateway.audit_gateway import AuditGateway


def test_list_autofix_jobs_maps_variants_and_preserves_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gateway_module,
        "_improvement_jobs",
        lambda *args, **kwargs: {
            "jobs": [
                {
                    "actionKey": "improvement.security",
                    "variantKey": "default",
                    "kind": "vuln-fix",
                    "enabled": True,
                    "autoFix": False,
                    "autofixVariantKey": "security-default",
                    "frequency": "weekly",
                    "daysOfWeek": ["mon", 2, "fri"],
                    "scheduleTime": "09:30",
                    "scheduleTimeSource": "user",
                    "timezone": "UTC",
                    "pentestMode": "safe",
                    "providerField": {"nested": True},
                },
                {"kind": "improvement.tests", "autofixVariantKey": "draft", "enabled": "yes"},
                {"actionKey": "missing-variant"},
            ]
        },
    )
    jobs = AuditGateway(auth_port=GatewayCredentialReader()).list_autofix_jobs("repo-1")
    assert len(jobs) == 2
    first, second = jobs
    assert first.action_key == "improvement.security"
    assert first.variant_key == "default"
    assert first.enabled is True and first.auto_fix is False
    assert first.days_of_week == ("mon", "fri")
    assert first.schedule_time_source == "user"
    assert first.extensions == (("providerField", {"nested": True}),)
    assert second.action_key == "improvement.tests"
    assert second.variant_key == "draft"
    assert second.enabled is None


@dataclass(frozen=True)
class _Target:
    repo_id: str


class _ScheduleGateway:
    def __init__(self, schedules: dict[str, tuple[AuditSchedule, ...]]) -> None:
        self.schedules = schedules
        self.calls: list[tuple[str, str, AuditSchedule]] = []

    def list_schedules(self, repo_id: str) -> tuple[AuditSchedule, ...]:
        return self.schedules.get(repo_id, ())

    def set_schedule(self, repo_id: str, audit_key: str, schedule: AuditSchedule) -> AuditSchedule:
        self.calls.append((repo_id, audit_key, schedule))
        return schedule


def _schedule(key: str, *, enabled: bool = False) -> AuditSchedule:
    return AuditSchedule(key, enabled, "daily", None, 1, "08:00", "user", "UTC")


def test_set_for_targets_updates_existing_creates_enabled_and_skips_unchanged() -> None:
    gateway = _ScheduleGateway(
        {"r1": (_schedule("audit.security", enabled=False), _schedule("audit.tests", enabled=True))}
    )
    result = set_for_targets(
        (_Target("r1"), _Target("r2")),
        ("audit.security", "audit.tests", "audit.recon"),
        AuditScheduleUpdate(enabled=True, timezone="Asia/Almaty"),
        gateway,
    )
    assert [call[:2] for call in gateway.calls] == [
        ("r1", "audit.security"),
        ("r1", "audit.tests"),
        ("r1", "audit.recon"),
        ("r2", "audit.security"),
        ("r2", "audit.tests"),
        ("r2", "audit.recon"),
    ]
    assert len(result) == 6
    assert all(item.audit_key.startswith("audit.") for item in result)
    assert result[0].timezone == "Asia/Almaty"


def test_set_for_targets_returns_unchanged_without_writes() -> None:
    current = _schedule("audit.security", enabled=True)
    gateway = _ScheduleGateway({"r1": (current,)})
    result = set_for_targets(
        (_Target("r1"),),
        ("audit.security",),
        AuditScheduleUpdate(enabled=True),
        gateway,
    )
    assert result == (current,)
    assert gateway.calls == []
