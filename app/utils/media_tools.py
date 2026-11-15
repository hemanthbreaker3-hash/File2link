"""
/ss and /sv helpers aligned with screenshot-bot (Heroku Support) logic:
- duration via ffprobe
- reduced_sec = duration - 2%
- screenshots: random.randint(0, reduced_sec) or evenly spaced
- sample: random start in [0, reduced_sec - sample_duration], ffmpeg -ss -t -c copy
Uses HTTP stream URL (Range) instead of full download when possible.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

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
    base = (os.environ.get("BASE_URL") or "").strip().rstrip("/")
    path = f"/_tgstream/{chat_id}/{message_id}"
    if base:
        try:
            u = urlparse(base)
            if u.scheme in ("http", "https") and u.netloc:
                return f"{base}{path}"
        except Exception:
            pass
    p = port or int(os.environ.get("PORT", "8000"))
    return f"http://127.0.0.1:{p}{path}"


def get_random_start_at(seconds: int, dur: int = 0) -> int:
    """Same as screenshot-bot Utilities.get_random_start_at."""
    seconds = max(0, int(seconds))
    dur = max(0, int(dur))
    if seconds <= dur:
        return 0
    return random.randint(0, seconds - dur)


async def probe_duration(source: str) -> float:
    """ffprobe duration (seconds), matching screenshot-bot get_duration style."""
    cmd = [
        "ffprobe",
        "-i", source,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        "-select_streams", "v:0",
    ]
    code, out, err = await _run(cmd, timeout=90)
    if code != 0 or not (out or b"").strip():
        # fallback without stream select
        cmd2 = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            source,
        ]
        code, out, err = await _run(cmd2, timeout=90)
    if code != 0:
        logger.warning("ffprobe duration failed: %s", err.decode(errors="replace")[-400:])
        return 0.0
    try:
        return max(0.0, float((out or b"0").decode().strip() or "0"))
    except ValueError:
        return 0.0


async def download_message_media(client, message, dest_path: str) -> str:
    path = await client.download_media(message, file_name=dest_path)
    if not path or not os.path.exists(path):
        raise RuntimeError("download_media failed")
    return str(path)


async def generate_screenshots(
    source: str,
    count: int,
    out_dir: str,
    region: str = "anywhere",
) -> List[str]:
    """
    screenshot-bot style:
      reduced_sec = duration - 2%
      random mode: get_random_start_at(reduced_sec) for each frame
      equal mode: int(reduced_sec / n) * i
    region ignored for compatibility except:
      equal -> evenly spaced (mode 0)
      anywhere / middle_* -> random (default)
    """
    count = max(1, min(int(count), 30))
    duration = await probe_duration(source)
    if duration <= 0:
        duration = 60.0

    duration_i = int(duration)
    reduced_sec = max(1, duration_i - int(duration_i * 2 / 100))

    if region == "equal":
        screenshot_secs = [
            int(reduced_sec / count) * i for i in range(1, 1 + count)
        ]
    else:
        # random totally (screenshot-bot mode != 0)
        screenshot_secs = [get_random_start_at(reduced_sec) for _ in range(count)]

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    paths: List[str] = []

    # Base ffmpeg command (screenshot-bot): -ss before -i, -vframes 1, scale
    for i, sec in enumerate(screenshot_secs):
        out = os.path.join(out_dir, f"{i + 1}.jpg")
        cmd = [
            "ffmpeg",
            "-ss", str(sec),
            "-i", source,
            "-vf", "scale=1280:-1",
            "-y",
            "-vframes", "1",
            out,
        ]
        code, _, err = await _run(cmd, timeout=120)
        if code == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            paths.append(out)
        else:
            logger.warning(
                "screenshot @%ss failed: %s", sec, err.decode(errors="replace")[-300:]
            )
    return paths


async def generate_sample_video(
    source: str,
    seconds: int,
    out_path: str,
    region: str = "middle_last",
) -> str:
    """
    screenshot-bot sample:
      reduced_sec = duration - 2%
      start_at = get_random_start_at(reduced_sec, sample_duration)
      ffmpeg -ss start -i -t duration -map 0 -c copy
    """
    seconds = max(1, min(int(seconds), 120))
    duration = await probe_duration(source)
    if duration <= 0:
        duration = float(seconds + 1)

    duration_i = int(duration)
    reduced_sec = max(1, duration_i - int(duration_i * 2 / 100))
    sample_duration = min(seconds, max(1, reduced_sec))
    start_at = get_random_start_at(reduced_sec, sample_duration)

    cmd_copy = [
        "ffmpeg",
        "-ss", str(start_at),
        "-i", source,
        "-t", str(sample_duration),
        "-map", "0",
        "-c", "copy",
        "-y",
        out_path,
    ]
    code, _, err = await _run(cmd_copy, timeout=180)
    if code == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path

    logger.info("sample copy failed, re-encoding: %s", err.decode(errors="replace")[-200:])
    cmd_enc = [
        "ffmpeg",
        "-ss", str(start_at),
        "-i", source,
        "-t", str(sample_duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-y",
        out_path,
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
