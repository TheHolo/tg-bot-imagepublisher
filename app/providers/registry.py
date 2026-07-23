from app.domain.exceptions import UnsupportedSourceError
from app.providers.base import BaseProvider


class ProviderRegistry:
    def __init__(self, providers: list[BaseProvider] | None = None) -> None:
        self._providers = providers or []

    def register(self, provider: BaseProvider) -> None:
        self._providers.append(provider)

    def resolve(self, url: str) -> BaseProvider:
        for provider in self._providers:
            if provider.can_handle(url):
                return provider
        raise UnsupportedSourceError("Этот источник пока не поддерживается.")

    @property
    def names(self) -> list[str]:
        return [provider.name for provider in self._providers]

    @property
    def providers(self) -> tuple[BaseProvider, ...]:
        return tuple(self._providers)
