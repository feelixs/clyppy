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

    # Matches reels, posts (single photo/video or carousel), and IGTV.
    # Group 1 = path segment (reel|p|tv), group 2 = shortcode.
    _URL_RE = re.compile(
        r'(?:https?://)?(?:www\.)?instagram\.com/(reel|p|tv)/([a-zA-Z0-9_-]+)(?:/|$|\?)'
    )

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the Instagram shortcode from reel, post, or IGTV URLs.
        Returns None if the URL is not a recognized Instagram URL.
        """
        match = self._URL_RE.match(url)
        return match.group(2) if match else None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=True) -> 'InstagramClip':
        match = self._URL_RE.match(url)
        if not match:
            self.logger.info(f"Invalid Instagram URL: {url}")
            raise NoDuration

        path, shortcode = match.group(1), match.group(2)
        return InstagramClip(shortcode, self.cdn_client, 0, 0, self, path=path)


class InstagramClip(BaseClip):
    def __init__(self, shortcode, cdn_client, tokens_used: int, duration: int, misc: InstagramMisc, path: str = "reel"):
        self._service = "instagram"
        self._shortcode = shortcode
        self._path = path  # "reel", "p", or "tv" — preserved when building proxy URLs
        self.misc = misc  # Reference to InstagramMisc for rate limiting
        super().__init__(shortcode, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/{self._path}/{self._shortcode}/"

    @property
    def clyppy_url(self) -> str:
        """Use /embed/ path for Instagram redirect-based embeds"""
        return f"https://clyppy.io/e/{self.clyppy_id}"

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True, extra_opts=None) -> DownloadResponse:
        """
        Create a redirect-based embed for Instagram using eeinstagram.com.

        We point `og:video` at `/videos/{shortcode}/1`, which 302s to the
        Instagram CDN mp4. Using `/reel/{shortcode}` instead would return
        HTML (not a video), and Discord refuses to embed that as og:video.
        """
        eeinstagram_url = f"https://eeinstagram.com/videos/{self._shortcode}/1"
        self.logger.info(f"({self.id}) Creating redirect embed via eeinstagram: {eeinstagram_url}")

        return DownloadResponse(
            remote_url=eeinstagram_url,
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
        Download Instagram video via eeinstagram.

        eeinstagram exposes `/videos/{shortcode}/{n}` which 302s to the
        Instagram CDN mp4. Only GET is allowed (HEAD returns 405).
        """
        if os.path.isfile(filename):
            self.logger.info("file already exists! returning...")
            return get_video_details(filename)

        video_endpoint = f"https://eeinstagram.com/videos/{self._shortcode}/1"
        discord_ua = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(video_endpoint, headers={"User-Agent": discord_ua}, allow_redirects=False) as response:
                    if response.status not in (301, 302, 307, 308):
                        self.logger.error(f"eeinstagram /videos did not redirect, got status {response.status}")
                        return None

                    cdn_url = response.headers.get("Location")
                    if not cdn_url:
                        self.logger.error("eeinstagram /videos redirect had no Location header")
                        return None

                # eeinstagram returns a 302 even for posts with no video — it points at a .jpg on the
                # image CDN (scontent.../t51.71878-15/...jpg). Bail before we save a JPEG as .mp4.
                cdn_path = cdn_url.split("?", 1)[0].lower()
                if not cdn_path.endswith(".mp4"):
                    self.logger.error(f"eeinstagram /videos redirect is not an mp4 ({cdn_path[-60:]}) — post has no video")
                    return None

                self.logger.info(f"({self.id}) Got CDN URL from eeinstagram: {cdn_url[:100]}...")

                async with session.get(cdn_url, headers={"User-Agent": discord_ua}) as response:
                    if response.status != 200:
                        self.logger.error(f"Failed to download from CDN, status {response.status}")
                        return None

                    async with aiofiles.open(filename, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            if os.path.exists(filename):
                d = get_video_details(filename)
                d.video_name = "Instagram Reel" if self._path == "reel" else "Instagram Post"
                if is_discord_compatible(d.filesize) and can_send_files:
                    self.logger.info(f"{self.id} can be uploaded to discord...")
                    d.can_be_discord_uploaded = True
                return d

            self.logger.error(f"dl_download error: Could not find file after download")
            return None

        except Exception as e:
            self.logger.error(f"eeinstagram download error: {str(e)}")
            return None
