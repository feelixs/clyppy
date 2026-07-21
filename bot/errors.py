import os


class NSFWEmbed(Exception):
    pass


class UploadFailed(Exception):
    pass


class UnknownError(Exception):
    pass


class InvalidClipType(Exception):
    pass


class VideoTooLong(Exception):
    """Raised when a video is longer than max allowed length when not using VIP tokens"""
    def __init__(self, video_dur):
        self.video_dur = video_dur
        super().__init__(f"Video duration ({video_dur} seconds) exceeds maximum allowed length")


class VideoLongerThanMaxLength(Exception):
    """Raised when even using VIP tokens, a video is longer than the complete max length"""
    def __init__(self, video_dur):
        self.video_dur = video_dur
        super().__init__(f"Video duration ({video_dur} seconds) exceeds maximum allowed length")


class VideoTooLongForExtend(Exception):
    def __init__(self, video_dur):
        self.video_dur = video_dur
        super().__init__(f"Video duration ({video_dur} seconds) exceeds maximum allowed length")


class VideoTooShortForExtend(Exception):
    def __init__(self, video_dur):
        self.video_dur = video_dur
        super().__init__(f"Video duration ({video_dur} seconds) is shorter than minimum needed length")


class ExceptionHandled(Exception):
    pass


class VideoExtensionFailed(Exception):
    pass


class VideoContainsNSFWContent(Exception):
    """Raised when video contains NSFW/inappropriate content detected by AI analysis"""
    def __init__(self, reason: str = "Content flagged as NSFW"):
        self.reason = reason
        super().__init__(f"Video contains NSFW content: {reason}")


class YtDlpForbiddenError(Exception):
    pass


class UrlUnparsable(Exception):
    pass


class UnsupportedError(Exception):
    pass


class RemoteTimeoutError(Exception):
    """The url couldn't be read, resulting in the remote or yt-dlp returning timeout"""
    pass


class NoPermsToView(Exception):
    pass


class VideoSaidUnavailable(Exception):
    """Video said unavailable (not certain that it's deleted/removed"""
    pass


class VideoUnavailable(Exception):
    """Video definitely unavailable"""
    pass


class NoDuration(Exception):
    """Raised when the url might not be a video, but we don't know for sure"""
    pass


class DefinitelyNoDuration(Exception):
    """Raised when we know for a fact that it's not a video - and won't download it to manually check"""
    pass


class InvalidFileType(Exception):
    pass


class DriverDownloadFailed(Exception):
    pass


class FailedTrim(Exception):
    pass


class FailureHandled(Exception):
    pass


class ClipNotExists(Exception):
    pass


class TooManyTriesError(Exception):
    """Exception raised when the maximum number of retries is exceeded."""
    pass


class IPBlockedError(Exception):
    pass


class RateLimitedByPlatformError(Exception):
    """Raised when the platform returns HTTP 429 Too Many Requests"""
    pass


class GeoRestrictedError(Exception):
    """The uploader/platform restricts this video to specific countries and
    the bot's server isn't in one of them"""
    pass


class DRMProtectedError(Exception):
    """The site serves its video through DRM (Crunchyroll, Netflix, ...) —
    yt-dlp will never support it"""
    pass


class RateLimitExceededError(Exception):
    def __init__(self, resets_when, *args):
        super().__init__(*args)
        self.resets_when = resets_when


