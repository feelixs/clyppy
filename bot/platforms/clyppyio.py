import re

from interactions import Message, SlashContext
from bot.errors import VideoTooLong, NoDuration
from bot.classes import BaseClip, BaseMisc, is_discord_compatible
from bot.io import get_clip_info
from bot.types import DownloadResponse
from typing import Optional, Union, Dict


# Matches clyppy.io/{id} and clyppy.io/e/{id}
CLYPPY_URL_RE = re.compile(r'(?:https?://)?(?:www\.)?clyppy\.(?:io|com)/(?:e/)?([a-zA-Z0-9]{8,10})')
CLYPPY_EMBED_URL_RE = re.compile(r'(?:https?://)?(?:www\.)?clyppy\.(?:io|com)/e/([a-zA-Z0-9]{8,10})')


class ClyppyioMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "clyppy"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        match = CLYPPY_URL_RE.match(url)
        return match.group(1) if match else None

    def is_clip_link(self, url: str) -> bool:
        # Clyppy links should never trigger quickembed — they're already embedded
        return False

    @staticmethod
    def is_embed_link(url: str) -> bool:
        """Check if this is a /e/ embed link specifically."""
        return bool(CLYPPY_EMBED_URL_RE.match(url))

    async def is_shortform(self, url: str, basemsg: Union[Message, SlashContext], cookies=False, info=None) -> tuple[bool, int, int]:
        # Clip is already stored on clyppy — no token cost, just use stored duration
        duration = int(info['duration'])
        return True, 0, duration

    async def get_clyppy_clip(self, clip_id: str) -> Optional[Dict]:
        result = await get_clip_info(clip_id, ctx_type='StoredVideo')
        if not result.get('match'):
            return None
        return {
            'clip_id': result['id'],
            'service': result['platform'],
            'duration': result['duration'],
            'width': result.get('width', 1280),
            'height': result.get('height', 720),
            'filesize': result.get('file_size', 0),
            'video_name': result.get('title', 'Clyppy Video'),
            'file_url': result.get('url', ''),
            'embedded_url': result.get('embedded_url', ''),
        }

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'ClyppyioClip':
        file_id = self.parse_clip_url(url)
        if not file_id:
            self.logger.info(f"Invalid Clyppy URL: {url}")
            raise NoDuration

        clip_info = await self.get_clyppy_clip(file_id)
        if not clip_info:
            self.logger.info(f"404 on Clyppy URL: {url}")
            raise NoDuration

        # Verify video length
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            info=clip_info
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        service = clip_info['service']
        self.platform_name = service
        return ClyppyioClip(clip_info, self.cdn_client, service.lower(), tokens_used, duration)


class ClyppyioClip(BaseClip):
    def __init__(self, data, cdn_client, service, tokens_used: int, duration: int):
        self._service = service
        self.data = data
        self.clip_id = data['clip_id']
        super().__init__(self.clip_id, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return 'https://clyppy.io/' + self.clip_id

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True) -> DownloadResponse:
        return DownloadResponse(
            remote_url=self.url,
            local_file_path=None,
            duration=self.data['duration'],
            width=self.data['width'],
            height=self.data['height'],
            filesize=self.data['filesize'],
            video_name=self.data['video_name'],
            clyppy_object_is_stored_as_redirect=False,
            can_be_discord_uploaded=is_discord_compatible(self.data['filesize']) and can_send_files
        )
