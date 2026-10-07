from bot.types import DownloadResponse, LocalFileInfo
from bot.errors import UnknownError, VideoContainsNSFWContent
from bot.classes import BaseClip, is_discord_compatible, tryremove
from pathlib import Path
from typing import Union
from moviepy import VideoFileClip
import asyncio
import os
import re
import uuid
import json
import fcntl
from contextlib import asynccontextmanager


class DownloadManager:
    def __init__(self, p):
        self._parent = p
        max_concurrent = os.getenv('MAX_RUNNING_AUTOEMBED_DOWNLOADS', 5)
        self._semaphore = asyncio.Semaphore(int(max_concurrent))

    async def download_clip(
            self,
            clip: BaseClip,
            can_send_files=False,
            skip_upload=False
    ) -> Union[DownloadResponse, LocalFileInfo]:
        desired_filename = f'{clip.service}_{clip.clyppy_id}' if clip.service != 'base' else f'{clip.clyppy_id}'
        if len(desired_filename) > 200:
            desired_filename = desired_filename[:200]
        desired_filename += ".mp4"
        async with self._semaphore:
            if not isinstance(clip, BaseClip):
                raise TypeError(f"Invalid clip object passed to download_clip of type {type(clip)}")
            self._parent.logger.info("Run clip.download()")

            if skip_upload:
                # force manual override of auto-upload (download() may upload, but dl_download() doesn't)
                r: LocalFileInfo = await clip.dl_download(filename=desired_filename, can_send_files=can_send_files, cookies=True)
            else:
                r: DownloadResponse = await clip.download(filename=desired_filename, can_send_files=can_send_files)

        if r is None:
            raise UnknownError
        return r
