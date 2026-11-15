import math
from telethon import TelegramClient
from telethon.tl.types import Document, Photo
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse
import logging

logger = logging.getLogger(__name__)

async def ultra_high_speed_streamer(clients: list, file, start: int, end: int, chunk_size: int = 1024 * 1024):
    """
    Refactored ultra-high-speed multi-session streamer.
    Uses sequential download for single-session to avoid throttling,
    and optimized parallel download for multi-session.
    """
    import asyncio
    total_to_send = end - start + 1
    bytes_sent = 0
    
    session_count = len(clients)
    
    # If only one session (especially if it's a bot), parallel chunking can be counter-productive
    # due to Telegram's per-connection limits. Sequential is safer and often faster for bots.
    if session_count == 1:
        logger.info(f"Single session detected. Using sequential streaming for stability.")
        try:
            async for chunk in clients[0].iter_download(file, offset=start, limit=total_to_send, request_size=chunk_size):
                if not chunk: continue
                yield bytes(chunk)
                bytes_sent += len(chunk)
            return
        except Exception as e:
            logger.error(f"Sequential stream failed: {e}. Falling back to parallel.")
            # Reset and try parallel if sequential fails

    # Multi-session parallel logic
    concurrency_per_session = 8
    total_concurrency = concurrency_per_session * session_count
    
    logger.info(f"Starting multi-session parallel stream: {session_count} sessions, {total_concurrency} workers")
    
    offsets = list(range(start, end + 1, chunk_size))
    chunk_queue = asyncio.Queue()
    for offset in offsets:
        remaining = end - offset + 1
        current_chunk_size = min(chunk_size, remaining)
        chunk_queue.put_nowait((offset, current_chunk_size))
    
    received_chunks = {}
    completion_event = asyncio.Event()
    
    async def fetch_worker(client: TelegramClient, worker_id: int):
        while True:
            try:
                offset, size = chunk_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            
            success = False
            for attempt in range(3):
                try:
                    chunk = b""
                    async for part in client.iter_download(file, offset=offset, limit=size):
                        chunk += part
                    
                    if len(chunk) > 0:
                        received_chunks[offset] = chunk
                        chunk_queue.task_done()
                        completion_event.set()
                        success = True
                        break
                except Exception as e:
                    await asyncio.sleep((attempt + 1) * 1)
            
            if not success:
                chunk_queue.put_nowait((offset, size))
                await asyncio.sleep(2)

    workers = [asyncio.create_task(fetch_worker(clients[i % session_count], i)) for i in range(total_concurrency)]
    
    next_offset = start
    while bytes_sent < total_to_send:
        if next_offset in received_chunks:
            chunk = received_chunks.pop(next_offset)
            if bytes_sent + len(chunk) > total_to_send:
                chunk = chunk[:total_to_send - bytes_sent]
            yield bytes(chunk)
            bytes_sent += len(chunk)
            next_offset += len(chunk)
        else:
            completion_event.clear()
            try:
                await asyncio.wait_for(completion_event.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                if bytes_sent >= total_to_send: break
                logger.warning(f"Still waiting for chunk at offset {next_offset}...")
                if all(w.done() for w in workers): break

    for w in workers: w.cancel()
    logger.info(f"Stream finished. Total: {bytes_sent/1024/1024:.2f} MB")

async def media_streamer(clients: list[TelegramClient], file, start: int, end: int):
    """
    Parallel media streamer that fetches multiple chunks from Telegram simultaneously
    using multiple sessions for load balancing.
    """
    import asyncio
    total_to_send = end - start + 1
    bytes_sent = 0
    
    # Adaptive Chunk Sizing
    if total_to_send < 10 * 1024 * 1024:  # < 10MB
        chunk_size = 512 * 1024
    elif total_to_send < 100 * 1024 * 1024:  # < 100MB
        chunk_size = 1024 * 1024
    elif total_to_send < 500 * 1024 * 1024:  # < 500MB
        chunk_size = 2 * 1024 * 1024
    else:
        chunk_size = 4 * 1024 * 1024

    concurrency = 16 if total_to_send < 100 * 1024 * 1024 else 32
    client_count = len(clients)
    
    # Divide the requested range into smaller chunks for parallel fetching
    offsets = list(range(start, end + 1, chunk_size))
    
    for i in range(0, len(offsets), concurrency):
        batch = offsets[i:i + concurrency]
        
        # Helper to fetch a single chunk with retries and session rotation
        async def fetch_part(offset, task_idx):
            # Rotate clients per task for multi-session load balancing
            client = clients[task_idx % client_count]
            remaining = end - offset + 1
            current_chunk_size = min(chunk_size, remaining)
            
            for attempt in range(3):
                try:
                    chunk = b""
                    async for part in client.iter_download(file, offset=offset, limit=current_chunk_size):
                        chunk += part
                    return chunk
                except Exception as e:
                    logger.warning(f"Fetch failed (offset {offset}, attempt {attempt+1}): {e}")
                    if attempt == 2: return None
                    await asyncio.sleep(0.5)
            return None

        # Fetch batch in parallel with distributed clients
        tasks = [fetch_part(offset, i + idx) for idx, offset in enumerate(batch)]
        chunks = await asyncio.gather(*tasks)
        
        for chunk in chunks:
            if not chunk: continue
            
            if bytes_sent + len(chunk) > total_to_send:
                chunk = chunk[:total_to_send - bytes_sent]
            
            yield bytes(chunk)
            bytes_sent += len(chunk)
            
            if bytes_sent >= total_to_send:
                break

def get_range_header(request: Request, file_size: int):
    """Parse one RFC 7233 byte range, including suffix ranges."""
    range_header = request.headers.get("Range")
    if not range_header:
        return 0, file_size - 1, False
    if not range_header.startswith("bytes=") or "," in range_header:
        raise HTTPException(
            status_code=416, detail="Only one byte range is supported",
            headers={"Content-Range": f"bytes */{file_size}"}
        )
    try:
        first, last = range_header[6:].split("-", 1)
        if first:
            start = int(first)
            end = int(last) if last else file_size - 1
        else:
            suffix_length = int(last)
            if suffix_length <= 0:
                raise ValueError("Invalid suffix range")
            start = max(0, file_size - suffix_length)
            end = file_size - 1
        if start < 0 or start >= file_size or end < start:
            raise ValueError("Unsatisfiable range")
        return start, min(end, file_size - 1), True
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=416, detail="Requested byte range is not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"}
        )


