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
        Button(style=ButtonStyle.LINK, url=CLYPPY_VOTE_URL, label='Vote for me!'),
    ]


YT_DLP_MAX_FILESIZE = 1610612736 * 4  # 6GB in bytes (1.5 * 1024 * 1024 * 1024 * 4) should handle most 3 hour videos

YT_DLP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"

EMBED_TXT_COMMAND = ".embed"

LOGGER_WEBHOOK = os.getenv('LOG_WEBHOOK')
APPUSE_LOG_WEBHOOK = os.getenv('APPUSE_WEBHOOK')

VERSION = "2.2.5"
CLYPPYIO_USER_AGENT = f"ClyppyBot/{VERSION}"

AI_EXTEND_TOKENS_COST = 10

EMBED_TOKEN_COST = 1
EMBED_W_TOKEN_MAX_LEN = 5 * 60  # 5 minutes
EMBED_TOTAL_MAX_LENGTH = 4 * 60 * 60  # 4 hours
MAX_VIDEO_LEN_SEC = 60 * 5
EMBED_TOKEN_GRACE_SEC = 30  # round-down leeway: a token isn't charged until 30s past each block boundary

MIN_VIDEO_LEN_FOR_EXTEND = 6
MAX_VIDEO_LEN_FOR_EXTEND = 60

MAX_FILE_SIZE_FOR_DISCORD = 8 * 1024 * 1024
DL_SERVER_ID = os.getenv("DL_SERVER_ID")
POSSIBLE_TOO_LARGE = ["trim", "info", "dm"]
POSSIBLE_ON_ERRORS = ["dm", "info"]
POSSIBLE_EMBED_BUTTONS = ["all", "view", "dl", "none"]
CLYPPYBOT_ID = 1111723928604381314

LOGGER_WEBHOOK_ID = 1341521799342588006
DOWNLOAD_THIS_WEBHOOK_ID = None if CONTRIB_INSTANCE else 1331035236041097326
CLYPPY_CMD_WEBHOOK_ID = None if CONTRIB_INSTANCE else 1352361462005370983
CLYPPY_CMD_WEBHOOK_CHANNEL = None if CONTRIB_INSTANCE else 1352361327770992690

CLYPPY_SUPPORT_SERVER_ID = None if CONTRIB_INSTANCE else 1117149574730104872
CLYPPY_VOTE_ROLE = None if CONTRIB_INSTANCE else 1337067081941647472
VOTE_WEBHOOK_USERID = None if CONTRIB_INSTANCE else 1337076281040179240

MONTHLY_WINNER_CHANNEL_ID = 1497641285279023164
MONTHLY_WINNER_TOKENS = 50

GITHUB_URL = "https://github.com/feelixs/clyppy"
SUPPORT_SERVER_URL = "https://discord.gg/Xts5YMUbeS"
INVITE_LINK = "https://clyppy.io/invite?ref=bot&utm_medium=bot_button"
TOPGG_VOTE_LINK = "https://top.gg/bot/1111723928604381314/vote"
TOPGG_REVIEW_LINK = "https://top.gg/bot/1111723928604381314#reviews"
INFINITY_VOTE_LINK = "https://infinitybots.gg/bot/1111723928604381314/vote"
DLIST_VOTE_LINK = "https://discordbotlist.com/bots/clyppy/upvote"
BOTLISTME_VOTE_LINK = "https://botlist.me/bots/1111723928604381314/vote"
CLYPPY_VOTE_URL = "https://clyppy.io/vote/"
BUY_TOKENS_URL = "https://clyppy.io/profile/tokens"


# Trigger labels — must match VoteLog.TRIGGER_CHOICES on the web side.
# Pass the trigger that best describes WHY the user is being shown this link
# so the web side can attribute the resulting vote in VoteLog.trigger.
_VOTE_TRIGGERS = {
    "unprompted",
    "low_token_dm",
    "post_vote_dm",
    "vote_command",
    "embed_button",
    "weekend_2x_dm",
    "unknown",
}

# Trigger labels for token PURCHASES — must match StripeTransaction.TRIGGER_CHOICES.
# These overlap with vote triggers (low_token_dm, embed_button) so the same
# DM context can drive both flows; treat them as separate vocabularies because
# the "what drove this purchase?" question doesn't always line up with votes
# (e.g. tokens_command, backup_command have no vote analogue).
_BUY_TOKENS_TRIGGERS = {
    "unprompted",
    "low_token_dm",
    "post_vote_dm",
    "vote_command",
    "embed_button",
    "tokens_command",
    "backup_command",
    "unknown",
}


def vote_url(ref: str = "unknown") -> str:
    """Build the canonical vote-page URL with a trigger ref attached.

    The web vote redirect preserves ?ref=, the tokens page propagates it onto
    each outbound vote-site URL (top.gg, etc.), and top.gg echoes it back via
    its `query` field on the vote webhook — closing the attribution loop.
    """
    if ref not in _VOTE_TRIGGERS:
        ref = "unknown"
    return f"{CLYPPY_VOTE_URL}?ref={ref}"


def buy_tokens_url(ref: str = "unknown") -> str:
    """Build the canonical buy-tokens URL with a trigger ref attached.

    The tokens page reads ?ref= from request.GET on render and stashes it in
    the user's session. When they POST to create-stripe-checkout, the endpoint
    pulls the stashed value, attaches it to Stripe checkout metadata, and the
    Stripe payment-success webhook reads it back into StripeTransaction.trigger.
    """
    if ref not in _BUY_TOKENS_TRIGGERS:
        ref = "unknown"
    return f"{BUY_TOKENS_URL}?ref={ref}"

