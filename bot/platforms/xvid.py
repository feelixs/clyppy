import re
from bot.types import DownloadResponse
from bot.errors import VideoTooLong, NoDuration
from bot.classes import BaseClip, BaseMisc
from yt_dlp import YoutubeDL
from bot.env import YT_DLP_USER_AGENT
from typing import Optional
import asyncio


class XvidMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Xvideos"
        self.is_nsfw = True

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        # Pattern to match xvideos URLs like:
        # - https://www.xvideos.com/video.otkaofv96c8/39997451/0/title_here
        # - https://www.xvideos.com/video.uculeohe76f/drake
        pattern = r'(?:https?://)?(?:www\.)?xvideos\.com/video\.([a-z0-9]+)(?:/.*)?'
        match = re.match(pattern, url)
        return match.group(1) if match else None

    @staticmethod
    def get_vid_id(url: str) -> Optional[str]:
        # Extract the numeric ID if present in the URL
        pattern = r'(?:https?://)?(?:www\.)?xvideos\.com/video\.[a-z0-9]+/([0-9]+)(?:/.*)?'
        match = re.match(pattern, url)
        return match.group(1) if match else None

    @staticmethod
    def get_title(url: str) -> Optional[str]:
        # Extract the title part if present
        pattern = r'(?:https?://)?(?:www\.)?xvideos\.com/video\.[a-z0-9]+(?:/[0-9]+(?:/[0-9]+)?)?/([^/?]+)'
        match = re.match(pattern, url)
        return match.group(1) if match else None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'XvidClip':
        first = self.parse_clip_url(url)
        if not first:
            self.logger.info(f"Invalid URL: {url}")
            raise NoDuration

        second = self.get_vid_id(url)
        title = self.get_title(url)

        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        return XvidClip(first, second, title, self.cdn_client, tokens_used, duration)


class XvidClip(BaseClip):
    def __init__(self, first, second=None, title=None, cdn_client=None, tokens_used: int = 0, duration: int = 0):
        self._service = "xvideos"
        self._first = first
        self._second = second
        self._title = title

        # For internal ID tracking
        self._id = first if second is None or second == "0" else f"{first}/{second}"
        super().__init__(self._id, cdn_client, tokens_used, duration)
        self._thumbnail_url = None

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        if self._title:
            if self._second and self._second != "0":
                return f"https://xvideos.com/video.{self._first}/{self._second}/0/{self._title}"
            else:
                return f"https://xvideos.com/video.{self._first}/{self._title}"
        else:
            # Fallback if no title
            if self._second and self._second != "0":
                return f"https://xvideos.com/video.{self._first}/{self._second}"
            else:
                return f"https://xvideos.com/video.{self._first}"

    @property
    def clyppy_url(self) -> str:
        """Use /e/ path for redirect-based embeds"""
        return f"https://clyppy.io/e/{self.clyppy_id}"

    async def get_thumbnail(self):
        return self._thumbnail_url

    async def download(self, filename=None, dlp_format='mp4-high', can_send_files=False, cookies=False, extra_opts=None) -> DownloadResponse:
        """
        Extract direct CDN URL and create a redirect-based embed.
        No video download needed - Discord follows the redirect to the CDN.
        """
        self.logger.info(f"({self.id}) run dl_check_size(upload_if_large=False)...")
        dl = await super().dl_check_size(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies,
            upload_if_large=False
        )
        if dl is not None and dl.can_be_discord_uploaded:
            return dl

        # Extract video info and CDN URL
        cdn_url, info = await self._extract_cdn_url(dlp_format)
        self.logger.info(f"({self.id}) Extracted CDN URL")
        return DownloadResponse(
            remote_url=cdn_url,
            local_file_path=None,
            duration=info.get('duration') or 0,
            width=dict(info).get('width') or 0,
            height=dict(info).get('height') or 0,
            filesize=dict(info).get('filesize') or 0,
            video_name=info.get('title'),
            can_be_discord_uploaded=False,
            clyppy_object_is_stored_as_redirect=True
        )

    async def _extract_cdn_url(self, dlp_format='mp4-high'):
        """
        Extract the CDN URL without downloading.
        Returns: (cdn_url, info_dict)
        """
        ydl_opts = {
            'format': dlp_format,
            'quiet': True,
            'no_warnings': True,
            'user_agent': YT_DLP_USER_AGENT
        }

        def extract():
            with YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(self.url, download=False)

        info = await asyncio.get_event_loop().run_in_executor(None, extract)

        self._thumbnail_url = info.get('thumbnail')

        cdn_url = info.get('url')
        if not cdn_url:
            raise Exception("Failed to extract CDN URL from Xvideos video")

        return cdn_url, info
