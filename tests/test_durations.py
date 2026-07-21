import pytest

from app.utils.durations import format_duration, parse_duration


@pytest.mark.parametrize(("value", "seconds"), [("0", 0), ("30s", 30), ("15m", 900), ("2h", 7200), ("1d", 86400)])
def test_parse_duration(value, seconds):
    assert parse_duration(value) == seconds


def test_invalid_or_excessive_duration():
    with pytest.raises(ValueError):
        parse_duration("tomorrow")
    with pytest.raises(ValueError):
        parse_duration("8d")


def test_format_duration():
    assert format_duration(0) == "без задержки"
    assert format_duration(900) == "15m"
