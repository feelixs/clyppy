"""Task queue for graceful shutdown and restart."""
import pickle
import logging
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class QuickembedTask:
    """Task for a quickembed from a message."""
    message_id: int
    channel_id: int
    guild_id: int
    guild_name: str
    is_dm: bool
    clip_url: str
    author_id: int
    author_username: str
    created_at: datetime = field(default_factory=datetime.now)
    task_type: str = "quickembed"


@dataclass
class SlashCommandTask:
    """Task for a deferred slash command."""
    interaction_id: int
    interaction_token: str
    channel_id: int
    channel_name: Optional[str]
    guild_id: Optional[int]
    guild_name: Optional[str]
    user_id: int
    user_username: str
    clip_url: str
    extend_with_ai: bool
    file_ext: Optional[str] = None   # None = embed task, str = download/giphify task
    created_at: datetime = field(default_factory=datetime.now)
    task_type: str = "slash_command"

    # Store additional context if needed
    context_data: Dict[str, Any] = field(default_factory=dict)


class TaskQueue:
    """Persistent task queue using pickle."""

    def __init__(self, queue_file: str = "task_queue.pkl"):
        self.queue_file = Path(queue_file)
        self.quickembed_tasks: List[QuickembedTask] = []
        self.slash_command_tasks: List[SlashCommandTask] = []

    def add_quickembed(self, task: QuickembedTask):
        """Add a quickembed task to the queue."""
        self.quickembed_tasks.append(task)
        logger.info(f"Queued quickembed task: {task.clip_url} from message {task.message_id}")

    def add_slash_command(self, task: SlashCommandTask):
        """Add a slash command task to the queue."""
        self.slash_command_tasks.append(task)
        logger.info(f"Queued slash command task: {task.clip_url} from user {task.user_username}")

    def save(self):
        """Persist the queue to disk using pickle."""
        try:
            queue_data = {
                'quickembed_tasks': self.quickembed_tasks,
                'slash_command_tasks': self.slash_command_tasks,
                'saved_at': datetime.now()
            }

            with open(self.queue_file, 'wb') as f:
                pickle.dump(queue_data, f)

            logger.info(f"Saved {len(self.quickembed_tasks)} quickembed tasks and "
                       f"{len(self.slash_command_tasks)} slash command tasks to {self.queue_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save task queue: {e}")
            return False

    def load(self) -> bool:
        """Load the queue from disk."""
        if not self.queue_file.exists():
            logger.info("No task queue file found, starting with empty queue")
            return False

        try:
            with open(self.queue_file, 'rb') as f:
                queue_data = pickle.load(f)

            self.quickembed_tasks = queue_data.get('quickembed_tasks', [])
            self.slash_command_tasks = queue_data.get('slash_command_tasks', [])
            saved_at = queue_data.get('saved_at')

            logger.info(f"Loaded {len(self.quickembed_tasks)} quickembed tasks and "
                       f"{len(self.slash_command_tasks)} slash command tasks from {self.queue_file}")
            logger.info(f"Queue was saved at {saved_at}")

            # Clean up old tasks (> 15 minutes, interaction tokens expired)
            self._clean_expired_tasks()

            return True
        except Exception as e:
            logger.error(f"Failed to load task queue: {e}")
            return False

    def _clean_expired_tasks(self):
        """Remove tasks older than 15 minutes (interaction token expiry)."""
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(minutes=15)

        # Keep quickembeds (they can be replied to anytime)
        # Remove expired slash commands
        original_count = len(self.slash_command_tasks)
        self.slash_command_tasks = [
            task for task in self.slash_command_tasks
            if task.created_at > cutoff
        ]

        expired_count = original_count - len(self.slash_command_tasks)
        if expired_count > 0:
            logger.warning(f"Removed {expired_count} expired slash command tasks")

    def clear(self):
        """Clear the queue and delete the pickle file."""
        self.quickembed_tasks.clear()
        self.slash_command_tasks.clear()

        if self.queue_file.exists():
            try:
                self.queue_file.unlink()
                logger.info(f"Deleted task queue file: {self.queue_file}")
            except Exception as e:
                logger.error(f"Failed to delete task queue file: {e}")

    def has_tasks(self) -> bool:
        """Check if there are any tasks in the queue."""
        return len(self.quickembed_tasks) > 0 or len(self.slash_command_tasks) > 0

    def get_task_count(self) -> tuple[int, int]:
        """Return (quickembed_count, slash_command_count)."""
        return len(self.quickembed_tasks), len(self.slash_command_tasks)


