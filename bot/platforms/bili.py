import re
from bot.classes import BaseClip, BaseMisc
from bot.errors import VideoTooLong, NoDuration
from bot.types import DownloadResponse
from typing import Optional


class BiliMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Bilibili"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts Bilibili video ID from various URL formats.
        Supports both av/BV IDs and mobile URLs.
        """
        # Matches URLs like:
        # - https://www.bilibili.com/video/BV1GJ411x7hx
        # - https://m.bilibili.com/video/BV1GJ411x7hx
        # - https://b23.tv/BV1GJ411x7hx
        # - https://www.bilibili.com/video/av79877423
        patterns = [
            r'(?:https?://)?(?:www\.|m\.)?bilibili\.com/video/((?:av|BV)\w+)',
            r'(?:https?://)?b23\.tv/((?:av|BV)\w+)',
            r'(?:https?://)?(?:www\.)?bilibili\.com/video/((?:av|BV)\w+)'
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'BiliClip':
        video_id = self.parse_clip_url(url)
        if not video_id:
            self.logger.info(f"Invalid Bilibili URL: {url}")
            raise NoDuration

        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        return BiliClip(video_id, self.cdn_client, tokens_used, duration)


class BiliClip(BaseClip):
    def __init__(self, video_id, cdn_client, tokens_used: int, duration: int):
        self._service = "bilibili"
        self._video_id = video_id
        super().__init__(video_id, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self._video_id}"

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False, ydl_opts=None) -> DownloadResponse:
        self.logger.info(f"({self.id}) run dl_check_size()...")
        dl = await super().dl_check_size(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies
        )
        if dl is not None:
            return dl

        return await super().download(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies
        )
