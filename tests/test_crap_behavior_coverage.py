from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import enji_guard_cli.enji_gateway.portfolio_gateway as portfolio_gateway_module
from enji_guard_cli.audit.catalog import published_autofix_keys
from enji_guard_cli.audit.ledger import FileAuditLedger, new_entry
from enji_guard_cli.audit.ports import AuditItemStatus
from enji_guard_cli.delivery.mcp.server import _json
from enji_guard_cli.enji_gateway.audit_gateway import _schedule
from enji_guard_cli.enji_gateway.portfolio_gateway import PortfolioGateway
from enji_guard_cli.enji_gateway.wire import audit_rerun_state_from_legacy_payload
from enji_guard_cli.portfolio.models import RepositoryRef
from enji_guard_cli.portfolio.status import RepositoryStatus


def test_schedule_maps_fields_and_rejects_missing_action() -> None:
    assert _schedule({"enabled": True}) is None
    result = _schedule(
        {
            "actionKey": "audit.security",
            "enabled": True,
            "scheduleDayOfMonth": True,
            "scheduleTimeSource": "legacy",
            "windowDays": ["mon", 3],
        }
    )
    assert result is not None
    assert result.enabled is True and result.schedule_day_of_month is None
    assert result.schedule_time_source is None and result.window_days == ("mon",)


def test_repository_freshness_all_states() -> None:
    repo = RepositoryRef("r", "p", None, "o/r")

    def status(items: tuple[AuditItemStatus, ...]) -> RepositoryStatus:
        return RepositoryStatus(repo, None, {}, items, (), None)

    def item(current: str | None, audited: str | None) -> AuditItemStatus:
        return AuditItemStatus("a", current, audited, True, None, None, None, False)

    assert status(()).freshness == "unknown"
    assert status((item(None, "x"),)).freshness == "unknown"
    assert status((item("x", "x"),)).freshness == "fresh"
    assert status((item("x", "y"),)).freshness == "stale"
    assert status((item("x", "x"), item("x", "y"))).freshness == "mixed"


def test_published_autofix_keys_filters_status_and_duplicates() -> None:
    payload = {
        "auditAutofixes": [
            {"actionKey": "a", "variantKey": "v", "status": "published"},
            {"actionKey": "a", "variantKey": "v", "status": "published"},
            {"actionKey": "pentest", "variantKey": "v", "status": "draft"},
            {"actionKey": "bad", "variantKey": 1, "status": "published"},
        ]
    }
    assert published_autofix_keys(payload) == [("a", "v")]


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


def test_rerun_state_reads_nested_and_empty_root_fallback() -> None:
    nested = audit_rerun_state_from_legacy_payload(
        {"state": {"currentHeadSha": "c", "actions": {"audit.a": {"lastAuditedHeadSha": "a"}, "bad": "x"}}}
    )
    assert nested.current_head_sha == "c" and nested.audited_head_shas == {"audit.a": "a"}
    empty = audit_rerun_state_from_legacy_payload({})
    assert empty.current_head_sha is None and empty.audited_head_shas == {}


def test_move_preflight_accepts_current_and_legacy_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portfolio_gateway_module,
        "_preflight_repo_move",
        lambda *args: {"canTransfer": False, "schedule_replacements": ["audit.a", 4], "reason": "blocked"},
    )
    result = PortfolioGateway().preflight_repository_move("source", "repo", "target")
    assert result.allowed is False
    assert result.schedule_replacements == ("audit.a",)
    assert result.message == "blocked"

    monkeypatch.setattr(portfolio_gateway_module, "_preflight_repo_move", lambda *args: {"allowed": True})
    assert PortfolioGateway().preflight_repository_move("s", "r", "t").schedule_replacements == ()
