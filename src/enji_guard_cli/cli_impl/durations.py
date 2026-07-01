SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR
SHORT_DURATION_SECONDS_LIMIT = 5 * SECONDS_PER_MINUTE

type DurationSeconds = int


def parse_duration_seconds(value: str) -> DurationSeconds:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("duration cannot be empty")
    suffix_multipliers = {
        "s": 1,
        "m": SECONDS_PER_MINUTE,
        "h": SECONDS_PER_HOUR,
        "d": SECONDS_PER_DAY,
    }
    suffix = normalized[-1]
    if suffix in suffix_multipliers:
        amount = normalized[:-1]
        multiplier = suffix_multipliers[suffix]
    else:
        amount = normalized
        multiplier = 1
    if not amount.isdigit():
        raise ValueError("duration must be an integer optionally followed by s, m, h, or d")
    return int(amount) * multiplier


def format_duration_seconds(seconds: int) -> str:
    normalized_seconds = max(seconds, 0)
    days, day_remainder = divmod(normalized_seconds, SECONDS_PER_DAY)
    hours, hour_remainder = divmod(day_remainder, SECONDS_PER_HOUR)
    minutes, remaining_seconds = divmod(hour_remainder, SECONDS_PER_MINUTE)

    if days > 0:
        return join_duration_parts((days, "d"), (hours, "h"))
    if hours > 0:
        return join_duration_parts((hours, "h"), (minutes, "m"))
    if normalized_seconds > SHORT_DURATION_SECONDS_LIMIT:
        return f"{minutes}m"
    if minutes > 0:
        return join_duration_parts((minutes, "m"), (remaining_seconds, "s"))
    return f"{remaining_seconds}s"


def join_duration_parts(*parts: tuple[int, str]) -> str:
    formatted = [f"{value}{suffix}" for value, suffix in parts if value > 0]
    return " ".join(formatted) if formatted else "0s"
