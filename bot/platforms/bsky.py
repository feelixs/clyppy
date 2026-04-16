import re
from bot.classes import BaseClip, BaseMisc
from bot.errors import VideoTooLong, InvalidClipType
from typing import Optional


class BlueSkyMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "BlueSky"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the post ID from various BlueSky URL formats.
        Returns None if the URL is not a valid BlueSky URL.
        """
        pattern = r'(?:https?://)?(?:www\.)?bsky\.app/profile/([^/]+)/post/([^/]+)'
        match = re.match(pattern, url)
        if match:
            return match.group(2)
        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'BlueSkyClip':
        slug = self.parse_clip_url(url)
        if slug is None:
            raise InvalidClipType
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        # Extract user handle from URL
        user_match = re.search(r'bsky\.app/profile/([^/]+)/post/', url)
        user = user_match.group(1) if user_match else None
        if user is None:
            raise InvalidClipType

        return BlueSkyClip(slug, user, self.cdn_client, tokens_used, duration)


class BlueSkyClip(BaseClip):
    def __init__(self, slug, user, cdn_client, tokens_used: int, duration: int):
        self._service = "bluesky"
        self._url = f"https://bsky.app/profile/{user}/post/{slug}"
        super().__init__(slug, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url
