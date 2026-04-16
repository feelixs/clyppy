import re
from bot.classes import BaseClip, BaseMisc
from bot.types import DownloadResponse
from bot.errors import InvalidClipType, VideoTooLong
from yt_dlp import YoutubeDL
from bot.env import YT_DLP_USER_AGENT
from typing import Optional
import asyncio


class R34Misc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Rule34Video"
        self.is_nsfw = True
        self.dl_timeout_secs = 180

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        pattern = r'(?:https?://)?(?:www\.)?rule34video\.co/watch/([a-zA-Z0-9_-]+)(?:/|$|\?)'
        match = re.match(pattern, url)
        return match.group(1) if match else None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=True) -> 'R34clip':
        slug = self.parse_clip_url(url, extended_url_formats)
        if slug is None:
            raise InvalidClipType
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        return R34clip(slug, self.cdn_client, tokens_used, duration)


class R34clip(BaseClip):
    def __init__(self, slug, cdn_client, tokens_used: int, duration: int):
        self._service = "rule34"
        self._url = f"https://rule34video.co/watch/{slug}/"
        super().__init__(slug, cdn_client, tokens_used, duration)
        self._thumbnail_url = None

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url

    @property
    def clyppy_url(self) -> str:
        """Use /e/ path for redirect-based embeds"""
        return f"https://clyppy.io/e/{self.clyppy_id}"

    async def get_thumbnail(self):
        return self._thumbnail_url

    async def download(self, filename=None, dlp_format='best', can_send_files=False, cookies=True, extra_opts=None) -> DownloadResponse:
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

    async def _extract_cdn_url(self, dlp_format='best'):
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
            raise Exception("Failed to extract CDN URL from Rule34 video")

        return cdn_url, info