async def process_queued_tasks(bot, task_queue: TaskQueue):
    """Process all tasks from the queue after restart."""
    quickembed_count, slash_count = task_queue.get_task_count()

    if not task_queue.has_tasks():
        logger.info("No queued tasks to process")
        return

    logger.info(f"Processing {quickembed_count} quickembed tasks and {slash_count} slash command tasks")

    # Process quickembed tasks
    for task in task_queue.quickembed_tasks:
        try:
            await process_quickembed_task(bot, task)
        except Exception as e:
            logger.error(f"Failed to process quickembed task {task.message_id}: {e}")

    # Process slash command tasks
    for task in task_queue.slash_command_tasks:
        try:
            await process_slash_command_task(bot, task)
        except Exception as e:
            logger.error(f"Failed to process slash command task {task.interaction_id}: {e}")

    # Clear the queue after processing
    task_queue.clear()
    logger.info("Finished processing queued tasks")


async def process_quickembed_task(bot, task: QuickembedTask):
    """Process a single quickembed task."""
    from bot.types import GuildType

    logger.info(f"Processing queued quickembed: {task.clip_url} from message {task.message_id}")

    try:
        # Fetch the channel
        channel = await bot.fetch_channel(task.channel_id)

        # Fetch the original message
        message = await channel.fetch_message(task.message_id)

        # Find the appropriate platform embedder
        platform_embedder = None
        for embedder_wrapper in bot.platform_embedders:
            # embedder_wrapper is a BaseAutoEmbed object with .platform and .embedder attributes
            if embedder_wrapper.platform.is_clip_link(task.clip_url):
                platform_embedder = embedder_wrapper.embedder
                break

        if not platform_embedder:
            logger.warning(f"No platform embedder found for {task.clip_url}")
            return

        # Create guild type
        guild = GuildType(task.guild_id, task.guild_name, task.is_dm)

        # Process the clip
        await platform_embedder._process_clip_one_at_a_time(
            clip_link=task.clip_url,
            respond_to=message,
            guild=guild
        )

        logger.info(f"Successfully processed queued quickembed: {task.clip_url}")

    except Exception as e:
        logger.error(f"Error processing queued quickembed {task.message_id}: {e}")
        # Could send error message to channel here


