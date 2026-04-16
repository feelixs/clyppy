from interactions import SlashContext, Message
from bot.errors import VideoLongerThanMaxLength
from bot.env import (CLYPPYIO_USER_AGENT, MAX_VIDEO_LEN_SEC, EMBED_W_TOKEN_MAX_LEN, EMBED_TOTAL_MAX_LENGTH,
                     EMBED_TOKEN_COST, DL_SERVER_ID, AI_EXTEND_TOKENS_COST, is_contrib_instance, log_api_bypass)
from typing import Tuple, Union
from math import ceil
from os import getenv
import aiohttp
import logging

logger = logging.getLogger(__name__)


def safe_get_post_key():
    post_key = getenv('clyppy_post_key')
    if post_key is None:
        raise Exception('Clyppy Post key not set')
    return post_key


def json_key_headers():
    return {'X-API-Key': safe_get_post_key(), 'Content-Type': 'application/json'}


def get_aiohttp_session():
    """Create an aiohttp ClientSession with the ClyppyBot user agent."""
    return aiohttp.ClientSession(headers={"User-Agent": CLYPPYIO_USER_AGENT})


async def check_text_is_nsfw(text: str):
    url = f"https://clyppy.io/api/check-nsfw/?text={text}"
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "GET", {})
    async with get_aiohttp_session() as session:
        async with session.get(url) as response:
            if response.status >= 500:
                logger.warning(f"[CHECK-TEXT-NSFW] Server error {response.status}. API may be down. Error was: {text}")
                return False
            r = await response.json()
            if 'is_nsfw' in r:
                return r['is_nsfw']

            logger.warning(f"[CHECK-TEXT-NSFW] Invalid response. Returning false")
            return False


async def fetch_video_status(clip_id: str):
    url = 'https://clyppy.io/api/clips/get-status/'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "GET", {"clip_id": clip_id})
        return {"exists": False, "code": 200}

    async with get_aiohttp_session() as session:
        async with session.get(url, json={'clip_id': clip_id}, headers=json_key_headers()) as response:
            return await response.json()


async def push_interaction_error(parent_msg: Union[Message, SlashContext], clip_url, platform_name: str, error_info: dict, handled: bool, clip=None, logger=None):
    url = 'https://clyppy.io/api/clips/publish/error/'
    if is_contrib_instance(logger):
        video_id = clip.clyppy_id if clip is not None else None
        log_api_bypass(logger, url, "POST", {
            "clyppy_id_ctx": video_id,
            "error_type": error_info['name'],
            "platform": platform_name,
            "handled": handled
        })
        return None

    video_id = None
    if clip is not None:
        video_id = clip.clyppy_id

    video_platform = platform_name.lower()
    video_url = clip_url
    error_name = error_info['name']
    error_msg = error_info['msg']

    try:
        async with get_aiohttp_session() as session:
            async with session.post(url, json={
                'clyppy_id_ctx': video_id,
                'error_type': error_name,
                'error_message': error_msg,
                'video_url': video_url,
                'video_platform': video_platform,
                'username': parent_msg.author.username or f"User_{parent_msg.author.id}",
                'user_id': parent_msg.author.id,
                'handled': handled,
                'server_id': getattr(getattr(parent_msg, 'guild', None), 'id', None),
            }, headers=json_key_headers()) as response:
                response_text = await response.text()

                # Check for Cloudflare errors (500, 502, 503, 504)
                if response.status >= 500:
                    if 'cloudflare' in response_text.lower():
                        if logger:
                            logger.warning(f"Cloudflare error when pushing interaction error (status {response.status}). API may be down. Error was: {error_name}")
                        return None  # Silently fail - the main error was already logged
                    else:
                        if logger:
                            logger.error(f"Server error {response.status} when pushing interaction error: {response_text[:200]}")
                        return None

                if response.status != 201:
                    if logger:
                        logger.warning(f"Failed to push interaction error (status {response.status}): {response_text[:200]}")
                    return None
                else:
                    return await response.json()
    except aiohttp.ClientError as e:
        # Handle connection errors, timeouts, etc.
        if logger:
            logger.warning(f"Network error when pushing interaction error: {e}")
        return None
    except Exception as e:
        # Catch any other unexpected errors
        if logger:
            logger.error(f"Unexpected error when pushing interaction error: {e}")
        return None


