from __future__ import annotations

from typing import Any, Optional
import logging
from urllib.parse import urlparse

from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    MONGODB_URI: Optional[str] = None
    DATABASE_URL: Optional[str] = None
    DATABASE_NAME: str = "tg_media_bot"
    REDIS_URL: str = "redis://localhost:6379/0"
    API_ID: int
    API_HASH: str
    BOT_TOKEN: str
    OWNER_ID: int
    ADMINS: str = ""
    FORCE_SUB_CHANNELS: str = ""
    BASE_URL: str = "http://localhost:8000"
    DEFAULT_EXPIRY: int = 24
    CHANNEL_ID: Optional[int] = None
    PORT: int = 8000
    DEBUG: bool = False
    SESSIONS: str = ""
    ADMIN_PANEL_TOKEN: str = ""
    STREAM_CHUNK_SIZE: int = 524288

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    @field_validator("CHANNEL_ID", mode="before")
    @classmethod
    def parse_optional_int(cls, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            cleaned = value.split("#", 1)[0].strip()
            return int(cleaned) if cleaned else None
        raise ValueError("CHANNEL_ID must be an integer")

    @field_validator("BASE_URL")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("BASE_URL must be a valid http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("BASE_URL cannot contain credentials, query, or fragment")
        return value

    @field_validator("STREAM_CHUNK_SIZE")
    @classmethod
    def validate_chunk_size(cls, value: int) -> int:
        if not 64 * 1024 <= value <= 1024 * 1024:
            raise ValueError("STREAM_CHUNK_SIZE must be between 65536 and 1048576")
        return value

    @property
    def mongo_uri(self) -> str:
        return self.MONGODB_URI or self.DATABASE_URL or "mongodb://localhost:27017"

    @property
    def admin_list(self) -> list[int]:
        return [int(x.strip()) for x in self.ADMINS.split(",") if x.strip()]

    @property
    def fsub_list(self) -> list[int]:
        return [int(x.strip()) for x in self.FORCE_SUB_CHANNELS.split(",") if x.strip()]


logger = logging.getLogger(__name__)
settings = Settings()
client = AsyncIOMotorClient(settings.mongo_uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000, maxPoolSize=100, minPoolSize=5)
db = client[settings.DATABASE_NAME]
files_col = db.files
users_col = db.users
settings_col = db.settings
logs_col = db.logs


async def create_indexes() -> None:
    await files_col.create_index("short_code", unique=True)
    await files_col.create_index("file_id")
    await files_col.create_index("uploader_id")
    await files_col.create_index("expiry_time", expireAfterSeconds=0)
    await users_col.create_index("user_id", unique=True)
    await settings_col.create_index("key", unique=True)


async def load_custom_settings() -> None:
    custom = await settings_col.find_one({"key": "base_url"}, {"value": 1})
    if custom and custom.get("value"):
        candidate = str(custom["value"]).strip().rstrip("/")
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not (parsed.username or parsed.password or parsed.query or parsed.fragment):
            settings.BASE_URL = candidate
        else:
            logger.warning("Ignoring invalid persisted BASE_URL; retaining environment configuration")
