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

STREAM_SEMAPHORE = asyncio.Semaphore(128)
# wzgram/Pyrogram stream_media uses fixed ~1 MiB chunks; offset/limit are in CHUNKS not bytes
TG_CHUNK = 1024 * 1024
DEFAULT_CHUNK = TG_CHUNK
MAX_PARALLEL_PARTS = 8
MIN_PART_BYTES = 2 * 1024 * 1024


def parse_range_header(request: Request, file_size: int) -> tuple[int, int, bool]:
    header = request.headers.get("range")
    if not header:
        return 0, max(file_size - 1, 0), False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip(), flags=re.IGNORECASE)
    if not match or "," in header or file_size <= 0:
        raise HTTPException(
            status_code=416,
            detail="Requested range is not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    first, last = match.groups()
    if not first:
        suffix = int(last or 0)
        if suffix <= 0:
            raise HTTPException(
                status_code=416,
                detail="Requested range is not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        start, end = max(file_size - suffix, 0), file_size - 1
    else:
        start = int(first)
        end = int(last) if last else file_size - 1
        if start >= file_size or end < start:
            raise HTTPException(
                status_code=416,
                detail="Requested range is not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        end = min(end, file_size - 1)
    return start, end, True


async def _resolve_message(client: Client, chat_id: int, message_id: int):
    msg = await client.get_messages(chat_id, message_id)
    if not msg or (isinstance(msg, list) and not msg):
        return None
    if isinstance(msg, list):
        msg = msg[0]
    if not msg or not (
        msg.media or msg.document or msg.video or msg.audio
        or msg.animation or msg.voice or msg.video_note or msg.photo
    ):
        return None
    return msg


async def _download_byte_range(client: Client, message, start: int, end: int):
    """
    Yield exact bytes [start, end] inclusive via stream_media.

    wzgram offset/limit are CHUNK counts (1 MiB), not bytes. We fetch covering
    chunks then trim the head/tail to the exact HTTP range.
    """
    if end < start:
        return
    length = end - start + 1
    off_chunks = start // TG_CHUNK
    head_skip = start % TG_CHUNK
    # chunks needed to cover head_skip + length
    need = head_skip + length
    limit_chunks = max(1, math.ceil(need / TG_CHUNK))

    async with STREAM_SEMAPHORE:
        yielded = 0
        skip = head_skip
        async for chunk in client.stream_media(
            message, offset=off_chunks, limit=limit_chunks
        ):
            if not chunk:
                continue
            data = bytes(chunk)
            if skip:
                if skip >= len(data):
                    skip -= len(data)
                    continue
                data = data[skip:]
                skip = 0
            if yielded + len(data) > length:
                data = data[: length - yielded]
            if data:
                yield data
                yielded += len(data)
            if yielded >= length:
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

    primary = live[0]
    message = await _resolve_message(primary, chat_id, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Media no longer available on Telegram")

    n = min(len(live), MAX_PARALLEL_PARTS)
    if total < MIN_PART_BYTES * 2:
        n = 1
    else:
        n = min(n, max(1, total // MIN_PART_BYTES))

    if n <= 1:
        async for chunk in _download_byte_range(primary, message, start, end):
            yield chunk
        return

    # Split byte range into ordered parts; each session fetches one part
    part_size = (total + n - 1) // n
    ranges = []
    cursor = start
    for _ in range(n):
        part_end = min(cursor + part_size - 1, end)
        ranges.append((cursor, part_end))
        cursor = part_end + 1
        if cursor > end:
            break

    async def fetch_part(idx: int, p_start: int, p_end: int) -> bytes:
        client = live[idx % len(live)]
        try:
            msg = await _resolve_message(client, chat_id, message_id) or message
        except Exception:
            msg = message
        buf = bytearray()
        async for chunk in _download_byte_range(client, msg, p_start, p_end):
            buf.extend(chunk)
        return bytes(buf)

    tasks = [asyncio.create_task(fetch_part(i, r0, r1)) for i, (r0, r1) in enumerate(ranges)]
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

    safe_name = re.sub(r'[\r\n"\\]', "_", filename or "media")[:180]
    is_download = request.url.path.startswith("/dl/")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": (
            f'{"attachment" if is_download else "inline"}; '
            f'filename="{safe_name}"; filename*=UTF-8\'\'{quote(safe_name)}'
        ),
        "Cache-Control": "private, no-cache",
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
    client: Client,
    chat_id: int,
    message_id: int,
    file_size: int,
    audio_track: int = 0,
    force_aac: bool = True,
):
    if audio_track < 0 or audio_track > 32:
        raise HTTPException(status_code=400, detail="Invalid audio track")

    message = await _resolve_message(client, chat_id, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Media no longer available on Telegram")

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


async def get_remux_response(
    client: Client,
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
        remux_streamer(client, chat_id, message_id, file_size, audio_track, force_aac=force_aac),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Cache-Control": "no-store",
            "Accept-Ranges": "none",
            "X-Accel-Buffering": "no",
        },
    )
