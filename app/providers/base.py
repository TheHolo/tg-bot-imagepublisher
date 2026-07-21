from abc import ABC, abstractmethod

import aiohttp

from app.domain.models import SourcePost


class BaseProvider(ABC):
    name: str

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    @abstractmethod
    def can_handle(self, url: str) -> bool: ...

    @abstractmethod
    def normalize_url(self, url: str) -> str: ...

    @abstractmethod
    async def fetch_post(self, url: str) -> SourcePost: ...
