import re
from bot.types import DownloadResponse
from bot.classes import BaseClip, BaseMisc
from bot.errors import VideoTooLong, NoDuration
from typing import Optional


class DailymotionMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Dailymotion"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the Dailymotion video ID from various URL formats.
        Returns None if the URL is not a valid Dailymotion video URL.
        """
        # Matches URLs like:
        # - https://www.dailymotion.com/video/x9es1fa
        # - https://dai.ly/x9es1fa
        # - https://www.dailymotion.com/embed/video/x9es1fa

        patterns = [
            r'(?:https?://)?(?:www\.)?dailymotion\.com/video/([a-zA-Z0-9]+)(?:/|$|\?)',  # Standard format
            r'(?:https?://)?dai\.ly/([a-zA-Z0-9]+)(?:/|$|\?)',  # Shortened format
            r'(?:https?://)?(?:www\.)?dailymotion\.com/embed/video/([a-zA-Z0-9]+)(?:/|$|\?)'  # Embed format
        ]

        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)

        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'DailymotionClip':
        video_id = self.parse_clip_url(url)
        if not video_id:
            self.logger.info(f"Invalid Dailymotion URL: {url}")
            raise NoDuration

        # Verify video length (you might want to adjust this for Dailymotion's limits)
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        return DailymotionClip(video_id, self.cdn_client, tokens_used, duration)


class DailymotionClip(BaseClip):
    def __init__(self, video_id, cdn_client, tokens_used: int, duration: int):
        self._service = "dailymotion"
        self._video_id = video_id
        super().__init__(video_id, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return f"https://www.dailymotion.com/video/{self._video_id}"

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False) -> DownloadResponse:
        self.logger.info(f"({self.id}) run dl_check_size(upload_if_large=True)...")
        return await super().dl_check_size(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies,
            upload_if_large=True
        )
