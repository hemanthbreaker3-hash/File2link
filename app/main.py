from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from app.database.connection import settings, files_col, create_indexes
from app.streamer.manager import session_manager
from app.streamer.engine import get_streaming_response, get_remux_response
from app.streamer.probe import probe_tracks
from app.bot.main import register_handlers
from app.admin.routes import router as admin_router
from fastapi.middleware.gzip import GZipMiddleware
import uvicorn
import asyncio
import logging
import sys

# Configure Windows Event Loop Policy for subprocess support
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    await create_indexes()
    await session_manager.start()
    register_handlers(session_manager.bot_client)
    logger.info("Application started")
    yield
    # Shutdown logic
    await session_manager.stop()
    logger.info("Application stopped")

app = FastAPI(title="Telegram Direct Media Link Generator", lifespan=lifespan)

# Include Routers
app.include_router(admin_router)

# Enable Compression
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Mount Static Files & Templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Fast health check – returns immediately so deploy scripts don't wait 180s
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/watch/{short_code}")
async def watch_page(request: Request, short_code: str):
    file_data = await files_col.find_one({"short_code": short_code})
    if not file_data:
        raise HTTPException(status_code=404, detail="Link not found or expired")
    
    from urllib.parse import quote, urlsplit

    stream_url = f"{settings.BASE_URL.rstrip('/')}/stream/{short_code}"
    parsed = urlsplit(stream_url)
    host_path = parsed.netloc + parsed.path
    fallback = quote(stream_url, safe="")
    source_mime = (file_data.get("mime_type") or "video/*").lower()
    intent_mime = "audio%2F*" if source_mime.startswith("audio/") else "video%2F*"
    # Package IDs target Android app packages. HTTPS remains the fallback
    # when the app is absent or the page is opened outside Android Chrome.
    player_apps = [
        ("VLC", "org.videolan.vlc", "fas fa-play-circle"),
        ("MX Player", "com.mxtech.videoplayer.ad", "fas fa-play-circle"),
        ("MX Player Pro", "com.mxtech.videoplayer.pro", "fas fa-play-circle"),
        ("PLAYit", "com.playit.videoplayer", "fas fa-play-circle"),
        ("nPlayer", "com.newin.nplayer.pro", "fas fa-film"),
        ("KMPlayer", "com.kmplayer", "fas fa-film"),
        ("XPlayer", "video.player.videoplayer", "fas fa-film"),
        ("Just Player", "com.brouken.player", "fas fa-film"),
        ("BSPlayer", "com.bsplayer.bspandroid.free", "fas fa-film"),
        ("Nova Player", "org.courville.nova", "fas fa-film"),
        ("mpv", "is.xyz.mpv", "fas fa-film"),
        ("Vimu Player", "net.gtvbox.videoplayer", "fas fa-film"),
    ]
    player_links = [{
        "name": name,
        "icon": icon,
        "intent_url": (
            f"intent://{host_path}#Intent;scheme={parsed.scheme};"
            f"package={package};type={intent_mime};"
            f"S.browser_fallback_url={fallback};end"
        ),
    } for name, package, icon in player_apps]

    return templates.TemplateResponse("watch.html", {
        "request": request,
        "file": file_data,
        "base_url": settings.BASE_URL.rstrip("/"),
        "stream_url": stream_url,
        "player_links": player_links,
    })

async def _stream_file_response(request: Request, short_code: str, as_attachment: bool = False):
    file_data = await files_col.find_one({"short_code": short_code})
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")

    # Use all available clients for ultra-high-speed downloads
    clients = session_manager.get_all_clients()
    client = clients[0]  # Use first client for initial message fetch
    
    try:
        # Fetch the message that contains the media
        msg = await client.get_messages(file_data['chat_id'], ids=file_data['message_id'])
        if not msg or not msg.media:
            raise HTTPException(status_code=404, detail="Media no longer available on Telegram")
        
        file = msg.media
        # Some media types are nested
        if hasattr(file, 'document'):
            file = file.document
        elif hasattr(file, 'photo'):
            file = file.photo
            
    except Exception as e:
        logger.error(f"Error fetching file: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving file from Telegram")

    return await get_streaming_response(
        clients,  # Pass ALL clients for parallel downloading
        file=file,
        file_size=file_data['file_size'],
        filename=file_data['filename'],
        mime_type=file_data['mime_type'],
        request=request,
        as_attachment=as_attachment,
    )


@app.get("/stream/{short_code}")
async def stream_file(request: Request, short_code: str):
    return await _stream_file_response(request, short_code, as_attachment=False)


