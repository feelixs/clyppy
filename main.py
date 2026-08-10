# Load environment variables from .env file FIRST before any other imports
from dotenv import load_dotenv
load_dotenv()

from interactions import AutoShardedClient, Client, Intents
from interactions.api.gateway.gateway import GatewayClient, OPCODE, FastJson
from bot.setup import init_misc
from bot.io import get_aiohttp_session
from bot.db import GuildDatabase
from bot.io.cdn import CdnSpacesClient
from bot.classes import upload_current_cookies_to_api

# Type hints for dynamically-attached bot attributes (set in init_misc / main.py).
# These are declared on Client so PyCharm/PyRight resolve `bot.<attr>` everywhere
# the bot is typed as Client or AutoShardedClient. They are intentionally None at
# class level — actual instances are assigned per-Bot below and in init_misc.
Client.guild_settings: GuildDatabase = None  # type: ignore[assignment]
Client.cdn_client: CdnSpacesClient = None    # type: ignore[assignment]
from bot.env import is_contrib_instance, log_api_bypass
from cogs.base import format_count
import aiohttp
import signal
import logging
import asyncio
import sys
import os
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def fetch_embed_count(client=None) -> str:
    """Fetch embed count from API (or use cached value) and return formatted string"""
    # Use cached value if available on bot
    if client and hasattr(client, 'cached_embed_count'):
        return format_count(client.cached_embed_count)

    # Contributor mode - return placeholder
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://clyppy.io/api/stats/embeds-count/", "GET")
        return "/help"

    # Otherwise fetch fresh
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://clyppy.io/api/stats/embeds-count/") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    count = data.get("count", 0)
                    return format_count(count)
    except Exception:
        pass
    return "/help"


# Monkey-patch to show mobile status indicator
async def _identify_mobile(self) -> None:
    """Send an identify payload to the gateway with mobile browser."""
    if self.ws is None:
        raise RuntimeError

    presence = {
        "status": "online",
        "since": None,
        "activities": [
            {
                "name": "video embeds for you",
                "type": 0,  # Playing
            }
        ],
        "afk": False,
    }

    payload = {
        "op": OPCODE.IDENTIFY,
        "d": {
            "token": self.state.client.http.token,
            "intents": self.state.intents,
            "shard": self.shard,
            "large_threshold": 250,
            "properties": {
                "os": sys.platform,
                "browser": "Discord Android",
                "device": "Discord Android",
            },
            "presence": presence,
        },
        "compress": True,
    }
    serialized = FastJson.dumps(payload)
    await self.ws.send_str(serialized)
    self.state.wrapped_logger(
        logging.DEBUG, f"Identification payload sent to gateway, requesting intents: {self.state.intents}"
    )


GatewayClient._identify = _identify_mobile


async def save_to_server():
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://felixcreations.com/api/products/clyppy/save_db/", "POST",
                      {"env": 'test' if os.getenv('TEST') else 'prod'})
        logger.info("[CONTRIB MODE] Database save bypassed")
        return

    env = 'test' if os.getenv('TEST') is not None else 'prod'
    async with get_aiohttp_session() as session:
        try:
            headers = {'X-API-Key': os.getenv('clyppy_post_key')}
            data = aiohttp.FormData()
            data.add_field('env', env)
            with open("guild_settings.db", "rb") as f:
                data.add_field('file', f)
                async with session.post(
                        url='https://felixcreations.com/api/products/clyppy/save_db/',
                        data=data,
                        headers=headers
                ) as response:
                    if response.status == 200:
                        logger.info("Database saved to server")
                    else:
                        logger.error(f"Failed with status {response.status}")
        except Exception as e:
            logger.error(f"Failed to save database to server: {e}")


async def load_from_server():
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://felixcreations.com/api/products/clyppy/get_db/", "GET",
                      {"env": 'test' if os.getenv('TEST') else 'prod'})
        logger.info("[CONTRIB MODE] Database load bypassed - using local database")
        return

    env = 'test' if os.getenv('TEST') is not None else 'prod'
    async with get_aiohttp_session() as session:
        try:
            headers = {'X-API-Key': os.getenv('clyppy_post_key')}
            params = {'env': env}
            async with session.get(
                    url='https://felixcreations.com/api/products/clyppy/get_db/',
                    headers=headers,
                    params=params
            ) as response:
                if response.status == 200:
                    content = await response.read()
                    with open('guild_settings.db', 'wb') as f:
                        f.write(content)
                    logger.info("Database loaded from server")
                else:
                    logger.error(f"Failed to get database from server: {response.status}")
        except Exception as e:
            logger.error(f"Failed to get database from server: {e}")


