import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_ids: Annotated[set[int], NoDecode] = Field(default_factory=set)
    database_url: str = "sqlite+aiosqlite:///./data/database.db"
    storage_path: Path = Path("./storage")
    log_level: str = "INFO"
    worker_count: int = Field(1, ge=1, le=8)
    max_job_attempts: int = Field(3, ge=1, le=10)
    download_timeout: int = Field(60, ge=5)
    max_download_size_mb: int = Field(100, ge=1)
    max_tags: int = Field(20, ge=1)
    max_tag_length: int = Field(64, ge=1)
    default_channel_alias: str = "artwork"
    channels_json: dict[str, dict[str, Any]] = Field(default_factory=dict)
    http_proxy: str | None = None
    socks_proxy: str | None = None
    pixiv_cookies: str | None = None
    auto_add_source_tags: bool = False
    delete_files_after_publish: bool = True
    files_ttl_hours: int = 24

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> set[int]:
        if isinstance(value, str):
            return {int(item.strip()) for item in value.split(",") if item.strip()}
        return set(value or [])

    @field_validator("channels_json", mode="before")
    @classmethod
    def parse_channels(cls, value: object) -> dict[str, dict[str, Any]]:
        return json.loads(value) if isinstance(value, str) else dict(value or {})

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_size_mb * 1024 * 1024

    @property
    def proxy_url(self) -> str | None:
        return self.socks_proxy or self.http_proxy
