import logging
import re
from typing import ClassVar


class SecretFilter(logging.Filter):
    patterns: ClassVar[tuple[re.Pattern[str], ...]] = (
        re.compile(
            r"(?i)([\"']?(?:authorization|cookie|(?:[a-z0-9]+_)*token|password)"
            r"[\"']?\s*[:=]\s*)(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,}]+)"
        ),
        re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        message = self.patterns[0].sub(r"\1[REDACTED]", message)
        message = self.patterns[1].sub("[REDACTED]", message)
        record.msg, record.args = message, ()
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SecretFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
