from app.bootstrap import bootstrap
from app.config import Settings


async def test_application_bootstraps_without_network():
    settings = Settings(
        _env_file=None,
        bot_token="123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI",
        admin_ids={1},
        database_url="sqlite+aiosqlite:///:memory:",
        channels_json={"artwork": {"chat_id": "-1001", "title": "Artwork"}},
    )
    application = await bootstrap(settings)
    assert application.dispatcher.resolve_used_update_types()
    await application.http.close()
    await application.bot.session.close()
    await application.engine.dispose()
