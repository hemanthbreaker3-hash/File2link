from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from telethon import TelegramClient

logger = logging.getLogger(__name__)
STREAM_SEMAPHORE = asyncio.Semaphore(32)


def parse_range_header(request: Request, file_size: int) -> tuple[int, int, bool]:
    """Return start, end, partial; reject malformed and unsatisfiable ranges."""
    header = request.headers.get("range")
    if not header:
        return 0, max(file_size - 1, 0), False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip(), flags=re.IGNORECASE)
    if not match or "," in header or file_size <= 0:
        raise HTTPException(status_code=416, detail="Requested range is not satisfiable", headers={"Content-Range": f"bytes */{file_size}"})
    first, last = match.groups()
    if not first:
        suffix = int(last or 0)
        if suffix <= 0:
            raise HTTPException(status_code=416, detail="Requested range is not satisfiable", headers={"Content-Range": f"bytes */{file_size}"})
        start, end = max(file_size - suffix, 0), file_size - 1
    else:
        start = int(first)
        end = int(last) if last else file_size - 1
        if start >= file_size or end < start:
            raise HTTPException(status_code=416, detail="Requested range is not satisfiable", headers={"Content-Range": f"bytes */{file_size}"})
        end = min(end, file_size - 1)
    return start, end, True


async def media_streamer(client: TelegramClient, media, start: int, end: int, chunk_size: int = 512 * 1024):
    """Ordered, bounded-memory Telegram streaming with a global concurrency cap."""
    remaining = end - start + 1
    async with STREAM_SEMAPHORE:
        async for chunk in client.iter_download(media, offset=start, limit=remaining, request_size=chunk_size):
            if chunk:
                yield bytes(chunk)


async def get_streaming_response(
    clients: list[TelegramClient], file, file_size: int, filename: str,
    mime_type: str, request: Request, chunk_size: int = 512 * 1024,
):
    if file_size <= 0:
        raise HTTPException(status_code=422, detail="Media file has an invalid size")
    start, end, partial = parse_range_header(request, file_size)
    client = next((item for item in clients if item and item.is_connected()), None)
    if client is None:
        raise HTTPException(status_code=503, detail="Telegram streaming session is unavailable")
    safe_name = re.sub(r"[\r\n\"\\]", "_", filename or "media")[:180]
    is_download = request.url.path.startswith("/dl/")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f'{"attachment" if is_download else "inline"}; filename="{safe_name}"; filename*=UTF-8\'\'{quote(safe_name)}',
        "Cache-Control": "private, no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    return StreamingResponse(
        media_streamer(client, file, start, end, chunk_size),
        status_code=206 if partial else 200,
        headers=headers,
        media_type=mime_type or "application/octet-stream",
    )


async def remux_streamer(client: TelegramClient, media, file_size: int, audio_track: int = 0):
    """FFmpeg stream-copy remux; process and pipe I/O are kept off the event loop."""
    import subprocess

    if audio_track < 0 or audio_track > 32:
        raise HTTPException(status_code=400, detail="Invalid audio track")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
           "-map", "0:v:0?", "-map", f"0:a:{audio_track}?", "-c", "copy",
           "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def feed():
        try:
            async for chunk in client.iter_download(media, offset=0, limit=file_size, request_size=512 * 1024):
                if process.returncode is not None:
                    break
                process.stdin.write(bytes(chunk))
                await process.stdin.drain()
        finally:
            if process.stdin:
                process.stdin.close()

    feed_task = asyncio.create_task(feed())
    try:
        while chunk := await process.stdout.read(256 * 1024):
            yield chunk
        await feed_task
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        if process.returncode:
            logger.error("FFmpeg remux failed: %s", stderr.decode(errors="replace")[-1000:])
    except asyncio.CancelledError:
        raise
    finally:
        if not feed_task.done():
            feed_task.cancel()
        if process.returncode is None:
            process.kill()
        await process.wait()


async def get_remux_response(client: TelegramClient, file, file_size: int, filename: str, audio_track: int = 0):
    base_name = (filename or "media").rsplit(".", 1)[0]
    safe_name = re.sub(r"[\r\n\"\\]", "_", f"{base_name}.mp4")[:180]
    return StreamingResponse(
        remux_streamer(client, file, file_size, audio_track),
        media_type="video/mp4",
        headers={"Content-Disposition": f'inline; filename="{safe_name}"', "Cache-Control": "no-store", "Accept-Ranges": "none"},
    )
