from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base


def create_database(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    if url.startswith("sqlite") and "///" in url:
        path = url.split("///", 1)[1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_upgrade_schema)


def _upgrade_schema(connection) -> None:
    """Apply additive MVP migrations to databases created by older versions."""
    columns = {column["name"] for column in inspect(connection).get_columns("channels")}
    if "publish_interval_seconds" not in columns:
        connection.execute(text("ALTER TABLE channels ADD COLUMN publish_interval_seconds INTEGER NOT NULL DEFAULT 0"))
    if "next_publish_at" not in columns:
        connection.execute(text("ALTER TABLE channels ADD COLUMN next_publish_at TIMESTAMP NULL"))
    job_columns = {column["name"] for column in inspect(connection).get_columns("jobs")}
    if "force_publish" not in job_columns:
        connection.execute(text("ALTER TABLE jobs ADD COLUMN force_publish BOOLEAN NOT NULL DEFAULT 0"))
