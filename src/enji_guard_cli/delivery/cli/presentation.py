"""Typed CLI presentation contracts and deterministic JSON projection."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import typer

from enji_guard_cli.delivery.presentation import json_projection


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
