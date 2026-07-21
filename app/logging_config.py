import logging
import re


class SecretFilter(logging.Filter):
    patterns = [
        re.compile(r"(?i)(authorization|cookie|token|password)=([^\s]+)"),
        re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in self.patterns:
            message = pattern.sub("[REDACTED]", message)
        record.msg, record.args = message, ()
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SecretFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
