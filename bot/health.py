import time

# Set once at module import — imported early in main.py
PROCESS_START_TIME = time.time()

# Rate-limit counters keyed by platform name (e.g. "youtube", "instagram").
# Written and read from the single async event loop — no lock needed.
_rl_counts: dict[str, int] = {}
_rl_last_seen: dict[str, float] = {}


def record_rate_limit(platform: str) -> None:
    _rl_counts[platform] = _rl_counts.get(platform, 0) + 1
    _rl_last_seen[platform] = time.time()


def get_rate_limit_snapshot() -> dict:
    now = time.time()
    return {
        p: {
            "count": _rl_counts[p],
            "last_seen_secs_ago": int(now - _rl_last_seen[p]),
        }
        for p in _rl_counts
    }


def reset_rate_limit_counts() -> None:
    """Clear per-window counts. last_seen is intentionally preserved so the
    operator can see "YouTube was rate-limited 8 minutes ago" across windows."""
    _rl_counts.clear()
