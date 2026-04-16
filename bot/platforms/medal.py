from bot.classes import BaseClip, DownloadResponse
from bot.classes import BaseMisc
from yt_dlp import YoutubeDL
from bot.env import YT_DLP_USER_AGENT
from typing import Optional
import asyncio
import re


class MedalMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Medal"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Parses a Medal.tv clip URL to extract just the clip ID.

        Example:
        "https://medal.tv/games/game/clips/abc123/xyz789" -> "abc123"
        "https://medal.tv/clips/abc123" -> "abc123"
        """
        patterns = [
            # Full game path pattern
            r'^(?:https?://)?(?:www\.)?medal\.tv/games/[\w-]+/clips/([\w-]+)',
            # Short clip pattern
            r'^(?:https?://)?(?:www\.)?medal\.tv/clips/([\w-]+)'
        ]

        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)
        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'MedalClip':
        # todo run is_shortform here
        slug = self.parse_clip_url(url)
        return MedalClip(slug, self.cdn_client, 0)


class MedalClip(BaseClip):
    def __init__(self, slug, cdn_client, tokens_used: int):
        self._service = "medal"
        self._url = f"https://medal.tv/clips/{slug}"
        # pass dummy duration because we haven't added is_valid (is_shortform) for medal links
        super().__init__(slug, cdn_client, tokens_used, 0)
        self._thumbnail_url = None

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url

    @property
    def clyppy_url(self) -> str:
        """Use /e/ path for Medal redirect-based embeds"""
        return f"https://clyppy.io/e/{self.clyppy_id}"

    async def get_thumbnail(self):
        return self._thumbnail_url

    async def download(self, filename=None, dlp_format='best', can_send_files=False, cookies=False, extra_opts=None) -> DownloadResponse:
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
            raise Exception("Failed to extract CDN URL from Medal clip")

        return cdn_url, info
