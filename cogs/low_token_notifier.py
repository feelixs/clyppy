import logging
from interactions import Extension, Task, IntervalTrigger, listen, Button, ButtonStyle
from interactions.api.events import Startup
from bot.io.io import get_low_token_users, mark_low_token_notified
from bot.env import CLYPPYBOT_ID

logger = logging.getLogger(__name__)


class LowTokenNotifier(Extension):
    def __init__(self, bot):
        self.bot = bot
        self.check_task = Task(self.check_low_tokens, IntervalTrigger(hours=1))

    @listen(Startup)
    async def on_startup(self):
        self.check_task.start()
        logger.info("Low token notifier task started")

    async def check_low_tokens(self):
        if not self.bot.is_ready:
            return
        if self.bot.user.id != CLYPPYBOT_ID:
            logger.info("Skipping low-token notification because I'm not logged in as clyppybot")
            return
        try:
            pending = await get_low_token_users(limit=50)
            if not pending:
                return

            notified_ids = []
            for entry in pending:
                user_id = entry.get('user_id')
                tokens = entry.get('tokens', 0)
                if not user_id:
                    continue
                try:
                    user = await self.bot.fetch_user(user_id)
                    if user:
                        dm = await user.fetch_dm(force=False)
                        await dm.send(
                            content=(
                                f"Your Clyppy VIP token count is low (`{tokens}` remaining). "
                                f"To make sure your backed up clips stay viewable, "
                                f"consider voting or buying more in bulk."
                            ),
                            components=[
                                Button(style=ButtonStyle.LINK, label="Vote for Tokens", url="https://clyppy.io/profile/tokens/#vote"),
                                Button(style=ButtonStyle.LINK, label="Buy Tokens", url="https://clyppy.io/profile/tokens/"),
                            ]
                        )
                        notified_ids.append(user_id)
                    else:
                        logger.debug(f"Could not DM user: user {user_id} not found.")
                        notified_ids.append(user_id)
                except Exception as e:
                    logger.debug(f"Could not DM user {user_id}: {e}")

            if notified_ids:
                await mark_low_token_notified(notified_ids)
                logger.info(f"Sent {len(notified_ids)} low-token DMs")
        except Exception as e:
            logger.error(f"Error in low token notifier: {e}")
