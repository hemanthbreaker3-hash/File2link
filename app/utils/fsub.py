from __future__ import annotations

import logging

from wzgram.enums import ChatMemberStatus
from wzgram.errors import UserNotParticipant, ChatAdminRequired, ChannelPrivate

from app.database.connection import settings

logger = logging.getLogger(__name__)

ACTIVE = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
    ChatMemberStatus.RESTRICTED,
}


async def is_user_fsubbed(client, user_id: int) -> bool:
    if not settings.FORCE_SUB_CHANNELS:
        return True

    for channel_id in settings.fsub_list:
        try:
            member = await client.get_chat_member(channel_id, user_id)
            status = member.status
            if status not in ACTIVE:
                return False
        except UserNotParticipant:
            return False
        except (ChatAdminRequired, ChannelPrivate) as e:
            logger.error(
                "FSUB ERROR: cannot verify %s in %s (%s). "
                "Bot must be admin in the force-sub channel.",
                user_id, channel_id, e,
            )
            return False
        except Exception as e:
            logger.error("FSUB ERROR for %s in %s: %s", user_id, channel_id, e)
            return False
    return True
