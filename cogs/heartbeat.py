import os
import time
import logging
import psutil
from interactions import Extension, Task, IntervalTrigger, listen, slash_command, SlashContext, Embed
from interactions.api.events import Startup
from bot.env import VERSION
from bot.io.io import post_health_snapshot
from bot.health import get_rate_limit_snapshot, reset_rate_limit_counts, PROCESS_START_TIME
from bot.types import COLOR_GREEN, COLOR_RED

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_MINUTES = 1

# A gateway heartbeat normally acks in well under a second; anything above
# this is a degraded (but alive) connection.
SHARD_DEGRADED_LATENCY_SEC = 1.0


class Heartbeat(Extension):
    def __init__(self, bot):
        self.bot = bot
        self._task = Task(self._send_heartbeat, IntervalTrigger(minutes=HEARTBEAT_INTERVAL_MINUTES))

    @listen(Startup)
    async def on_startup(self):
        self._task.start()
        logger.info("Heartbeat task started")

    def _collect_shard_health(self) -> list[dict]:
        """Snapshot per-shard gateway health.

        A shard whose websocket never connected (or whose gateway object is
        gone) reports latency inf — surfaced as connected=False. Latency is
        None in that case so the payload stays JSON-clean.
        """
        shards = []
        for state in getattr(self.bot, 'shards', None) or []:
            try:
                latency = state.latency  # seconds; inf if no heartbeat ack yet
            except (AttributeError, TypeError):
                latency = float('inf')
            connected = latency != float('inf')
            try:
                guild_count = len(self.bot.get_shards_guild(state.shard_id))
            except (AttributeError, TypeError):
                guild_count = None
            shards.append({
                "id": state.shard_id,
                "connected": connected,
                "degraded": connected and latency >= SHARD_DEGRADED_LATENCY_SEC,
                "latency_ms": round(latency * 1000, 1) if connected else None,
                "guilds": guild_count,
            })
        return shards

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
                # per-shard gateway health — a dead shard means the bot shows
                # offline (and quickembeds stop) for that shard's guilds only
                "shards": self._collect_shard_health(),
                # bot version (clyppy-web caches the last pushed value for portfolio_health)
                "version": VERSION,
            }

            await post_health_snapshot(payload, logger)
        except Exception as e:
            logger.error(f"[heartbeat] Failed to send heartbeat: {e}")

    @slash_command(name="status", description="Check Clyppy's connection health across all shards")
    async def status_cmd(self, ctx: SlashContext):
        shards = self._collect_shard_health()
        all_connected = all(s["connected"] for s in shards) if shards else False

        # which shard serves the server this command was run in
        my_shard_id = None
        if ctx.guild_id:
            try:
                my_shard_id = self.bot.get_shard_id(ctx.guild_id)
            except (AttributeError, TypeError):
                pass

        lines = []
        for s in shards:
            if not s["connected"]:
                icon, detail = "🔴", "disconnected"
            elif s["degraded"]:
                icon, detail = "🟡", f"{s['latency_ms']:.0f}ms (slow)"
            else:
                icon, detail = "🟢", f"{s['latency_ms']:.0f}ms"
            guilds = f" · {s['guilds']:,} servers" if s["guilds"] is not None else ""
            marker = " ← this server" if s["id"] == my_shard_id else ""
            lines.append(f"{icon} **Shard {s['id']}** — {detail}{guilds}{marker}")

        uptime_start = int(PROCESS_START_TIME)
        total_guilds = len(getattr(self.bot, 'guilds', None) or [])
        embed = Embed(
            title="Clyppy Status",
            description=(
                f"Online since <t:{uptime_start}:R> · v{VERSION} · {total_guilds:,} servers\n\n"
                + "\n".join(lines)
            ),
            color=COLOR_GREEN if all_connected else COLOR_RED,
        )
        if not all_connected:
            embed.description += (
                "\n\nA 🔴 shard means Clyppy appears offline (and auto-embeds pause) "
                "in that shard's servers — we're on it, this usually self-heals in a few minutes."
            )
        await ctx.send(embed=embed)
