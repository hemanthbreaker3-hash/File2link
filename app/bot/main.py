from __future__ import annotations

import datetime
import html
import logging
import os
import mimetypes
import httpx
import secrets
import re

from wzgram import filters
from wzgram.enums import ParseMode
from wzgram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from app.database.connection import files_col, users_col, settings_col, settings, db
from app.models.schemas import FileMetadata, User
from app.utils.helpers import generate_short_code, format_bytes, normalize_public_base_url
from app.utils.fsub import is_user_fsubbed
from app.utils.rate_limit import check_rate_limit
from app.utils.media_tools import (
    cleanup_dir,
    download_message_media,
    generate_sample_video,
    generate_screenshots,
    make_work_dir,
    local_tg_stream_url,
)
from app.utils.mediainfo_fmt import extract_mediainfo, format_output
import io

logger = logging.getLogger(__name__)


def get_ist_greeting():
    ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    now = datetime.datetime.now(ist_tz)
    hour = now.hour
    if 5 <= hour < 12:
        return "Good Morning 🌅"
    if 12 <= hour < 17:
        return "Good Afternoon ☀️"
    return "Good Evening 🌙"


def _kb(rows):
    return InlineKeyboardMarkup(rows)


async def is_access_allowed(user_id: int) -> bool:
    config = await db.site_settings.find_one({"_id": "runtime"}) or {}
    mode = config.get("access_mode", "all")
    if mode == "owners":
        return user_id == settings.OWNER_ID
    if mode == "admins":
        return user_id == settings.OWNER_ID or user_id in settings.admin_list
    return True


