"""One machine-output contract for the CLI and MCP delivery adapters."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from enji_guard_cli.application.views import RepositoryIdentityView
from enji_guard_cli.delivery.cli.presentation import json_projection as cli_json_projection
from enji_guard_cli.delivery.mcp.server import _json as mcp_json_projection
from enji_guard_cli.delivery.presentation import repository_selector
from enji_guard_cli.portfolio.models import RepositoryIdentity, RepositoryProvider


def test_cli_and_mcp_share_semantic_null_and_provider_neutral_json_projection() -> None:
    fixture = {
        "repository": RepositoryIdentityView("gitlab", "group/service", "gitlab.example.com"),
        "score": None,
        "optional_upstream_value": None,
        "scores": {"audit.security": None, "audit.tests": 0},
    }

    expected = {
        "repository": {"provider": "gitlab", "locator": "group/service", "host": "gitlab.example.com"},
        "score": None,
        "scores": {"audit.security": None, "audit.tests": 0},
    }

    assert cli_json_projection(fixture) == expected
    assert mcp_json_projection(fixture) == expected


@dataclass(frozen=True, slots=True)
class ProjectionFixture:
    value: int


class OpaqueFixture:
    def __str__(self) -> str:
        return "opaque"


def test_shared_projection_covers_scalar_datetime_collection_dataclass_and_fallback_values() -> None:
    identity = RepositoryIdentity(RepositoryProvider.GITHUB, "acme/service", "github.com")

    assert cli_json_projection(None) is None
    assert cli_json_projection(False) is False
    assert cli_json_projection(0) == 0
    assert cli_json_projection("ready") == "ready"
    assert cli_json_projection(Path("audit.md")) == "audit.md"
    assert cli_json_projection(date(2026, 7, 30)) == "2026-07-30"
    assert cli_json_projection(datetime(2026, 7, 30, 12, tzinfo=UTC)) == "2026-07-30T12:00:00+00:00"
    assert cli_json_projection(identity) == "github@github.com:acme/service"
    assert cli_json_projection((ProjectionFixture(3),)) == [{"value": 3}]
    assert cli_json_projection({"one", "two"}) in (["one", "two"], ["two", "one"])
    assert cli_json_projection(frozenset({"one"})) == ["one"]
    assert cli_json_projection(OpaqueFixture()) == "opaque"
    assert cli_json_projection({"optional": None}, preserve_mapping_nulls=True) == {"optional": None}


def test_repository_selector_rejects_incomplete_identity_lookalikes() -> None:
    incomplete_identity = type("RepositoryIdentity", (), {})()

    assert repository_selector(incomplete_identity) is None
