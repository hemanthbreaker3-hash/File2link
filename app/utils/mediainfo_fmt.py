"""MediaInfo / ffprobe extraction and pretty formatting for /mi."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import ipaddress
import socket
from urllib.parse import urlparse
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def validate_download_url(url: str) -> None:
    """Reject non-HTTP and private/local destinations before probing user-supplied URLs."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP/HTTPS download URLs are supported.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded credentials are not supported.")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost"} or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Local/private download URLs are not allowed.")
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                           type=socket.SOCK_STREAM)
            addresses = {ipaddress.ip_address(info[4][0]) for info in infos}
        except Exception as exc:
            raise ValueError("Could not resolve the download URL host.") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Private or reserved network destinations are not allowed.")


async def _run(cmd: list[str], timeout: float = 120.0) -> tuple[int, str, str]:
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
        raise TimeoutError("probe timed out")
    return proc.returncode or 0, (out or b"").decode(errors="replace"), (err or b"").decode(errors="replace")


async def extract_mediainfo(path_or_url: str) -> Dict[str, Any]:
    """Prefer mediainfo JSON; fall back to ffprobe. Accepts local path or http URL."""
    if shutil.which("mediainfo"):
        code, out, err = await _run(["mediainfo", "--Output=JSON", path_or_url])
        if code == 0 and out.strip():
            return json.loads(out)
        logger.warning("mediainfo failed (%s): %s", code, err[-300:])

    if shutil.which("ffprobe"):
        code, out, err = await _run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", path_or_url,
        ])
        if code == 0 and out.strip():
            return json.loads(out)
        raise RuntimeError(f"ffprobe failed: {err[-400:]}")

    raise RuntimeError("Neither mediainfo nor ffprobe is available on the system.")


def align(key: str, value: Any, width: int = 28) -> str:
    if value is None or value == "" or value == "N/A":
        return ""
    return f"{key:<{width}} : {value}\n"


def format_output(data: Dict[str, Any]) -> str:
    if "media" in data:
        return format_mediainfo(data)
    if "streams" in data or "format" in data:
        return format_ffprobe(data)
    return "Could not parse media information."


def format_mediainfo(data: Dict[str, Any]) -> str:
    text = ""
    tracks = (data.get("media") or {}).get("track") or []
    for track in tracks:
        ttype = track.get("@type")
        if ttype == "General":
            text += "General\n"
            text += align("Unique ID", track.get("UniqueID"))
            text += align("Format", track.get("Format"))
            text += align("Format version", track.get("Format_Version"))
            text += align("File size", track.get("FileSize_String", track.get("FileSize")))
            text += align("Duration", track.get("Duration_String3", track.get("Duration_String", track.get("Duration"))))
            text += align("Overall bit rate", track.get("OverallBitRate_String"))
            text += align("Frame rate", track.get("FrameRate"))
            text += align("Title", track.get("Title"))
            text += align("Encoded date", track.get("Encoded_Date"))
            text += align("Writing application", track.get("Encoded_Application"))
            text += align("Writing library", track.get("Encoded_Library"))
            text += "\n"
        elif ttype == "Video":
            text += "Video\n"
            text += align("ID", track.get("ID"))
            text += align("Format", track.get("Format"))
            text += align("Format/Info", track.get("Format_Info"))
            text += align("Format profile", track.get("Format_Profile"))
            text += align("Codec ID", track.get("CodecID"))
            text += align("Duration", track.get("Duration_String3", track.get("Duration_String")))
            text += align("Bit rate", track.get("BitRate_String"))
            text += align("Width", track.get("Width"))
            text += align("Height", track.get("Height"))
            text += align("Display aspect ratio", track.get("DisplayAspectRatio_String"))
            text += align("Frame rate mode", track.get("FrameRate_Mode_String"))
            fr = track.get("FrameRate")
            text += align("Frame rate", f"{fr} FPS" if fr else None)
            text += align("Color space", track.get("ColorSpace"))
            text += align("Chroma subsampling", track.get("ChromaSubsampling"))
            text += align("Bit depth", track.get("BitDepth"))
            text += align("Scan type", track.get("ScanType_String", track.get("ScanType")))
            text += "\n"
        elif ttype == "Audio":
            idx = track.get("StreamOrder") or track.get("ID") or "?"
            text += f"Audio #{idx}\n"
            text += align("ID", track.get("ID"))
            text += align("Format", track.get("Format"))
            text += align("Format/Info", track.get("Format_Info"))
            text += align("Codec ID", track.get("CodecID"))
            text += align("Duration", track.get("Duration_String3", track.get("Duration_String")))
            text += align("Bit rate", track.get("BitRate_String"))
            text += align("Channels", track.get("Channels_String", track.get("Channels")))
            text += align("Channel layout", track.get("ChannelLayout"))
            text += align("Sampling rate", track.get("SamplingRate_String", track.get("SamplingRate")))
            text += align("Bit depth", track.get("BitDepth"))
            text += align("Language", track.get("Language_String", track.get("Language")))
            text += align("Title", track.get("Title"))
            text += align("Default", track.get("Default"))
            text += align("Forced", track.get("Forced"))
            text += "\n"
        elif ttype == "Text":
            idx = track.get("StreamOrder") or track.get("ID") or "?"
            text += f"Subtitle #{idx}\n"
            text += align("ID", track.get("ID"))
            text += align("Format", track.get("Format"))
            text += align("Codec ID", track.get("CodecID"))
            text += align("Language", track.get("Language_String", track.get("Language")))
            text += align("Title", track.get("Title"))
            text += align("Default", track.get("Default"))
            text += align("Forced", track.get("Forced"))
            text += "\n"
    return text.strip() or "No tracks found."


