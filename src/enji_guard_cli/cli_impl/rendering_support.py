from typing import cast


def object_dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def object_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def number_or_none(value: object) -> int | float | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None
