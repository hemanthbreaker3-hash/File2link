import ipaddress
import random
import string
from urllib.parse import urlparse


def generate_short_code(length: int = 8):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choices(characters, k=length))


def format_bytes(size: int):
    if not size or size <= 0:
        return "0 B"
    power = 1024
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size >= power and n < 4:
        size /= power
        n += 1
    if n == 0:
        return f"{int(size)} B"
    return f"{size:.2f} {power_labels[n]}B"


def normalize_public_base_url(value: str) -> str:
    """Validate and normalize a URL that can safely be placed in Telegram buttons."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("BASE_URL is empty. Set it to a public HTTPS URL using /baseurl.")

    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("BASE_URL must start with http:// or https:// and include a hostname.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("BASE_URL must not contain credentials, query parameters, or fragments.")

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("BASE_URL must be publicly reachable; localhost/private URLs cannot be Telegram buttons.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("BASE_URL contains an invalid port.") from exc

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A public DNS hostname is acceptable. Reject obvious single-label hostnames.
        if "." not in host:
            raise ValueError("BASE_URL must use a public hostname or public IP address.")
    else:
        if not address.is_global:
            raise ValueError("BASE_URL must use a public IP address, not a private or reserved IP.")

    # Rebuild without a trailing slash while preserving an optional public port/path.
    return value