async def get_streaming_response(
    clients: list[TelegramClient], file, file_size: int, filename: str,
    mime_type: str, request: Request, as_attachment: bool = False
):
    if file_size <= 0:
        raise HTTPException(status_code=416, detail="Empty media file")
    start, end, is_partial = get_range_header(request, file_size)
    from urllib.parse import quote
    safe_name = (filename or "media").replace("\r", "").replace("\n", "")
    safe_name = safe_name.replace('"', "'")
    disposition = "attachment" if as_attachment else "inline"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Type": mime_type or "application/octet-stream",
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(safe_name)}",
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
        "X-Content-Type-Options": "nosniff",
    }
    if is_partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    return StreamingResponse(
        ultra_high_speed_streamer(clients, file, start, end),
        status_code=206 if is_partial else 200,
        headers=headers,
    )


# ─── FFmpeg Remux Streaming (Audio Track Switching) ───────────────────────────

async def remux_streamer(client: TelegramClient, file, file_size: int, audio_track: int = 0):
    """Pipe Telegram media through FFmpeg, selecting one audio track and
    converting it to broadly supported AAC while stream-copying the video.
    """
    import asyncio

    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", "pipe:0",
        "-map", "0:v:0?",
    ]
    if audio_track >= 0:
        command += ["-map", f"0:a:{audio_track}"]
    command += [
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]

    process = None
    feeder = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async def feed_source():
            try:
                async for chunk in client.iter_download(
                    file, offset=0, limit=file_size, request_size=512 * 1024
                ):
                    if not chunk:
                        continue
                    process.stdin.write(bytes(chunk))
                    await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                logger.info("FFmpeg closed input before Telegram download completed")
            finally:
                if process.stdin and not process.stdin.is_closing():
                    process.stdin.close()
                    try:
                        await process.stdin.wait_closed()
                    except Exception:
                        pass

        feeder = asyncio.create_task(feed_source())
        while True:
            chunk = await process.stdout.read(256 * 1024)
            if not chunk:
                break
            yield chunk

        await feeder
        return_code = await process.wait()
        if return_code:
            logger.error("FFmpeg remux exited with status %s", return_code)
    except FileNotFoundError:
        logger.exception("FFmpeg is not installed")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("FFmpeg remux stream failed")
    finally:
        if feeder and not feeder.done():
            feeder.cancel()
            try:
                await feeder
            except (asyncio.CancelledError, Exception):
                pass
        if process and process.returncode is None:
            process.kill()
            try:
                await process.wait()
            except Exception:
                pass


async def get_remux_response(
    client: TelegramClient,
    file,
    file_size: int,
    filename: str,
    audio_track: int = 0
):
    """Return a browser-friendly fragmented MP4 stream with AAC audio."""
    from urllib.parse import quote
    safe_name = (filename.rsplit(".", 1)[0] if "." in filename else filename) + ".mp4"
    safe_name = safe_name.replace("\r", "").replace("\n", "").replace('"', "'")
    headers = {
        "Content-Type": "video/mp4",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(safe_name)}",
        "Cache-Control": "no-store",
        "Access-Control-Allow-Origin": "*",
        "Accept-Ranges": "none",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        remux_streamer(client, file, file_size, audio_track),
        status_code=200,
        headers=headers,
        media_type="video/mp4",
    )
