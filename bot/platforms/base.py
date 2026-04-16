from bot.env import YT_DLP_USER_AGENT
from bot.classes import BaseMisc, BaseClip
from bot.types import DownloadResponse
from bot.errors import VideoTooLong
from yt_dlp import YoutubeDL
from urllib.parse import urlparse
import asyncio


COOKIES_PLATFORMS = ['facebook']


class BASIC_MISC(BaseMisc):
    """Raw implementation for usage in BaseAutoEmbed (bot.base)"""
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "base"
        self.is_nsfw = None  # unknown - auto detect via api

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False):
        # enable cookies without needing to make a new file for a site
        cookies = any(domain in url for domain in COOKIES_PLATFORMS)
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")
        return BASIC_CLIP(url, self.cdn_client, tokens_used, duration)

    def parse_clip_url(self, url: str, extended_url_formats=False):
        if 'http' not in url:
            return None
        return url


class BASIC_CLIP(BaseClip):
    def __init__(self, url: str, cdn_client, tokens_used: int, duration: int):
        self._service = 'base'
        self._url = url
        self._uses_redirect = False
        super().__init__(url, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url

    @property
    def clyppy_url(self) -> str:
        """Use /e/ path for redirect-based embeds, regular path for downloaded files"""
        if self._uses_redirect:
            return f"https://clyppy.io/e/{self.clyppy_id}"
        return f"https://clyppy.io/{self.clyppy_id}"

    async def _extract_cdn_url(self, dlp_format='best/bv*+ba', cookies=False):
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

        cdn_url = info.get('url')
        if not cdn_url:
            raise Exception("Failed to extract CDN URL")

        return cdn_url, info

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False, extra_opts=None) -> DownloadResponse:
        cookies = any(domain in self.url for domain in COOKIES_PLATFORMS)

        # First try to download for Discord upload
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

        # Try CDN URL extraction for redirect-based embed
        cdn_url, info = await self._extract_cdn_url(dlp_format, cookies)

        # Check if URL ends with .mp4 (ignoring query params) - if not, fall back to download
        cdn_path = urlparse(cdn_url).path.lower() if cdn_url else ''
        if not cdn_path.endswith('.mp4'):
            self.logger.info(f"({self.id}) CDN URL is not .mp4, falling back to download...")
            return await super().dl_check_size(
                filename=filename,
                dlp_format=dlp_format,
                can_send_files=can_send_files,
                cookies=cookies,
                upload_if_large=True
            )

        # Direct URL - use redirect approach
        self._uses_redirect = True
        self.logger.info(f"({self.id}) Extracted CDN URL (redirect)")
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
