import re
from bot.types import DownloadResponse
from bot.errors import VideoTooLong, NoDuration
from bot.classes import BaseClip, BaseMisc
from typing import Optional


class VimeoMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Vimeo"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the main video ID from a Vimeo URL.
        Works with all supported URL formats.
        """
        patterns = [
            r'^(?:https?://)?(?:www\.)?vimeo\.com/(\d+)(?:/[a-zA-Z0-9]+)?(?:\?|$)',
            r'^(?:https?://)?(?:www\.)?vimeo\.com/[\w-]+/(\d+)(?:/[a-zA-Z0-9]+)?(?:\?|$)',
            r'^(?:https?://)?(?:www\.)?vimeo\.com/channels/[\w-]+/(\d+)(?:/[a-zA-Z0-9]+)?(?:\?|$)'
        ]
        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def get_clip_hash(url: str) -> Optional[str]:
        """
        Extracts the secondary hash ID from a Vimeo URL if present.
        """
        patterns = [
            r'^(?:https?://)?(?:www\.)?vimeo\.com/\d+/([a-zA-Z0-9]+)(?:\?|$)',
            r'^(?:https?://)?(?:www\.)?vimeo\.com/[\w-]+/\d+/([a-zA-Z0-9]+)(?:\?|$)',
            r'^(?:https?://)?(?:www\.)?vimeo\.com/channels/[\w-]+/\d+/([a-zA-Z0-9]+)(?:\?|$)'
        ]
        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)
        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'VimeoClip':
        video_id = self.parse_clip_url(url)
        video_hash = self.get_clip_hash(url)
        if not video_id:
            self.logger.info(f"Invalid Vimeo URL: {url}")
            raise NoDuration

        # Verify video length
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        return VimeoClip(video_id, video_hash, self.cdn_client, tokens_used, duration)


class VimeoClip(BaseClip):
    def __init__(self, video_id, video_hash=None, cdn_client=None, tokens_used: int = 0, duration: int = 0):
        self._service = "vimeo"
        if video_hash:
            self._video_id = f'{video_id}/{video_hash}'
            self._url = f"https://vimeo.com/{self._video_id}"
        else:
            self._video_id = video_id
            self._url = f"https://vimeo.com/{self._video_id}"
        super().__init__(self._video_id, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False) -> DownloadResponse:
        self.logger.info(f"({self.id}) run dl_check_size(upload_if_large=True)...")
        return await super().dl_check_size(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies,
            upload_if_large=True
        )