Bot = AutoShardedClient(intents=Intents.DEFAULT | Intents.MESSAGE_CONTENT)
cdn_client = CdnSpacesClient()
Bot.cdn_client = cdn_client
Bot = init_misc(Bot)

Bot.guild_settings = GuildDatabase(on_load=load_from_server, on_save=save_to_server)


async def cleanup_old_videos():
    """Background task to periodically delete old video files."""
    cleanup_logger = logging.getLogger("video_cleanup")
    DEFAULT_EDIT_WAIT = 60 * 2  # 2 minutes
    MIN_FILE_AGE = 60 * 10  # 10 minutes

    while True:
        try:
            await asyncio.sleep(DEFAULT_EDIT_WAIT)
            this_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
            cleanup_logger.info(f"Checking {this_dir} for videos to delete")
            current_time = time.time()

            files = os.listdir(this_dir)
            for file in files:
                if file.endswith((".mp4", ".m3u8", ".webm", ".mov", ".avi", ".mp3", ".wav", ".ogg", ".flac", ".gif")):
                    file_path = os.path.join(this_dir, file)
                    file_age = current_time - os.path.getctime(file_path)

                    if file_age < MIN_FILE_AGE:
                        cleanup_logger.info(f"Skipping {file} - too new (age: {file_age / 60:.1f} minutes)")
                        continue

                    cleanup_logger.info(f"Deleting {file} (age: {file_age / 60:.1f} minutes)")
                    os.remove(file_path)
        except asyncio.CancelledError:
            cleanup_logger.info("Video cleanup task cancelled")
            break
        except Exception as e:
            cleanup_logger.error(f"Error during cleanup: {str(e)}")


async def wait_for_active_tasks(bot):
    """Wait for active download/embed tasks to complete before shutting down."""
    logger.info("Waiting for active tasks to complete...")

    # Give tasks a grace period to start (tasks in setup phase might not be tracked yet)
    await asyncio.sleep(2)

    timeout = 60 * 3  # 3 minutes max
    last_log_time = asyncio.get_event_loop().time()
    total_elapsed = 0

    while timeout > 0:
        try:
            embedding_count = len(bot.currently_embedding)
            downloading_count = len(bot.currently_downloading)

            # Log immediately if there are tasks
            if total_elapsed == 0 and (embedding_count > 0 or downloading_count > 0):
                logger.info(f"Found active tasks: {embedding_count} embedding, {downloading_count} downloading")

            # Check if tasks completed
            if embedding_count == 0 and downloading_count == 0:
                logger.info(f"All active tasks completed (checked at {total_elapsed}s)")
                break

            # Log every second during first 10 seconds for debugging
            if total_elapsed < 10:
                logger.info(f"[{total_elapsed}s] Embedding: {embedding_count}, Downloading: {downloading_count}")

            current_time = asyncio.get_event_loop().time()
            if current_time - last_log_time >= 10:
                # Log status every 10 seconds after the first 10
                logger.info(f"Still waiting ({total_elapsed}s elapsed): {embedding_count} embedding, {downloading_count} downloading")
                if embedding_count > 0:
                    logger.info(f"  Embedding IDs: {bot.currently_embedding}")
                if downloading_count > 0:
                    logger.info(f"  Downloading slugs: {bot.currently_downloading}")
                last_log_time = current_time

            await asyncio.sleep(1)
            timeout -= 1
            total_elapsed += 1
        except asyncio.CancelledError:
            logger.warning(f"Wait loop was cancelled at {total_elapsed}s!")
            raise
        except Exception as e:
            logger.error(f"Error in wait loop at {total_elapsed}s: {e}")
            import traceback
            logger.error(traceback.format_exc())
            break

    if timeout <= 0:
        logger.warning(f"Timeout reached after {total_elapsed}s - forcing shutdown with {len(bot.currently_embedding)} "
                      f"embedding and {len(bot.currently_downloading)} downloading tasks remaining")
    else:
        logger.info(f"All tasks completed after {total_elapsed}s")


