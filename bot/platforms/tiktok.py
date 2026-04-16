import re
import os
import aiohttp
import aiofiles
from aiohttp import ClientSession
from bot.types import DownloadResponse, LocalFileInfo
from bot.errors import VideoTooLong, NoDuration
from bot.classes import BaseClip, BaseMisc, get_video_details, is_discord_compatible
from typing import Optional, Tuple


class TikTokMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "TikTok"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the TikTok video ID from various URL formats.
        Returns None if the URL is not a valid TikTok video URL.
        """
        # Mathes URLs like:
        # - https://www.tiktok.com/@username/video/123456789
        # - https://m.tiktok.com/video/123456789
        # - https://vm.tiktok.com/video/123456789
        pattern = [
            r'(?:https?://)?(?:www\.|vm\.|m\.)?tiktok\.com/(?:@[^/]+/)?video/(\d+)',
            r'(?:https?://)?(?:www\.)?tiktok\.com/t/([A-Za-z0-9]+)/?',
            r'(?:https?://)?(?:vt\.|vm\.)?tiktok\.com/([A-Za-z0-9]+)/?'
        ]
        for p in pattern:
            match = re.match(p, url)
            if match:
                return match.group(1)
        return None

    async def _resolve_url(self, shorturl) -> Tuple[str, str, str]:
        # retrieve actual url
        self.logger.info(f'Retrieving actual url from shortened url {shorturl}')
        async with ClientSession() as session:
            async with session.get(shorturl) as response:
                v = r'"canonical":"https:\\u002F\\u002Fwww\.tiktok\.com\\u002F@([\w.]+)\\u002Fvideo\\u002F(\d+)"'
                txt = await response.text()
                match = re.search(v, txt)
                if match is None:
                    self.logger.info(f"(video) Invalid TikTok URL: {shorturl} (match was None)")
                    raise NoDuration
                else:
                    user = match.group(1)
                    video_id = match.group(2)
                    return f"https://www.tiktok.com/@{user}/video/{video_id}", video_id, user

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'TikTokClip':
        video_id = self.parse_clip_url(url)
        if not video_id:
            self.logger.info(f"Invalid TikTok URL: {url}")
            raise NoDuration

        short_url_patterns = [
            r'(?:https?://)?(?:www\.)?tiktok\.com/t/([A-Za-z0-9]+)/?',
            r'(?:https?://)?(?:vt\.|vm\.)?tiktok\.com/([A-Za-z0-9]+)/?'
        ]

        if any(re.match(pattern, url) for pattern in short_url_patterns):
            url, video_id, user = await self._resolve_url(url)
        else:
            # Extract username if available
            user_match = re.search(r'tiktok\.com/@([^/]+)/', url)
            user = user_match.group(1) if user_match else None
            if user is None:
                self.logger.info(f"Invalid TikTok URL: {url} (user was None)")
                raise NoDuration

        return TikTokClip(video_id, user, self.cdn_client, 0, 0)


class TikTokClip(BaseClip):
    def __init__(self, video_id, user, cdn_client, tokens_used: int, duration: int):
        self._service = "tiktok"
        self._user = user
        self._video_id = video_id
        super().__init__(video_id, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        if self._user:
            return f"https://www.tiktok.com/@{self._user}/video/{self._video_id}"
        return f"https://www.tiktok.com/video/{self._video_id}"

    @property
    def clyppy_url(self) -> str:
        return f"https://clyppy.io/e/{self.clyppy_id}"

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False, extra_opts=None) -> DownloadResponse:
        # Build the tnktok offload redirect URL (302s to TikTok CDN)
        tnktok_url = f"https://offload.tnktok.com/generate/video/{self._video_id}.mp4"
        self.logger.info(f"({self.id}) Creating redirect embed via tnktok: {tnktok_url}")

        return DownloadResponse(
            remote_url=tnktok_url,
            local_file_path=None,
            duration=self.duration,
            width=0,
            height=0,
            filesize=0,
            video_name=None,
            can_be_discord_uploaded=False,
            clyppy_object_is_stored_as_redirect=True,
        )

    async def dl_download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False, extra_opts=None) -> Optional[LocalFileInfo]:
        """
        Download TikTok video via tnktok.

        Uses the tnktok offload URL to download the video directly.
        """
        if os.path.isfile(filename):
            self.logger.info("file already exists! returning...")
            return get_video_details(filename)

        cdn_url = f"https://offload.tnktok.com/generate/video/{self._video_id}.mp4"
        discord_ua = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"

        try:
            async with aiohttp.ClientSession() as session:
                self.logger.info(f"({self.id}) Downloading from tnktok CDN: {cdn_url}")

                async with session.get(cdn_url, headers={"User-Agent": discord_ua}) as response:
                    if response.status != 200:
                        self.logger.error(f"Failed to download from tnktok CDN, status {response.status}")
                        return None

                    async with aiofiles.open(filename, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            if os.path.exists(filename):
                d = get_video_details(filename)
                d.video_name = "TikTok Video"
                if is_discord_compatible(d.filesize) and can_send_files:
                    self.logger.info(f"{self.id} can be uploaded to discord...")
                    d.can_be_discord_uploaded = True
                return d

            self.logger.error(f"dl_download error: Could not find file after download")
            return None

        except Exception as e:
            self.logger.error(f"tnktok download error: {str(e)}")
            return None
