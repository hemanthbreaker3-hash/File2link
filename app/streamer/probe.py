"""Reliable media track discovery using ffprobe.

A short prefix is not reliable for MP4/MOV files whose moov atom is at the end.
We download the media once to a temporary file, probe it, and cache the result
in MongoDB at the API layer.
"""
import asyncio
import json
import os
import tempfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _empty_result(error=None):
    result = {
        "video_tracks": [],
        "audio_tracks": [],
        "subtitle_tracks": [],
        "has_multiple_audio": False,
    }
    if error:
        result["error"] = str(error)
    return result


async def probe_tracks(client, message_or_media, file_size: int, filename: str = "media"):
    """Download the complete source to a temporary file and inspect all tracks."""
    suffix = Path(filename or "media").suffix[:12] or ".media"
    fd, temp_path = tempfile.mkstemp(prefix="filetolink-probe-", suffix=suffix)
    os.close(fd)
    try:
        media = getattr(message_or_media, "media", message_or_media)
        if media is None:
            return _empty_result("No media was supplied for probing")

        logger.info("Downloading %s bytes temporarily for complete audio-track detection", file_size)
        result_path = await client.download_media(media, file=temp_path)
        if not result_path or not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
            return _empty_result("Telegram returned an empty media download")

        return await _run_ffprobe(temp_path)
    except Exception as exc:
        logger.exception("Track probing failed")
        return _empty_result(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove probe temporary file: %s", temp_path)


async def _run_ffprobe(file_path: str) -> dict:
    import subprocess
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", file_path,
    ]
    try:
        completed = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, timeout=180, check=False
        )
    except FileNotFoundError:
        return _empty_result("ffprobe is not installed; install FFmpeg in the deployment image")
    except Exception as exc:
        return _empty_result(f"ffprobe execution failed: {exc}")

    if completed.returncode != 0:
        return _empty_result(completed.stderr.decode("utf-8", "replace").strip() or "ffprobe failed")
    try:
        return _parse_probe_data(json.loads(completed.stdout.decode("utf-8", "replace")))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _empty_result(f"Invalid ffprobe output: {exc}")


def _parse_probe_data(probe_data: dict) -> dict:
    video_tracks, audio_tracks, subtitle_tracks = [], [], []
    audio_index = 0
    for stream in probe_data.get("streams", []):
        kind = stream.get("codec_type")
        tags = stream.get("tags") or {}
        language = tags.get("language", "und")
        title = tags.get("title", "")
        if kind == "video":
            video_tracks.append({
                "index": stream.get("index", 0),
                "codec": stream.get("codec_name", "unknown"),
                "width": stream.get("width", 0),
                "height": stream.get("height", 0),
                "fps": _parse_fps(stream.get("r_frame_rate", "0/1")),
                "language": language,
                "title": title,
            })
        elif kind == "audio":
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
                "default": bool((stream.get("disposition") or {}).get("default", 0)),
                "label": _build_audio_label(language, title, stream),
            })
            audio_index += 1
        elif kind == "subtitle":
            subtitle_tracks.append({
                "index": stream.get("index", 0),
                "codec": stream.get("codec_name", "unknown"),
                "language": language,
                "title": title,
            })
    return {
        "video_tracks": video_tracks,
        "audio_tracks": audio_tracks,
        "subtitle_tracks": subtitle_tracks,
        "has_multiple_audio": len(audio_tracks) > 1,
        "format": (probe_data.get("format") or {}).get("format_name", ""),
        "probe_version": 2,
    }


def _build_audio_label(language: str, title: str, stream: dict) -> str:
    names = {
        "eng":"English","hin":"Hindi","jpn":"Japanese","kor":"Korean",
        "spa":"Spanish","fre":"French","fra":"French","ger":"German",
        "deu":"German","ita":"Italian","por":"Portuguese","rus":"Russian",
        "ara":"Arabic","chi":"Chinese","zho":"Chinese","tam":"Tamil",
        "tel":"Telugu","kan":"Kannada","mal":"Malayalam","ben":"Bengali",
        "mar":"Marathi","guj":"Gujarati","pan":"Punjabi","urd":"Urdu",
        "und":"Unknown",
    }
    lang = names.get((language or "und").lower(), (language or "und").upper())
    codec = (stream.get("codec_name") or "unknown").upper()
    channels = int(stream.get("channels") or 2)
    layout = stream.get("channel_layout") or (
        "5.1" if channels == 6 else "7.1" if channels == 8
        else "Stereo" if channels == 2 else "Mono" if channels == 1 else f"{channels}ch"
    )
    parts = [lang]
    if title and title.lower() not in {language.lower(), lang.lower()}:
        parts.append(f"- {title}")
    parts.append(f"({codec} {layout})")
    return " ".join(parts)


def _parse_fps(value: str) -> float:
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            return round(float(numerator) / float(denominator), 2)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