async def is_404(url: str) -> Tuple[bool, int]:
    try:
        async with get_aiohttp_session() as session:
            async with session.get(url) as response:
                logger.info(f"Got response status {response.status} for {url}")
                return not str(response.status).startswith('2'), response.status
    except aiohttp.ClientError:
        # Handle connection errors, invalid URLs etc
        return True, 500  # Consider failed connections as effectively 404


async def add_reqqed_by(data, key):
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://clyppy.io/api/clips/add-requested-by/", "POST", data)
        return {"success": True, "msg": "[test] success", "code": 201}

    async with get_aiohttp_session() as session:
        async with session.post(
                'https://clyppy.io/api/clips/add-requested-by/',
                json=data,
                headers=json_key_headers()
        ) as response:
            return await response.json()


async def callback_clip_delete_msg(data, key, ctx_type: str = "StoredVideo") -> dict:
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://clyppy.io/api/clips/msg-get-delete/", "POST", data)
        return {"success": True, "msg": "[test] Successfully deleted", "code": 200}

    async with get_aiohttp_session() as session:
        async with session.post(
                'https://clyppy.io/api/clips/msg-get-delete/',
                json=data,
                headers={
                    'X-API-Key': key,
                    'Request-Type': ctx_type,
                    'Content-Type': 'application/json'
                }
        ) as response:
            return await response.json()


async def get_clip_info(clip_id: str, ctx_type='StoredVideo'):
    if is_contrib_instance(logger):
        log_api_bypass(logger, f"https://clyppy.io/api/clips/get/{clip_id}", "GET", {"ctx_type": ctx_type})
        return {'success': False, 'error': '[test] Clip not found', 'code': 404}

    url = f"https://clyppy.io/api/clips/get/{clip_id}"
    headers = {
        'X-API-Key': safe_get_post_key(),
        'Request-Type': ctx_type,
        'Content-Type': 'application/json'
    }
    async with get_aiohttp_session() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                j = await response.json()
                return j
            elif response.status == 404:
                return {'match': False}
            else:
                raise Exception(f"Failed to get clip info: (Server returned code: {response.status})")


async def subtract_tokens(user, amt, clip_url: str=None, reason: str=None, description: str=None):
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://clyppy.io/api/tokens/subtract/", "POST", {
            "user_id": user.id,
            "amount": amt,
            "reason": reason or 'Clyppy Embed'
        })
        return {"success": True, "user_success": True, "tokens": 999}

    if reason is None:
        reason = 'Clyppy Embed'

    url = 'https://clyppy.io/api/tokens/subtract/'
    j = {
        'userid': user.id,
        'username': user.username or f"User_{user.id}",
        'amount': amt,
        'reason': reason,
        'original_url': clip_url,
        'description': description,
    }
    async with get_aiohttp_session() as session:
        async with session.post(url, json=j, headers=json_key_headers()) as response:
            if response.status == 200:
                return await response.json()
            else:
                error_data = await response.json()
                raise Exception(f"Failed to subtract user's VIP tokens: {error_data.get('error', 'Unknown error')}")


async def refresh_clip(clip_id: str, user_id: int):
    if is_contrib_instance(logger):
        log_api_bypass(logger, f"https://clyppy.io/api/clips/refresh/{clip_id}", "POST", {"user_id": user_id})
        return {"success": True, "msg": "[test] would initate refresh", "code": 200}

    url = f"https://clyppy.io/api/clips/refresh/{clip_id}"
    head = {
        'X-Discord-User-Id': str(user_id),
        'Not-Encoded': 'true',
        'Ignore-User-Check': 'true'
    }
    async with get_aiohttp_session() as session:
        async with session.post(url, headers=head) as response:
            return await response.json()


