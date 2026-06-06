import os
import time
import logging
import psutil
from interactions import Extension, Task, IntervalTrigger, listen
from interactions.api.events import Startup
from bot.env import VERSION
from bot.io.io import post_health_snapshot
from bot.health import get_rate_limit_snapshot, reset_rate_limit_counts, PROCESS_START_TIME

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_MINUTES = 1


class Heartbeat(Extension):
    def __init__(self, bot):
        self.bot = bot
        self._task = Task(self._send_heartbeat, IntervalTrigger(minutes=HEARTBEAT_INTERVAL_MINUTES))

    @listen(Startup)
    async def on_startup(self):
        self._task.start()
        logger.info("Heartbeat task started")

    async def _send_heartbeat(self):
        if not self.bot.is_ready:
            return
        try:
            proc = psutil.Process(os.getpid())
            vm = psutil.virtual_memory()
            disk = psutil.disk_usage('/')

            ffmpeg_waiters = 0
            try:
                from bot.tools import converter as conv_mod
                ffmpeg_waiters = len(conv_mod._semaphore._waiters or [])
            except (AttributeError, ImportError, TypeError):
                pass

            dl_waiters = 0
            try:
                dl_waiters = len(self.bot.base_embedder.platform._download_manager._semaphore._waiters or [])
            except (AttributeError, TypeError):
                pass

            rl_snapshot = get_rate_limit_snapshot()
            reset_rate_limit_counts()

            queue_counts = (0, 0)
            try:
                queue_counts = self.bot.task_queue.get_task_count()
            except AttributeError:
                pass

            payload = {
                # concurrency — use getattr in case setup() hasn't run yet on first tick
                "active_embeds": len(getattr(self.bot, 'currently_embedding', None) or []),
                "active_downloads": len(getattr(self.bot, 'currently_downloading', None) or []),
                "active_embed_users": len(getattr(self.bot, 'currently_embedding_users', None) or []),
                "ffmpeg_semaphore_waiters": ffmpeg_waiters,
                "dl_semaphore_waiters": dl_waiters,
                # rate limits per platform
                "rate_limits": rl_snapshot,
                # system resources
                "ram_used_mb": round(proc.memory_info().rss / 1024 ** 2, 1),
                "ram_host_percent": vm.percent,
                "disk_used_gb": round(disk.used / 1024 ** 3, 2),
                "disk_total_gb": round(disk.total / 1024 ** 3, 2),
                "disk_percent": disk.percent,
                # process health
                "uptime_seconds": int(time.time() - PROCESS_START_TIME),
                "task_queue_quickembeds": queue_counts[0],
                "task_queue_slash": queue_counts[1],
                "guilds_count": len(getattr(self.bot, 'guilds', None) or []),
                # bot version (clyppy-web caches the last pushed value for portfolio_health)
                "version": VERSION,
            }

            await post_health_snapshot(payload, logger)
        except Exception as e:
            logger.error(f"[heartbeat] Failed to send heartbeat: {e}")