@app.get("/dl/{short_code}")
async def download_file(request: Request, short_code: str):
    return await _stream_file_response(request, short_code, as_attachment=True)


# ─── Audio Track Discovery API ────────────────────────────────────────────────

@app.get("/api/tracks/{short_code}")
async def get_tracks(short_code: str):
    """Return available audio/video/subtitle tracks for a media file."""
    file_data = await files_col.find_one({"short_code": short_code})
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")
    
    # Track probing is expensive because MP4 metadata may be stored at EOF.
    # Cache successful version-2 probes and reuse them on subsequent visits.
    cached = file_data.get("tracks_info")
    if cached and cached.get("probe_version") == 2 and not cached.get("error"):
        return JSONResponse(cached)
    
    # Need to probe the file
    client = session_manager.get_client()
    
    try:
        msg = await client.get_messages(file_data['chat_id'], ids=file_data['message_id'])
        if not msg or not msg.media:
            raise HTTPException(status_code=404, detail="Media no longer available")
        
        media = msg.media
        
        # Photos don't have audio tracks
        if hasattr(media, 'photo') and not hasattr(media, 'document'):
            return JSONResponse({
                "video_tracks": [],
                "audio_tracks": [],
                "subtitle_tracks": [],
                "has_multiple_audio": False
            })
        
        # Pass the message for download_media fallback, and media for iter_download
        tracks_info = await probe_tracks(
            client, msg, file_data["file_size"], file_data.get("filename", "media")
        )
        
        # Only cache if no error
        if not tracks_info.get("error"):
            await files_col.update_one(
                {"short_code": short_code},
                {"$set": {"tracks_info": tracks_info}}
            )
        
        return JSONResponse(tracks_info)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Track probing error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Failed to probe tracks")


# ─── Remux Streaming (Audio Track Selection) ──────────────────────────────────

@app.get("/remux/{short_code}")
async def remux_file(request: Request, short_code: str, audio: int = 0):
    """
    Stream media remuxed with a selected audio track.
    Uses FFmpeg to remux (no transcoding) into fragmented MP4.
    
    Query params:
        audio: Audio track index (0-based, default 0)
    """
    file_data = await files_col.find_one({"short_code": short_code})
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")
    
    if audio < -1:
        raise HTTPException(status_code=400, detail="Audio track index must be -1 or greater")

    # Validate requested audio index against cached/probed tracks. -1 means
    # video-only output and is used for media without an audio stream.
    track_info = file_data.get("tracks_info")
    if not track_info or track_info.get("probe_version") != 2:
        client_for_probe = session_manager.get_client()
        probe_msg = await client_for_probe.get_messages(
            file_data["chat_id"], ids=file_data["message_id"]
        )
        if not probe_msg or not probe_msg.media:
            raise HTTPException(status_code=404, detail="Media no longer available")
        track_info = await probe_tracks(
            client_for_probe, probe_msg, file_data["file_size"],
            file_data.get("filename", "media")
        )
        if track_info.get("error"):
            raise HTTPException(status_code=422, detail=f"Cannot inspect media tracks: {track_info['error']}")
        await files_col.update_one(
            {"short_code": short_code}, {"$set": {"tracks_info": track_info}}
        )

    audio_tracks = track_info.get("audio_tracks", [])
    if audio == -1:
        if not track_info.get("video_tracks"):
            raise HTTPException(status_code=422, detail="This file has no video stream")
    elif audio >= len(audio_tracks):
        raise HTTPException(status_code=400, detail="Requested audio track does not exist")

    # FFmpeg needs sequential input; use one Telegram session for this stream.
    client = session_manager.get_client()
    
    try:
        msg = await client.get_messages(file_data['chat_id'], ids=file_data['message_id'])
        if not msg or not msg.media:
            raise HTTPException(status_code=404, detail="Media no longer available")
        
        file = msg.media
        if hasattr(file, 'document'):
            file = file.document
        elif hasattr(file, 'photo'):
            file = file.photo
        
        return await get_remux_response(
            client=client,
            file=file,
            file_size=file_data['file_size'],
            filename=file_data['filename'],
            audio_track=audio
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Remux error: {e}")
        raise HTTPException(status_code=500, detail="Error starting remux stream")


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    # use loop="asyncio" to prevent Uvicorn from forcing SelectorEventLoop on Windows,
    # which causes NotImplementedError with asyncio.create_subprocess_exec
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True, loop="asyncio")
