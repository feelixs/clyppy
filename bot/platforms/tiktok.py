import re
import os
import asyncio
import aiohttp
import aiofiles
from aiohttp import ClientSession
from bot.types import DownloadResponse, LocalFileInfo
from bot.errors import VideoTooLong, NoDuration, RemoteTimeoutError
from bot.classes import BaseClip, BaseMisc, get_video_details, is_discord_compatible
from typing import Optional, Tuple

# Ordered list of TikTok fixer providers we use to resolve a video_id to a
# direct TikTok CDN mp4 URL. Each entry is (host, path_template_with_{vid}),
# expected to 302 to https://*.tiktokcdn-*.com/.../X.mp4
#
# These two providers have INDEPENDENT backends (different scrapers + different
# CDN response chains), so when one is throttled or its scraper breaks the
# other usually keeps working. Probed in order; first 3xx wins.
# Add new hosts here as we discover them.
_TIKTOK_PROVIDERS = (
    ("tnktok.com",     "/generate/video/{vid}.mp4"),     # backend A — fast, current default
    ("www.tikwm.com",  "/video/media/play/{vid}.mp4"),   # backend B — independent fallback
)
_DISCORD_UA = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"


async def _resolve_via_providers(
    video_id: str,
    logger,
    session: aiohttp.ClientSession,
    per_provider_timeout: float = 4.0,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Try each provider until one returns a 3xx redirect to a TikTok CDN URL.

    Returns (provider_url, cdn_url, status) where status is:
      - "ok"        — got a usable redirect (cdn_url is set)
      - "exhausted" — all providers errored/timed out (cdn_url is None)
    """
    last_status = None
    for host, path_template in _TIKTOK_PROVIDERS:
        provider_url = f"https://{host}" + path_template.format(vid=video_id)
        try:
            async with session.get(
                provider_url,
                headers={"User-Agent": _DISCORD_UA},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=per_provider_timeout),
            ) as response:
                if response.status not in (301, 302, 307, 308):
                    last_status = response.status
                    logger.warning(
                        f"tiktok provider {host} returned {response.status} for {video_id}"
                    )
                    continue

                cdn_url = response.headers.get("Location")
                if not cdn_url:
                    logger.warning(f"tiktok provider {host} 3xx with no Location for {video_id}")
                    continue

                logger.info(f"tiktok provider {host} resolved {video_id} (status {response.status})")
                return provider_url, cdn_url, "ok"

        except asyncio.TimeoutError:
            logger.warning(f"tiktok provider {host} timed out for {video_id}")
            continue
        except Exception as e:
            logger.warning(f"tiktok provider {host} errored for {video_id}: {e}")
            continue

    logger.error(
        f"tiktok: all {len(_TIKTOK_PROVIDERS)} providers failed for {video_id} "
        f"(last upstream status: {last_status})"
    )
    return None, None, "exhausted"


class TikTokMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "TikTok"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the TikTok video ID from various URL formats.
        Returns None if the URL is not a valid TikTok video URL.
        """
        # Mathes URLs like:
        # - https://www.tiktok.com/@username/video/123456789
        # - https://m.tiktok.com/video/123456789
        # - https://vm.tiktok.com/video/123456789
        pattern = [
            r'(?:https?://)?(?:www\.|vm\.|m\.)?tiktok\.com/(?:@[^/]+/)?video/(\d+)',
            r'(?:https?://)?(?:www\.)?tiktok\.com/t/([A-Za-z0-9]+)/?',
            r'(?:https?://)?(?:vt\.|vm\.)?tiktok\.com/([A-Za-z0-9]+)/?'
        ]
        for p in pattern:
            match = re.match(p, url)
            if match:
                return match.group(1)
        return None

    async def _resolve_url(self, shorturl) -> Tuple[str, str, str]:
        # retrieve actual url
        self.logger.info(f'Retrieving actual url from shortened url {shorturl}')
        async with ClientSession() as session:
            async with session.get(shorturl) as response:
                v = r'"canonical":"https:\\u002F\\u002Fwww\.tiktok\.com\\u002F@([\w.]+)\\u002Fvideo\\u002F(\d+)"'
                txt = await response.text()
                match = re.search(v, txt)
                if match is None:
                    self.logger.info(f"(video) Invalid TikTok URL: {shorturl} (match was None)")
                    raise NoDuration
                else:
                    user = match.group(1)
                    video_id = match.group(2)
                    return f"https://www.tiktok.com/@{user}/video/{video_id}", video_id, user

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'TikTokClip':
        video_id = self.parse_clip_url(url)
        if not video_id:
            self.logger.info(f"Invalid TikTok URL: {url}")
            raise NoDuration

        short_url_patterns = [
            r'(?:https?://)?(?:www\.)?tiktok\.com/t/([A-Za-z0-9]+)/?',
            r'(?:https?://)?(?:vt\.|vm\.)?tiktok\.com/([A-Za-z0-9]+)/?'
        ]

        if any(re.match(pattern, url) for pattern in short_url_patterns):
            url, video_id, user = await self._resolve_url(url)
        else:
            # Extract username if available
            user_match = re.search(r'tiktok\.com/@([^/]+)/', url)
            user = user_match.group(1) if user_match else None
            if user is None:
                self.logger.info(f"Invalid TikTok URL: {url} (user was None)")
                raise NoDuration

        return TikTokClip(video_id, user, self.cdn_client, 0, 0)


class TikTokClip(BaseClip):
    def __init__(self, video_id, user, cdn_client, tokens_used: int, duration: int):
        self._service = "tiktok"
        self._user = user
        self._video_id = video_id
        super().__init__(video_id, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        if self._user:
            return f"https://www.tiktok.com/@{self._user}/video/{self._video_id}"
        return f"https://www.tiktok.com/video/{self._video_id}"

    @property
    def clyppy_url(self) -> str:
        return f"https://clyppy.io/e/{self.clyppy_id}"

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False, extra_opts=None) -> DownloadResponse:
        """
        Create a redirect-based embed for TikTok.

        Probes the provider list and uses the first one that successfully
        resolves the video_id — so a single provider being throttled or having
        its scraper broken doesn't take TikTok embeds offline.
        """
        async with aiohttp.ClientSession() as session:
            provider_url, _, status = await _resolve_via_providers(
                self._video_id, self.logger, session, per_provider_timeout=3.0
            )

        if status != "ok" or not provider_url:
            self.logger.error(f"({self.id}) all TikTok providers failed for embed")
            raise RemoteTimeoutError

        self.logger.info(f"({self.id}) Creating redirect embed via {provider_url}")
        return DownloadResponse(
            remote_url=provider_url,
            local_file_path=None,
            duration=self.duration,
            width=0,
            height=0,
            filesize=0,
            video_name=None,
            can_be_discord_uploaded=False,
            clyppy_object_is_stored_as_redirect=True,
        )

    async def dl_download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False, extra_opts=None) -> Optional[LocalFileInfo]:
        """
        Download TikTok video bytes through the provider chain.

        Each provider 302s to a TikTok CDN mp4. We probe in order and download
        from the first one that resolves successfully.
        """
        if os.path.isfile(filename):
            self.logger.info("file already exists! returning...")
            return get_video_details(filename)

        try:
            async with aiohttp.ClientSession() as session:
                _, cdn_url, status = await _resolve_via_providers(
                    self._video_id, self.logger, session, per_provider_timeout=5.0
                )

                if status != "ok" or not cdn_url:
                    return None

                self.logger.info(f"({self.id}) Got CDN URL: {cdn_url[:100]}...")

                # tiktokcdn-*.com URLs frequently stall for non-app clients: the
                # connection opens but the body never (or barely) flows. Without an
                # explicit timeout this falls back to aiohttp's 300s default and the
                # download coroutine blocks for ~5 min. sock_read fails fast on a
                # stalled mid-stream body.
                cdn_timeout = aiohttp.ClientTimeout(total=60, sock_connect=5, sock_read=15)
                async with session.get(cdn_url, headers={"User-Agent": _DISCORD_UA}, timeout=cdn_timeout) as response:
                    if response.status != 200:
                        self.logger.error(f"Failed to download from CDN, status {response.status}")
                        return None

                    async with aiofiles.open(filename, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            if os.path.exists(filename):
                d = get_video_details(filename)
                d.video_name = "TikTok Video"
                if is_discord_compatible(d.filesize) and can_send_files:
                    self.logger.info(f"{self.id} can be uploaded to discord...")
                    d.can_be_discord_uploaded = True
                return d

            self.logger.error(f"dl_download error: Could not find file after download")
            return None

        except Exception as e:
            self.logger.error(f"tiktok download error: {str(e)}")
            return None
