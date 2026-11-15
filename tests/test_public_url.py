import pytest

from app.utils.helpers import normalize_public_base_url


def test_normalizes_public_base_url():
    assert normalize_public_base_url("https://example.trycloudflare.com/") == "https://example.trycloudflare.com"


def test_rejects_localhost():
    with pytest.raises(ValueError, match="publicly reachable"):
        normalize_public_base_url("http://localhost:8000")


def test_rejects_private_ip():
    with pytest.raises(ValueError, match="public IP"):
        normalize_public_base_url("http://192.168.1.10:8000")


def test_rejects_query_and_fragment():
    with pytest.raises(ValueError, match="credentials, query parameters"):
        normalize_public_base_url("https://example.com/?token=abc")


def test_rejects_missing_scheme():
    with pytest.raises(ValueError, match="http:// or https://"):
        normalize_public_base_url("example.com")