def handle_yt_dlp_err(err: str, file_path: str = None):
    if 'Duration: N/A, bitrate: N/A' in err:
        raise NoDuration
    elif 'No video could be found in this tweet' in err:
        raise DefinitelyNoDuration
    elif 'Incomplete YouTube ID' in err:
        raise VideoUnavailable
    elif 'This clip is no longer available' in err:
        raise VideoUnavailable
    # Geo/DRM checks must run before the generic 'Video unavailable' match:
    # YouTube phrases geo-locks as "Video unavailable. The uploader has not
    # made this video available in your country".
    elif (
        'not made this video available in your country' in err  # YouTube uploader geo-lock
        or 'due to geo restriction' in err  # yt-dlp generic GeoRestrictedError message
        or 'not available in your country' in err
        or 'geo-restricted' in err.lower()
    ):
        raise GeoRestrictedError
    elif 'known to use DRM protection' in err or 'DRM protected' in err:
        raise DRMProtectedError
    elif 'HTTP Error 404: Not Found' in err:
        raise VideoSaidUnavailable
    elif 'Video unavailable' in err:
        raise VideoSaidUnavailable
    elif 'Your IP address is blocked from accessing this post' in err:
        raise IPBlockedError
    elif 'https://www.facebook.com/checkpoint' in err:
        raise IPBlockedError
    elif "login required" in err:
        raise IPBlockedError
    elif "Explicit content cannot be sent to the desired recipient" in err:
        raise NoPermsToView
    elif 'You don\'t have permission' in err or "unable to view this" in err:
        raise NoPermsToView
    elif 'ERROR: Unsupported URL' in err or 'is not a valid URL' in err:
        raise UnsupportedError
    elif 'JSONDecodeError("Expecting value in \'\': line 1 column 1 (char 0)"));' in err:  # yt-dlp not supporting twitter right now
        raise RemoteTimeoutError
    elif 'Read timed out.' in err:
        raise RemoteTimeoutError
    elif '401:Unauthorized' in err.replace(" ", ""):
        raise YtDlpForbiddenError
    elif (
        'HTTP Error 403: Forbidden' in err
        or 'Use --cookies,' in err
        or 'Use --cookies-from-browser or --cookies' in err  # YouTube bot detection
        or "Sign in to confirm you're not a bot" in err  # YouTube bot detection
        or "Sign in to confirm you’re not a bot" in err  # variant with curly apostrophe
        or 'The downloaded file is empty' in err  # YouTube SABR/HLS delivery failure
        or 'unable to download video data' in err  # CDN-level rejection
    ):
        raise YtDlpForbiddenError
    elif (
        'HTTP Error 429' in err
        or '429: Too Many Requests' in err
        or "This content isn't available, try again later" in err  # YouTube per-account rate limit
        or 'Your account has been rate-limited by YouTube' in err
    ):
        raise RateLimitedByPlatformError
    elif 'Temporary failure in name resolution' in err or 'Name or service not known' in err:
        raise UrlUnparsable
    elif 'No host supplied' in err or "Invalid URL '" in err:
        # requests/httpx RequestError for malformed URLs (e.g. "https:///...")
        raise UrlUnparsable
    elif 'MoviePy error: failed to read the first frame of video file' in err:
        if file_path is not None:  # this can be raised after the file is partially downloaded
            try:
                os.remove(file_path)
            except:
                pass
        raise InvalidFileType
    elif 'label empty or too long' in err:
        raise UrlUnparsable
    elif 'Error passing `ffmpeg -i` command output:' in err or 'At least one output file must be specified' in err:
        raise InvalidFileType
    elif 'error 404' in err.lower() or 'video is currently unavailable' in err.lower():
        raise VideoUnavailable
    raise


def friendly_yt_dlp_error_message(exception: Exception) -> str | None:
    """Map a yt-dlp / platform exception to a user-facing message string.
    Returns None if the exception type isn't one of the recognized friendly cases —
    callers should fall back to a generic error message in that case.
    Mirrors the friendly messages used in /embed's _main_embed_task error handling."""
    if isinstance(exception, IPBlockedError):
        return "The platform said my IP was blocked from viewing that link."
    if isinstance(exception, RateLimitedByPlatformError):
        return "The platform is rate-limiting me right now (429 Too Many Requests). Please try again in a few minutes."
    if isinstance(exception, GeoRestrictedError):
        return "The uploader has region-locked that video, and it isn't available in my server's country — so I can't fetch it."
    if isinstance(exception, DRMProtectedError):
        return "That site protects its videos with DRM (like Crunchyroll or Netflix), so they can't be downloaded or embedded."
    if isinstance(exception, YtDlpForbiddenError):
        return "I couldn't download that video file (Error 403 Forbidden). Maybe try again later, or use a different hosting website?"
    if isinstance(exception, VideoUnavailable):
        return "That video is not available anymore."
    if isinstance(exception, VideoSaidUnavailable):
        return "The url returned 'Video Unavailable'. It could be the wrong url, or maybe it's just not available in my region."
    if isinstance(exception, RemoteTimeoutError):
        return "The url returned 'Timeout Error'. Maybe there's an issue with the site at the moment..."
    if isinstance(exception, UrlUnparsable):
        return "I couldn't parse that url. Did you enter it correctly?"
    if isinstance(exception, UnsupportedError):
        return "That platform is not supported."
    if isinstance(exception, (NoDuration, DefinitelyNoDuration)):
        return "Couldn't process that url (not a video post)."
    if isinstance(exception, InvalidFileType):
        return "Couldn't process that url (invalid type or corrupted video file)."
    if isinstance(exception, NoPermsToView):
        return "I don't have permission to view that url."
    if isinstance(exception, FileNotFoundError):
        return "The file could not be downloaded. Does the url point to a video?"
    return None
