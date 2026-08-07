import socket
import sys
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import AsyncMock

import pytest

from app.news.classifier import (
    classify_news_input,
    normalize_telegram_post_url,
    normalize_youtube_url,
)
from app.news.errors import (
    InvalidNewsInputError,
    NewsContentTooLargeError,
    UnsafeNewsUrlError,
)
from app.news.facade import NewsSourceFacade
from app.news.http import FetchedText, PublicOnlyResolver, SafeHttpFetcher
from app.news.models import (
    ExtractedNewsSource,
    ExtractionStage,
    NewsMedia,
    NewsMediaKind,
    NewsSourceKind,
    NewsSourceRequest,
)
from app.news.telegram import ForwardedTelegramPost, TelegramPostExtractor
from app.news.website import WebsiteExtractor
from app.news.youtube import Transcript, YouTubeExtractor, YoutubeTranscriptApiAdapter


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(chunks or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self._responses = list(responses)
        self.requested: list[str] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.requested.append(url)
        return self._responses.pop(0)


def _resolved_address(host: str) -> dict:
    return {
        "hostname": "news.example",
        "host": host,
        "port": 443,
        "family": socket.AF_INET6 if ":" in host else socket.AF_INET,
        "proto": 0,
        "flags": 0,
    }


def test_news_input_classifier_routes_supported_sources():
    assert classify_news_input("Обычный ручной текст").kind is NewsSourceKind.MANUAL
    assert (
        classify_news_input("https://example.org/story").kind is NewsSourceKind.WEBSITE
    )
    assert (
        classify_news_input("https://youtu.be/abcdefghijk").kind
        is NewsSourceKind.YOUTUBE
    )
    assert (
        classify_news_input("https://t.me/example_channel/42").kind
        is NewsSourceKind.TELEGRAM
    )


def test_supported_source_urls_are_normalized():
    assert normalize_youtube_url(
        "https://youtube.com/shorts/abcdefghijk?feature=share"
    ) == ("https://www.youtube.com/watch?v=abcdefghijk")
    assert normalize_telegram_post_url(
        "https://telegram.me/s/example_channel/42?single"
    ) == ("https://t.me/example_channel/42")


def test_private_telegram_link_is_rejected_explicitly():
    with pytest.raises(InvalidNewsInputError, match="закрытый"):
        normalize_telegram_post_url("https://t.me/c/123456/7")


def test_extracted_source_round_trips_through_json_dict():
    source = ExtractedNewsSource(
        kind=NewsSourceKind.WEBSITE,
        source_id="story-1",
        source_url="https://example.org/story",
        normalized_url="https://example.org/story",
        title="Заголовок",
        raw_text="Текст",
        published_at=datetime(2026, 7, 31, 8, 30, tzinfo=UTC),
        media=[
            NewsMedia(kind=NewsMediaKind.IMAGE, url="https://cdn.example.org/hero.jpg")
        ],
        metadata={
            "site_name": "Example",
            "checked_at": datetime(2026, 7, 31, tzinfo=UTC),
        },
    )

    serialized = source.to_dict()
    restored = ExtractedNewsSource.from_dict(serialized)

    assert serialized["metadata"]["checked_at"] == "2026-07-31T00:00:00+00:00"
    assert restored.source_id == source.source_id
    assert restored.media == source.media


def test_news_request_accepts_home_worker_manual_payload():
    request = NewsSourceRequest.from_payload(
        {
            "kind": "manual",
            "source_text": "Ручная новость",
        }
    )

    assert request == NewsSourceRequest(NewsSourceKind.MANUAL, "Ручная новость")


async def test_safe_fetcher_checks_each_redirect_and_reads_text():
    session = FakeSession(
        FakeResponse(302, headers={"Location": "https://news.example/story"}),
        FakeResponse(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            chunks=["текст".encode()],
        ),
    )
    dns_validator = AsyncMock()
    fetcher = SafeHttpFetcher(session, dns_validator=dns_validator)

    page = await fetcher.fetch_text("https://example.org/go")

    assert page.final_url == "https://news.example/story"
    assert page.text == "текст"
    assert session.requested == ["https://example.org/go", "https://news.example/story"]
    assert dns_validator.await_count == 2


async def test_safe_fetcher_rejects_private_address_before_request():
    session = FakeSession()
    fetcher = SafeHttpFetcher(session, dns_validator=AsyncMock())

    with pytest.raises(UnsafeNewsUrlError):
        await fetcher.fetch_text("http://127.0.0.1/internal")

    assert session.requested == []


async def test_safe_fetcher_limits_decompressed_stream_size():
    session = FakeSession(
        FakeResponse(
            200,
            headers={"Content-Type": "text/html"},
            chunks=[b"a" * 60, b"b" * 41],
        )
    )
    fetcher = SafeHttpFetcher(session, max_bytes=100, dns_validator=AsyncMock())

    with pytest.raises(NewsContentTooLargeError):
        await fetcher.fetch_text("https://example.org/story")


async def test_public_only_resolver_returns_the_validated_connection_addresses():
    addresses = [
        _resolved_address("93.184.216.34"),
        _resolved_address("2606:4700:4700::1111"),
    ]
    delegate = AsyncMock()
    delegate.resolve.return_value = addresses
    resolver = PublicOnlyResolver(delegate)

    resolved = await resolver.resolve("news.example", 443, socket.AF_UNSPEC)

    assert resolved is addresses
    delegate.resolve.assert_awaited_once_with("news.example", 443, socket.AF_UNSPEC)
    await resolver.close()
    delegate.close.assert_awaited_once()


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.4",
        "169.254.1.1",
        "224.0.0.1",
        "::1",
        "ff02::1",
    ],
)
async def test_public_only_resolver_rejects_non_public_dns_answers(address):
    delegate = AsyncMock()
    delegate.resolve.return_value = [
        _resolved_address("93.184.216.34"),
        _resolved_address(address),
    ]
    resolver = PublicOnlyResolver(delegate)

    with pytest.raises(OSError, match="not public"):
        await resolver.resolve("rebind.example", 443, socket.AF_UNSPEC)


