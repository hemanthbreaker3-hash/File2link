from __future__ import annotations

import datetime
import html
import logging
import os
import mimetypes

from wzgram import filters
from wzgram.enums import ParseMode
from wzgram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from app.database.connection import files_col, users_col, settings_col, settings
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


def register_handlers(bot):
    """Register wzgram (Pyrogram-compatible) handlers on the bot client."""

    @bot.on_message(filters.command("start") & filters.private)
    async def start_handler(client, message: Message):
        user = message.from_user
        if not user or user.is_bot:
            return
        user_id = user.id

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
            download_url = f"{base_url}/dl/{short_code}"
            archive_exts = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".iso", ".cab", ".zst"}
            is_archive = os.path.splitext(file_name)[1].lower() in archive_exts
            if is_archive:
                await files_col.update_one({"short_code": short_code}, {"$set": {"is_archive": True}})
            stream_url = f"{base_url}/stream/{short_code}"
            caption = (
                "💎 <b>Media Links Generated</b>\n\n"
                f"📂 <b>File Name:</b> <code>{safe_name}</code>\n"
                f"📦 <b>File Size:</b> <code>{html.escape(formatted_size)}</code>\n"
                f"⚙️ <b>MIME Type:</b> <code>{safe_mime}</code>\n\n"
                "🚀 <b>Fast Links</b>\n"
                f"⚡ <a href=\"{html.escape(download_url, quote=True)}\">Direct Download</a>\n"
                + ("" if is_archive else f"🍿 <a href=\"{html.escape(stream_url, quote=True)}\">Watch Online</a>\n")
                + f"\n📢 <a href=\"{html.escape(settings.UPDATE_CHANNEL_URL, quote=True)}\">Updates Channel</a>"
            )
            media_row = [InlineKeyboardButton("⚡ Direct Download", url=download_url)]
            if not is_archive:
                media_row.append(InlineKeyboardButton("🍿 Watch Online", url=stream_url))
            user_buttons = _kb([media_row,
                [InlineKeyboardButton("📢 Updates Channel", url=settings.UPDATE_CHANNEL_URL)],
                [InlineKeyboardButton("🗑️ Delete Link", callback_data=f"del_{short_code}")],
            ])
            channel_buttons = _kb([media_row,
                [InlineKeyboardButton("📢 Updates Channel", url=settings.UPDATE_CHANNEL_URL)],
            ])
        else:
            caption = (
                "💎 <b>File Received Successfully</b>\n\n"
                f"📂 <b>File Name:</b> <code>{safe_name}</code>\n"
                f"📦 <b>File Size:</b> <code>{html.escape(formatted_size)}</code>\n"
                f"⚙️ <b>MIME Type:</b> <code>{safe_mime}</code>\n\n"
                "⚠️ <b>Download and streaming links are not active yet.</b>\n"
                "The administrator must set a public HTTPS address using <code>/baseurl https://your-public-domain</code>.\n\n"
                f"📢 <a href=\"{html.escape(settings.UPDATE_CHANNEL_URL, quote=True)}\">Updates Channel</a>"
            )
            user_buttons = _kb([
                [InlineKeyboardButton("📢 Updates Channel", url=settings.UPDATE_CHANNEL_URL)],
                [InlineKeyboardButton("🗑️ Delete Link", callback_data=f"del_{short_code}")],
            ])
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


    @bot.on_message(filters.private & filters.text & ~filters.command(["start", "ss", "sv", "mi", "stats", "broadcast", "ban", "unban", "autodel", "baseurl"]))
    async def direct_url_handler(client, message: Message):
        """Create File2link URLs from an existing direct HTTP(S) download URL."""
        user = message.from_user
        raw_url = (message.text or "").strip()
        if not user or user.is_bot or not raw_url.lower().startswith(("http://", "https://")):
            return
        if not await check_rate_limit(user.id):
            return await message.reply("⏳ **Rate Limit Exceeded**\\n\\n> Please wait a few seconds.")
        if not await is_user_fsubbed(client, user.id):
            return await message.reply(
                "🔒 **Access Restricted**\\n\\n> Join our official updates channel first.",
                reply_markup=_kb([[InlineKeyboardButton("📢 Join Updates Channel", url=settings.UPDATE_CHANNEL_URL)]]),
            )
        from urllib.parse import urlparse, unquote, parse_qs
        from app.utils.mediainfo_fmt import validate_download_url
        try:
            await validate_download_url(raw_url)
        except Exception as exc:
            return await message.reply(f"❌ **Invalid download URL:** `{html.escape(str(exc))}`")

        parsed = urlparse(raw_url)
        query_name = (parse_qs(parsed.query).get("filename") or parse_qs(parsed.query).get("name") or [""])[0]
        filename = unquote(os.path.basename(parsed.path)) or unquote(os.path.basename(query_name)) or "download"
        filename = filename.replace("\\r", " ").replace("\\n", " ")[:180]
        ext = os.path.splitext(filename)[1].lower()
        archive_exts = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".iso", ".cab", ".zst"}
        is_archive = ext in archive_exts
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        short_code = generate_short_code()
        expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=settings.DEFAULT_EXPIRY) if settings.DEFAULT_EXPIRY > 0 else None
        await files_col.insert_one({
            "file_id": "", "file_unique_id": short_code, "filename": filename,
            "mime_type": mime_type, "file_size": 0, "uploader_id": user.id,
            "created_at": datetime.datetime.utcnow(), "expiry_time": expiry,
            "access_count": 0, "short_code": short_code, "chat_id": 0, "message_id": 0,
            "external_url": raw_url, "is_archive": is_archive,
        })
        try:
            base_url = normalize_public_base_url(settings.BASE_URL)
        except ValueError as exc:
            await files_col.delete_one({"short_code": short_code})
            return await message.reply(f"⚠️ **Set a public Base URL first:** `{html.escape(str(exc))}`")
        download_url = f"{base_url}/dl/{short_code}"
        stream_url = f"{base_url}/stream/{short_code}"
        caption = (
            "💎 <b>Download URL Converted Successfully</b>\\n\\n"
            f"📂 <b>File Name:</b> <code>{html.escape(filename)}</code>\\n"
            f"⚙️ <b>Type:</b> <code>{'Archive' if is_archive else html.escape(mime_type)}</code>\\n\\n"
            f'⬇️ <a href="{html.escape(download_url, quote=True)}">Direct Download</a>'
        )
        rows = [[InlineKeyboardButton("⬇️ Direct Download", url=download_url)]]
        if not is_archive:
            caption += f'\n▶️ <a href="{html.escape(stream_url, quote=True)}">Watch / Stream</a>'
            rows.append([InlineKeyboardButton("▶️ Watch Online", url=stream_url)])
        rows.append([InlineKeyboardButton("🗑️ Delete Link", callback_data=f"del_{short_code}")])
        await message.reply(caption, parse_mode=ParseMode.HTML, reply_markup=_kb(rows))

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