async def author_has_premium(user):
    # 'premium' is not a feature that exists yet
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://clyppy.io/api/users/has-premium", "POST", {"user_id": str(user.id)})
        return True

    url = f"https://clyppy.io/api/users/has-premium"
    head = {
        'X-Discord-User-Id': str(user.id)
    }
    async with get_aiohttp_session() as session:
        async with session.post(url, headers=head) as response:
            resp = await response.json()
            if resp['success']:
                return resp['premium']

            return False


def get_token_cost(video_dur):
    """Raises VideoLongerThanMaxLength if video is too long"""
    if video_dur >= EMBED_TOTAL_MAX_LENGTH:
        raise VideoLongerThanMaxLength(video_dur)

    # Free embed up to MAX_VIDEO_LEN_SEC
    if video_dur <= MAX_VIDEO_LEN_SEC:
        return 0

    # Calculate tokens only for the portion exceeding the free limit
    extra_duration = video_dur - MAX_VIDEO_LEN_SEC
    return EMBED_TOKEN_COST * ceil(extra_duration / EMBED_W_TOKEN_MAX_LEN)  # 1 token per 5 minutes of additional time


async def author_has_enough_tokens_for_ai_extend(msg, url: str):
    # -> bool-> can extend video, int-> number of tokens used, int->current tokens after embed
    user = msg.author
    sub = await subtract_tokens(
        user=user,
        amt=AI_EXTEND_TOKENS_COST,
        clip_url=url,
        reason="AI Video Extend",
        description=f"User requested an AI extended video for {url}"
    )
    if sub['success']:
        if sub['user_success']:  # the user had enough tokens to subtract successfully
            return True, AI_EXTEND_TOKENS_COST, sub['tokens']

    return False, 0, None


async def fetch_vote_ranking(user):
    """Fetch the user's vote ranking for the current month."""
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://clyppy.io/api/votes/ranking/", "GET", {"user_id": user.id})
        return {
            "success": True,
            "user": {"monthly_votes": 0, "total_votes": 0, "rank": 1},
            "top_voter": None,
            "total_voters": 0,
            "vote_month": "2026-01"
        }

    url = 'https://clyppy.io/api/votes/ranking/'
    j = {'userid': user.id}
    async with get_aiohttp_session() as session:
        async with session.get(url, json=j, headers=json_key_headers()) as response:
            return await response.json()


async def get_pending_vote_notifications(limit: int = 50) -> list:
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://clyppy.io/api/internal/votes/pending-notifications", "GET")
        return []
    url = 'https://clyppy.io/api/internal/votes/pending-notifications'
    async with get_aiohttp_session() as session:
        async with session.get(url, params={'limit': limit}, headers=json_key_headers()) as response:
            if response.status == 200:
                return await response.json()
            logger.warning(f"get_pending_vote_notifications returned {response.status}")
            return []


async def mark_votes_notified(ids: list) -> None:
    url = 'https://clyppy.io/api/internal/votes/mark-notified'
    if not ids:
        return
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", {'ids': ids})
        return
    async with get_aiohttp_session() as session:
        async with session.post(url, json={'ids': ids}, headers=json_key_headers()) as response:
            if response.status != 200:
                logger.warning(f"mark_votes_notified returned {response.status}")


async def get_low_token_users(limit: int = 50) -> list:
    url = 'https://clyppy.io/api/internal/tokens/low-balance-users'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "GET")
        return []
    async with get_aiohttp_session() as session:
        async with session.get(url, params={'limit': limit}, headers=json_key_headers()) as response:
            if response.status == 200:
                return await response.json()
            logger.warning(f"get_low_token_users returned {response.status}")
            return []


async def mark_low_token_notified(ids: list) -> None:
    if not ids:
        return
    url = 'https://clyppy.io/api/internal/tokens/mark-low-notified'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", {'ids': ids})
        return
    async with get_aiohttp_session() as session:
        async with session.post(url, json={'ids': ids}, headers=json_key_headers()) as response:
            if response.status != 200:
                logger.warning(f"mark_low_token_notified returned {response.status}")


