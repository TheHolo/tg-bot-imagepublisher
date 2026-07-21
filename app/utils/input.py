import shlex

from app.domain.exceptions import InvalidUrlError
from app.utils.urls import extract_urls


def parse_submission(text: str) -> tuple[str, list[str], str | None, int]:
    urls = extract_urls(text)
    if not urls:
        raise InvalidUrlError("В сообщении не найдена поддерживаемая ссылка")
    selected = urls[0]
    tokens = shlex.split(text)
    tags: list[str] = []
    channel: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--channel":
            if index + 1 >= len(tokens):
                raise InvalidUrlError("После --channel нужен alias канала")
            channel = tokens[index + 1].strip().lower()
            index += 2
            continue
        if token != selected and not token.startswith(("http://", "https://")):
            tags.append(token)
        index += 1
    return selected, tags, channel, len(urls) - 1
