from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from app.database.connection import settings, files_col, client as mongo_client, create_indexes, load_custom_settings
from app.streamer.manager import session_manager
from app.streamer.engine import get_streaming_response, get_remux_response
from app.streamer.probe import probe_tracks
from app.bot.main import register_handlers
from app.admin.routes import router as admin_router
from app.cms import router as cms_router, get_settings, seed_categories
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import logging
import os
import sys
import datetime
from pathlib import Path

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
    try:
        await mongo_client.admin.command("ping")
        await create_indexes()
        await load_custom_settings()
        await seed_categories()
        session_manager.prepare_bot()
        register_handlers(session_manager.bot_client)
        await session_manager.start()
        if getattr(settings, "BASE_URL", None):
            os.environ["BASE_URL"] = str(settings.BASE_URL).rstrip("/")
        logger.info("HITLER service started (wzgram)")
        yield
    finally:
        await session_manager.stop()
        mongo_client.close()
        logger.info("HITLER service stopped")

app = FastAPI(title="HITLER Media Link Service", lifespan=lifespan)

# Include Routers
app.include_router(admin_router)
app.include_router(cms_router)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enable Compression
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Mount Static Files & Templates
APP_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

@app.get("/health")
async def health():
    return {"status": "ok", "service": "HITLER"}

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request})

@app.get("/watch/{short_code}")
async def watch_page(request: Request, short_code: str):
    file_data = await files_col.find_one({"short_code": short_code})
    if not file_data or (file_data.get("expiry_time") and file_data["expiry_time"] < datetime.datetime.utcnow()):
        raise HTTPException(status_code=404, detail="Link not found or expired")
    
    return templates.TemplateResponse(
        request=request,
        name="watch.html",
        context={
            "request": request,
            "file": file_data,
            "base_url": settings.BASE_URL.rstrip("/"),
            "player_settings": await get_settings(),
        },
    )

@app.get("/dl/{short_code}")
@app.get("/stream/{short_code}")
async def stream_file(request: Request, short_code: str):
    runtime = await get_settings()
    if request.url.path.startswith("/dl/") and not runtime["downloads_enabled"]:
        raise HTTPException(status_code=403, detail="Downloads are currently disabled")
    if request.url.path.startswith("/stream/") and not runtime["streaming_enabled"]:
        raise HTTPException(status_code=403, detail="Streaming is currently disabled")
    file_data = await files_col.find_one({"short_code": short_code})
    if not file_data:
        raise HTTPException(status_code=404, detail="File not found")
    if file_data.get("expiry_time") and file_data["expiry_time"] < datetime.datetime.utcnow():
        await files_col.delete_one({"short_code": short_code})
        raise HTTPException(status_code=404, detail="Link expired")

    await files_col.update_one({"short_code": short_code}, {"$inc": {"access_count": 1}})

    all_clients = session_manager.get_all_clients()
    if not all_clients:
        raise HTTPException(status_code=503, detail="Telegram sessions are unavailable")

    return await get_streaming_response(
        clients=all_clients,
        chat_id=file_data["chat_id"],
        message_id=file_data["message_id"],
        file_size=file_data["file_size"],
        filename=file_data["filename"],
        mime_type=file_data["mime_type"],
        request=request,
        chunk_size=settings.STREAM_CHUNK_SIZE,
    )



# ─── Audio Track Discovery API ────────────────────────────────────────────────

@app.get("/api/tracks/{short_code}")
async def get_tracks(short_code: str):
    """Return available audio/video/subtitle tracks for a media file."""
    file_data = await files_col.find_one({"short_code": short_code})
    if not file_data or (file_data.get("expiry_time") and file_data["expiry_time"] < datetime.datetime.utcnow()):
        raise HTTPException(status_code=404, detail="File not found or expired")
    
    # Check if tracks are already cached in the database (and not an error result)
    cached = file_data.get('tracks_info')
    if cached and not cached.get('error'):
        return JSONResponse(cached)
    
    # Need to probe the file
    client = session_manager.get_client()
    
    try:
        msg = await client.get_messages(file_data['chat_id'], file_data['message_id'])
        if isinstance(msg, list):
            msg = msg[0] if msg else None
        if not msg:
            raise HTTPException(status_code=404, detail="Media no longer available")

        if msg.photo and not (msg.document or msg.video or msg.audio):
            return JSONResponse({
                "video_tracks": [],
                "audio_tracks": [],
                "subtitle_tracks": [],
                "has_multiple_audio": False
            })

        tracks_info = await probe_tracks(client, msg, file_data['file_size'])
        
        # Only cache if no error
        if not tracks_info.get('error'):
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
async def remux_file(request: Request, short_code: str, audio: int = 0, aac: int = 1):
    """Browser-friendly fragmented MP4; AAC by default for universal audio."""
    if not (await get_settings())["streaming_enabled"]:
        raise HTTPException(status_code=403, detail="Streaming is currently disabled")
    file_data = await files_col.find_one({"short_code": short_code})
    if not file_data or (file_data.get("expiry_time") and file_data["expiry_time"] < datetime.datetime.utcnow()):
        raise HTTPException(status_code=404, detail="File not found or expired")
    if audio < 0 or audio > 32:
        raise HTTPException(status_code=400, detail="Invalid audio track")

    clients = session_manager.get_all_clients()
    if not clients:
        raise HTTPException(status_code=503, detail="Telegram sessions are unavailable")
    try:
        return await get_remux_response(
            clients=clients,
            chat_id=file_data["chat_id"],
            message_id=file_data["message_id"],
            file_size=file_data["file_size"],
            filename=file_data["filename"],
            audio_track=audio,
            force_aac=bool(aac),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Remux error: %s", e)
        raise HTTPException(status_code=500, detail="Error starting remux stream")




@app.get("/_tgstream/{chat_id}/{message_id}")
async def internal_tg_stream(request: Request, chat_id: int, message_id: int):
    """Local-only style stream of a Telegram message media for mediainfo/ffmpeg (Range supported)."""
    clients = session_manager.get_all_clients()
    if not clients:
        raise HTTPException(status_code=503, detail="No Telegram sessions")
    client = clients[0]
    try:
        msg = await client.get_messages(chat_id, message_id)
        if isinstance(msg, list):
            msg = msg[0] if msg else None
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")
        media = msg.video or msg.document or msg.audio or msg.animation or msg.voice or msg.video_note
        if not media and not msg.photo:
            raise HTTPException(status_code=404, detail="No media")
        if msg.photo and not media:
            # photos are small; still stream
            file_size = msg.photo.file_size or 0
            filename = f"photo_{message_id}.jpg"
            mime = "image/jpeg"
        else:
            file_size = getattr(media, "file_size", None) or 0
            filename = getattr(media, "file_name", None) or f"file_{message_id}"
            mime = getattr(media, "mime_type", None) or "application/octet-stream"
        if file_size <= 0:
            raise HTTPException(status_code=422, detail="Unknown file size")
        return await get_streaming_response(
            clients=clients,
            chat_id=chat_id,
            message_id=message_id,
            file_size=file_size,
            filename=filename,
            mime_type=mime,
            request=request,
            chunk_size=settings.STREAM_CHUNK_SIZE,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("internal_tg_stream")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    # use loop="asyncio" to prevent Uvicorn from forcing SelectorEventLoop on Windows,
    # which causes NotImplementedError with asyncio.create_subprocess_exec
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False, loop="asyncio")
