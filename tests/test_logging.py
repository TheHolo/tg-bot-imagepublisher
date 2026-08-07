import logging

from app.logging_config import SecretFilter


def test_secrets_are_redacted():
    record = logging.LogRecord(
        "x",
        logging.INFO,
        "",
        0,
        (
            'token=abc Authorization: Bearer api-secret '
            '"lease_token": "lease-secret" cookie=session-secret '
            "123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI"
        ),
        (),
        None,
    )
    assert SecretFilter().filter(record)
    assert "abc" not in record.getMessage()
    assert "api-secret" not in record.getMessage()
    assert "lease-secret" not in record.getMessage()
    assert "session-secret" not in record.getMessage()
    assert "123456:" not in record.getMessage()
    assert record.getMessage().count("[REDACTED]") == 5
