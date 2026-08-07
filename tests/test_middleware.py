from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.middleware import AdminOnlyMiddleware


async def test_admin_middleware_calls_handler_for_allowed_user():
    middleware = AdminOnlyMiddleware({42})
    handler = AsyncMock(return_value="handled")
    event = SimpleNamespace(answer=AsyncMock())

    result = await middleware(
        handler,
        event,
        {"event_from_user": SimpleNamespace(id=42)},
    )

    assert result == "handled"
    handler.assert_awaited_once_with(event, {"event_from_user": SimpleNamespace(id=42)})
    event.answer.assert_not_awaited()


async def test_admin_middleware_rejects_unknown_user_without_calling_handler():
    middleware = AdminOnlyMiddleware({42})
    handler = AsyncMock()
    event = SimpleNamespace(answer=AsyncMock())

    result = await middleware(
        handler,
        event,
        {"event_from_user": SimpleNamespace(id=7)},
    )

    assert result is None
    handler.assert_not_awaited()
    event.answer.assert_awaited_once_with("У вас нет доступа к этому боту.")


async def test_admin_middleware_silently_rejects_event_without_reply_method():
    middleware = AdminOnlyMiddleware({42})
    handler = AsyncMock()

    assert await middleware(handler, object(), {}) is None
    handler.assert_not_awaited()