async def preview_backup(user_id: int, clip_id: str) -> dict:
    url = 'https://clyppy.io/api/backups/preview'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", {'user_id': user_id, 'clip_id': clip_id})
        return {'success': False, 'error': 'clip_not_found'}

    async with get_aiohttp_session() as session:
        async with session.post(url, json={'user_id': user_id, 'clip_id': clip_id}, headers=json_key_headers()) as response:
            if response.status == 200:
                return await response.json()
            return {'success': False, 'error': f'http_{response.status}'}


async def create_backup(user_id: int, username: str, clip_id: str, is_anonymous: bool) -> dict:
    url = 'https://clyppy.io/api/backups/create'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", {'user_id': user_id, 'clip_id': clip_id})
        return {'success': True, 'position': 0, 'charged': 0, 'next_charge_at': None}

    payload = {'user_id': user_id, 'username': username, 'clip_id': clip_id, 'is_anonymous': is_anonymous}
    async with get_aiohttp_session() as session:
        async with session.post(url, json=payload, headers=json_key_headers()) as response:
            if response.status == 200:
                return await response.json()
            return {'success': False, 'error': f'http_{response.status}'}


async def cancel_backup(user_id: int, clip_id: str) -> dict:
    url = 'https://clyppy.io/api/backups/cancel'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", {'user_id': user_id, 'clip_id': clip_id})
        return {'success': True, 'effect': 'end_of_cycle'}
    async with get_aiohttp_session() as session:
        async with session.post(url, json={'user_id': user_id, 'clip_id': clip_id}, headers=json_key_headers()) as response:
            if response.status == 200:
                return await response.json()
            return {'success': False, 'error': f'http_{response.status}'}


async def toggle_backup_privacy(user_id: int, clip_id: str, is_anonymous: bool) -> dict:
    url = 'https://clyppy.io/api/backups/toggle-privacy'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", {'user_id': user_id, 'clip_id': clip_id})
        return {'success': True}
    headers = {'X-API-Key': safe_get_post_key(), 'Content-Type': 'application/json'}
    payload = {'user_id': user_id, 'clip_id': clip_id, 'is_anonymous': is_anonymous}
    async with get_aiohttp_session() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            return {'success': False, 'error': f'http_{response.status}'}


async def get_pending_backup_warnings(limit: int = 50) -> list:
    url = 'https://clyppy.io/api/internal/backups/pending-warnings'
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://clyppy.io/api/internal/backups/pending-warnings", "GET")
        return []
    async with get_aiohttp_session() as session:
        async with session.get(url, params={'limit': limit}, headers=json_key_headers()) as response:
            if response.status == 200:
                return await response.json()
            logger.warning(f"get_pending_backup_warnings returned {response.status}")
            return []


async def mark_backup_warned(ids: list) -> None:
    url = 'https://clyppy.io/api/internal/backups/mark-warned'
    if not ids:
        return
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", {'ids': ids})
        return
    async with get_aiohttp_session() as session:
        async with session.post(url, json={'ids': ids}, headers=json_key_headers()) as response:
            if response.status != 200:
                logger.warning(f"mark_backup_warned returned {response.status}")


async def register_download(clyppy_id: str, service: str, title: str, file_url: str,
                            file_size: int, duration: float, width: int, height: int,
                            file_ext: str, user_id: int, username: str, original_url: str,
                            guild_id: int | None, guild_name: str | None,
                            channel_name: str | None, is_nsfw: bool,
                            tokens_used: int = 0) -> dict:
    payload = {
        'clyppy_id': clyppy_id,
        'service': service,
        'title': title,
        'file_url': file_url,
        'file_size': file_size,
        'duration': duration,
        'width': width,
        'height': height,
        'file_ext': file_ext,
        'user_id': user_id,
        'username': username,
        'original_url': original_url,
        'guild_id': guild_id,
        'guild_name': guild_name,
        'channel_name': channel_name,
        'is_nsfw': is_nsfw,
        'tokens_used': tokens_used,
    }
    url = 'https://clyppy.io/api/downloads/register'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", payload)
        return {'success': True, 'clip_id': clyppy_id, 'reactivated': False}
    async with get_aiohttp_session() as session:
        async with session.post(url, json=payload, headers=json_key_headers()) as response:
            if response.status == 200:
                return await response.json()
            return {'success': False, 'error': f'http_{response.status}'}


