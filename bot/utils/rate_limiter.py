import asyncio
import time
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    A rate limiter that ensures only one request goes through at a time,
    with a configurable delay between requests.

    This prevents simultaneous requests from all firing at once -
    each request must wait for the lock and the delay.
    """

    def __init__(self, delay_seconds: float = 5.0, name: str = "default"):
        self.delay_seconds = delay_seconds
        self.name = name
        self._lock = asyncio.Lock()
        self._last_request_time = 0.0

    async def acquire(self):
        """
        Acquire the rate limiter. This will:
        1. Wait for any other request to finish (via lock)
        2. Wait for the delay period since the last request
        """
        async with self._lock:
            now = time.monotonic()
            time_since_last = now - self._last_request_time

            if time_since_last < self.delay_seconds:
                wait_time = self.delay_seconds - time_since_last
                logger.info(f"[RateLimiter:{self.name}] Waiting {wait_time:.2f}s before next request")
                await asyncio.sleep(wait_time)

            self._last_request_time = time.monotonic()
            logger.debug(f"[RateLimiter:{self.name}] Request acquired")

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# Global rate limiters for different platforms
# YouTube: 5 second delay between requests to avoid rate limiting
youtube_rate_limiter = RateLimiter(delay_seconds=5.0, name="youtube")
