from enji_guard_cli.core_impl.models import DEFAULT_FLOW_CONFIG
from enji_guard_cli.enji_api import JsonObjectPayload, JsonValue


def json_object_payload(payload: object) -> JsonObjectPayload:
    if not isinstance(payload, dict):
        raise ValueError("schedule payload must be a JSON object")
    normalized: JsonObjectPayload = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ValueError("schedule payload keys must be strings")
        normalized[key] = json_value(value)
    return normalized


def json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return json_object_payload(value)
    raise ValueError("schedule payload contains a non-JSON value")


def json_list(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def json_object_list(value: JsonValue | None) -> list[dict[str, JsonValue]]:
    return [item for item in json_list(value) if isinstance(item, dict)]


def json_str(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def json_bool(value: JsonValue | None) -> bool | None:
    return value if isinstance(value, bool) else None


def json_dict(value: JsonValue | None) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def json_list_of_str(value: JsonValue | None) -> list[str]:
    return [item for item in json_list(value) if isinstance(item, str)]


def json_str_values(values: list[str]) -> list[JsonValue]:
    json_values: list[JsonValue] = []
    json_values.extend(values)
    return json_values


def json_object_or_default(value: JsonValue | None) -> JsonObjectPayload:
    if not isinstance(value, dict):
        return dict(DEFAULT_FLOW_CONFIG)
    return value


def required_str(payload: dict[str, JsonValue], key: str, message: str) -> str:
    value = json_str(payload.get(key))
    if value is None:
        raise ValueError(message)
    return value
