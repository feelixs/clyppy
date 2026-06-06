import logging
from interactions import Extension, Task, IntervalTrigger, listen, Embed, Button, ButtonStyle
from interactions.api.events import Startup
from bot.io.io import get_pending_vote_notifications, mark_votes_notified
from bot.env import vote_url, CLYPPYBOT_ID
from bot.types import COLOR_GREEN

logger = logging.getLogger(__name__)


async def _build_vote_dm(entry: dict, user, bot) -> tuple[Embed, list[Button]]:
    total = entry.get('vote_count') or 0
    monthly = entry.get('vote_month_count') or 0
    source = entry.get('source', 'a bot list site')
    tokens = entry.get('tokens_awarded', 1)
    user_id = entry['user_id']

    try:
        t = await bot.base_embedder.fetch_tokens(user)
        t = f'`{t}`'
    except Exception as e:
        logger.debug(f"Could not fetch tokens for user {user_id}: {e}")
        t = '(unknown - use `/tokens`)'

    lines = [
        f"You voted on **{source}** and earned **{tokens} VIP tokens**.",
        f"You now have {t} tokens. Go embed some videos with them!",
        "",
    ]
    if monthly > 1:
        lines.append(f"You've voted **{monthly}x** this month!")
    if total > 1:
        lines.append(f"You have **{total} total votes** — thank you for the support.")
    lines += [
        "",
        "You can vote again in **12 hours**.",
    ]

    embed = Embed(
        title="Thanks for voting for Clyppy! 🎬",
        description="\n".join(lines),
        color=COLOR_GREEN,
    )
    components = [
        Button(style=ButtonStyle.LINK, label="Vote again", url=vote_url('post_vote_dm')),
        Button(style=ButtonStyle.LINK, label="Enjoying Clyppy? Leave a review", url="https://top.gg/bot/1111723928604381314#reviews"),
    ]
    return embed, components


class VoteNotifier(Extension):
    def __init__(self, bot):
        self.bot = bot
        self.notify_task = Task(self.notify_voters, IntervalTrigger(minutes=2))

    @listen(Startup)
    async def on_startup(self):
        self.notify_task.start()
        logger.info("Vote notifier task started")

    async def notify_voters(self):
        if not self.bot.is_ready:
            return

        try:
            pending = await get_pending_vote_notifications(limit=50)
            if not pending:
                return

            notified_ids = []
            for entry in pending:
                user_id = entry.get('user_id')
                log_id = entry.get('id')
                if not user_id or not log_id:
                    continue
                try:
                    if self.bot.user.id == CLYPPYBOT_ID:
                        user = await self.bot.fetch_user(user_id)
                        if user:
                            dm = await user.fetch_dm(force=False)
                            embed, components = await _build_vote_dm(entry, user, self.bot)
                            await dm.send(embeds=[embed], components=components)
                        else:
                            logger.debug(f"Count not DM user: user {user_id} not found.")
                    else:
                        logger.info("Skipping vote notification because I'm not logged in as clyppybot")
                except Exception as e:
                    logger.debug(f"Could not DM user {user_id}: {e}")
                notified_ids.append(log_id)

            if notified_ids:
                await mark_votes_notified(notified_ids)
                logger.info(f"Sent {len(notified_ids)} vote DMs")
        except Exception as e:
            logger.error(f"Error in vote notifier: {e}")
