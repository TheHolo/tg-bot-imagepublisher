import re
import shlex

from app.domain.exceptions import InvalidMediaSelectionError, InvalidUrlError
from app.utils.urls import URL_RE

_URL_TRAILING_PUNCTUATION = ".,;:!?)]}"
_SELECTION_RE = re.compile(r"[ \t]*\[([^\]]*)\]")
_SOURCE_SELECTION_RE = re.compile(r"^(https?://\S+) \[([^\]]+)\]$", re.IGNORECASE)
_SELECTION_PART_RE = re.compile(r"(\d+)(?:\s*-\s*(\d+))?")
_MAX_SELECTION_SIZE = 10_000


def parse_media_selection(value: str) -> tuple[int, ...]:
    """Parse a 1-based comma-separated image selection, preserving its order."""
    value = value.strip()
    if not value:
        raise InvalidMediaSelectionError("Список изображений в квадратных скобках пуст")

    selected: list[int] = []
    seen: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        match = _SELECTION_PART_RE.fullmatch(part)
        if not match:
            raise InvalidMediaSelectionError(
                "Некорректный выбор изображений. Используйте номера и диапазоны, например [1,3,5-7]."
            )
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1:
            raise InvalidMediaSelectionError("Номера изображений начинаются с 1")
        if end < start:
            raise InvalidMediaSelectionError(f"Некорректный диапазон {start}-{end}: начало больше конца")
        if end - start + 1 > _MAX_SELECTION_SIZE or len(selected) + end - start + 1 > _MAX_SELECTION_SIZE:
            raise InvalidMediaSelectionError("В списке выбрано слишком много изображений")
        for number in range(start, end + 1):
            if number not in seen:
                seen.add(number)
                selected.append(number)
    return tuple(selected)


def split_source_selection(source: str) -> tuple[str, tuple[int, ...] | None]:
    """Split an internal source specification into URL and optional image numbers."""
    match = _SOURCE_SELECTION_RE.fullmatch(source)
    if not match:
        return source, None
    return match.group(1), parse_media_selection(match.group(2))


def _source_requests(text: str) -> tuple[list[str], str]:
    requests: list[str] = []
    consumed: list[tuple[int, int]] = []
    for match in URL_RE.finditer(text):
        source_start = match.start()
        if source_start > 0 and text[source_start - 1] in "([{":
            source_start -= 1
        raw_url = match.group(0)
        url = raw_url.rstrip(_URL_TRAILING_PUNCTUATION)
        url_end = match.start() + len(url)
        source_end = match.end()
        selector_match = _SELECTION_RE.match(text, url_end)
        if selector_match:
            selected = parse_media_selection(selector_match.group(1))
            requests.append(f"{url} [{','.join(map(str, selected))}]")
            source_end = selector_match.end()
        else:
            # A bracket directly following whitespace is intended as a selector;
            # report an unfinished expression instead of treating it as a tag.
            tail = text[url_end:]
            if re.match(r"[ \t]*\[", tail):
                raise InvalidMediaSelectionError(
                    "Не закрыта квадратная скобка в выборе изображений"
                )
            requests.append(url)
        separator = re.match(r",[ \t]*(?=https?://)", text[source_end:], re.IGNORECASE)
        if separator:
            source_end += separator.end()
        consumed.append((source_start, source_end))

    if not requests:
        raise InvalidUrlError("В сообщении не найдена поддерживаемая ссылка")

    characters = list(text)
    for start, end in consumed:
        characters[start:end] = " " * (end - start)
    return requests, "".join(characters)


def parse_submission_batch(text: str, max_urls: int = 10) -> tuple[list[str], list[str], str | None]:
    urls, remaining_text = _source_requests(text)
    urls = list(dict.fromkeys(urls))
    if len(urls) > max_urls:
        raise InvalidUrlError(f"За одно сообщение можно отправить не более {max_urls} ссылок")
    tokens = shlex.split(remaining_text)
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
        tags.append(token)
        index += 1
    return urls, tags, channel


def parse_submission(text: str) -> tuple[str, list[str], str | None, int]:
    urls, tags, channel = parse_submission_batch(text)
    return urls[0], tags, channel, len(urls) - 1
