from bot.errors import InvalidClipType, VideoTooLong
from bot.classes import BaseClip, BaseMisc
from bot.types import DownloadResponse
from bot.env import YT_DLP_USER_AGENT
from bot.utils.rate_limiter import youtube_rate_limiter
from yt_dlp import YoutubeDL
from typing import Optional
import asyncio
import re


class YtMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "YouTube"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
            Extracts the video ID from a YouTube URL if present.
            Works with all supported URL formats.
        """
        # Common YouTube URL patterns
        patterns = [
            r'^(?:https?://)?(?:(?:www|m|music)\.)?(?:youtube\.com/(?:[^/]+/.+/|(?:v|e(?:mbed)?)/|.*[?&]v=)|youtu\.be/)([^"&?/ ]{11})',
            r'^(?:https?://)?(?:(?:www|m|music)\.)?(?:youtube\.com/shorts/)([^"&?/ ]{11})',
            r'^(?:https?://)?(?:(?:www|m|music)\.)?youtube\.com/clip/([^"&?/ ]{11})'
        ]
        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)
        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=True) -> 'YtClip':
        slug = self.parse_clip_url(url)
        if slug is None:
            raise InvalidClipType
        if "&list=" in url:
            url = url.split("&list=")[0]
        
        # Rate limit the is_shortform check
        await youtube_rate_limiter.acquire()
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")
        return YtClip(slug, bool(re.search(r'youtube\.com/shorts/', url)), self.cdn_client, tokens_used, duration, prefetched_file=self._prefetched_file)


class YtClip(BaseClip):
    def __init__(self, slug, short, cdn_client, tokens_used: int, duration: int, prefetched_file=None):
        self._service = "youtube"
        self._prefetched_file = prefetched_file
        if short:
            self._url = f"https://youtube.com/shorts/{slug}"
        else:
            self._url = f"https://youtube.com/watch/?v={slug}"
        super().__init__(slug, cdn_client, tokens_used, duration)
        self._broadcaster_username = None
        self._cached_info = None

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True, extra_opts=False) -> DownloadResponse:
        self.logger.info(f"({self.id}) run dl_check_size(upload_if_large=True)...")

        # Extract channel info first (rate limited)
        await self._extract_clip_info()

        # Rate limit before the main download
        await youtube_rate_limiter.acquire()
        response = await super().dl_check_size(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies,
            upload_if_large=True,
            prefetched_file=self._prefetched_file
        )

        response.video_uploader_username = self._broadcaster_username
        return response

    async def _extract_clip_info(self):
        """Extract channel info from yt-dlp (cached to avoid rate limiting)"""
        if self._cached_info is not None:
            return

        # Rate limit before extracting info
        await youtube_rate_limiter.acquire()

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'user_agent': YT_DLP_USER_AGENT
        }

        def extract():
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                return info

        try:
            info = await asyncio.get_event_loop().run_in_executor(None, extract)
            self._cached_info = info
            self._broadcaster_username = info.get('channel')
        except Exception as e:
            self.logger.warning(f"Failed to extract clip info for {self.id}: {e}")
