"""Typed CLI presentation contracts and deterministic JSON projection."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from pathlib import Path

import typer

_JSON_NULL_FIELDS = frozenset({"job", "connected", "recon_done", "enabled", "auto_fix", "score"})


def repository_selector(value: object) -> str | None:
    """Render a repository identity without importing portfolio domain types."""
    if type(value).__name__ != "RepositoryIdentity":
        return None
    provider = getattr(getattr(value, "provider", None), "value", None)
    host = getattr(value, "host", None)
    locator = getattr(value, "locator", None)
    if not all(isinstance(part, str) for part in (provider, host, locator)):
        return None
    return f"{provider}@{host}:{locator}"


def json_projection(value: object, *, preserve_mapping_nulls: bool = False) -> object:  # noqa: PLR0911
    """Convert application DTOs to stable JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    selector = repository_selector(value)
    if selector is not None:
        return selector
    if isinstance(value, Mapping):
        return {
            str(key): json_projection(item, preserve_mapping_nulls=preserve_mapping_nulls or str(key) == "scores")
            for key, item in value.items()
            if item is not None or str(key) in _JSON_NULL_FIELDS or preserve_mapping_nulls
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_projection(item, preserve_mapping_nulls=preserve_mapping_nulls) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return json_projection(asdict(value), preserve_mapping_nulls=preserve_mapping_nulls)
    return str(value)


def render_json(value: object) -> str:
    return json.dumps(json_projection(value), indent=2, sort_keys=True)


def _field_value_text(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _mapping_fields_text(value: dict[object, object]) -> str:
    return "\n".join(f"{key}: {_field_value_text(item)}" for key, item in value.items())


def _sequence_fields_text(value: list[object]) -> str:
    return "\n".join(json.dumps(item, sort_keys=True) for item in value)


def render_fields(value: object) -> str:
    """Readable field output for scalar and mutation DTOs."""
    rendered = json_projection(value)
    if isinstance(rendered, dict):
        return _mapping_fields_text(rendered)
    if isinstance(rendered, list):
        return _sequence_fields_text(rendered)
    return str(rendered)


@dataclass(frozen=True, slots=True)
class CliPresentation[T]:
    """The mandatory human and machine presentation for one CLI command."""

    human_renderer: Callable[[T], str]
    json_renderer: Callable[[T], object] = json_projection

    def human(self, value: T) -> str:
        return self.human_renderer(value)

    def json(self, value: T) -> object:
        return self.json_renderer(value)


FIELDS_PRESENTATION: CliPresentation[object] = CliPresentation(render_fields)


def emit_text(text: str) -> None:
    if text:
        typer.echo(text)
