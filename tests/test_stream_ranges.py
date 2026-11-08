import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.streamer.engine import parse_range_header


def make_request(range_header=None):
    headers = [] if range_header is None else [(b"range", range_header.encode())]
    return Request({
        "type": "http", "method": "GET", "path": "/stream/example",
        "headers": headers, "scheme": "https", "server": ("example.test", 443),
        "client": ("127.0.0.1", 1234), "query_string": b"",
    })


def test_full_response_range():
    assert parse_range_header(make_request(), 100) == (0, 99, False)


@pytest.mark.parametrize(("header", "expected"), [
    ("bytes=10-19", (10, 19, True)),
    ("bytes=10-", (10, 99, True)),
    ("bytes=-10", (90, 99, True)),
    ("bytes=-500", (0, 99, True)),
])
def test_valid_ranges(header, expected):
    assert parse_range_header(make_request(header), 100) == expected


@pytest.mark.parametrize("header", [
    "items=1-2", "bytes=100-", "bytes=20-10", "bytes=-0", "bytes=1-2,4-5", "bytes=abc-def",
])
def test_invalid_ranges_return_416(header):
    with pytest.raises(HTTPException) as exc:
        parse_range_header(make_request(header), 100)
    assert exc.value.status_code == 416
