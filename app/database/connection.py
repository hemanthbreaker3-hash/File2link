from motor.motor_asyncio import AsyncIOMotorClient
from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional, Union, Any
import os

class Settings(BaseSettings):
    MONGODB_URI: Optional[str] = None
    DATABASE_URL: Optional[str] = None
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "change-this-password"
    REDIS_URL: str = "redis://localhost:6379/0"
    API_ID: int
    API_HASH: str
    BOT_TOKEN: str
    OWNER_ID: int
    ADMINS: str = ""
    FORCE_SUB_CHANNELS: str = ""
    BASE_URL: str = "http://localhost:8000"
    DEFAULT_EXPIRY: int = 24  # hours
    CHANNEL_ID: Optional[int] = None
    PORT: int = 8000
    DEBUG: bool = False
    SESSIONS: str = ""

    @field_validator("CHANNEL_ID", mode="before")
    @classmethod
    def parse_optional_int(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            # Strip inline comments e.g. "-1001234567890 # For log storage"
            cleaned = v.split("#")[0].strip()
            if not cleaned:
                return None
            try:
                return int(cleaned)
            except ValueError:
                return None
        return None

    @property
    def mongo_uri(self) -> str:
        return self.MONGODB_URI or self.DATABASE_URL or "mongodb://localhost:27017/anizoneflix"
    
    @property
    def admin_list(self):
        return [int(x) for x in self.ADMINS.split(",") if x.strip()]
    
    @property
    def fsub_list(self):
        return [int(x) for x in self.FORCE_SUB_CHANNELS.split(",") if x.strip()]

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
        "case_sensitive": True,
    }

settings = Settings()

client = AsyncIOMotorClient(settings.mongo_uri)
db = client.get_database("anizoneflix")

# Collections
files_col = db.files
users_col = db.users
settings_col = db.settings
logs_col = db.logs

async def create_indexes():
    await files_col.create_index("short_code", unique=True)
    await files_col.create_index("file_id")
    await users_col.create_index("user_id", unique=True)
    await files_col.create_index("expiry_time")

async def load_custom_settings():
    try:
        custom_base = await settings_col.find_one({"key": "base_url"})
        if custom_base and custom_base.get('value'):
            settings.BASE_URL = custom_base['value']
    except Exception as e:
        pass
