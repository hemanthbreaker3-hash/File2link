"""Track probe via ffprobe on a partial download from Telegram (wzgram)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import traceback

logger = logging.getLogger(__name__)
PROBE_DOWNLOAD_SIZE = 5 * 1024 * 1024


async def probe_tracks(client, message, file_size: int) -> dict:
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
        probe_size = min(PROBE_DOWNLOAD_SIZE, file_size)
        downloaded = 0

        with open(temp_path, "wb") as f:
            async for chunk in client.stream_media(message, offset=0, limit=probe_size):
                if not chunk:
                    continue
                data = bytes(chunk)
                f.write(data)
                downloaded += len(data)
                if downloaded >= probe_size:
                    break

        logger.info("Downloaded %sKB for probing", downloaded / 1024)
        if downloaded == 0:
            return {
                "video_tracks": [],
                "audio_tracks": [],
                "subtitle_tracks": [],
                "has_multiple_audio": False,
                "error": "Downloaded 0 bytes from Telegram",
            }
        return await _run_ffprobe(temp_path)
    except Exception as e:
        logger.error("Track probing failed: %s\n%s", e, traceback.format_exc())
        return {
            "video_tracks": [],
            "audio_tracks": [],
            "subtitle_tracks": [],
            "has_multiple_audio": False,
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


async def _run_ffprobe(file_path: str) -> dict:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", file_path]
    try:
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=30.0, check=False
        )
    except Exception as e:
        return {
            "video_tracks": [], "audio_tracks": [], "subtitle_tracks": [],
            "has_multiple_audio": False, "error": f"ffprobe execution failed: {e}",
        }
    if result.returncode != 0:
        return {
            "video_tracks": [], "audio_tracks": [], "subtitle_tracks": [],
            "has_multiple_audio": False,
            "error": f"ffprobe error: {result.stderr.decode(errors='replace').strip()}",
        }
    try:
        probe_data = json.loads(result.stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return {
            "video_tracks": [], "audio_tracks": [], "subtitle_tracks": [],
            "has_multiple_audio": False, "error": "Failed to parse ffprobe output",
        }
    return _parse_probe_data(probe_data)


def _parse_probe_data(probe_data: dict) -> dict:
    video_tracks, audio_tracks, subtitle_tracks = [], [], []
    audio_index = 0
    for stream in probe_data.get("streams", []):
        codec_type = stream.get("codec_type", "")
        tags = stream.get("tags", {}) or {}
        language = tags.get("language", "und")
        title = tags.get("title", "")
        if codec_type == "video":
            video_tracks.append({
                "index": stream.get("index", 0),
                "codec": stream.get("codec_name", "unknown"),
                "width": stream.get("width", 0),
                "height": stream.get("height", 0),
                "fps": _parse_fps(stream.get("r_frame_rate", "0/1")),
                "language": language,
                "title": title,
            })
        elif codec_type == "audio":
            audio_tracks.append({
                "index": stream.get("index", 0),
                "audio_index": audio_index,
                "codec": stream.get("codec_name", "unknown"),
                "channels": stream.get("channels", 2),
                "channel_layout": stream.get("channel_layout", ""),
                "sample_rate": stream.get("sample_rate", ""),
                "bit_rate": stream.get("bit_rate", ""),
                "language": language,
                "title": title,
                "label": _build_audio_label(language, title, stream),
            })
            audio_index += 1
        elif codec_type == "subtitle":
            subtitle_tracks.append({
                "index": stream.get("index", 0),
                "codec": stream.get("codec_name", "unknown"),
                "language": language,
                "title": title,
            })
    return {
        "probe_version": 2,
        "video_tracks": video_tracks,
        "audio_tracks": audio_tracks,
        "subtitle_tracks": subtitle_tracks,
        "has_multiple_audio": len(audio_tracks) > 1,
    }


def _build_audio_label(language: str, title: str, stream: dict) -> str:
    lang_names = {
        "eng": "English", "hin": "Hindi", "jpn": "Japanese", "kor": "Korean",
        "spa": "Spanish", "fre": "French", "fra": "French", "ger": "German",
        "deu": "German", "ita": "Italian", "por": "Portuguese", "rus": "Russian",
        "ara": "Arabic", "chi": "Chinese", "zho": "Chinese", "tam": "Tamil",
        "tel": "Telugu", "kan": "Kannada", "mal": "Malayalam", "ben": "Bengali",
        "mar": "Marathi", "guj": "Gujarati", "pan": "Punjabi", "urd": "Urdu",
        "und": "Unknown",
    }
    lang_display = lang_names.get(language, language.upper() if language else "Unknown")
    codec = (stream.get("codec_name") or "").upper()
    channels = stream.get("channels", 2) or 2
    if channels >= 8:
        ch_label = "7.1"
    elif channels >= 6:
        ch_label = "5.1"
    elif channels == 2:
        ch_label = "Stereo"
    elif channels == 1:
        ch_label = "Mono"
    else:
        ch_label = f"{channels}ch"
    parts = [lang_display]
    if title and title.lower() != language:
        parts.append(f"- {title}")
    parts.append(f"({codec} {ch_label})")
    return " ".join(parts)


def _parse_fps(fps_str: str) -> float:
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/")
            return round(float(num) / float(den), 2)
        return float(fps_str)
    except (ValueError, ZeroDivisionError):
        return 0.0
