import logging
from interactions import Extension, Task, IntervalTrigger, listen, Button, ButtonStyle
from interactions.api.events import Startup
from bot.io.io import get_pending_backup_warnings, mark_backup_warned
from bot.env import buy_tokens_url, vote_url, CLYPPYBOT_ID

logger = logging.getLogger(__name__)


class BackupNotifier(Extension):
    def __init__(self, bot):
        self.bot = bot
        self.check_task = Task(self.check_pending_warnings, IntervalTrigger(minutes=30))

    @listen(Startup)
    async def on_startup(self):
        self.check_task.start()
        logger.info("Backup notifier task started")

    async def check_pending_warnings(self):
        if not self.bot.is_ready:
            return
        if self.bot.user.id != CLYPPYBOT_ID:
            logger.info("Skipping backup warning notification because I'm not logged in as clyppybot")
            return
        try:
            pending = await get_pending_backup_warnings(limit=50)
            if not pending:
                return

            acknowledged_ids = []
            for entry in pending:
                user_id = entry.get('user_id')
                backup_id = entry.get('id')
                clip_title = entry.get('clip_title', 'a clip')
                cost = entry.get('monthly_cost', 0)
                user_tokens = entry.get('user_tokens', 0)
                position = entry.get('position', 0)
                clip_expired = entry.get('notify_clip_expired', False)
                if not user_id or not backup_id:
                    continue

                if clip_expired:
                    msg = (
                        f"ℹ️ The clip `{clip_title}` is no longer being backed up. "
                        f"The active sponsor stopped paying, and when it was your turn in the reserve "
                        f"(position `{position}`), you only had `{user_tokens}` tokens — `{cost}` were needed, "
                        f"so you were skipped. No one else in the reserve could afford it either, "
                        f"and the clip is being deleted from Clyppy storage.\n\n"
                        f"**You were not charged.** If the clip gets re-embedded later, you'll need to "
                        f"manually run `/backup` on it again."
                    )
                elif position == 0:
                    msg = (
                        f"⚠️ Your backup of `{clip_title}` will be charged `{cost}` tokens "
                        f"in less than 24 hours, but you only have `{user_tokens}`. "
                        f"The clip will be passed to the next sponsor in the reserve (if any) "
                        f"if you don't top up in time."
                    )
                else:
                    msg = (
                        f"⚠️ The active sponsor of `{clip_title}` dropped, and you were next in the reserve "
                        f"(position `{position}`), but you only have `{user_tokens}` tokens — `{cost}` are needed. "
                        f"You were skipped this round and remain in the queue. "
                        f"Top up to be considered next time the active sponsor falls through."
                    )

                try:
                    user = await self.bot.fetch_user(user_id)
                    if user:
                        dm = await user.fetch_dm(force=False)
                        await dm.send(
                            content=msg,
                            components=[
                                Button(style=ButtonStyle.LINK, label="Free Tokens (Vote)", url=vote_url("low_token_dm")),
                                Button(style=ButtonStyle.LINK, label="Buy Tokens", url=buy_tokens_url("low_token_dm")),
                            ]
                        )
                        acknowledged_ids.append(backup_id)
                    else:
                        logger.debug(f"Could not DM user: user {user_id} not found.")
                        acknowledged_ids.append(backup_id)
                except Exception as e:
                    logger.debug(f"Could not DM user {user_id}: {e}")

            if acknowledged_ids:
                await mark_backup_warned(acknowledged_ids)
                logger.info(f"Sent {len(acknowledged_ids)} backup warning DMs")
        except Exception as e:
            logger.error(f"Error in backup notifier: {e}")