async def save_state(bot):
    """Save task queue and database before shutting down."""
    # Save task queue to disk
    logger.info("Saving task queue...")
    queue_count = bot.task_queue.get_task_count()
    logger.info(f"Task queue: {queue_count[0]} quickembeds, {queue_count[1]} slash commands")
    bot.task_queue.save()

    # Save database
    try:
        logger.info("Saving database...")
        await bot.guild_settings.save()
        logger.info("Database saved successfully")
    except Exception as e:
        logger.error(f"Failed to save database: {e}")

    # Persist rotated YouTube cookies back to the API so the next container
    # start begins from the freshest rotation state, not the last manual
    # export. Hard 15s timeout — a stuck API must never block shutdown.
    try:
        logger.info("Uploading rotated cookies...")
        await upload_current_cookies_to_api(logger, timeout_seconds=15.0)
    except Exception as e:
        logger.error(f"Failed to upload cookies: {e}")


async def main():
    # Set up shutdown event
    shutdown_event = asyncio.Event()

    def handle_shutdown_signal():
        """Signal handler that triggers graceful shutdown."""
        logger.info("Received shutdown signal (SIGTERM/SIGINT), initiating graceful shutdown...")
        shutdown_event.set()

    # Register signal handlers using asyncio's mechanism
    # Use get_running_loop() since we're inside an async function
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_shutdown_signal)

    Bot.load_extension('cogs.base')
    Bot.load_extension('cogs.vote_notifier')
    Bot.load_extension('cogs.low_token_notifier')
    Bot.load_extension('cogs.backup_notifier')
    Bot.load_extension('cogs.heartbeat')
    await Bot.guild_settings.setup_db()

    # Load and process queued tasks from previous shutdown
    logger.info("Loading task queue from previous session...")
    Bot.task_queue.load()

    # Start background tasks
    cleanup_task = asyncio.create_task(cleanup_old_videos())
    bot_task = asyncio.create_task(Bot.astart(token=os.getenv('CLYPP_TOKEN')))

    try:
        # Wait for either the bot to finish or shutdown signal
        done, pending = await asyncio.wait(
            [bot_task, asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED
        )

        # If shutdown was triggered, do graceful shutdown
        if shutdown_event.is_set():
            logger.info("Shutdown event triggered, initiating graceful shutdown...")

            try:
                # Step 1: Set shutdown flag (no new tasks will be accepted)
                Bot.is_shutting_down = True
                logger.info("Shutdown flag set - new tasks will be queued")

                # Step 2: Wait for active downloads/embeds to complete (bot still running)
                logger.info("About to call wait_for_active_tasks()")
                logger.info(f"bot_task status: done={bot_task.done()}, cancelled={bot_task.cancelled()}")

                # Create wrapper to monitor bot_task
                async def monitored_wait():
                    wait_task = asyncio.create_task(wait_for_active_tasks(Bot))
                    done, pending = await asyncio.wait(
                        [wait_task, bot_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    if bot_task in done:
                        logger.warning("bot_task completed unexpectedly during wait!")
                        if bot_task.exception():
                            logger.error(f"bot_task exception: {bot_task.exception()}")
                        # Cancel the wait and continue shutdown
                        wait_task.cancel()
                        try:
                            await wait_task
                        except asyncio.CancelledError:
                            pass
                    else:
                        # wait_task completed normally
                        await wait_task

                await monitored_wait()
                logger.info("wait_for_active_tasks() completed")

                # Step 3: Now stop the bot tasks
                logger.info("Stopping bot tasks...")

                # Cancel cleanup task
                cleanup_task.cancel()
                try:
                    await cleanup_task
                except asyncio.CancelledError:
                    logger.info("Cleanup task cancelled")

                # Cancel bot task
                if not bot_task.done():
                    bot_task.cancel()
                    try:
                        await bot_task
                    except asyncio.CancelledError:
                        logger.info("Bot task cancelled")

                # Step 4: Save state
                logger.info("About to save state...")
                await save_state(Bot)
                logger.info("State saved")

                logger.info("Shutdown complete")
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")
                import traceback
                logger.error(traceback.format_exc())

        else:
            # Bot task completed naturally (shouldn't happen normally)
            logger.info("Bot task completed")
            cleanup_task.cancel()

    except Exception as e:
        logger.error(f"Error in main loop: {e}")
        import traceback
        logger.error(traceback.format_exc())


# Manually create and manage event loop to have full control over signal handling
# (asyncio.run() installs its own signal handlers that interfere with ours)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
try:
    loop.run_until_complete(main())
finally:
    try:
        # Cancel all remaining tasks
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        # Wait for all tasks to finish cancelling
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        # Shutdown async generators
        loop.run_until_complete(loop.shutdown_asyncgens())
        # Shutdown default executor
        loop.run_until_complete(loop.shutdown_default_executor())
    finally:
        loop.close()
