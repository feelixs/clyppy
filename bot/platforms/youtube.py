from bot.errors import InvalidClipType, VideoTooLong
from bot.classes import BaseClip, BaseMisc
from bot.types import DownloadResponse
from bot.utils.rate_limiter import youtube_rate_limiter
from typing import Optional
import re


class YtMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "YouTube"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
            Extracts the video ID from a YouTube URL if present.
            Works with all supported URL formats.
        """
        # Common YouTube URL patterns
        patterns = [
            r'^(?:https?://)?(?:(?:www|m|music)\.)?(?:youtube\.com/(?:[^/]+/.+/|(?:v|e(?:mbed)?)/|.*[?&]v=)|youtu\.be/)([^"&?/ ]{11})',
            r'^(?:https?://)?(?:(?:www|m|music)\.)?(?:youtube\.com/shorts/)([^"&?/ ]{11})',
            r'^(?:https?://)?(?:(?:www|m|music)\.)?youtube\.com/clip/([^"&?/ ]{11})'
        ]
        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)
        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=True) -> 'YtClip':
        slug = self.parse_clip_url(url)
        if slug is None:
            raise InvalidClipType
        if "&list=" in url:
            url = url.split("&list=")[0]
        
        # Rate limit the is_shortform check
        await youtube_rate_limiter.acquire()
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")
        return YtClip(slug, bool(re.search(r'youtube\.com/shorts/', url)), self.cdn_client, tokens_used, duration, prefetched_file=self._prefetched_file)


class YtClip(BaseClip):
    def __init__(self, slug, short, cdn_client, tokens_used: int, duration: int, prefetched_file=None):
        self._service = "youtube"
        self._prefetched_file = prefetched_file
        if short:
            self._url = f"https://youtube.com/shorts/{slug}"
        else:
            self._url = f"https://youtube.com/watch/?v={slug}"
        super().__init__(slug, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True, extra_opts=False) -> DownloadResponse:
        self.logger.info(f"({self.id}) run dl_check_size(upload_if_large=True)...")

        # Rate limit before the main download. Channel name is now piped
        # through dl_check_size → LocalFileInfo → DownloadResponse from the
        # post-download _extract_info call in dl_download, so we no longer
        # need a separate rate-limited hit here for channel metadata.
        await youtube_rate_limiter.acquire()
        response = await super().dl_check_size(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies,
            upload_if_large=True,
            prefetched_file=self._prefetched_file
        )

        response.video_uploader_username = response.broadcaster_username
        return response
