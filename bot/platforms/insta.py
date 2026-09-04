import re
import os
import asyncio
import aiohttp
import aiofiles
from bot.classes import BaseClip, BaseMisc, get_video_details, is_discord_compatible
from bot.errors import VideoTooLong, NoDuration, RemoteTimeoutError, LoginRequiredError
from bot.types import DownloadResponse, LocalFileInfo
from typing import Optional, Tuple

# Ordered list of Instagram fixer providers we use to resolve a shortcode to
# a direct Instagram CDN mp4 URL. Each provider exposes the same shape:
#   GET https://{host}/videos/{shortcode}/1
#     -> 302 with Location: https://scontent.cdninstagram.com/.../X.mp4
# Probed in order; first one that 302s to an mp4 wins. Add new hosts here as
# we discover them. Order = preference (fastest/most-reliable first).
_INSTA_PROVIDERS = (
    "kkinstagram.com",
    "eeinstagram.com",
)
_DISCORD_UA = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"


async def _resolve_via_providers(
    shortcode: str,
    logger,
    session: aiohttp.ClientSession,
    per_provider_timeout: float = 4.0,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Try each provider until one returns a 302 to an mp4.

    Returns (provider_url, cdn_url, content_status) where content_status is:
      - "ok"       — got a usable mp4 CDN URL (cdn_url is set)
      - "no_video" — every provider redirected to a non-video (genuinely no video)
      - "login_required" — a provider bounced back to the instagram.com page:
        the post exists but isn't served to anonymous clients (private /
        login-gated), so no provider can ever fetch it
      - "exhausted" — all providers errored/timed out (cdn_url is None)
    """
    last_error_status = None
    # A single provider redirecting to a .jpg is NOT proof the post has no
    # video — providers intermittently serve the poster image for valid reels
    # (flaky/rate-limited). Only trust "no video" if EVERY provider agrees, so
    # remember non-mp4 redirects but keep falling back to the next provider.
    saw_non_mp4 = False
    non_mp4_provider_url = None
    # Set whenever a provider gives an inconclusive result (error, timeout,
    # missing Location). If any provider was inconclusive we can't be sure the
    # post has no video, so we must not conclude "no_video".
    had_inconclusive = False
    # A provider 302ing back to an instagram.com page URL means it reached the
    # post but couldn't extract media — the signature of a private/login-gated
    # post (anonymous scrapers get served the login wall, not the video).
    saw_instagram_bounce = False
    for host in _INSTA_PROVIDERS:
        provider_url = f"https://{host}/videos/{shortcode}/1"
        try:
            async with session.get(
                provider_url,
                headers={"User-Agent": _DISCORD_UA},
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=per_provider_timeout),
            ) as response:
                if response.status not in (301, 302, 307, 308):
                    last_error_status = response.status
                    had_inconclusive = True
                    logger.warning(
                        f"insta provider {host} returned {response.status} for {shortcode}"
                    )
                    continue

                cdn_url = response.headers.get("Location")
                if not cdn_url:
                    had_inconclusive = True
                    logger.warning(f"insta provider {host} 3xx with no Location for {shortcode}")
                    continue

                # Non-mp4 redirects come in two flavors:
                #  - an image file (.jpg/.webp/...) — evidence the post may be
                #    image-only, BUT flaky providers also serve the poster frame
                #    for valid reels, so it only counts if every provider agrees
                #  - anything else (e.g. a bounce back to instagram.com's HTML
                #    page) — pure provider failure, no content evidence at all
                cdn_path = cdn_url.split("?", 1)[0].lower()
                if not cdn_path.endswith(".mp4"):
                    if cdn_path.endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic', '.gif')):
                        logger.info(
                            f"insta provider {host} redirected to image ({cdn_path[-60:]}) "
                            f"— trying next provider before concluding no video"
                        )
                        saw_non_mp4 = True
                        non_mp4_provider_url = provider_url
                    elif re.match(r'https?://(www\.)?instagram\.com/', cdn_url):
                        logger.info(
                            f"insta provider {host} bounced back to instagram.com "
                            f"({cdn_url[:80]}) — post is likely private/login-gated"
                        )
                        saw_instagram_bounce = True
                    else:
                        logger.warning(
                            f"insta provider {host} redirected to non-media URL "
                            f"({cdn_url[:80]}) — treating as provider failure"
                        )
                        had_inconclusive = True
                    continue

                logger.info(f"insta provider {host} resolved {shortcode} (status {response.status})")
                return provider_url, cdn_url, "ok"

        except asyncio.TimeoutError:
            had_inconclusive = True
            logger.warning(f"insta provider {host} timed out for {shortcode}")
            continue
        except Exception as e:
            had_inconclusive = True
            logger.warning(f"insta provider {host} errored for {shortcode}: {e}")
            continue

    # Only conclude "no video" when every provider gave a definitive non-mp4
    # redirect and none were inconclusive — otherwise a flaky provider serving
    # the poster .jpg for a valid reel would falsely read as an image-only post.
    if saw_non_mp4 and not had_inconclusive and not saw_instagram_bounce:
        logger.info(f"insta: all providers redirected to non-mp4 for {shortcode} — post has no video")
        return non_mp4_provider_url, None, "no_video"

    if saw_instagram_bounce:
        logger.info(f"insta: provider(s) bounced back to instagram.com for {shortcode} — treating as login-required")
        return None, None, "login_required"

    logger.error(
        f"insta: all {len(_INSTA_PROVIDERS)} providers failed for {shortcode} "
        f"(last upstream status: {last_error_status}, saw_non_mp4: {saw_non_mp4})"
    )
    return None, None, "exhausted"


class InstagramMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Instagram"
        self.last_request_time = 0  # Track last Instagram request time
        self.min_delay = 5  # Minimum 5 seconds between requests

    # Matches reels, posts (single photo/video or carousel), and IGTV.
    # Group 1 = path segment (reel|reels|p|tv), group 2 = shortcode.
    # The app's share button produces the plural /reels/ form, and
    # account-context links insert a /{username}/ segment before the path
    # (e.g. instagram.com/flxcproxy/reel/<shortcode>/).
    _URL_RE = re.compile(
        r'(?:https?://)?(?:www\.)?instagram\.com/(?:[a-zA-Z0-9_.]+/)?(reels?|p|tv)/([a-zA-Z0-9_-]+)(?:/|$|\?)'
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
        if path == "reels":
            path = "reel"  # normalize so rebuilt/proxy URLs use the canonical form
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
        Create a redirect-based embed for Instagram.

        We point `og:video` at a fixer's `/videos/{shortcode}/1`, which 302s to
        the Instagram CDN mp4. Using `/reel/{shortcode}` instead would return
        HTML (not a video), and Discord refuses to embed that as og:video.

        Probes the provider list and uses the first one that successfully
        resolves the shortcode — so a single provider going down (eeinstagram
        intermittent 500s, etc.) doesn't take Instagram embeds offline.
        """
        async with aiohttp.ClientSession() as session:
            provider_url, cdn_url, status = await _resolve_via_providers(
                self._shortcode, self.logger, session, per_provider_timeout=3.0
            )

        if status == "no_video":
            self.logger.error(f"({self.id}) Instagram post has no video — bailing")
            raise NoDuration

        if status == "login_required":
            self.logger.error(f"({self.id}) Instagram post is private/login-gated — bailing")
            raise LoginRequiredError

        if status != "ok" or not provider_url:
            # All fixer services are down/erroring. Distinct from "post has no video"
            # so the user gets a "try again later" message rather than "not a video."
            self.logger.error(f"({self.id}) all Instagram providers failed for embed")
            raise RemoteTimeoutError

        self.logger.info(f"({self.id}) Creating redirect embed via {provider_url}")
        return DownloadResponse(
            remote_url=provider_url,
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
        Download Instagram video bytes through the provider chain.

        Each provider exposes `/videos/{shortcode}/{n}` which 302s to the
        Instagram CDN mp4. We probe providers in order and download from the
        first one that resolves successfully.
        """
        if os.path.isfile(filename):
            self.logger.info("file already exists! returning...")
            return get_video_details(filename)

        try:
            async with aiohttp.ClientSession() as session:
                _, cdn_url, status = await _resolve_via_providers(
                    self._shortcode, self.logger, session, per_provider_timeout=5.0
                )

                if status == "no_video":
                    return None
                if status != "ok" or not cdn_url:
                    return None

                self.logger.info(f"({self.id}) Got CDN URL: {cdn_url[:100]}...")

                # CDN URLs can stall for non-app clients: the connection opens but
                # the body never (or barely) flows. Without an explicit timeout this
                # falls back to aiohttp's 300s default and the download coroutine
                # blocks for ~5 min. sock_read fails fast on a stalled mid-stream body.
                cdn_timeout = aiohttp.ClientTimeout(total=60, sock_connect=5, sock_read=15)
                async with session.get(cdn_url, headers={"User-Agent": _DISCORD_UA}, timeout=cdn_timeout) as response:
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
            self.logger.error(f"insta download error: {str(e)}")
            return None
