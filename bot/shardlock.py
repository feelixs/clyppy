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
        # The sync open() + fcntl.flock() syscalls can stall the event loop
        # under filesystem load or heavy lock contention. Offload the per-pass
        # slot scan to a worker thread; sleep between passes asynchronously.
        while True:
            result = await asyncio.to_thread(self._try_acquire_any_slot)
            if result is not None:
                self._file, self._acquired_slot = result
                return self
            await asyncio.sleep(0.1)

    def _try_acquire_any_slot(self):
        """Blocking helper: scan slots and return (file, slot) on success."""
        for slot in range(self.max_concurrent):
            f = open(self._slot_path(slot), 'w')
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return f, slot
            except BlockingIOError:
                f.close()
        return None

    async def __aexit__(self, *args):
        if self._file:
            # Small delay before releasing to space out requests
            await asyncio.sleep(self.min_interval)
            f = self._file
            self._file = None
            self._acquired_slot = None
            await asyncio.to_thread(self._release_sync, f)

    @staticmethod
    def _release_sync(f):
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()