def format_ffprobe(data: Dict[str, Any]) -> str:
    text = ""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    text += "General\n"
    text += align("Format", fmt.get("format_long_name") or fmt.get("format_name"))
    try:
        size = int(float(fmt.get("size") or 0))
        if size:
            if size >= 1024 ** 3:
                text += align("File size", f"{size / (1024 ** 3):.2f} GiB")
            else:
                text += align("File size", f"{size / (1024 ** 2):.2f} MiB")
    except (TypeError, ValueError):
        pass
    try:
        dur = float(fmt.get("duration") or 0)
        if dur:
            h = int(dur // 3600)
            m = int((dur % 3600) // 60)
            s = dur % 60
            text += align("Duration", f"{h:02d}:{m:02d}:{s:06.3f}" if h else f"{m:02d}:{s:06.3f}")
    except (TypeError, ValueError):
        pass
    try:
        br = int(float(fmt.get("bit_rate") or 0))
        if br:
            text += align("Bit rate", f"{br / 1000:.1f} kb/s")
    except (TypeError, ValueError):
        pass
    text += "\n"

    a_i = v_i = s_i = 0
    for s in streams:
        stype = s.get("codec_type")
        tags = s.get("tags") or {}
        if stype == "video":
            v_i += 1
            text += f"Video #{v_i}\n"
            text += align("Codec", s.get("codec_long_name") or s.get("codec_name"))
            text += align("Profile", s.get("profile"))
            w, h = s.get("width"), s.get("height")
            if w and h:
                text += align("Resolution", f"{w}x{h}")
            text += align("Pixel format", s.get("pix_fmt"))
            text += align("Frame rate", s.get("avg_frame_rate") or s.get("r_frame_rate"))
            text += align("Bit rate", s.get("bit_rate"))
            text += align("Bit depth", s.get("bits_per_raw_sample"))
            text += align("Color space", s.get("color_space"))
            text += align("Language", tags.get("language"))
            text += align("Title", tags.get("title"))
            text += "\n"
        elif stype == "audio":
            a_i += 1
            text += f"Audio #{a_i}\n"
            text += align("Codec", s.get("codec_long_name") or s.get("codec_name"))
            text += align("Channels", s.get("channels"))
            text += align("Channel layout", s.get("channel_layout"))
            text += align("Sample rate", s.get("sample_rate"))
            text += align("Bit rate", s.get("bit_rate"))
            text += align("Language", tags.get("language"))
            text += align("Title", tags.get("title"))
            text += "\n"
        elif stype == "subtitle":
            s_i += 1
            text += f"Subtitle #{s_i}\n"
            text += align("Codec", s.get("codec_name"))
            text += align("Language", tags.get("language"))
            text += align("Title", tags.get("title"))
            text += "\n"
    return text.strip() or "No streams found."
