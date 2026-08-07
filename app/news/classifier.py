import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

from app.domain.exceptions import InvalidUrlError
from app.news.errors import InvalidNewsInputError
from app.news.models import NewsSourceKind, NewsSourceRequest
from app.utils.urls import validate_public_url

_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }
)
_TELEGRAM_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me", "www.telegram.me"})
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def classify_news_input(value: str) -> NewsSourceRequest:
    candidate = value.strip()
    if not candidate:
        raise InvalidNewsInputError("Источник новости пуст")
    if any(character.isspace() for character in candidate):
        return NewsSourceRequest(NewsSourceKind.MANUAL, candidate)

    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return NewsSourceRequest(NewsSourceKind.MANUAL, candidate)

    try:
        validate_public_url(candidate)
    except InvalidUrlError as exc:
        raise InvalidNewsInputError("Некорректная или небезопасная ссылка") from exc
    host = parsed.hostname.rstrip(".").lower()
    if host in _YOUTUBE_HOSTS:
        return NewsSourceRequest(
            NewsSourceKind.YOUTUBE, normalize_youtube_url(candidate)
        )
    if host in _TELEGRAM_HOSTS:
        return NewsSourceRequest(
            NewsSourceKind.TELEGRAM, normalize_telegram_post_url(candidate)
        )
    return NewsSourceRequest(NewsSourceKind.WEBSITE, _without_fragment(candidate))


def youtube_video_id(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif host in _YOUTUBE_HOSTS:
        path_parts = [part for part in parsed.path.split("/") if part]
        if parsed.path.rstrip("/") == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
            candidate = path_parts[1]
    if not _VIDEO_ID_RE.fullmatch(candidate):
        raise InvalidNewsInputError("Ссылка YouTube не содержит корректный ID видео")
    return candidate


def normalize_youtube_url(url: str) -> str:
    return f"https://www.youtube.com/watch?v={youtube_video_id(url)}"


def telegram_post_parts(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    if host not in _TELEGRAM_HOSTS:
        raise InvalidNewsInputError("Некорректная ссылка Telegram")
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0] == "s":
        parts = parts[1:]
    if parts and parts[0] == "c":
        raise InvalidNewsInputError(
            "Ссылка ведёт в закрытый Telegram-канал; пришлите публичную ссылку или перешлите пост"
        )
    if len(parts) != 2 or not _TELEGRAM_USERNAME_RE.fullmatch(parts[0]):
        raise InvalidNewsInputError(
            "Ссылка должна вести на отдельный пост публичного Telegram-канала"
        )
    try:
        message_id = int(parts[1])
    except ValueError as exc:
        raise InvalidNewsInputError("Ссылка Telegram не содержит ID сообщения") from exc
    if message_id <= 0:
        raise InvalidNewsInputError("Некорректный ID сообщения Telegram")
    return parts[0], message_id


def normalize_telegram_post_url(url: str) -> str:
    username, message_id = telegram_post_parts(url)
    return f"https://t.me/{username}/{message_id}"


def _without_fragment(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, "")
    )
