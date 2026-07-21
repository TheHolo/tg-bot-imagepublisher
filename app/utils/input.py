import shlex

from app.domain.exceptions import InvalidUrlError
from app.utils.urls import extract_urls


def parse_submission_batch(text: str, max_urls: int = 10) -> tuple[list[str], list[str], str | None]:
    urls = extract_urls(text)
    if not urls:
        raise InvalidUrlError("В сообщении не найдена поддерживаемая ссылка")
    urls = list(dict.fromkeys(urls))
    if len(urls) > max_urls:
        raise InvalidUrlError(f"За одно сообщение можно отправить не более {max_urls} ссылок")
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
        if not token.startswith(("http://", "https://")):
            tags.append(token)
        index += 1
    return urls, tags, channel


def parse_submission(text: str) -> tuple[str, list[str], str | None, int]:
    urls, tags, channel = parse_submission_batch(text)
    return urls[0], tags, channel, len(urls) - 1