async def test_public_only_resolver_rejects_literal_private_ip_without_dns_lookup():
    delegate = AsyncMock()
    resolver = PublicOnlyResolver(delegate)

    with pytest.raises(OSError, match="not public"):
        await resolver.resolve("127.0.0.1", 80)

    delegate.resolve.assert_not_awaited()


async def test_website_extractor_combines_trafilatura_and_structured_metadata():
    fetcher = AsyncMock()
    fetcher.fetch_text.return_value = FetchedText(
        requested_url="https://example.org/story",
        final_url="https://example.org/story",
        status=200,
        content_type="text/html",
        text="<html></html>",
        headers={},
    )
    fetcher.ensure_safe_url.side_effect = lambda url: url
    extractor = WebsiteExtractor(
        fetcher,
        content_extractor=lambda html, url: {
            "title": "Главная новость",
            "text": "Первый абзац.\n\nВторой абзац.",
            "date": "2026-07-31T10:15:00+10:00",
            "tags": ["город", "события"],
        },
        metadata_extractor=lambda html, url: {
            "json-ld": [
                {
                    "@type": "NewsArticle",
                    "author": {"name": "Редакция", "url": "/authors/news"},
                    "image": {"url": "/images/hero.jpg"},
                    "publisher": {"name": "Example News"},
                }
            ],
        },
    )
    progress = AsyncMock()

    source = await extractor.extract("https://example.org/story", progress=progress)

    assert source.kind is NewsSourceKind.WEBSITE
    assert source.title == "Главная новость"
    assert source.raw_text == "Первый абзац.\nВторой абзац."
    assert source.author_name == "Редакция"
    assert source.author_url == "https://example.org/authors/news"
    assert source.media[0].url == "https://example.org/images/hero.jpg"
    assert source.metadata["tags"] == ["город", "события"]
    assert progress.await_args.args[0].message == "Парсим новость с Trafilatura"


async def test_youtube_extractor_uses_injected_metadata_and_transcript_adapters():
    metadata_adapter = AsyncMock()
    metadata_adapter.fetch_metadata.return_value = {
        "id": "abcdefghijk",
        "title": "Разбор события",
        "description": "Описание ролика",
        "channel": "Example Channel",
        "channel_url": "https://www.youtube.com/@example",
        "upload_date": "20260731",
        "duration": 125,
        "thumbnail": "https://i.ytimg.com/vi/abcdefghijk/maxresdefault.jpg",
    }
    transcript_adapter = AsyncMock()
    transcript_adapter.fetch_transcript.return_value = Transcript(
        text="Первая реплика.\nВторая реплика.",
        language="ru",
        is_generated=True,
    )
    extractor = YouTubeExtractor(metadata_adapter, transcript_adapter)
    updates = []

    async def progress(update):
        updates.append(update)

    source = await extractor.extract("https://youtu.be/abcdefghijk", progress=progress)

    assert source.source_id == "abcdefghijk"
    assert "Субтитры:\nПервая реплика." in source.raw_text
    assert source.metadata["transcript_generated"] is True
    assert [item.stage for item in updates] == [
        ExtractionStage.EXTRACTING_METADATA,
        ExtractionStage.EXTRACTING_SUBTITLES,
    ]
    transcript_adapter.fetch_transcript.assert_awaited_once_with(
        "abcdefghijk", ("ru", "en")
    )


