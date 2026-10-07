from interactions import Button, ButtonStyle
import os

# Contributor mode - bypasses API calls for contributors without access
CONTRIB_INSTANCE = os.getenv('CONTRIB_INSTANCE', '0') == '1'

def is_contrib_instance(logger):
    """Check if running in contributor mode (API calls bypassed)"""
    if CONTRIB_INSTANCE:
        logger.info("[CONTRIB MODE: TESTING] Contributor mode enabled")
        return True
    else:
        logger.info("[CONTRIB MODE: PRODUCTION] Contributor mode disabled")
        return False


def log_api_bypass(logger, endpoint: str, method: str = "POST", data: dict = None):
    """Log that an API call would have been made in contributor mode"""
    logger.info(f"[CONTRIB MODE] Would call {method} {endpoint}")
    if data:
        logger.debug(f"[CONTRIB MODE] With data: {data}")

def create_nexus_comps():
    return [
        Button(style=ButtonStyle.LINK, url=INVITE_LINK, label='Use CLYPPY'),
        Button(style=ButtonStyle.LINK, url=SUPPORT_SERVER_URL, label='Join the Community'),
        #Button(style=ButtonStyle.LINK, url=CLYPPY_VOTE_URL, label='Vote for me!'),
    ]


YT_DLP_MAX_FILESIZE = 1610612736 * 4  # 6GB in bytes (1.5 * 1024 * 1024 * 1024 * 4) should handle most 3 hour videos
YT_DLP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"

# With cookies, yt-dlp defaults to YouTube's tv_downgraded player client, which
# YouTube currently serves "The page needs to be reloaded" instead of formats
# (yt-dlp#17389). Steer to working clients; don't force mweb — it 403s due to a
# separate PO-token issue (yt-dlp#17368).
YOUTUBE_EXTRACTOR_ARGS = {
    'extractor_args': {'youtube': {'player_client': ['default', 'web_embedded']}}
}

EMBED_TXT_COMMAND = ".embed"
LOGGER_WEBHOOK = os.getenv('LOG_WEBHOOK')
APPUSE_LOG_WEBHOOK = os.getenv('APPUSE_WEBHOOK')

VERSION = "2.3.0"
CLYPPYIO_USER_AGENT = f"ClyppyBot/{VERSION}"

EMBED_TOKEN_COST = 1
EMBED_W_TOKEN_MAX_LEN = 5 * 60  # 5 minutes
EMBED_TOTAL_MAX_LENGTH = 4 * 60 * 60  # 4 hours
MAX_VIDEO_LEN_SEC = 60 * 5
EMBED_TOKEN_GRACE_SEC = 30  # round-down leeway: a token isn't charged until 30s past each block boundary

# Discord's default upload limit for bots/apps was raised to 20 MiB on 2026-09-03
# (changelog: "increased from 10 MiB to 20 MiB for users, bots, webhooks, and
# interaction responses"). Boosted guilds allow more — see discord_upload_limit().
MAX_FILE_SIZE_FOR_DISCORD = 20 * 1024 * 1024


def discord_upload_limit(guild) -> int:
    """Effective Discord upload cap for a guild — bots inherit the guild's
    boost-tier limit (tier 2 = 50 MiB, tier 3 = 100 MiB). Falls back to the
    20 MiB default for DMs, unknown tiers, or unboosted guilds. We don't use
    interactions.py's Guild.filesize_limit because its base value (25 MiB)
    predates Discord's current 20 MiB default and would overshoot."""
    try:
        tier = int(getattr(guild, 'premium_tier', 0) or 0)
    except (TypeError, ValueError):
        tier = 0
    if tier >= 3:
        return 100 * 1024 * 1024
    if tier == 2:
        return 50 * 1024 * 1024
    return MAX_FILE_SIZE_FOR_DISCORD

DL_SERVER_ID = os.getenv("DL_SERVER_ID")
POSSIBLE_TOO_LARGE = ["trim", "info", "dm"]
POSSIBLE_ON_ERRORS = ["dm", "info"]
POSSIBLE_EMBED_BUTTONS = ["all", "view", "dl", "none"]

CLYPPYBOT_ID = None
LOGGER_WEBHOOK_ID = None
DOWNLOAD_THIS_WEBHOOK_ID = None
CLYPPY_CMD_WEBHOOK_ID = None
CLYPPY_CMD_WEBHOOK_CHANNEL = None
CLYPPY_SUPPORT_SERVER_ID = None
CLYPPY_VOTE_ROLE = None
VOTE_WEBHOOK_USERID = None

MONTHLY_WINNER_CHANNEL_ID = 1497641285279023164
MONTHLY_WINNER_TOKENS = 50

GITHUB_URL = "https://github.com/feelixs/clyppy"
SUPPORT_SERVER_URL = "https://discord.gg/Xts5YMUbeS"
INVITE_LINK = GITHUB_URL # "https://clyppy.io/invite?ref=bot&utm_medium=bot_button"
TOPGG_VOTE_LINK = "https://top.gg/bot/1111723928604381314/vote"
TOPGG_REVIEW_LINK = "https://top.gg/bot/1111723928604381314#reviews"
INFINITY_VOTE_LINK = "https://infinitybots.gg/bot/1111723928604381314/vote"
DLIST_VOTE_LINK = "https://discordbotlist.com/bots/clyppy/upvote"
BOTLISTME_VOTE_LINK = "https://botlist.me/bots/1111723928604381314/vote"
CLYPPY_VOTE_URL = "https://clyppy.io/vote/"
BUY_TOKENS_URL = "https://clyppy.io/profile/tokens"



def vote_url(ref: str = "unknown") -> str:
    _vote_url = CLYPPY_VOTE_URL
    return _vote_url


def buy_tokens_url(ref: str = "unknown") -> str:
    _token_url = BUY_TOKENS_URL
    return _token_url
