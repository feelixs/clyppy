from dataclasses import dataclass
from typing import Optional


# Default user-agent for yt-dlp
YT_DLP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0"


COLOR_RED = 16711680
COLOR_GREEN = 65280


@dataclass
class DiscordAttachmentId:
    """Structured ID for Discord CDN attachments"""
    channel: str
    some_id: str
    filename: str
    url_params: Optional[str]

    def to_string(self) -> str:
        """Convert to a string identifier for tracking in currently_embedding"""
        return self.some_id


@dataclass
class GuildType:
    id: int
    name: str
    is_dm: bool


@dataclass
class DownloadResponse:
    remote_url: Optional[str]
    local_file_path: Optional[str]
    duration: float
    width: int
    height: int
    filesize: float
    video_name: Optional[str]
    can_be_discord_uploaded: Optional[bool]
    clyppy_object_is_stored_as_redirect: Optional[bool]
    broadcaster_username: Optional[str] = None  # channel/creator (e.g., the streamer)
    video_uploader_username: Optional[str] = None  # uploader (e.g., who clipped it)


@dataclass
class LocalFileInfo:
    local_file_path: Optional[str]
    duration: float
    width: int
    height: int
    filesize: float
    video_name: Optional[str]
    can_be_discord_uploaded: Optional[bool]
