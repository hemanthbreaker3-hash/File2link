import pytest
import os

# Set dummy environment variables for settings initialization
os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "test_hash")
os.environ.setdefault("BOT_TOKEN", "123456:test_token")
os.environ.setdefault("OWNER_ID", "123456789")

from app.utils.helpers import generate_short_code, format_bytes
from app.bot.main import get_ist_greeting
from app.database.connection import Settings

def test_generate_short_code():
    code1 = generate_short_code()
    code2 = generate_short_code(10)
    assert len(code1) == 8
    assert len(code2) == 10
    assert code1 != code2

def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1024 * 1024) == "1.00 MB"
    assert format_bytes(1024 * 1024 * 1024) == "1.00 GB"

def test_get_ist_greeting():
    greeting = get_ist_greeting()
    assert any(g in greeting for g in ["Good Morning", "Good Afternoon", "Good Evening"])

def test_settings_properties():
    test_settings = Settings(
        API_ID=123456,
        API_HASH="hash",
        BOT_TOKEN="token",
        OWNER_ID=123,
        ADMINS="123, 456, 789",
        FORCE_SUB_CHANNELS="-1001, -1002"
    )
    assert test_settings.admin_list == [123, 456, 789]
    assert test_settings.fsub_list == [-1001, -1002]

def test_channel_id_parsing_with_comments():
    test_settings_comment = Settings(
        API_ID=123456,
        API_HASH="hash",
        BOT_TOKEN="token",
        OWNER_ID=123,
        CHANNEL_ID="-1001234567890 # For log storage (optional)"
    )
    assert test_settings_comment.CHANNEL_ID == -1001234567890

    test_settings_only_comment = Settings(
        API_ID=123456,
        API_HASH="hash",
        BOT_TOKEN="token",
        OWNER_ID=123,
        CHANNEL_ID="# For log storage (optional)"
    )
    assert test_settings_only_comment.CHANNEL_ID is None
