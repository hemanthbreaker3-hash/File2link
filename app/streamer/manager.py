from __future__ import annotations

import logging
from typing import List, Optional

from wzgram import Client
from wzgram.types import BotCommand

from app.database.connection import settings

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self):
        self.clients: List[Client] = []
        self.bot_client: Optional[Client] = None
        self._index = 0
        self._started = False

    def prepare_bot(self):
        """Create bot Client so handlers can be registered before start()."""
        if self.bot_client is not None:
            return
        self.bot_client = Client(
            name="hitler_bot",
            api_id=settings.API_ID,
            api_hash=settings.API_HASH,
            bot_token=settings.BOT_TOKEN,
            in_memory=True,
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

        session_strings = [s.strip() for s in settings.SESSIONS.split(",") if s.strip()]
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
        logger.info("Streaming clients ready: %s", len(self.clients))

    async def stop(self):
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
        if not self.clients:
            return self.bot_client
        client = self.clients[self._index]
        self._index = (self._index + 1) % len(self.clients)
        return client

    def get_all_clients(self) -> List[Client]:
        return self.clients if self.clients else ([self.bot_client] if self.bot_client else [])


session_manager = SessionManager()
