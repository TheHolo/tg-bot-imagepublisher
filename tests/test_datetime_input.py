from datetime import UTC, datetime

import pytest

from app.utils.datetime_input import format_schedule_datetime, parse_schedule_datetime


def test_local_schedule_time_is_converted_to_utc_and_formatted_back():
    now = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)

    parsed = parse_schedule_datetime(
        "29.07.2026 18:30", "Asia/Vladivostok", now=now,
    )

    assert parsed == datetime(2026, 7, 29, 8, 30, tzinfo=UTC)
    assert format_schedule_datetime(parsed, "Asia/Vladivostok") == "29.07.2026 18:30"


def test_schedule_time_must_be_in_the_future():
    now = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="будущем"):
        parse_schedule_datetime("28.07.2026 10:00", "Asia/Vladivostok", now=now)
