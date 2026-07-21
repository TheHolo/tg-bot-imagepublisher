import re

_DURATION = re.compile(r"^(\d+)\s*([smhd]?)$", re.IGNORECASE)
_MULTIPLIERS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
MAX_INTERVAL_SECONDS = 7 * 86400


def parse_duration(value: str) -> int:
    match = _DURATION.fullmatch(value.strip())
    if not match:
        raise ValueError("Интервал должен выглядеть как 30s, 15m, 2h или 1d")
    seconds = int(match.group(1)) * _MULTIPLIERS[match.group(2).lower()]
    if seconds > MAX_INTERVAL_SECONDS:
        raise ValueError("Максимальный интервал — 7d")
    return seconds


def format_duration(seconds: int) -> str:
    if seconds == 0:
        return "без задержки"
    for suffix, multiplier in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % multiplier == 0:
            return f"{seconds // multiplier}{suffix}"
    return f"{seconds}s"
