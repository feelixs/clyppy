import re
import asyncio
from bot.classes import BaseClip, BaseMisc
from bot.types import DownloadResponse
from bot.errors import InvalidClipType, VideoTooLong
from bot.env import YT_DLP_USER_AGENT
from yt_dlp import YoutubeDL
from typing import Optional


class Xmisc(BaseMisc):
    def __init__(self, bot):
        super().__init__(bot)
        self.platform_name = "Twitter"

    @staticmethod
    def _parse_video_index(url: str) -> int:
        """Multi-video tweets link individual videos as /status/{id}/video/N."""
        idx_match = re.search(r'/status/\d+/video/(\d+)', url)
        return int(idx_match.group(1)) if idx_match else 1

    def parse_clip_url(self, url: str, extended_url_formats=False) -> Optional[str]:
        """
        Extracts the tweet ID/slug from various Twitter URL formats.
        Returns None if the URL is not a valid Twitter URL.

        For multi-video tweets (/status/{id}/video/N with N > 1), the video
        index is appended to the slug ("{id}-{N}") so different videos in the
        same tweet don't collide in caches/filenames. Video 1 keeps the plain
        tweet id, matching the historical slug for single-video tweets.
        """
        patterns = [
            r'(?:https?://)?(?:www\.)?twitter\.com/\w+/status/(\d+)',
            r'(?:https?://)?(?:www\.)?x\.com/\w+/status/(\d+)',
        ]
        if extended_url_formats:
            patterns.extend([r'(?:https?://)?(?:www\.)?fxtwitter\.com/\w+/status/(\d+)',
                             r'(?:https?://)?(?:www\.)?fixupx\.com/\w+/status/(\d+)'])

        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                tweet_id = match.group(1)
                video_index = self._parse_video_index(url)
                return tweet_id if video_index <= 1 else f"{tweet_id}-{video_index}"
        return None

    async def get_clip(self, url: str, extended_url_formats=False, basemsg=None, cookies=True) -> 'Xclip':
        slug = self.parse_clip_url(url, extended_url_formats)
        if slug is None:
            raise InvalidClipType
        video_index = self._parse_video_index(url)
        valid, tokens_used, duration = await self.is_shortform(
            url=url,
            basemsg=basemsg,
            cookies=cookies,
            # Tweets with multiple videos extract as a playlist; select the
            # linked video so duration checks see a single entry.
            extra_opts={'playlist_items': str(video_index)}
        )
        if not valid:
            self.logger.info(f"{url} is_shortform=False")
            raise VideoTooLong(duration)
        self.logger.info(f"{url} is_shortform=True")

        # Extract user from URL
        user_match = re.search(r'(?:(?:fx)?twitter\.com|(?:fixup)?x\.com)/(\w+)/status/', url)
        user = user_match.group(1) if user_match else None

        return Xclip(slug, user, self.cdn_client, tokens_used, duration, video_index=video_index)


class Xclip(BaseClip):
    def __init__(self, slug, user, cdn_client, tokens_used: int, duration: int, video_index: int = 1):
        self._service = "twitter"
        self._video_index = video_index
        tweet_id = slug.split('-')[0]  # slug may carry a "-{N}" video-index suffix
        self._url = f"https://x.com/{user}/status/{tweet_id}"
        if video_index > 1:
            self._url += f"/video/{video_index}"
        super().__init__(slug, cdn_client, tokens_used, duration)
        self._video_uploader_username = None
        self._cached_info = None

    @property
    def service(self) -> str:
        return self._service

    @property
    def url(self) -> str:
        return self._url

    async def download(self, filename=None, dlp_format='best/bv*+ba', can_send_files=False, cookies=True) -> DownloadResponse:
        # Extract uploader info first
        await self._extract_clip_info()

        # download & upload to clyppy.io
        self.logger.info(f"({self.id}) run dl_download()...")
        local_file = await super().dl_download(
            filename=filename,
            dlp_format=dlp_format,
            can_send_files=can_send_files,
            cookies=cookies,
            # Multi-video tweets download as a playlist; only fetch the
            # video this clip points at.
            extra_opts={'playlist_items': str(self._video_index)}
        )
        if local_file.can_be_discord_uploaded:
            return DownloadResponse(
                remote_url=None,
                local_file_path=local_file.local_file_path,
                duration=local_file.duration,
                width=local_file.width,
                height=local_file.height,
                filesize=local_file.filesize,
                video_name=local_file.video_name,
                can_be_discord_uploaded=True,
                clyppy_object_is_stored_as_redirect=False,
                video_uploader_username=self._video_uploader_username
            )
        else:
            self.logger.info(f"({self.id}) hosting on clyppy.io...")
            response = await self.upload_to_clyppyio(local_file)
            response.video_uploader_username = self._video_uploader_username
            return response

    async def _extract_clip_info(self):
        """Extract uploader info from yt-dlp (cached to avoid rate limiting)"""
        if self._cached_info is not None:
            return

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'user_agent': YT_DLP_USER_AGENT,
            'playlist_items': str(self._video_index)
        }

        def extract():
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                return info

        try:
            info = await asyncio.get_event_loop().run_in_executor(None, extract)
            # Multi-video tweets return a playlist wrapper; uploader info
            # lives on the entry.
            if info and 'entries' in info:
                entries = [e for e in info['entries'] if e]
                info = entries[0] if entries else {}
            self._cached_info = info
            self._video_uploader_username = info.get('uploader_id')
        except Exception as e:
            self.logger.warning(f"Failed to extract clip info for {self.id}: {e}")
