from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import List, Optional

from wzgram import Client
from wzgram.types import BotCommand

try:
    from wzgram.errors import FloodWait
except ImportError:  # pragma: no cover - fallback if wzgram ever renames it
    FloodWait = None  # type: ignore

from app.database.connection import settings

logger = logging.getLogger(__name__)

# Bot/user auth is persisted to disk (instead of in_memory=True) so a
# container restart reuses the existing, already-authorized session instead
# of calling Telegram's auth.ImportBotAuthorization again. Repeating that
# call on every crash/restart is what escalates a FloodWait, and because the
# wait is tied to the api_id/IP (not the specific bot token), swapping to a
# "new" BOT_TOKEN does not clear it - only persisting the session and letting
# the real wait elapse does.
SESSION_DIR = os.environ.get("SESSION_DIR", "/app/sessions")


class SessionManager:
    def __init__(self):
        self.clients: List[Client] = []
        self.bot_client: Optional[Client] = None
        self._index = 0
        self._started = False
        # Surfaced on /health so the process can report "still waiting out a
        # Telegram FloodWait" instead of looking like a hard crash.
        self.telegram_ready = False
        self.last_error: Optional[str] = None
        self.flood_wait_until: Optional[float] = None
        self._retry_task: Optional[asyncio.Task] = None

    def prepare_bot(self):
        """Create bot Client so handlers can be registered before start()."""
        if self.bot_client is not None:
            return
        os.makedirs(SESSION_DIR, exist_ok=True)
        self.bot_client = Client(
            name="hitler_bot",
            api_id=settings.API_ID,
            api_hash=settings.API_HASH,
            bot_token=settings.BOT_TOKEN,
            workdir=SESSION_DIR,
            in_memory=False,
            workers=16,
            max_concurrent_transmissions=16,
        )

    async def start(self):
        if self._started and self.bot_client is not None and self.bot_client.is_connected:
            return
        self.clients.clear()
        self._index = 0

        if self.bot_client is None:
            self.prepare_bot()
        await self.bot_client.start()
        logger.info("HITLER bot client started (wzgram)")
        await self.register_bot_commands()

        # Accept comma-, semicolon-, or newline-separated exported sessions.
        # Strip optional surrounding quotes and ignore blank/comment lines.
        raw_sessions = str(settings.SESSIONS or "").strip()
        session_strings = [
            item.strip().strip("\'\"")
            for item in re.split(r"[,;\r\n]+", raw_sessions)
            if item.strip() and not item.strip().startswith("#")
        ]
        # Avoid starting the same exported session more than once.
        session_strings = list(dict.fromkeys(session_strings))
        if not session_strings:
            logger.warning(
                "No user sessions found! Using bot for streaming (slower). "
                "Add SESSIONS=string1,string2 for multi-connection speed."
            )
            self.clients.append(self.bot_client)
        else:
            for i, session_str in enumerate(session_strings):
                try:
                    client = Client(
                        name=f"hitler_user_{i}",
                        api_id=settings.API_ID,
                        api_hash=settings.API_HASH,
                        session_string=session_str,
                        in_memory=True,
                        workers=16,
                        max_concurrent_transmissions=16,
                        no_updates=True,
                    )
                    await client.start()
                    self.clients.append(client)
                    logger.info("User session %s started (wzgram)", i + 1)
                except Exception as e:
                    logger.error("Session %s failed: %s", i + 1, e)
            if not self.clients:
                self.clients.append(self.bot_client)

        self._started = True
        self.telegram_ready = True
        self.last_error = None
        self.flood_wait_until = None
        logger.info("Streaming clients ready: %s", len(self.clients))

    async def start_with_retry(self) -> None:
        """Start Telegram clients in the background, surviving FloodWait.

        Runs as a background task so a bad token or an in-progress FloodWait
        never crashes the FastAPI process: the HTTP server (and /health)
        comes up immediately, and this loop keeps retrying with the correct
        backoff until Telegram lets the session through. This is what stops
        the restart-crash-reauth loop that escalates FloodWait in the first
        place.
        """
        delay = 5.0
        while True:
            try:
                await self.start()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                wait = None
                if FloodWait is not None and isinstance(e, FloodWait):
                    wait = getattr(e, "value", None) or getattr(e, "x", None)
                else:
                    # wzgram re-exports pyrogram's error hierarchy; fall back
                    # to duck-typing in case the import path above ever
                    # breaks, matching the pattern already used in engine.py.
                    if type(e).__name__ == "FloodWait":
                        wait = getattr(e, "value", None) or getattr(e, "x", None)
                if wait:
                    wait = float(wait) + 5  # small safety buffer
                    self.flood_wait_until = time.time() + wait
                    self.last_error = (
                        f"Telegram FloodWait: waiting {int(wait)}s before retrying "
                        "auth (do not change BOT_TOKEN or restart the container - "
                        "that resets this timer and makes it worse)."
                    )
                    logger.warning(self.last_error)
                    await asyncio.sleep(wait)
                    continue
                # Non-flood error (bad token, network blip, etc.): log and
                # retry with a capped exponential backoff instead of raising
                # and letting the container crash-loop.
                self.last_error = f"{type(e).__name__}: {e}"
                logger.error("Telegram client start failed, retrying in %.0fs: %s", delay, e)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)

    async def stop(self):
        if self._retry_task is not None:
            self._retry_task.cancel()
            try:
                await self._retry_task
            except (asyncio.CancelledError, Exception):
                pass
            self._retry_task = None
        unique = {id(c): c for c in [*self.clients, self.bot_client] if c is not None}
        for client in unique.values():
            try:
                if client.is_connected:
                    await client.stop()
            except Exception:
                logger.exception("Error while stopping wzgram client")
        self.clients.clear()
        self.bot_client = None
        self._started = False
        self.telegram_ready = False


    async def register_bot_commands(self) -> None:
        """Register all bot slash commands with Telegram on startup."""
        if not self.bot_client:
            return
        commands = [
            BotCommand("start", "Start / welcome & how to use"),
            BotCommand("ss", "Screenshots — reply to media"),
            BotCommand("sv", "Sample video — reply to media"),
            BotCommand("mi", "MediaInfo — reply to media or pass a download URL"),
            BotCommand("stats", "Bot statistics (admin)"),
            BotCommand("broadcast", "Broadcast message (admin)"),
            BotCommand("ban", "Ban user (admin)"),
            BotCommand("unban", "Unban user (admin)"),
            BotCommand("autodel", "Auto-delete timer (admin)"),
            BotCommand("baseurl", "Set public base URL (admin)"),
        ]
        try:
            await self.bot_client.set_bot_commands(commands)
            logger.info("Registered %s bot commands with Telegram", len(commands))
        except Exception as e:
            logger.warning("Failed to register bot commands: %s", e)

    def get_client(self) -> Optional[Client]:
        clients = self.get_all_clients()
        if not clients:
            return None
        client = clients[self._index % len(clients)]
        self._index = (self._index + 1) % len(clients)
        return client

    def get_all_clients(self) -> List[Client]:
        # Keep the bot client available as a fallback: user sessions may not
        # have joined the source/storage channel even when they are valid.
        result: List[Client] = []
        for client in [*self.clients, self.bot_client]:
            if client is not None and client.is_connected and all(client is not x for x in result):
                result.append(client)
        return result


session_manager = SessionManager()