async def process_slash_command_task(bot, task: SlashCommandTask):
    """Process a single slash command task."""
    logger.info(f"Processing queued slash command: {task.clip_url} from user {task.user_username}")

    try:
        # Check if interaction is still valid (< 15 minutes old)
        from datetime import timedelta
        age = datetime.now() - task.created_at

        if age > timedelta(minutes=15):
            logger.warning(f"Slash command task is too old ({age}), skipping")
            return

        # Create a minimal context object that can be used by command_embed
        # We'll use the interaction token to edit the deferred response
        from interactions import SlashContext, InteractionContext

        # Find the appropriate platform and slug
        platform = None
        slug = None
        for p in bot.platform_embedders:
            parsed = p.platform.parse_clip_url(task.clip_url)
            if parsed:
                platform = p.platform
                slug = parsed
                break

        if not platform or not slug:
            logger.warning(f"No platform found for {task.clip_url}")
            await edit_deferred_response(bot, task, "❌ Unsupported platform or invalid URL")
            return

        # Reconstruct a minimal SlashContext-like object
        # This is hacky but necessary since we can't fully serialize/deserialize SlashContext
        class MinimalContext:
            def __init__(self, bot, task):
                self.bot = bot._client if hasattr(bot, '_client') else bot
                self.interaction_id = task.interaction_id
                self.token = task.interaction_token
                self.channel_id = task.channel_id
                self.guild_id = task.guild_id
                self.author_id = task.user_id
                # Create mock author with send method for DMs
                async def author_send(*args, **kwargs):
                    # Can't send DMs during restoration, just log
                    logger.info(f"Skipping DM to user {task.user_id} during task restoration")
                    return None

                self.author = type('obj', (object,), {
                    'id': task.user_id,
                    'username': task.user_username,
                    'send': author_send,
                    'bot': True  # dont send dm
                })()
                # Alias for SlashContext compatibility
                self.user = self.author
                self._deferred = True
                # Flag to indicate this is a restored task - skip permission checks
                self._restored_task = True
                # Create mock channel object
                self.channel = type('obj', (object,), {
                    'id': task.channel_id,
                    'nsfw': False,  # Default to safe
                    'name': task.channel_name or 'unknown-channel'
                })()
                # Create mock guild object if guild_id exists
                if task.guild_id:
                    self.guild = type('obj', (object,), {
                        'id': task.guild_id,
                        'name': task.guild_name or 'Unknown Guild'
                    })()
                else:
                    self.guild = None

            async def send(self, *args, **kwargs):
                # Edit the deferred response
                return await edit_deferred_response_with_data(self.bot, self, *args, **kwargs)

        ctx = MinimalContext(bot, task)

        if task.file_ext is not None:
            # Download/giphify task
            if not hasattr(bot, 'download_runner'):
                logger.error("bot.download_runner not set — cannot resume download task")
                await edit_deferred_response(bot, task, "❌ Bot not ready to process your download. Please retry.")
                return
            await bot.download_runner(ctx, task.clip_url, task.file_ext)
        else:
            # Embed task — existing path
            await bot.base_embedder.command_embed(
                ctx=ctx,
                url=task.clip_url,
                platform=platform,
                slug=slug,
                extend_with_ai=task.extend_with_ai,
                already_deferred=True
            )

        logger.info(f"Successfully processed queued slash command: {task.clip_url}")

    except Exception as e:
        logger.error(f"Error processing queued slash command {task.interaction_id}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        try:
            await edit_deferred_response(bot, task, f"❌ Error processing your request: {str(e)}")
        except Exception as edit_error:
            logger.error(f"Failed to send error response: {edit_error}")


async def edit_deferred_response(bot, task: SlashCommandTask, content: str):
    """Edit a deferred interaction response using the webhook endpoint."""
    from bot.io import get_aiohttp_session
    url = f"https://discord.com/api/v10/webhooks/{bot.user.id}/{task.interaction_token}/messages/@original"
    async with get_aiohttp_session() as session:
        async with session.patch(url, json={"content": content}) as resp:
            if resp.status == 200:
                logger.info(f"Successfully edited deferred response for {task.interaction_id}")
            else:
                error = await resp.text()
                logger.error(f"Failed to edit deferred response: {resp.status} - {error}")


async def edit_deferred_response_with_data(bot, ctx, *args, **kwargs):
    """Edit deferred response with full embed data."""
    from bot.io import get_aiohttp_session
    url = f"https://discord.com/api/v10/webhooks/{bot.user.id}/{ctx.token}/messages/@original"

    # Build payload from args/kwargs
    payload = {}
    if args and isinstance(args[0], str):
        payload["content"] = args[0]
    if "content" in kwargs:
        payload["content"] = kwargs["content"]
    if "embeds" in kwargs:
        payload["embeds"] = [e.to_dict() if hasattr(e, 'to_dict') else e for e in kwargs["embeds"]]
    if "components" in kwargs:
        # Handle both single component and list of components
        components = kwargs["components"]
        if not isinstance(components, list):
            components = [components]
        payload["components"] = [c.to_dict() if hasattr(c, 'to_dict') else c for c in components]

    async with get_aiohttp_session() as session:
        # Handle file uploads
        if "file" in kwargs:
            import aiohttp
            form = aiohttp.FormData()
            form.add_field('payload_json', json.dumps(payload))

            file_path = kwargs["file"]
            with open(file_path, 'rb') as f:
                form.add_field('files[0]', f, filename=file_path.split('/')[-1])
                resp = await session.patch(url, data=form)
        else:
            resp = await session.patch(url, json=payload)

        async with resp:
            if resp.status == 200:
                logger.info(f"Successfully edited deferred response")
                return await resp.json()
            else:
                error = await resp.text()
                logger.error(f"Failed to edit deferred response: {resp.status} - {error}")
                return None
