"""FFmpeg helpers for /ss and /sv — prefer HTTP URL (no full download)."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


async def _run(cmd: list[str], timeout: float = 300.0) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise TimeoutError("ffmpeg timed out")
    return proc.returncode or 0, out or b"", err or b""


def local_tg_stream_url(chat_id: int, message_id: int, port: int | None = None) -> str:
    """URL handled by FastAPI /_tgstream — mediainfo/ffmpeg use Range requests."""
    p = port or int(os.environ.get("PORT", "8000"))
    return f"http://127.0.0.1:{p}/_tgstream/{chat_id}/{message_id}"


async def probe_duration(source: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        source,
    ]
    code, out, err = await _run(cmd, timeout=90)
    if code != 0:
        logger.warning("ffprobe duration failed: %s", err.decode(errors="replace")[-400:])
        return 0.0
    try:
        return max(0.0, float((out or b"0").decode().strip() or "0"))
    except ValueError:
        return 0.0


async def download_message_media(client, message, dest_path: str) -> str:
    """Fallback full download (large files / when URL probe fails)."""
    path = await client.download_media(message, file_name=dest_path)
    if not path or not os.path.exists(path):
        raise RuntimeError("download_media failed")
    return str(path)


async def generate_screenshots(source: str, count: int, out_dir: str) -> List[str]:
    """Evenly spaced JPEG screenshots from URL or local path (no full file required for URL)."""
    count = max(1, min(int(count), 30))
    duration = await probe_duration(source)
    if duration <= 0:
        duration = 1.0

    paths: List[str] = []
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    for i in range(count):
        if count == 1:
            t = min(0.5, duration * 0.1)
        else:
            t = (duration * (i + 1)) / (count + 1)
        out = os.path.join(out_dir, f"ss_{i + 1:02d}.jpg")
        # -ss before -i enables input seeking (HTTP Range on our streamer)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{t:.3f}",
            "-i", source,
            "-frames:v", "1",
            "-q:v", "2",
            "-y", out,
        ]
        code, _, err = await _run(cmd, timeout=120)
        if code == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            paths.append(out)
        else:
            logger.warning("screenshot %s failed: %s", i + 1, err.decode(errors="replace")[-300:])
    return paths


async def generate_sample_video(source: str, seconds: int, out_path: str) -> str:
    """Sample from start via URL/path — only reads needed ranges when source is HTTP."""
    seconds = max(1, min(int(seconds), 120))
    duration = await probe_duration(source)
    if duration > 0:
        seconds = min(seconds, max(1, int(duration)))

    cmd_copy = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", "0",
        "-i", source,
        "-t", str(seconds),
        "-c", "copy",
        "-movflags", "+faststart",
        "-y", out_path,
    ]
    code, _, err = await _run(cmd_copy, timeout=180)
    if code == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path

    logger.info("sample copy failed, re-encoding: %s", err.decode(errors="replace")[-200:])
    cmd_enc = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", "0",
        "-i", source,
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-y", out_path,
    ]
    code, _, err = await _run(cmd_enc, timeout=300)
    if code != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"sample encode failed: {err.decode(errors='replace')[-400:]}")
    return out_path


def make_work_dir(prefix: str = "hitler_media_") -> str:
    return tempfile.mkdtemp(prefix=prefix)


def cleanup_dir(path: Optional[str]) -> None:
    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
