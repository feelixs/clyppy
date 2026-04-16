import re
from bot.errors import VideoTooLong, NoDuration, UnsupportedError
from bot.classes import BaseClip, BaseMisc
from bot.types import DownloadResponse
from typing import Optional


class FacebookMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Facebook"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        pattern = r'(?:https?://)?(?:www\.)?facebook\.com/([a-zA-Z0-9_-]+)(?:/|$|\?)'
        match = re.match(pattern, url)
        return match.group(1) if match else None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> Optional['FacebookClip']:
        raise UnsupportedError  # incompatible because facebook easily detects bot behavior -> limits this IP -> could lead to other issues with other platforms?
        #shortcode = self.parse_clip_url(url)
        #if not shortcode:
        #    self.logger.info(f"Invalid Facebook URL: {url}")
        #    raise NoDuration

        #valid, tokens_used, duration = await self.is_shortform(
        #    url=url,
        #    basemsg=basemsg,
        #    cookies=cookies
        #)
        #if not valid:
        #    self.logger.info(f"{url} is_shortform=False")
        #    raise VideoTooLong(duration)
        #self.logger.info(f"{url} is_shortform=True")

        #return FacebookClip(shortcode, self.cdn_client, tokens_used, duration)


class FacebookClip(BaseClip):
    def __init__(self, last_part, cdn_client, tokens_used: int, duration: int):
        self._service = "facebook"
        self._code = last_part
        super().__init__(last_part, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return f"https://facebook.com/{self._code}"

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True) -> DownloadResponse:
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