async def log_guild_event(guild_id: int, event_type: str, data: dict):
    payload = {'guild_id': guild_id, 'event_type': event_type, 'data': data}
    url = 'https://clyppy.io/api/internal/guild/event'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", payload)
        return
    async with get_aiohttp_session() as session:
        async with session.post(url, json=payload, headers=json_key_headers()) as response:
            if response.status != 200:
                logger.warning(f"[GUILD-EVENT] Failed: {response.status}")


async def log_guild_install(guild_id: int, guild_name: str, member_count: int | None,
                             owner_id: int | None, welcome_sent: bool):
    payload = {
        'guild_id': guild_id,
        'guild_name': guild_name,
        'member_count': member_count,
        'owner_id': owner_id,
        'welcome_sent': welcome_sent,
    }
    url = 'https://clyppy.io/api/internal/guild/install'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", payload)
        return
    async with get_aiohttp_session() as session:
        async with session.post(url, json=payload, headers=json_key_headers()) as response:
            if response.status != 200:
                logger.warning(f"[GUILD-INSTALL] Failed: {response.status}")


async def log_guild_left(guild_id: int, guild_name: str):
    payload = {'guild_id': guild_id, 'guild_name': guild_name}
    url = 'https://clyppy.io/api/internal/guild/left'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", payload)
        return
    async with get_aiohttp_session() as session:
        async with session.post(url, json=payload, headers=json_key_headers()) as response:
            if response.status != 200:
                logger.warning(f"[GUILD-LEFT] Failed: {response.status}")


async def fetch_previous_vote_winner():
    """Fetch the winner of the previous month's vote competition."""
    url = 'https://clyppy.io/api/votes/previous-winner/'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "GET")
        return {"success": True, "winners": [], "vote_month": "2026-01"}
    async with get_aiohttp_session() as session:
        async with session.get(url, headers=json_key_headers()) as response:
            return await response.json()


async def author_has_enough_tokens(msg, video_dur, url: str) -> tuple[bool, int, int]:
    """Returns: bool->can embed video, int->number of tokens used"""
    def is_dl_server(guild):
        if guild is None:
            return False
        elif str(guild.id) == str(DL_SERVER_ID):
            return True
        return False

    user = msg.author
    if video_dur <= MAX_VIDEO_LEN_SEC:  # no tokens need to be used
        return True, 0, video_dur
    elif video_dur < EMBED_TOTAL_MAX_LENGTH:
        # if we're in dl server, automatically return true without needing any tokens (only for videos under 30min)
        if is_dl_server(msg.guild):
            return video_dur <= EMBED_W_TOKEN_MAX_LEN, 0, video_dur

        cost = get_token_cost(video_dur)
        sub = await subtract_tokens(
            user=user,
            amt=cost,
            clip_url=url
        )
        if sub['success']:
            if sub['user_success']:  # the user had enough tokens to subtract successfully
                return True, cost, video_dur

    return False, 0, video_dur


async def post_health_snapshot(payload: dict, logger=None) -> None:
    url = 'https://clyppy.io/api/internal/health/snapshot'
    if is_contrib_instance(logger):
        log_api_bypass(logger, url, "POST", payload)
        return
    try:
        async with get_aiohttp_session() as session:
            async with session.post(url, json=payload, headers=json_key_headers()) as response:
                if response.status != 201:
                    if logger:
                        logger.warning(f"[heartbeat] Snapshot POST returned {response.status}")
    except Exception as e:
        if logger:
            logger.warning(f"[heartbeat] Snapshot POST failed: {e}")
