from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import aiohttp

from app.logging_config import configure_logging
from app.news.facade import NewsSourceFacade
from app.news.home_runtime import HomeNewsWorker
from app.news.http import PublicOnlyResolver
from app.news.ollama_client import OllamaNewsClient
from app.news.vps_client import VpsNewsApiClient
from app.news.worker_config import HomeWorkerSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process news drafts through a local Ollama instance",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("bot-settings.toml"),
        help="TOML file containing the [home_worker] section",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Lease at most one task and exit",
    )
    return parser


async def main(*, config_file: Path, once: bool = False) -> None:
    settings = HomeWorkerSettings(_config_file=config_file)
    configure_logging(settings.log_level)

    extractor_connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver())
    async with (
        aiohttp.ClientSession(trust_env=False) as session,
        aiohttp.ClientSession(
            trust_env=False, connector=extractor_connector,
        ) as extractor_session,
    ):
        extractor = NewsSourceFacade.from_session(extractor_session)
        api = VpsNewsApiClient(
            session,
            base_url=settings.vps_api_url,
            token=settings.token.get_secret_value(),
            worker_id=settings.worker_id,
            lease_seconds=settings.lease_seconds,
            source_types=settings.source_types,
            model=settings.ollama_model,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
        )
        ollama = OllamaNewsClient(
            session,
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            max_retries=settings.ollama_max_retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
            temperature=settings.temperature,
            keep_alive=settings.ollama_keep_alive,
            context_length=settings.ollama_context_length,
            max_predict_tokens=settings.ollama_max_predict_tokens,
            max_source_chars_per_chunk=settings.max_source_chars_per_chunk,
            max_source_chunks=settings.max_source_chunks,
        )
        worker = HomeNewsWorker(
            api=api,
            extractor=extractor,
            ollama=ollama,
            poll_interval_seconds=settings.poll_interval_seconds,
        )
        if once:
            await worker.run_once()
        else:
            await worker.run_forever()


def run() -> None:
    args = _parser().parse_args()
    try:
        asyncio.run(main(config_file=args.config, once=args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
