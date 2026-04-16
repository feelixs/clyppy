from bot.classes import BaseClip, DownloadResponse
from bot.io.cdn import CdnSpacesClient
from bot.classes import BaseMisc
from bot.env import YT_DLP_USER_AGENT
from yt_dlp import YoutubeDL
from typing import Optional
import asyncio
import re


class KickMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Kick"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the clip ID from a Kick URL if present.
        Works with all supported URL formats.
        """
        patterns = [
            r'^(?:https?://)?(?:www\.)?kick\.com/[a-zA-Z0-9_-]+/clips/clip_([a-zA-Z0-9]+)',
            r'^(?:https?://)?(?:www\.)?kick\.com/[a-zA-Z0-9_-]+\?clip=clip_([a-zA-Z0-9]+)'
        ]
        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def get_clip_user(url: str) -> Optional[str]:
        patterns = [
            r'^(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)/clips/clip_[a-zA-Z0-9]+',
            r'^(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)\?clip=clip_[a-zA-Z0-9]+'
        ]
        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)
        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'KickClip':
        slug, user = self.parse_clip_url(url), self.get_clip_user(url)
        return KickClip(slug, user, self.cdn_client, 0, self._prefetched_file)


class KickClip(BaseClip):
    def __init__(self, slug, user, cdn_client: CdnSpacesClient, tokens_used: int, prefetched_file=None):
        self._service = "kick"
        self._url = f"https://kick.com/{user}/clips/clip_{slug}"
        self.user = user
        self._prefetched_file = prefetched_file
        # pass dummy duration because we know kick clips will never need to use vip tokens
        super().__init__(slug, cdn_client, tokens_used, 0)
        self._broadcaster_username = None
        self._video_uploader_username = None
        self._cached_info = None

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url

    async def download(self, filename: str = None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True, useragent=None, ydl_opts=None) -> DownloadResponse:
        self.logger.info(f"({self.id}) run dl_check_size(upload_if_large=True)...")

        # Extract channel/uploader info before downloading
        await self._extract_clip_info()

        response = await super().dl_check_size(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies,
            upload_if_large=True
        )

        response.broadcaster_username = self._broadcaster_username
        response.video_uploader_username = self._video_uploader_username
        return response

    async def _extract_clip_info(self):
        """Extract channel and uploader info from yt-dlp (cached to avoid rate limiting)"""
        if self._cached_info is not None:
            return

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
            self._video_uploader_username = info.get('uploader')
        except Exception as e:
            self.logger.warning(f"Failed to extract clip info for {self.id}: {e}")