async def test_youtube_transcript_falls_back_to_any_manual_language(monkeypatch):
    class NoTranscriptFound(Exception):
        pass

    class Track:
        def __init__(self, language_code: str, *, generated: bool) -> None:
            self.language_code = language_code
            self.is_generated = generated
            self.fetch_calls = 0

        def fetch(self, *, preserve_formatting: bool):
            self.fetch_calls += 1
            return [
                {"text": "Первая строка"},
                {"text": "Вторая строка"},
            ]

    generated = Track("de", generated=True)
    manual = Track("ja", generated=False)

    class FakeYouTubeTranscriptApi:
        instance = None

        def __init__(self) -> None:
            self.requested_languages = None
            type(self).instance = self

        def fetch(self, video_id, *, languages, preserve_formatting):
            self.requested_languages = languages
            raise NoTranscriptFound()

        def list(self, video_id):
            return [generated, manual]

    module = ModuleType("youtube_transcript_api")
    module.YouTubeTranscriptApi = FakeYouTubeTranscriptApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", module)

    transcript = await YoutubeTranscriptApiAdapter().fetch_transcript(
        "abcdefghijk", ("ru", "en")
    )

    assert FakeYouTubeTranscriptApi.instance.requested_languages == ["ru", "en"]
    assert generated.fetch_calls == 0
    assert manual.fetch_calls == 1
    assert transcript.text == "Первая строка\nВторая строка"
    assert transcript.language == "ja"
    assert transcript.is_generated is False


async def test_public_telegram_post_is_extracted_from_embed_page():
    embed = """
        <div class="tgme_widget_message_owner_name"><span>Канал новостей</span></div>
        <div class="tgme_widget_message_text js-message_text">
          Важная <b>новость</b><br>Второй абзац
        </div>
        <a class="tgme_widget_message_photo_wrap"
           style="background-image:url('https://cdn.example.org/photo.jpg')"></a>
        <time datetime="2026-07-31T08:00:00+00:00"></time>
    """
    fetcher = AsyncMock()
    fetcher.fetch_text.return_value = FetchedText(
        requested_url="https://t.me/example_channel/42?embed=1&mode=tme",
        final_url="https://t.me/example_channel/42?embed=1&mode=tme",
        status=200,
        content_type="text/html",
        text=embed,
        headers={},
    )
    fetcher.ensure_safe_url.side_effect = lambda url: url
    extractor = TelegramPostExtractor(fetcher)

    source = await extractor.extract("https://t.me/example_channel/42")

    assert source.source_id == "example_channel:42"
    assert source.raw_text == "Важная новость\nВторой абзац"
    assert source.author_name == "Канал новостей"
    assert source.media[0].url == "https://cdn.example.org/photo.jpg"
    assert source.metadata["scope"] == "single-user-submitted-public-post"


async def test_bot_forward_path_reuses_telegram_file_ids_without_network():
    extractor = TelegramPostExtractor(AsyncMock())
    forwarded = ForwardedTelegramPost(
        message_id=15,
        text="Текст пересланной новости",
        channel_username="example_channel",
        channel_title="Example",
        media=[
            NewsMedia(kind=NewsMediaKind.IMAGE, telegram_file_id="telegram-file-id")
        ],
    )

    source = await extractor.extract_forwarded(forwarded)

    assert source.normalized_url == "https://t.me/example_channel/15"
    assert source.media[0].telegram_file_id == "telegram-file-id"
    assert source.metadata["extractor"] == "telegram-bot-forward"


async def test_facade_handles_manual_text_without_configured_network_extractors():
    facade = NewsSourceFacade()

    source = await facade.extract(
        NewsSourceRequest(
            kind=NewsSourceKind.MANUAL,
            value="  Заголовок\n\nТекст новости.  ",
        )
    )

    assert source.kind is NewsSourceKind.MANUAL
    assert source.title == "Заголовок"
    assert source.raw_text == "Заголовок\n\nТекст новости."
    assert source.source_url is None
