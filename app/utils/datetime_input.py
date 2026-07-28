from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_LOCAL_FORMATS = (
    "%d.%m.%Y %H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
)


def parse_schedule_datetime(
    value: str, timezone_name: str, *, now: datetime | None = None,
) -> datetime:
    raw = value.strip()
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        for pattern in _LOCAL_FORMATS:
            try:
                parsed = datetime.strptime(raw, pattern).replace(
                    tzinfo=ZoneInfo(timezone_name),
                )
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(
            "Введите время как ДД.ММ.ГГГГ ЧЧ:ММ, например 29.07.2026 18:30."
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    scheduled = parsed.astimezone(UTC)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if scheduled <= current:
        raise ValueError("Время публикации должно быть в будущем.")
    return scheduled


def format_schedule_datetime(value: datetime, timezone_name: str) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%d.%m.%Y %H:%M")
