import re
import os
import aiohttp
import aiofiles
from bot.classes import BaseClip, BaseMisc, get_video_details, is_discord_compatible
from bot.errors import VideoTooLong, NoDuration
from bot.types import DownloadResponse, LocalFileInfo
from typing import Optional


class InstagramMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Instagram"
        self.last_request_time = 0  # Track last Instagram request time
        self.min_delay = 5  # Minimum 5 seconds between requests

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the Instagram Reel shortcode from various URL formats.
        Returns None if the URL is not a valid Instagram Reel URL.
        """
        # Matches URLs like:
        # - https://www.instagram.com/reel/Cq8YJ3sJzHk/
        # - https://instagram.com/reel/Cq8YJ3sJzHk
        # - https://www.instagram.com/reel/Cq8YJ3sJzHk/?hl=en
        pattern = r'(?:https?://)?(?:www\.)?instagram\.com/reel/([a-zA-Z0-9_-]+)(?:/|$|\?)'
        match = re.match(pattern, url)
        return match.group(1) if match else None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=True) -> 'InstagramClip':
        shortcode = self.parse_clip_url(url)
        if not shortcode:
            self.logger.info(f"Invalid Instagram URL: {url}")
            raise NoDuration

        return InstagramClip(shortcode, self.cdn_client, 0, 0, self)


class InstagramClip(BaseClip):
    def __init__(self, shortcode, cdn_client, tokens_used: int, duration: int, misc: InstagramMisc):
        self._service = "instagram"
        self._shortcode = shortcode
        self.misc = misc  # Reference to InstagramMisc for rate limiting
        super().__init__(shortcode, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/reel/{self._shortcode}/"

    @property
    def clyppy_url(self) -> str:
        """Use /embed/ path for Instagram redirect-based embeds"""
        return f"https://clyppy.io/e/{self.clyppy_id}"

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True, extra_opts=None) -> DownloadResponse:
        """
        Create a redirect-based embed for Instagram using kkinstagram.com.
        Instead of downloading the video, we create a clyppy.io/embed/<id> URL
        that redirects to kkinstagram, which then redirects to the Instagram CDN.
        """
        # Build the kkinstagram redirect URL
        kkinstagram_url = f"https://www.kkinstagram.com/reel/{self._shortcode}"
        self.logger.info(f"({self.id}) Creating redirect embed via kkinstagram: {kkinstagram_url}")

        return DownloadResponse(
            remote_url=kkinstagram_url,
            local_file_path=None,
            duration=self.duration,
            width=0,  # Unknown for redirect-based embeds
            height=0,
            filesize=0,
            video_name=None,
            can_be_discord_uploaded=False,
            clyppy_object_is_stored_as_redirect=True,
        )

    async def dl_download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=False, extra_opts=None) -> Optional[LocalFileInfo]:
        """
        Download Instagram video via kkinstagram redirect.

        Uses kkinstagram.com with a Discord bot user agent to get the
        Instagram CDN URL, then downloads the video directly.
        """
        if os.path.isfile(filename):
            self.logger.info("file already exists! returning...")
            return get_video_details(filename)

        kkinstagram_url = f"https://www.kkinstagram.com/reel/{self._shortcode}"
        discord_ua = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"

        try:
            async with aiohttp.ClientSession() as session:
                # Get the redirect URL (don't follow it, just get the Location header)
                async with session.head(kkinstagram_url, headers={"User-Agent": discord_ua}, allow_redirects=False) as response:
                    if response.status not in (301, 302, 307, 308):
                        self.logger.error(f"kkinstagram did not redirect, got status {response.status}")
                        return None

                    cdn_url = response.headers.get("Location")
                    if not cdn_url:
                        self.logger.error("kkinstagram redirect had no Location header")
                        return None

                self.logger.info(f"({self.id}) Got CDN URL from kkinstagram: {cdn_url[:100]}...")

                # Download the video from the CDN
                async with session.get(cdn_url, headers={"User-Agent": discord_ua}) as response:
                    if response.status != 200:
                        self.logger.error(f"Failed to download from CDN, status {response.status}")
                        return None

                    async with aiofiles.open(filename, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            if os.path.exists(filename):
                d = get_video_details(filename)
                d.video_name = "Instagram Reel"
                if is_discord_compatible(d.filesize) and can_send_files:
                    self.logger.info(f"{self.id} can be uploaded to discord...")
                    d.can_be_discord_uploaded = True
                return d

            self.logger.error(f"dl_download error: Could not find file after download")
            return None

        except Exception as e:
            self.logger.error(f"kkinstagram download error: {str(e)}")
            return None
