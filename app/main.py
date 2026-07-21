import asyncio

from app.bootstrap import bootstrap
from app.config import Settings
from app.logging_config import configure_logging


async def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    application = await bootstrap(settings)
    await application.run()


if __name__ == "__main__":
    asyncio.run(main())
