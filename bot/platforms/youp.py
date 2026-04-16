import re
from bot.types import DownloadResponse
from bot.errors import VideoTooLong, NoDuration
from bot.classes import BaseClip, BaseMisc
from typing import Optional


class YoupoMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "YouPorn"
        self.is_nsfw = True

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        pattern = r'(?:https?://)?(?:www\.)?youporn\.com/watch/([a-zA-Z0-9_-]+)'
        match = re.match(pattern, url)
        return match.group(1) if match else None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'YoupoClip':
        shortcode = self.parse_clip_url(url)
        if not shortcode:
            self.logger.info(f"Invalid URL: {url}")
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

        return YoupoClip(shortcode, self.cdn_client, tokens_used, duration)


class YoupoClip(BaseClip):
    def __init__(self, shortcode, cdn_client, tokens_used: int, duration: int):
        self._service = "youporn"
        self._id = shortcode
        super().__init__(shortcode, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return f"https://www.youporn.com/watch/{self._id}"

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False) -> DownloadResponse:
        self.logger.info(f"({self.id}) run dl_check_size(upload_if_large=True)...")
        return await super().dl_check_size(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies,
            upload_if_large=True
        )
