from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: set[int]) -> None:
        self.admin_ids = admin_ids

    async def __call__(
        self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if user and user.id in self.admin_ids:
            return await handler(event, data)
        answer = getattr(event, "answer", None)
        if callable(answer):
            await answer("У вас нет доступа к этому боту.")
        return None
