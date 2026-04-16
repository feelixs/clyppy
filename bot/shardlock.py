import fcntl
import asyncio
from pathlib import Path


class ShardLock:
    """File-lock based semaphore for cross-process synchronization.

    Supports multiple concurrent slots (counting semaphore).
    """

    _locks_dir = Path("/tmp/clyppybot_locks")

    def __init__(self, platform: str, max_concurrent: int = 1, min_interval: float = 0.5):
        self.platform = platform
        self.max_concurrent = max_concurrent
        self.min_interval = min_interval
        self._acquired_slot = None
        self._file = None

        # Ensure locks directory exists
        self._locks_dir.mkdir(exist_ok=True)

    @classmethod
    def get(cls, platform: str, max_concurrent: int = 1, min_interval: float = 0.5) -> 'ShardLock':
        """Create a new lock instance (each async context needs its own state)."""
        return cls(platform, max_concurrent, min_interval)

    def _slot_path(self, slot: int) -> Path:
        return self._locks_dir / f"{self.platform}_{slot}.lock"

    async def __aenter__(self):
        # Try to acquire any available slot
        while True:
            for slot in range(self.max_concurrent):
                lock_path = self._slot_path(slot)
                f = None
                try:
                    f = open(lock_path, 'w')
                    # Try non-blocking lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    # Got it
                    self._file = f
                    self._acquired_slot = slot
                    return self
                except BlockingIOError:
                    # Slot busy, try next
                    if f:
                        f.close()
                    continue
            # All slots busy, wait and retry
            await asyncio.sleep(0.1)

    async def __aexit__(self, *args):
        if self._file:
            # Small delay before releasing to space out requests
            await asyncio.sleep(self.min_interval)
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None
            self._acquired_slot = None
