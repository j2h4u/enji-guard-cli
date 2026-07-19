from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from enji_guard_cli.audit.ledger import FileAuditLedger, new_entry
from enji_guard_cli.audit.ports import AuditFreshness, AuditStatus, AuditStatusItem
from enji_guard_cli.delivery.mcp.server import _json
from enji_guard_cli.enji_gateway.audit_gateway import _schedule
from enji_guard_cli.portfolio.models import RepositoryRef
from enji_guard_cli.portfolio.ports import PortfolioAuditStatus
from enji_guard_cli.portfolio.status import RepositoryStatus


def test_schedule_maps_fields_and_rejects_missing_action() -> None:
    assert _schedule({"enabled": True}) is None
    result = _schedule(
        {
            "actionKey": "audit.security",
            "enabled": True,
            "scheduleDayOfMonth": True,
            "scheduleTimeSource": "unsupported",
            "windowDays": ["mon", 3],
        }
    )
    assert result is not None
    assert result.enabled is True and result.schedule_day_of_month is None
    assert result.schedule_time_source is None and result.window_days == ("mon",)


def test_repository_freshness_all_states() -> None:
    repo = RepositoryRef("r", "p", None, "o/r")

    def status(items: tuple[AuditStatusItem, ...]) -> RepositoryStatus:
        return RepositoryStatus(repo, PortfolioAuditStatus(AuditStatus("r", "x", items)))

    def item(current: str | None, audited: str | None) -> AuditStatusItem:
        state = "unknown" if current is None or audited is None else "fresh" if current == audited else "stale"
        return AuditStatusItem("a", "Audit", AuditFreshness(current, audited, state), True, "completed", None, None)

    assert status(()).audit.summary.items == ()
    assert status((item(None, "x"),)).audit.summary.items[0].freshness.state == "unknown"
    assert status((item("x", "x"),)).audit.summary.fresh is True
    assert status((item("x", "y"),)).audit.summary.stale == ("a",)
    assert status((item("x", "x"), item("x", "y"))).audit.summary.mixed is True


def test_ledger_active_for_filters_repo_action_expiry_and_terminal(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    ledger = FileAuditLedger(tmp_path / "ledger.json")
    for key, status, observed, ttl in [
        ("a", "running", now, 100),
        ("b", "completed", now, 100),
        ("c", "running", now - timedelta(seconds=200), 100),
        ("a", "running", now, 100),
    ]:
        ledger.record_started(
            new_entry(
                repo_id="r" if key != "a" or observed == now else "other",
                project_id="p",
                audit_key=key,
                task_id=None,
                task_status=status,
                current_head_sha=None,
                audited_head_sha=None,
                observed_at=observed,
                ttl_seconds=ttl,
            )
        )
    assert [e.audit_key for e in ledger.active_for("r", now=now)] == ["a"]
    assert ledger.active_for("r", "missing", now=now) == ()


@dataclass(frozen=True)
class _Thing:
    value: int


def test_mcp_json_recurses_dataclass_mapping_and_sequences() -> None:
    assert _json(_Thing(3)) == {"value": 3}
    assert _json({1: (_Thing(2), "x")}) == {"1": [{"value": 2}, "x"]}
    assert _json(datetime(2026, 7, 20, 12, 30, tzinfo=UTC)) == "2026-07-20T12:30:00+00:00"
    assert _json(Path("audit.json")) == "audit.json"
    assert _json(b"opaque") == "b'opaque'"
