"""
High-speed Telegram media streaming (FileToLink-style ByteStreamer + multi-client).
Keeps browser remux (AAC multi-audio) separate from raw /stream and /dl.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from wzgram import Client

logger = logging.getLogger(__name__)

# FileToLink CHUNK_SIZE
TG_CHUNK = 1024 * 1024
DEFAULT_CHUNK = 4 * TG_CHUNK
MAX_PARALLEL_PARTS = 12
MIN_PART_BYTES = 2 * 1024 * 1024
MAX_CONCURRENT_PER_CLIENT = 8

# Per-client in-flight counters (workload balancing like FileToLink work_loads)
_work_loads: dict[int, int] = {}
_work_lock = asyncio.Lock()


def _cid(client: Client) -> int:
    return id(client)


async def _acquire_client(clients: list[Client]) -> Client:
    async with _work_lock:
        for c in clients:
            _work_loads.setdefault(_cid(c), 0)
        # Prefer clients under concurrency cap, else least loaded
        under = [(c, _work_loads[_cid(c)]) for c in clients if _work_loads[_cid(c)] < MAX_CONCURRENT_PER_CLIENT]
        if under:
            client = min(under, key=lambda x: x[1])[0]
        else:
            client = min(clients, key=lambda c: _work_loads.get(_cid(c), 0))
        _work_loads[_cid(client)] = _work_loads.get(_cid(client), 0) + 1
        return client


async def _release_client(client: Client) -> None:
    async with _work_lock:
        k = _cid(client)
        _work_loads[k] = max(0, _work_loads.get(k, 0) - 1)


def parse_range_header(request: Request, file_size: int) -> tuple[int, int, bool]:
    """FileToLink-compatible Range parser."""
    header = request.headers.get("range") or request.headers.get("Range") or ""
    if not header:
        return 0, max(file_size - 1, 0), False
    match = re.search(r"bytes=(?P<start>\d*)-(?P<end>\d*)", header.strip(), flags=re.I)
    if not match or file_size <= 0:
        raise HTTPException(
            status_code=416,
            detail="Requested range is not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    start_s, end_s = match.group("start"), match.group("end")
    if start_s == "" and end_s == "":
        raise HTTPException(
            status_code=416,
            detail="Requested range is not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    if start_s == "":
        # suffix: bytes=-N
        suffix = int(end_s or 0)
        if suffix <= 0:
            raise HTTPException(
                status_code=416,
                detail="Requested range is not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        start, end = max(file_size - suffix, 0), file_size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else file_size - 1
        if start >= file_size or end < start:
            raise HTTPException(
                status_code=416,
                detail="Requested range is not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        end = min(end, file_size - 1)
    return start, end, True


async def _resolve_message(client: Client, chat_id: int, message_id: int):
    last_err = None
    for attempt in range(4):
        try:
            msg = await client.get_messages(chat_id, message_id)
            if isinstance(msg, list):
                msg = msg[0] if msg else None
            if not msg:
                return None
            if not (
                msg.media or msg.document or msg.video or msg.audio
                or msg.animation or msg.voice or msg.video_note or msg.photo
            ):
                return None
            return msg
        except Exception as e:
            last_err = e
            # FloodWait-style backoff
            wait = getattr(e, "value", None) or getattr(e, "x", None) or (1 + attempt)
            try:
                wait = float(wait)
            except Exception:
                wait = 1.0 + attempt
            wait = min(max(wait, 0.5), 30.0)
            logger.debug("get_messages retry %s: %s (sleep %.1fs)", attempt, e, wait)
            await asyncio.sleep(wait)
    logger.warning("resolve_message failed: %s", last_err)
    return None


async def _acquire_message_client(clients: list[Client], chat_id: int, message_id: int):
    """Return a locked client that can actually read the requested Telegram media.

    A valid user session is not necessarily a member of the source channel.
    Resolve and stream with the SAME client; never resolve on one client and
    accidentally call stream_media on another.
    """
    last_error = None
    remaining = list(clients)
    while remaining:
        client = await _acquire_client(remaining)
        remaining = [candidate for candidate in remaining if candidate is not client]
        try:
            message = await _resolve_message(client, chat_id, message_id)
            if message is not None:
                return client, message
            last_error = "message unavailable to this session (check source-channel membership)"
        except Exception as exc:
            last_error = exc
            logger.warning("Session cannot access Telegram media: %s", exc)
        await _release_client(client)
    raise HTTPException(
        status_code=404,
        detail=f"No connected Telegram session can access this media. Add the bot and user sessions to the source channel. ({last_error})",
    )


async def _stream_file_chunks(
    client: Client,
    message,
    offset: int = 0,
    limit: int = 0,
):
    """
    FileToLink ByteStreamer.stream_file:
      chunk_offset = offset // 1MiB
      chunk_limit  = ceil(limit / 1MiB)
    Yields raw 1MiB-oriented chunks from Telegram.
    """
    chunk_offset = offset // TG_CHUNK
    chunk_limit = (limit + TG_CHUNK - 1) // TG_CHUNK if limit > 0 else 0

    for attempt in range(4):
        try:
            async for chunk in client.stream_media(
                message, offset=chunk_offset, limit=chunk_limit
            ):
                if chunk:
                    yield bytes(chunk)
            return
        except Exception as e:
            wait = getattr(e, "value", None) or getattr(e, "x", None) or (1 + attempt)
            try:
                wait = float(wait)
            except Exception:
                wait = 1.0 + attempt
            wait = min(max(wait, 0.5), 30.0)
            logger.warning("stream_media retry %s: %s (sleep %.1fs)", attempt, e, wait)
            await asyncio.sleep(wait)
    raise HTTPException(status_code=502, detail="Telegram stream failed after retries")


async def _download_byte_range(client: Client, message, start: int, end: int):
    """Yield exact bytes [start, end] — FileToLink stream_generator trim logic."""
    if end < start:
        return
    content_length = end - start + 1
    bytes_to_skip = start % TG_CHUNK
    bytes_sent = 0

    async for chunk in _stream_file_chunks(
        client, message, offset=start, limit=content_length
    ):
        if bytes_to_skip > 0:
            if len(chunk) <= bytes_to_skip:
                bytes_to_skip -= len(chunk)
                continue
            chunk = chunk[bytes_to_skip:]
            bytes_to_skip = 0

        remaining = content_length - bytes_sent
        if len(chunk) > remaining:
            chunk = chunk[:remaining]
        if chunk:
            yield chunk
            bytes_sent += len(chunk)
        if bytes_sent >= content_length:
            return


async def media_streamer(
    clients: list[Client],
    chat_id: int,
    message_id: int,
    start: int,
    end: int,
    chunk_size: int = DEFAULT_CHUNK,
):
    total = end - start + 1
    if total <= 0:
        return

    live = [c for c in clients if c is not None]
    if not live:
        raise HTTPException(status_code=503, detail="Telegram streaming session is unavailable")

    # FileToLink-style: sequential for seek/buffer ranges (fast TTFB).
    # Parallel only for large sequential downloads (>= 16 MiB).
    SEEK_FAST_MAX = 16 * 1024 * 1024
    n = min(len(live), MAX_PARALLEL_PARTS)
    if total <= SEEK_FAST_MAX:
        n = 1
    elif total < MIN_PART_BYTES * 2:
        n = 1
    else:
        n = min(n, max(1, total // MIN_PART_BYTES))

    if n <= 1:
        client, message = await _acquire_message_client(live, chat_id, message_id)
        try:
            async for chunk in _download_byte_range(client, message, start, end):
                yield chunk
        finally:
            await _release_client(client)
        return

    part_size = (total + n - 1) // n
    ranges = []
    cursor = start
    for _ in range(n):
        part_end = min(cursor + part_size - 1, end)
        ranges.append((cursor, part_end))
        cursor = part_end + 1
        if cursor > end:
            break

    async def fetch_part(p_start: int, p_end: int) -> bytes:
        client, msg = await _acquire_message_client(live, chat_id, message_id)
        try:
            buf = bytearray()
            async for chunk in _download_byte_range(client, msg, p_start, p_end):
                buf.extend(chunk)
            return bytes(buf)
        finally:
            await _release_client(client)

    tasks = [asyncio.create_task(fetch_part(r0, r1)) for r0, r1 in ranges]
    try:
        for task in tasks:
            data = await task
            view = memoryview(data)
            step = max(chunk_size, 256 * 1024)
            for off in range(0, len(view), step):
                yield bytes(view[off : off + step])
    except Exception:
        for task in tasks:
            if not task.done():
                task.cancel()
        raise


async def get_streaming_response(
    clients: list[Client],
    chat_id: int,
    message_id: int,
    file_size: int,
    filename: str,
    mime_type: str,
    request: Request,
    chunk_size: int = DEFAULT_CHUNK,
):
    if file_size <= 0:
        raise HTTPException(status_code=422, detail="Media file has an invalid size")
    start, end, partial = parse_range_header(request, file_size)
    if not clients:
        raise HTTPException(status_code=503, detail="Telegram streaming session is unavailable")

    # Full-file range → treat as non-partial (FileToLink behavior)
    if start == 0 and end == file_size - 1:
        partial = False

    safe_name = re.sub(r'[\r\n"\\]', "_", filename or "media")[:180]
    is_download = request.url.path.startswith("/dl/")
    disposition = "attachment" if is_download else "inline"
    headers = {
        "Content-Type": mime_type or "application/octet-stream",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": (
            f'{disposition}; filename="{safe_name}"; filename*=UTF-8\'\'{quote(safe_name)}'
        ),
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        "X-Accel-Buffering": "no",
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    return StreamingResponse(
        media_streamer(clients, chat_id, message_id, start, end, chunk_size),
        status_code=206 if partial else 200,
        headers=headers,
        media_type=mime_type or "application/octet-stream",
    )


def _build_ffmpeg_cmd(audio_track: int, force_aac: bool = True) -> list[str]:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts+discardcorrupt", "-threads", "0",
        "-i", "pipe:0",
        "-map", "0:v:0?", "-map", f"0:a:{audio_track}?",
        "-c:v", "copy",
    ]
    if force_aac:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000", "-profile:a", "aac_low"]
    else:
        cmd += ["-c:a", "copy"]
    cmd += ["-movflags", "frag_keyframe+empty_moov+default_base_moof+faststart", "-f", "mp4", "pipe:1"]
    return cmd


async def remux_streamer(
    clients: list[Client],
    chat_id: int,
    message_id: int,
    file_size: int,
    audio_track: int = 0,
    force_aac: bool = True,
):
    if audio_track < 0 or audio_track > 32:
        raise HTTPException(status_code=400, detail="Invalid audio track")

    client, message = await _acquire_message_client(clients, chat_id, message_id)

    cmd = _build_ffmpeg_cmd(audio_track, force_aac=force_aac)
    logger.info("Starting remux ffmpeg: %s", " ".join(cmd))

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def feed():
        try:
            async for chunk in _download_byte_range(client, message, 0, max(file_size - 1, 0)):
                if process.returncode is not None or process.stdin is None:
                    break
                process.stdin.write(chunk)
                await process.stdin.drain()
        except Exception as e:
            logger.warning("Remux feed error: %s", e)
        finally:
            if process.stdin and not process.stdin.is_closing():
                try:
                    process.stdin.close()
                except Exception:
                    pass

    feed_task = asyncio.create_task(feed())
    try:
        while True:
            chunk = await process.stdout.read(512 * 1024)
            if not chunk:
                break
            yield chunk
        await feed_task
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        except asyncio.TimeoutError:
            stderr = b""
        if process.returncode:
            logger.error("FFmpeg remux failed: %s", (stderr or b"").decode(errors="replace")[-1500:])
    except asyncio.CancelledError:
        raise
    finally:
        if not feed_task.done():
            feed_task.cancel()
            try:
                await feed_task
            except asyncio.CancelledError:
                pass
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await process.wait()
        except Exception:
            pass
        await _release_client(client)


async def get_remux_response(
    clients: list[Client],
    chat_id: int,
    message_id: int,
    file_size: int,
    filename: str,
    audio_track: int = 0,
    force_aac: bool = True,
):
    base_name = (filename or "media").rsplit(".", 1)[0]
    safe_name = re.sub(r'[\r\n"\\]', "_", f"{base_name}.mp4")[:180]
    return StreamingResponse(
        remux_streamer(clients, chat_id, message_id, file_size, audio_track, force_aac=force_aac),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Cache-Control": "no-store",
            "Accept-Ranges": "none",
            "X-Accel-Buffering": "no",
        },
    )
