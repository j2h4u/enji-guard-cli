"""Deterministic JSON projection shared by every delivery surface."""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import cast

_JSON_NULL_FIELDS = frozenset({"job", "connected", "recon_done", "enabled", "automatic_execution", "score"})


def repository_selector(value: object) -> str | None:
    """Render a domain repository identity without leaking provider wire fields."""
    if type(value).__name__ != "RepositoryIdentity":
        return None
    provider = getattr(getattr(value, "provider", None), "value", None)
    host = getattr(value, "host", None)
    locator = getattr(value, "locator", None)
    if not all(isinstance(part, str) for part in (provider, host, locator)):
        return None
    return f"{provider}@{host}:{locator}"


def _mapping_projection(mapping: Mapping[object, object], *, preserve_mapping_nulls: bool) -> dict[str, object]:
    return {
        str(key): json_projection(item, preserve_mapping_nulls=preserve_mapping_nulls or str(key) == "scores")
        for key, item in mapping.items()
        if item is not None or str(key) in _JSON_NULL_FIELDS or preserve_mapping_nulls
    }


def json_projection(value: object, *, preserve_mapping_nulls: bool = False) -> object:  # noqa: PLR0911
    """Convert typed delivery DTOs to stable JSON-safe values.

    Optional values are omitted unless null carries a distinct product meaning.
    Application views carry only approved provider-neutral fields, so provider
    extensions cannot become an accidental CLI or MCP contract.
    """
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
        return _mapping_projection(cast(Mapping[object, object], value), preserve_mapping_nulls=preserve_mapping_nulls)
    if isinstance(value, (list, tuple, set, frozenset)):
        values = cast(Iterable[object], value)
        return [json_projection(item, preserve_mapping_nulls=preserve_mapping_nulls) for item in values]
    if is_dataclass(value) and not isinstance(value, type):
        return json_projection(asdict(value), preserve_mapping_nulls=preserve_mapping_nulls)
    return str(value)


__all__ = ["json_projection", "repository_selector"]
