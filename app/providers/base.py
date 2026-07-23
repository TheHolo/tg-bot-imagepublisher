from abc import ABC, abstractmethod

import aiohttp

from app.domain.models import SourcePost


class BaseProvider(ABC):
    name: str
    healthcheck_url: str | None = None
    healthcheck_statuses: frozenset[int] = frozenset(range(200, 400))

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    @abstractmethod
    def can_handle(self, url: str) -> bool: ...

    @abstractmethod
    def normalize_url(self, url: str) -> str: ...

    @abstractmethod
    async def fetch_post(self, url: str) -> SourcePost: ...

    async def healthcheck(self) -> int | None:
        if self.healthcheck_url is None:
            return None
        async with self.session.get(self.healthcheck_url, allow_redirects=True) as response:
            if response.status not in self.healthcheck_statuses:
                raise RuntimeError(f"HTTP {response.status}")
            return response.status
