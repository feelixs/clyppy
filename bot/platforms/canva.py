import re
from bot.types import DownloadResponse
from bot.errors import VideoTooLong, NoDuration
from bot.classes import BaseClip, BaseMisc
from yt_dlp.networking.impersonate import ImpersonateTarget
from typing import Optional

CANVA_EXTRA_OPTS = {'impersonate': ImpersonateTarget.from_str('firefox')}


class CanvaMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Canva"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        pattern = r'https?://(?:www\.)?canva\.com/design/(?P<id>[^/]+)/(?P<token>[^/]+)/(?:watch|view|edit)'
        match = re.match(pattern, url)
        return match.group('id') if match else None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=True) -> 'CanvaClip':
        shortcode = self.parse_clip_url(url)
        if not shortcode:
            self.logger.info(f"Invalid URL: {url}")
            raise NoDuration

        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies,
            extra_opts=CANVA_EXTRA_OPTS
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        return CanvaClip(shortcode, url, self.cdn_client, tokens_used, duration, prefetched_file=self._prefetched_file)


class CanvaClip(BaseClip):
    def __init__(self, shortcode, full_url, cdn_client, tokens_used: int, duration: int, prefetched_file=None):
        self._service = "canva"
        self._shortcode = shortcode
        self._full_url = full_url
        self._prefetched_file = prefetched_file
        super().__init__(shortcode, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._full_url

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True, extra_opts=None) -> DownloadResponse:
        self.logger.info(f"({self.id}) run dl_check_size(upload_if_large=True)...")
        if extra_opts is None:
            extra_opts = {}
        extra_opts['impersonate'] = ImpersonateTarget.from_str('firefox')
        return await super().dl_check_size(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies,
            upload_if_large=True,
            extra_opts=extra_opts,
            prefetched_file=self._prefetched_file
        )