def register_handlers(bot):
    """Register wzgram (Pyrogram-compatible) handlers on the bot client."""

    @bot.on_message(filters.command("start") & filters.private)
    async def start_handler(client, message: Message):
        user = message.from_user
        if not user or user.is_bot:
            return
        user_id = user.id
        access_cfg = await db.site_settings.find_one({"_id": "runtime"}) or {}
        access_mode = access_cfg.get("access_mode", "all")
        if access_mode == "owners" and user_id != settings.OWNER_ID:
            return await message.reply("🔒 This bot is currently restricted to its owner.")
        if access_mode == "admins" and user_id not in settings.admin_list and user_id != settings.OWNER_ID:
            return await message.reply("🔒 This bot is currently restricted to administrators.")

        user_data = await users_col.find_one({"user_id": user_id})
        if user_data and user_data.get("is_banned"):
            return await message.reply(
                "🚫 **Access Suspended**\n\n"
                "> Your account has been banned from using this service.\n"
                "> Contact support if you believe this is a mistake."
            )

        if not user_data:
            new_user = User(
                user_id=user_id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
            )
            await users_col.insert_one(new_user.dict())
            if settings.CHANNEL_ID:
                try:
                    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                    await client.send_message(
                        settings.CHANNEL_ID,
                        f"👤 **#NewUser Alert**\n\n"
                        f"> 🆔 **ID:** `{user_id}`\n"
                        f"> 📛 **Name:** {name}\n"
                        f"> 🌐 **Username:** @{user.username or 'N/A'}",
                    )
                except Exception as e:
                    logger.error("Error sending new user log: %s", e)

        if not await is_user_fsubbed(client, user_id):
            return await message.reply(
                "🔒 **Access Restricted**\n\n"
                "> Join our official updates channel, then send /start again.",
                reply_markup=_kb([[InlineKeyboardButton("📢 Join Updates Channel", url=settings.UPDATE_CHANNEL_URL)]]),
            )

        first_name = user.first_name or "User"
        mention = f"[{first_name}](tg://user?id={user_id})"
        greeting = get_ist_greeting()
        start_text = (
            f"👑 **{greeting}, {mention}!**\n\n"
            f"> ⚡ **Welcome to Premium Direct Link & Streaming Service**\n"
            f">\n"
            f"> 🚀 *Upload or forward any Telegram file to generate ultra-fast direct download & streaming links instantly.*\n"
            f">\n"
            f"> 📸 `/ss` reply to media → screenshots\n"
            f"> 🎬 `/sv` reply to media → sample video\n"
            f"> 📊 `/mi` reply to media → MediaInfo"
        )
        await message.reply(
            start_text,
            reply_markup=_kb([[InlineKeyboardButton("📢 Join Updates Channel", url=settings.UPDATE_CHANNEL_URL)]]),
        )

    @bot.on_message(filters.media & ~filters.channel)
    async def media_handler(client, message: Message):
        user = message.from_user
        if not user or user.is_bot:
            return

        access_cfg = await db.site_settings.find_one({"_id": "runtime"}) or {}
        access_mode = access_cfg.get("access_mode", "all")
        if access_mode == "owners" and user.id != settings.OWNER_ID:
            return await message.reply("🔒 This bot is currently restricted to its owner.")
        if access_mode == "admins" and user.id not in settings.admin_list and user.id != settings.OWNER_ID:
            return await message.reply("🔒 This bot is currently restricted to administrators.")
        runtime_cfg = await db.site_settings.find_one({"_id": "runtime"}) or {}
        downloads_enabled = runtime_cfg.get("downloads_enabled", True)
        streaming_enabled = runtime_cfg.get("streaming_enabled", True)
        if not downloads_enabled and not streaming_enabled:
            return await message.reply("🚫 **Service Disabled**\n\n> Downloads and streaming are currently disabled by the administrator.")
        user_data = await users_col.find_one({"user_id": user.id})
        if user_data and user_data.get("is_banned"):
            return await message.reply(
                "🚫 **Access Suspended**\n\n"
                "> Your account has been banned from using this service."
            )

        if not await check_rate_limit(user.id):
            return await message.reply(
                "⏳ **Rate Limit Exceeded**\n\n"
                "> Please wait a few seconds before uploading more files."
            )

        if not await is_user_fsubbed(client, user.id):
            return await message.reply(
                "🔒 **Access Restricted**\n\n"
                "> Join our official updates channel, then upload your file again.",
                reply_markup=_kb([[InlineKeyboardButton("📢 Join Updates Channel", url=settings.UPDATE_CHANNEL_URL)]]),
            )

        detecting_msg = await message.reply("⚡ **Analyzing media & preparing instant links...**")

        file_name = "file"
        file_size = 0
        mime_type = "application/octet-stream"
        file_id = ""

        if message.document:
            doc = message.document
            file_name = doc.file_name or "file"
            file_size = doc.file_size or 0
            mime_type = doc.mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            file_id = doc.file_id
        elif message.video:
            vid = message.video
            file_name = getattr(vid, "file_name", None) or f"video_{vid.file_unique_id}.mp4"
            file_size = vid.file_size or 0
            mime_type = vid.mime_type or "video/mp4"
            file_id = vid.file_id
        elif message.audio:
            aud = message.audio
            file_name = aud.file_name or f"audio_{aud.file_unique_id}.mp3"
            file_size = aud.file_size or 0
            mime_type = aud.mime_type or "audio/mpeg"
            file_id = aud.file_id
        elif message.animation:
            ani = message.animation
            file_name = getattr(ani, "file_name", None) or f"animation_{ani.file_unique_id}.mp4"
            file_size = ani.file_size or 0
            mime_type = ani.mime_type or "video/mp4"
            file_id = ani.file_id
        elif message.photo:
            photo = message.photo
            file_name = f"photo_{photo.file_unique_id}.jpg"
            file_size = photo.file_size or 0
            mime_type = "image/jpeg"
            file_id = photo.file_id
        elif message.voice:
            voice = message.voice
            file_name = f"voice_{voice.file_unique_id}.ogg"
            file_size = voice.file_size or 0
            mime_type = voice.mime_type or "audio/ogg"
            file_id = voice.file_id
        elif message.video_note:
            vn = message.video_note
            file_name = f"videonote_{vn.file_unique_id}.mp4"
            file_size = vn.file_size or 0
            mime_type = "video/mp4"
            file_id = vn.file_id
        else:
            return await detecting_msg.edit_text("❌ **Error:** Unsupported media type.")

        if not file_id:
            return await detecting_msg.edit_text("❌ **Error:** Could not extract media details.")

        short_code = generate_short_code()
        file_meta = FileMetadata(
            file_id=file_id,
            file_unique_id=str(message.id),
            filename=file_name,
            mime_type=mime_type,
            file_size=file_size,
            uploader_id=user.id,
            short_code=short_code,
            chat_id=message.chat.id,
            message_id=message.id,
            expiry_time=(
                datetime.datetime.utcnow() + datetime.timedelta(hours=settings.DEFAULT_EXPIRY)
                if settings.DEFAULT_EXPIRY > 0
                else None
            ),
        )
        await files_col.insert_one(file_meta.dict())

        try:
            base_url = normalize_public_base_url(settings.BASE_URL)
            links_ready = True
        except ValueError as exc:
            logger.warning("Public BASE_URL not configured: %s", exc)
            base_url = None
            links_ready = False

        formatted_size = format_bytes(file_size) if file_size else "N/A"
        safe_name = html.escape(file_name)
        safe_mime = html.escape(mime_type)

        if links_ready:
            download_url = f"{base_url}/dl/{short_code}" if downloads_enabled else None
            stream_url = f"{base_url}/watch/{short_code}" if streaming_enabled else None
            active_links = []
            button_rows = []
            if downloads_enabled:
                active_links.append(f"⚡ <a href=\"{html.escape(download_url, quote=True)}\">Direct Download</a>")
                button_rows.append(InlineKeyboardButton("⚡ Direct Download", url=download_url))
            if streaming_enabled:
                active_links.append(f"🍿 <a href=\"{html.escape(stream_url, quote=True)}\">Watch Online</a>")
                button_rows.append(InlineKeyboardButton("🍿 Watch Online", url=stream_url))
            caption = (
                "💎 <b>Media Links Generated</b>\n\n"
                f"📂 <b>File Name:</b> <code>{safe_name}</code>\n"
                f"📦 <b>File Size:</b> <code>{html.escape(formatted_size)}</code>\n"
                f"⚙️ <b>MIME Type:</b> <code>{safe_mime}</code>\n\n"
                "🚀 <b>Available Links</b>\n" + "\n".join(active_links) + "\n\n"
                f"📢 <a href=\"{html.escape(settings.UPDATE_CHANNEL_URL, quote=True)}\">Updates Channel</a>"
            )
            user_buttons = _kb([button_rows, [InlineKeyboardButton("📢 Updates Channel", url=settings.UPDATE_CHANNEL_URL)], [InlineKeyboardButton("🗑️ Delete Link", callback_data=f"del_{short_code}")]])
            channel_buttons = _kb([button_rows, [InlineKeyboardButton("📢 Updates Channel", url=settings.UPDATE_CHANNEL_URL)]])
        else:
            caption = (
                "💎 <b>File Received Successfully</b>\n\n"
                f"📂 <b>File Name:</b> <code>{safe_name}</code>\n"
                f"📦 <b>File Size:</b> <code>{html.escape(formatted_size)}</code>\n"
                f"⚙️ <b>MIME Type:</b> <code>{safe_mime}</code>\n\n"
                "⚠️ <b>Public links are not active yet.</b>\n"
                "The administrator must configure the public Base URL.\n\n"
                f"📢 <a href=\"{html.escape(settings.UPDATE_CHANNEL_URL, quote=True)}\">Updates Channel</a>"
            )
            user_buttons = _kb([[InlineKeyboardButton("📢 Updates Channel", url=settings.UPDATE_CHANNEL_URL)], [InlineKeyboardButton("🗑️ Delete Link", callback_data=f"del_{short_code}")]])
            channel_buttons = _kb([[InlineKeyboardButton("📢 Updates Channel", url=settings.UPDATE_CHANNEL_URL)]])

        try:
            await detecting_msg.delete()
        except Exception:
            pass

        try:
            await message.copy(
                chat_id=message.chat.id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=user_buttons,
            )
        except Exception as e:
            logger.exception("Error sending file with buttons: %s", e)
            try:
                await message.reply(caption, parse_mode=ParseMode.HTML, reply_markup=user_buttons)
            except Exception as e2:
                logger.exception("Fallback reply failed: %s", e2)

        if settings.CHANNEL_ID and settings.CHANNEL_ID != message.chat.id:
            try:
                await message.copy(
                    chat_id=settings.CHANNEL_ID,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=channel_buttons,
                )
            except Exception as e:
                logger.exception("Error mirroring to log channel: %s", e)

    @bot.on_callback_query(filters.regex(r"^del_"))
    async def delete_callback(client, callback: CallbackQuery):
        user = callback.from_user
        if not user or user.is_bot:
            return
        if not await is_user_fsubbed(client, user.id):
            return await callback.answer("🔒 Please join the update channel first!", show_alert=True)

        short_code = callback.data.split("_", 1)[1]
        file_data = await files_col.find_one({"short_code": short_code})
        if file_data and file_data["uploader_id"] == user.id:
            await files_col.delete_one({"short_code": short_code})
            await callback.message.edit_text("🗑️ **Link deleted successfully!**")
        else:
            await callback.answer("🚫 You are not authorized to delete this link.", show_alert=True)

    # Admin-only website CMS commands. The secure web dashboard performs the full edits.
    @bot.on_message(filters.command("search"))
    async def cms_search_handler(client, message: Message):
        user = message.from_user
        if not user or (user.id not in settings.admin_list and user.id != settings.OWNER_ID):
            return await message.reply("🚫 Admins only.")
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            return await message.reply("Usage: /search movie or series name")
        api_key = settings.TMDB_API_KEY
        if not api_key:
            return await message.reply("TMDB_API_KEY is missing in the backend environment.")
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                response = await http.get("https://api.themoviedb.org/3/search/multi", params={"api_key": api_key, "query": args[1], "include_adult": "false"})
                response.raise_for_status()
                results = response.json().get("results", [])[:20]
            rows = []
            for item in results:
                media_type = item.get("media_type")
                if media_type not in ("movie", "tv"):
                    continue
                title = item.get("title") or item.get("name") or "Untitled"
                pending_id = secrets.token_hex(5)
                await db.cms_pending.insert_one({"_id": pending_id, "tmdb_id": item["id"], "media_type": media_type, "title": title, "overview": item.get("overview", ""), "poster_path": item.get("poster_path") or "", "release_date": item.get("release_date") or item.get("first_air_date", ""), "rating": item.get("vote_average")})
                rows.append([InlineKeyboardButton(f"{title[:45]} · {media_type}", callback_data=f"cmscreate:{pending_id}")])
            if not rows:
                return await message.reply("No matching movie or series found.")
            return await message.reply("Select a TMDB result to publish its page:", reply_markup=_kb(rows))
        except Exception as exc:
            logger.exception("TMDB search failed")
            return await message.reply(f"TMDB search failed: {type(exc).__name__}")

    @bot.on_callback_query(filters.regex(r"^cmscreate:"))
    async def cms_create_callback(client, callback: CallbackQuery):
        user = callback.from_user
        if not user or (user.id not in settings.admin_list and user.id != settings.OWNER_ID):
            return await callback.answer("Admins only", show_alert=True)
        pending_id = callback.data.split(":", 1)[1]
        pending = await db.cms_pending.find_one({"_id": pending_id})
        if not pending:
            return await callback.answer("Search result expired", show_alert=True)
        from app.database.connection import settings as app_settings
        slug = re.sub(r"[^a-z0-9]+", "-", pending["title"].lower()).strip("-") or secrets.token_hex(5)
        base, n = slug, 2
        while await db.catalog_pages.find_one({"slug": slug}):
            slug = f"{base}-{n}"; n += 1
        category = "web-series" if pending["media_type"] == "tv" else "movies"
        extra = {"genres": [], "imdb_id": "", "runtime": None, "tagline": ""}
        if settings.TMDB_API_KEY:
            try:
                endpoint = "tv" if pending["media_type"] == "tv" else "movie"
                async with httpx.AsyncClient(timeout=15) as http:
                    detail = await http.get(f"https://api.themoviedb.org/3/{endpoint}/{pending['tmdb_id']}", params={"api_key": settings.TMDB_API_KEY, "append_to_response": "external_ids"})
                    detail.raise_for_status(); metadata = detail.json()
                extra = {"genres": [g.get("name", "") for g in metadata.get("genres", [])], "imdb_id": (metadata.get("external_ids") or {}).get("imdb_id") or metadata.get("imdb_id", ""), "runtime": metadata.get("runtime") or (metadata.get("episode_run_time") or [None])[0], "tagline": metadata.get("tagline", "")}
            except Exception:
                logger.exception("TMDB details lookup failed; publishing search metadata only")
        await db.catalog_pages.insert_one({"slug": slug, "title": pending["title"], "overview": pending.get("overview", ""), "poster_path": pending.get("poster_path", ""), "release_date": pending.get("release_date", ""), "rating": pending.get("rating"), "media_type": pending["media_type"], "category_slugs": [category], "groups": [], "published": True, "created_at": datetime.datetime.utcnow(), **extra})
        await db.cms_pending.delete_one({"_id": pending_id})
        await callback.message.edit_text(f"✅ Page created: {app_settings.BASE_URL.rstrip('/')}/title/{slug}\nUse /edit while replying with this link, or open /panel to manage page groups and buttons.")
        await callback.answer("Published")

    @bot.on_callback_query(filters.regex(r"^cmscat:"))
    async def cms_category_callback(client, callback: CallbackQuery):
        user = callback.from_user
        if not user or (user.id not in settings.admin_list and user.id != settings.OWNER_ID):
            return await callback.answer("Admins only", show_alert=True)
        pending_id = callback.data.split(":", 1)[1]
        pending = await db.cms_pending.find_one({"_id": pending_id, "action": "category"})
        if not pending:
            return await callback.answer("Selection expired", show_alert=True)
        await db.catalog_pages.update_one({"slug": pending["page_slug"]}, {"$addToSet": {"category_slugs": pending["category_slug"]}})
        await db.cms_pending.delete_one({"_id": pending_id})
        await callback.message.edit_text("✅ Page category updated. The website catalog is updated automatically.")
        await callback.answer("Category assigned")

    @bot.on_message(filters.command(["categories", "edit", "panel"]))
    async def cms_admin_commands(client, message: Message):
        user = message.from_user
        if not user or (user.id not in settings.admin_list and user.id != settings.OWNER_ID):
            return await message.reply("🚫 Admins only.")
        base = settings.BASE_URL.rstrip("/")
        cmd = (message.text or "").split()[0].split("@")[0]
        if cmd == "/categories":
            reply = getattr(message, "reply_to_message", None)
            if reply:
                source_text = (getattr(reply, "text", None) or getattr(reply, "caption", None) or "")
                match = re.search(r"/title/([a-z0-9-]+)", source_text, re.I)
                if not match:
                    return await message.reply("Reply to a published /title/ page link to assign its category.")
                page_slug = match.group(1)
                if not await db.catalog_pages.find_one({"slug": page_slug}):
                    return await message.reply("That catalog page was not found.")
                available = await db.catalog_categories.find({}).to_list(None)
                rows = []
                for cat in available:
                    pending_id = secrets.token_hex(5)
                    await db.cms_pending.insert_one({"_id": pending_id, "action": "category", "page_slug": page_slug, "category_slug": cat["slug"]})
                    rows.append([InlineKeyboardButton(cat["name"], callback_data=f"cmscat:{pending_id}")])
                return await message.reply("Choose a category for this page:", reply_markup=_kb(rows))
            cats = await db.catalog_categories.find({}, {"_id": 0}).to_list(None)
            names = "\n".join(f"• {c['name']}" for c in cats) or "No categories created yet."
            return await message.reply(f"🗂️ **Categories**\n\n{names}\n\nCreate or manage unlimited categories in the dashboard.", reply_markup=_kb([[InlineKeyboardButton("➕ Create / Manage Categories", url=f"{base}/panel")]]))
        if cmd == "/edit":
            return await message.reply("✏️ Page editor\n\nEdit page metadata, poster, categories, unlimited groups and buttons in the dashboard.", reply_markup=_kb([[InlineKeyboardButton("✏️ Open Page Editor", url=f"{base}/panel")]]))
        return await message.reply("🔐 Admin dashboard", reply_markup=_kb([[InlineKeyboardButton("Open HITLER YT Panel", url=f"{base}/panel")]]))

    @bot.on_message(filters.command("baseurl"))
    async def baseurl_handler(client, message: Message):
        user = message.from_user
        if not user or user.is_bot:
            return
        if user.id not in settings.admin_list and user.id != settings.OWNER_ID:
            return await message.reply(
                "🚫 **Unauthorized Access**\n\n"
                "> Only authorized bot administrators can update the Base URL."
            )
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            return await message.reply(
                "🌐 **Base URL Management**\n\n"
                f"> 🔗 **Current Base URL:** `{settings.BASE_URL}`\n"
                f"> 📢 **Updates Channel:** `{settings.UPDATE_CHANNEL_URL}`\n\n"
                "⚠️ **Usage:** `/baseurl https://your-public-domain.com`"
            )
        new_url = args[1].strip()
        if not new_url.startswith(("http://", "https://")):
            new_url = f"https://{new_url}"
        try:
            new_url = normalize_public_base_url(new_url)
        except ValueError as exc:
            return await message.reply(
                "❌ **Invalid public Base URL**\n\n"
                f"> {html.escape(str(exc))}\n\n"
                "Use your public URL, for example: `/baseurl https://name.trycloudflare.com`"
            )
        try:
            await settings_col.update_one(
                {"key": "base_url"},
                {"$set": {"value": new_url, "updated_by": user.id, "updated_at": datetime.datetime.utcnow()}},
                upsert=True,
            )
            settings.BASE_URL = new_url
            await message.reply(
                "🌐 **Base URL Updated Successfully!**\n\n"
                f"> 🔗 **New Base URL:** `{new_url}`\n"
                f"> 👤 **Updated By:** `{user.id}`\n\n"
                "✨ *All newly generated stream & download links will now use this updated Base URL.*"
            )
        except Exception as e:
            logger.error("Error updating baseurl: %s", e)
            await message.reply(f"❌ **Error updating Base URL:** `{e}`")

    @bot.on_message(filters.command("stats"))
    async def stats_handler(client, message: Message):
        user = message.from_user
        if not user or user.is_bot:
            return
        if user.id not in settings.admin_list and user.id != settings.OWNER_ID:
            return
        total_files = await files_col.count_documents({})
        total_users = await users_col.count_documents({})
        await message.reply(
            f"📊 **System Statistics**\n\n"
            f"> 👥 **Total Users:** `{total_users}`\n"
            f"> 📁 **Total Files Generated:** `{total_files}`\n"
            f"> ⚡ **System Status:** `Online & High-Speed`\n"
            f"> 🔧 **Engine:** `wzgram[fast]`"
        )

    @bot.on_message(filters.command("broadcast"))
    async def broadcast_handler(client, message: Message):
        user = message.from_user
        if not user or user.is_bot:
            return
        if user.id not in settings.admin_list and user.id != settings.OWNER_ID:
            return
        if not message.reply_to_message:
            return await message.reply("⚠️ **Usage:** Please reply to a message to broadcast it.")
        users = await users_col.find().to_list(None)
        status = await message.reply(
            f"📢 **Broadcast Initiated...**\n\n> 🎯 **Target:** `{len(users)}` users"
        )
        done = failed = 0
        for u in users:
            try:
                await message.reply_to_message.copy(u["user_id"])
                done += 1
            except Exception:
                failed += 1
            if done % 20 == 0:
                await status.edit_text(
                    f"🔄 **Broadcast In Progress...**\n\n"
                    f"> ✅ **Sent:** `{done}`\n> ❌ **Failed:** `{failed}`"
                )
        await status.edit_text(
            f"🎉 **Broadcast Completed!**\n\n"
            f"> 👥 **Total Target:** `{len(users)}` users\n"
            f"> ✅ **Success:** `{done}`\n"
            f"> ❌ **Failed:** `{failed}`"
        )

    @bot.on_message(filters.command("ban"))
    async def ban_handler(client, message: Message):
        user = message.from_user
        if not user or user.is_bot:
            return
        if user.id not in settings.admin_list and user.id != settings.OWNER_ID:
            return
        try:
            target = int((message.text or "").split()[1])
            await users_col.update_one({"user_id": target}, {"$set": {"is_banned": True}})
            await message.reply(
                f"🛡️ **User Banned**\n\n> User ID `{target}` has been banned from using the bot."
            )
        except Exception:
            await message.reply("⚠️ **Usage:** `/ban USER_ID`")

    @bot.on_message(filters.command("unban"))
    async def unban_handler(client, message: Message):
        user = message.from_user
        if not user or user.is_bot:
            return
        if user.id not in settings.admin_list and user.id != settings.OWNER_ID:
            return
        try:
            target = int((message.text or "").split()[1])
            await users_col.update_one({"user_id": target}, {"$set": {"is_banned": False}})
            await message.reply(
                f"🛡️ **User Unbanned**\n\n> User ID `{target}` has been restored and unbanned."
            )
        except Exception:
            await message.reply("⚠️ **Usage:** `/unban USER_ID`")

    @bot.on_message(filters.command("autodel"))
    async def autodel_handler(client, message: Message):
        user = message.from_user
        if not user or user.is_bot:
            return
        if user.id not in settings.admin_list and user.id != settings.OWNER_ID:
            return
        try:
            args = (message.text or "").split()
            if len(args) < 2:
                return await message.reply("⚠️ **Usage:** `/autodel 24h` or `/autodel off`")
            val = args[1].lower()
            if val == "off":
                await message.reply(
                    "⏳ **Auto-Delete Configured**\n\n> Auto-delete feature has been disabled."
                )
            else:
                hours = int(val.replace("h", ""))
                await message.reply(
                    f"⏳ **Auto-Delete Configured**\n\n> Link expiration set to `{hours}` hours."
                )
        except Exception:
            await message.reply("⚠️ **Usage:** `/autodel 24h` or `/autodel off`")


    def _is_media_message(msg: Message) -> bool:
        if not msg:
            return False
        return bool(
            msg.document or msg.video or msg.animation or msg.audio
            or msg.voice or msg.video_note or msg.photo
        )

    @bot.on_message(filters.command("ss"))
    async def ss_command(client, message: Message):
        """Reply to a video/file then choose screenshot count."""
        user = message.from_user
        if not user or user.is_bot:
            return
        if not await is_access_allowed(user.id):
            return await message.reply("🔒 This bot is restricted by the administrator.")
        if not await is_user_fsubbed(client, user.id):
            return await message.reply(
                "🔒 **Access Restricted**\n\n> Join the updates channel first.",
                reply_markup=_kb([[InlineKeyboardButton("📢 Join Updates Channel", url=settings.UPDATE_CHANNEL_URL)]]),
            )
        target = message.reply_to_message
        if not target or not _is_media_message(target):
            return await message.reply(
                "📸 **Screenshots**\n\n"
                "> Reply to a **video / document / animation** with `/ss`,\n"
                "> then pick how many screenshots to generate."
            )
        chat_id = target.chat.id
        msg_id = target.id
        rows = [
            [
                InlineKeyboardButton("1", callback_data=f"ss:{chat_id}:{msg_id}:1"),
                InlineKeyboardButton("3", callback_data=f"ss:{chat_id}:{msg_id}:3"),
                InlineKeyboardButton("5", callback_data=f"ss:{chat_id}:{msg_id}:5"),
            ],
            [
                InlineKeyboardButton("10", callback_data=f"ss:{chat_id}:{msg_id}:10"),
                InlineKeyboardButton("20", callback_data=f"ss:{chat_id}:{msg_id}:20"),
                InlineKeyboardButton("30", callback_data=f"ss:{chat_id}:{msg_id}:30"),
            ],
        ]
        await message.reply(
            "📸 **How many screenshots?**\n\n"
            "> Choose a count. Frames are taken evenly across the media.",
            reply_markup=_kb(rows),
        )

    @bot.on_message(filters.command("sv"))
    async def sv_command(client, message: Message):
        """Reply to a video/file then choose sample duration."""
        user = message.from_user
        if not user or user.is_bot:
            return
        if not await is_access_allowed(user.id):
            return await message.reply("🔒 This bot is restricted by the administrator.")
        if not await is_user_fsubbed(client, user.id):
            return await message.reply(
                "🔒 **Access Restricted**\n\n> Join the updates channel first.",
                reply_markup=_kb([[InlineKeyboardButton("📢 Join Updates Channel", url=settings.UPDATE_CHANNEL_URL)]]),
            )
        target = message.reply_to_message
        if not target or not _is_media_message(target):
            return await message.reply(
                "🎬 **Sample Video**\n\n"
                "> Reply to a **video / document / media** with `/sv`,\n"
                "> then pick the sample length in seconds."
            )
        chat_id = target.chat.id
        msg_id = target.id
        rows = [
            [
                InlineKeyboardButton("5s", callback_data=f"sv:{chat_id}:{msg_id}:5"),
                InlineKeyboardButton("10s", callback_data=f"sv:{chat_id}:{msg_id}:10"),
                InlineKeyboardButton("20s", callback_data=f"sv:{chat_id}:{msg_id}:20"),
            ],
            [
                InlineKeyboardButton("30s", callback_data=f"sv:{chat_id}:{msg_id}:30"),
                InlineKeyboardButton("60s", callback_data=f"sv:{chat_id}:{msg_id}:60"),
                InlineKeyboardButton("120s", callback_data=f"sv:{chat_id}:{msg_id}:120"),
            ],
        ]
        await message.reply(
            "🎬 **Sample length?**\n\n"
            "> Choose how many seconds from the start of the media.",
            reply_markup=_kb(rows),
        )

    @bot.on_callback_query(filters.regex(r"^ss:"))
    async def ss_callback(client, callback: CallbackQuery):
        user = callback.from_user
        if not user or user.is_bot:
            return
        if not await is_access_allowed(user.id):
            return await callback.answer("Restricted by administrator", show_alert=True)
        try:
            _, chat_s, msg_s, count_s = callback.data.split(":", 3)
            chat_id, msg_id, count = int(chat_s), int(msg_s), int(count_s)
        except Exception:
            return await callback.answer("Invalid request", show_alert=True)

        await callback.answer("Generating screenshots…")
        status = await callback.message.reply(f"📸 Generating **{count}** screenshot(s)…")
        work = None
        try:
            src_msg = await client.get_messages(chat_id, msg_id)
            if isinstance(src_msg, list):
                src_msg = src_msg[0] if src_msg else None
            if not src_msg or not _is_media_message(src_msg):
                return await status.edit_text("❌ Media not found or no longer available.")

            # Stream URL — ffmpeg seeks via Range, no full download
            source = local_tg_stream_url(chat_id, msg_id)
            work = make_work_dir("ss_")
            await status.edit_text(f"🖼️ Random frames from stream (anywhere)…")
            shots = await generate_screenshots(source, count, os.path.join(work, "out"), region="anywhere")
            if not shots:
                # fallback: full download once
                await status.edit_text("⬇️ Stream seek failed — downloading once…")
                media_path = os.path.join(work, "input.bin")
                await download_message_media(client, src_msg, media_path)
                shots = await generate_screenshots(media_path, count, os.path.join(work, "out2"), region="anywhere")
            if not shots:
                return await status.edit_text("❌ Could not extract screenshots from this file.")

            from wzgram.types import InputMediaPhoto
            for i in range(0, len(shots), 10):
                batch = shots[i : i + 10]
                media = []
                for j, p in enumerate(batch):
                    cap = f"Screenshot {i + j + 1}/{len(shots)}" if j == 0 else None
                    media.append(InputMediaPhoto(p, caption=cap))
                await client.send_media_group(callback.message.chat.id, media)

            await status.edit_text(f"✅ Sent **{len(shots)}** screenshot(s).")
        except Exception as e:
            logger.exception("ss_callback failed")
            try:
                await status.edit_text(f"❌ Screenshot failed: `{e}`")
            except Exception:
                pass
        finally:
            cleanup_dir(work)

    @bot.on_callback_query(filters.regex(r"^sv:"))
    async def sv_callback(client, callback: CallbackQuery):
        user = callback.from_user
        if not user or user.is_bot:
            return
        if not await is_access_allowed(user.id):
            return await callback.answer("Restricted by administrator", show_alert=True)
        try:
            _, chat_s, msg_s, sec_s = callback.data.split(":", 3)
            chat_id, msg_id, seconds = int(chat_s), int(msg_s), int(sec_s)
        except Exception:
            return await callback.answer("Invalid request", show_alert=True)

        await callback.answer("Generating sample…")
        status = await callback.message.reply(f"🎬 Generating **{seconds}s** sample…")
        work = None
        try:
            src_msg = await client.get_messages(chat_id, msg_id)
            if isinstance(src_msg, list):
                src_msg = src_msg[0] if src_msg else None
            if not src_msg or not _is_media_message(src_msg):
                return await status.edit_text("❌ Media not found or no longer available.")

            source = local_tg_stream_url(chat_id, msg_id)
            work = make_work_dir("sv_")
            out_path = os.path.join(work, "sample.mp4")
            await status.edit_text(f"✂️ Random mid→end {seconds}s sample via stream…")
            try:
                await generate_sample_video(source, seconds, out_path, region="middle_last")
            except Exception:
                await status.edit_text("⬇️ Stream cut failed — downloading once…")
                media_path = os.path.join(work, "input.bin")
                await download_message_media(client, src_msg, media_path)
                await generate_sample_video(media_path, seconds, out_path, region="middle_last")

            await status.edit_text("📤 Uploading sample…")
            await client.send_video(
                callback.message.chat.id,
                out_path,
                caption=f"🎬 Sample · **{seconds}s**",
                supports_streaming=True,
            )
            await status.edit_text(f"✅ Sample video (**{seconds}s**) sent.")
        except Exception as e:
            logger.exception("sv_callback failed")
            try:
                await status.edit_text(f"❌ Sample failed: `{e}`")
            except Exception:
                pass
        finally:
            cleanup_dir(work)

    @bot.on_message(filters.command("mi"))
    async def mi_command(client, message: Message):
        """Reply to media -> MediaInfo via stream URL (no full download)."""
        user = message.from_user
        if not user or user.is_bot:
            return
        if not await is_access_allowed(user.id):
            return await message.reply("🔒 This bot is restricted by the administrator.")
        if not await is_user_fsubbed(client, user.id):
            return await message.reply(
                "🔒 **Access Restricted**\n\n> Join the updates channel first.",
                reply_markup=_kb([[InlineKeyboardButton("📢 Join Updates Channel", url=settings.UPDATE_CHANNEL_URL)]]),
            )
        target = message.reply_to_message
        args = (message.text or "").split(maxsplit=1)
        direct_url = args[1].strip() if len(args) > 1 else ""
        if direct_url and not direct_url.lower().startswith(("http://", "https://")):
            return await message.reply("❌ Send a valid downloadable HTTP/HTTPS URL.")
        if not direct_url and (not target or not _is_media_message(target)):
            return await message.reply(
                "📊 **MediaInfo**\\n\\n"
                "> Reply to Telegram media with `/mi`, or send `/mi https://example.com/file.mkv`.\\n"
                "> Direct downloadable HTTP/HTTPS links are supported."
            )

        status = await message.reply(
            "🔍 **Extracting MediaInfo…**\\n> " +
            ("Probing downloadable URL (no full download)." if direct_url else "Probing Telegram media via stream.")
        )
        work = None
        try:
            if direct_url:
                from urllib.parse import urlparse, unquote
                from app.utils.mediainfo_fmt import validate_download_url
                await validate_download_url(direct_url)
                source = direct_url
                raw_name = os.path.basename(urlparse(direct_url).path)
                name = unquote(raw_name) or "download"
                data = await extract_mediainfo(source)
            else:
                chat_id = target.chat.id
                msg_id = target.id
                source = local_tg_stream_url(chat_id, msg_id)
                try:
                    data = await extract_mediainfo(source)
                except Exception:
                    await status.edit_text("⬇️ Stream probe failed — downloading Telegram media once…")
                    work = make_work_dir("mi_")
                    media_path = os.path.join(work, "input.bin")
                    await download_message_media(client, target, media_path)
                    data = await extract_mediainfo(media_path)

                media = target.video or target.audio or target.document or target.animation
                name = getattr(media, "file_name", None) if media else None
                if not name and target.video:
                    name = "video"
                name = name or "file"

            name = name.replace("\\n", " ").replace("\\r", " ")[:180]

            header = f"📊 **MediaInfo for** `{name}`\n\n"
            full = header + f"```\n{body}\n```"
            if len(full) > 4000:
                bio = io.BytesIO(body.encode("utf-8"))
                bio.name = f"{name}.mediainfo.txt"
                await status.edit_text(
                    f"📊 **MediaInfo for** `{name}` is long — sending as file…"
                )
                await message.reply_document(
                    document=bio,
                    caption=f"📊 MediaInfo · `{name}`",
                )
            else:
                await status.edit_text(full)
        except Exception as e:
            logger.exception("mi_command failed")
            try:
                await status.edit_text(f"❌ **MediaInfo failed:** `{e}`")
            except Exception:
                pass
        finally:
            cleanup_dir(work)
