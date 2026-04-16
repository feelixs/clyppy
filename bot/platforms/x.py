import re
import asyncio
from bot.classes import BaseClip, BaseMisc
from bot.types import DownloadResponse
from bot.errors import InvalidClipType, VideoTooLong
from bot.env import YT_DLP_USER_AGENT
from yt_dlp import YoutubeDL
from typing import Optional


class Xmisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Twitter"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the tweet ID/slug from various Twitter URL formats.
        Returns None if the URL is not a valid Twitter URL.
        """
        patterns = [
            r'(?:https?://)?(?:www\.)?twitter\.com/\w+/status/(\d+)',
            r'(?:https?://)?(?:www\.)?x\.com/\w+/status/(\d+)',
        ]
        if extended_url_formats:
            patterns.extend([r'(?:https?://)?(?:www\.)?fxtwitter\.com/\w+/status/(\d+)',
                             r'(?:https?://)?(?:www\.)?fixupx\.com/\w+/status/(\d+)'])

        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)
        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=True) -> 'Xclip':
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

        # Extract user from URL
        user_match = re.search(r'(?:(?:fx)?twitter\.com|(?:fixup)?x\.com)/(\w+)/status/', url)
        user = user_match.group(1) if user_match else None

        return Xclip(slug, user, self.cdn_client, tokens_used, duration)


class Xclip(BaseClip):
    def __init__(self, slug, user, cdn_client, tokens_used: int, duration: int):
        self._service = "twitter"
        self._url = f"https://x.com/{user}/status/{slug}"
        super().__init__(slug, cdn_client, tokens_used, duration)
        self._video_uploader_username = None
        self._cached_info = None

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True) -> DownloadResponse:
        # Extract uploader info first
        await self._extract_clip_info()

        # download & upload to clyppy.io
        self.logger.info(f"({self.id}) run dl_download()...")
        local_file = await super().dl_download(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies
        )
        if local_file.can_be_discord_uploaded:
            return DownloadResponse(
                remote_url=None,
                local_file_path=local_file.local_file_path,
                duration=local_file.duration,
                width=local_file.width,
                height=local_file.height,
                filesize=local_file.filesize,
                video_name=local_file.video_name,
                can_be_discord_uploaded=True,
                clyppy_object_is_stored_as_redirect=False,
                video_uploader_username=self._video_uploader_username
            )
        else:
            self.logger.info(f"({self.id}) hosting on clyppy.io...")
            response = await self.upload_to_clyppyio(local_file)
            response.video_uploader_username = self._video_uploader_username
            return response

    async def _extract_clip_info(self):
        """Extract uploader info from yt-dlp (cached to avoid rate limiting)"""
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
            self._video_uploader_username = info.get('uploader_id')
        except Exception as e:
            self.logger.warning(f"Failed to extract clip info for {self.id}: {e}")
