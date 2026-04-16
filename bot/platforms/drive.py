import re
from bot.errors import VideoTooLong, NoDuration
from bot.classes import BaseClip, BaseMisc
from bot.types import DownloadResponse
from typing import Optional


class GoogleDriveMisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Google Drive"

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the Google Drive file ID from various URL formats.
        Returns None if the URL is not a valid Google Drive URL.
        """
        # Matches URLs like:
        # - https://drive.google.com/file/d/1uwKGCxNxTJUxUTvViQi_Z7A7cQgSJQLA/view
        # - https://drive.google.com/file/d/1uwKGCxNxTJUxUTvViQi_Z7A7cQgSJQLA/view?usp=sharing
        # - https://drive.google.com/open?id=1uwKGCxNxTJUxUTvViQi_Z7A7cQgSJQLA
        # - https://drive.google.com/uc?export=download&id=1uwKGCxNxTJUxUTvViQi_Z7A7cQgSJQLA
        patterns = [
            r'(?:https?://)?drive\.google\.com/file/d/([a-zA-Z0-9_-]+)(?:/|$|\?)',
            r'(?:https?://)?drive\.google\.com/open\?(?:.+&)?id=([a-zA-Z0-9_-]+)',
            r'(?:https?://)?drive\.google\.com/uc\?(?:.+&)?id=([a-zA-Z0-9_-]+)'
        ]

        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1)

        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=False) -> 'GoogleDriveClip':
        file_id = self.parse_clip_url(url)
        if not file_id:
            self.logger.info(f"Invalid Google Drive URL: {url}")
            raise NoDuration

        # Verify video length
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        return GoogleDriveClip(file_id, self.cdn_client, tokens_used, duration)


class GoogleDriveClip(BaseClip):
    def __init__(self, file_id, cdn_client, tokens_used: int, duration: int):
        self._service = "drive"
        self._file_id = file_id
        super().__init__(file_id, cdn_client, tokens_used, duration)

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return f"https://drive.google.com/file/d/{self._file_id}/view"

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
