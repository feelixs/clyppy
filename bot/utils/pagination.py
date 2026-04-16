from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
import aiohttp
import json
from os import getenv
import base64
from interactions import Embed, Button, ButtonStyle, ActionRow
from bot.env import CLYPPYIO_USER_AGENT, is_contrib_instance, log_api_bypass
import logging
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class ServerRankPaginationState:
    """State for server ranking pagination."""
    message_id: int
    guild_id: str
    time_period: str = "all"
    page: int = 1
    total_pages: int = 1
    user_server_page: int = 1
    entries_per_page: int = 10


class ServerRankPagination:
    """Utilities for server ranking pagination."""

    API_BASE_URL = "https://clyppy.io/api/servers/ranking/"
    CACHE = {}  # Simple in-memory cache: {cache_key: (data, timestamp)}
    CACHE_TTL = 3600  # 1 hour in seconds

    @staticmethod
    async def fetch_ranking_data(guild_id: str, page: int = 1,
                                  time_period: str = "all") -> Dict[str, Any]:
        """
        Fetch server ranking from API with caching.

        Args:
            guild_id: Discord guild/server ID
            page: Page number to fetch
            time_period: Time period filter ('today', 'week', 'month', 'all')

        Returns:
            API response dict with 'success', 'data', 'page', 'total_count', etc.
        """
        import time

        if is_contrib_instance(logger):
            log_api_bypass(logger, ServerRankPagination.API_BASE_URL, "GET", {
                "guild_id": guild_id,
                "page": page,
                "time_period": time_period
            })
            return {
                "success": True,
                "data": [],
                "page": page,
                "total_count": 0,
                "has_more": False
            }

        # Check cache first
        cache_key = f"server_ranking_{time_period}_{page}"
        if cache_key in ServerRankPagination.CACHE:
            cached_data, timestamp = ServerRankPagination.CACHE[cache_key]
            if time.time() - timestamp < ServerRankPagination.CACHE_TTL:
                return cached_data

        # Fetch from API
        try:
            params = {
                "page": page,
                "time_period": time_period
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(ServerRankPagination.API_BASE_URL, params=params, headers={
                    "User-Agent": CLYPPYIO_USER_AGENT,
                    'X-API-Key': getenv('clyppy_post_key'),
                    'Content-Type': 'application/json'
                }) as response:
                    if response.status == 200:
                        data = await response.json()

                        # Cache the result
                        ServerRankPagination.CACHE[cache_key] = (data, time.time())

                        return data
                    else:
                        return {
                            "success": False,
                            "error": f"API returned status {response.status}"
                        }
        except Exception as e:
            logger.error(f"fetch_ranking_data failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @staticmethod
    async def find_server_page(guild_id: str, time_period: str = "all") -> Optional[int]:
        """
        Find which page the server appears on in the ranking.

        Args:
            guild_id: Discord guild/server ID to find
            time_period: Time period filter

        Returns:
            Page number where server appears, or None if not found
        """
        # API returns 100 per page, we display 10 per page
        # So we need to fetch and search through pages

        page = 1
        while True:
            data = await ServerRankPagination.fetch_ranking_data(guild_id, page, time_period)

            if not data.get("success", False):
                return None

            # Search for guild_id in this page's data
            for idx, server in enumerate(data.get("data", [])):
                if server.get("server_id") == guild_id:
                    # Calculate which display page this server is on
                    # API page has 100 entries, we show 10 per page
                    overall_rank = (page - 1) * 100 + idx + 1
                    display_page = (overall_rank + 9) // 10  # Ceiling division
                    return display_page

            # Check if there are more pages
            if not data.get("has_more", False):
                # Server not found in ranking
                return None

            page += 1

            # Safety limit to avoid infinite loops
            if page > 100:
                return None

    @staticmethod
    def create_embed(ranking_data: List[Dict], page: int, total_pages: int,
                     guild_id: str, entries_per_page: int = 10) -> Embed:
        """
        Create Discord embed showing server ranking.

        Args:
            ranking_data: List of server data dicts from API
            page: Current page number
            total_pages: Total number of pages
            guild_id: Current guild ID (to highlight)
            entries_per_page: Number of entries to show per page

        Returns:
            Discord Embed object
        """
        # Determine time period from context (default to "All Time")
        time_period_display = {
            "today": "Today",
            "week": "This Week",
            "month": "This Month",
            "all": "All Time"
        }.get("all", "All Time")  # Default for now, can be passed as param

        # Calculate start rank for this page
        start_rank = (page - 1) * entries_per_page

        # Find current server's rank and data
        current_server_rank = None
        current_server_name = None

        for idx, server in enumerate(ranking_data):
            rank = start_rank + idx + 1
            if server.get("server_id") == guild_id:
                current_server_rank = rank
                current_server_name = server.get("server_name", "Unknown Server")
                break

        # Create embed
        # Use gold color for top 10, blue otherwise
        if current_server_rank and current_server_rank <= 10:
            color = 0xFFD700  # Gold
        else:
            color = 0x5865F2  # Discord Blurple

        embed = Embed(
            title=f"📊 Server Clip Ranking - {time_period_display}",
            color=color
        )

        # Add description with current server info if found
        if current_server_rank and current_server_name:
            embed.description = (
                f"Showing servers ranked by unique clips embedded\n"
                f"Your server: **{current_server_name}** is ranked **#{current_server_rank}**"
            )
        else:
            embed.description = "Showing servers ranked by unique clips embedded"

        # Add field for each server on this page
        for idx, server in enumerate(ranking_data[:entries_per_page]):
            rank = start_rank + idx + 1
            server_name = server.get("server_name", "Unknown Server")
            unique_clips = server.get("unique_clip_count", 0)
            total_embeds = server.get("total_embed_count", 0)
            rate = server.get("embeds_per_hour", 0)

            # Highlight current server
            if server.get("server_id") == guild_id:
                server_name = f"**{server_name}** ⭐"

            # Format the field value
            value = (
                f"🎬 Unique Clips: **{unique_clips:,}**\n"
                f"📊 Total Embeds: **{total_embeds:,}**\n"
                f"⚡ Rate: **{rate:.2f}**/hour"
            )

            embed.add_field(
                name=f"#{rank} - {server_name}",
                value=value,
                inline=False
            )

        # Add footer
        embed.set_footer(text=f"Page {page} of {total_pages} • Updated every hour")

        return embed

    @staticmethod
    def create_buttons(page: int, total_pages: int,
                       state: ServerRankPaginationState) -> List[ActionRow]:
        """
        Create navigation buttons for pagination.

        Args:
            page: Current page number
            total_pages: Total number of pages
            state: Pagination state to encode in button custom_ids

        Returns:
            List of ActionRow components with buttons
        """
        # Encode state as base64 JSON for custom_id
        state_dict = asdict(state)
        state_json = json.dumps(state_dict)
        encoded_state = base64.b64encode(state_json.encode()).decode()

        # Create buttons
        buttons = [
            Button(
                style=ButtonStyle.PRIMARY,
                label="⏮️ First",
                custom_id=f"server_rank_first_{encoded_state}",
                disabled=(page == 1)
            ),
            Button(
                style=ButtonStyle.PRIMARY,
                label="◀️ Prev",
                custom_id=f"server_rank_prev_{encoded_state}",
                disabled=(page == 1)
            ),
            Button(
                style=ButtonStyle.SECONDARY,
                label=f"Page {page}/{total_pages}",
                custom_id=f"server_rank_page_{encoded_state}",
                disabled=True
            ),
            Button(
                style=ButtonStyle.PRIMARY,
                label="Next ▶️",
                custom_id=f"server_rank_next_{encoded_state}",
                disabled=(page >= total_pages)
            ),
            Button(
                style=ButtonStyle.PRIMARY,
                label="Last ⏭️",
                custom_id=f"server_rank_last_{encoded_state}",
                disabled=(page >= total_pages)
            ),
        ]

        return [ActionRow(*buttons)]


ENTRIES_PER_PAGE = 10


@dataclass
class UserRankPaginationState:
    """State for user ranking pagination."""
    user_id: str
    message_id: int = 0
    time_period: str = "all"
    page: int = 1
    total_pages: int = 1
    user_target_page: int = 1
    entries_per_page: int = ENTRIES_PER_PAGE
    include_bots: bool = False


class UserRankPagination:
    """Utilities for user ranking pagination."""

    API_BASE_URL = "https://clyppy.io/api/users/ranking/"
    CACHE = {}  # Simple in-memory cache: {cache_key: (data, timestamp)}
    CACHE_TTL = 3600  # 1 hour in seconds
    API_ENTRIES_PER_PAGE = 100  # API returns 100 entries per page

    @staticmethod
    def _convert_display_page_to_api_page(display_page: int) -> tuple[int, int]:
        """
        Convert display page number to API page and start index.

        Display shows 10 entries per page, API returns 100 per page.
        So display pages 1-10 all come from API page 1, pages 11-20 from page 2, etc.

        Args:
            display_page: The page number shown to user (1-indexed)

        Returns:
            Tuple of (api_page, start_index_in_api_response)
            Example: display_page 7 -> (1, 60) means API page 1, starting at index 60
        """
        api_page = (display_page - 1) // 10 + 1
        start_index = ((display_page - 1) % 10) * ENTRIES_PER_PAGE
        return api_page, start_index

    @staticmethod
    async def _fetch_api_page(api_page: int, time_period: str = "all",
                              requester_id: str = None,
                              include_bots: bool = False) -> Dict[str, Any]:
        """
        Internal method to fetch a full API page (100 entries) without slicing.
        Used by find_user_page which needs to search through full pages.

        Args:
            api_page: API page number (1-indexed)
            time_period: Time period filter
            requester_id: ID of the user making the request
            include_bots: Whether to include bot accounts in results

        Returns:
            API response dict with full 100 entries
        """
        if is_contrib_instance(logger):
            log_api_bypass(logger, UserRankPagination.API_BASE_URL, "GET", {
                "page": api_page,
                "time_period": time_period
            })
            return {
                "success": True,
                "data": [],
                "page": api_page,
                "total_count": 0,
                "has_more": False
            }

        # Check cache first
        cache_key = f"user_ranking_{time_period}_{api_page}_{include_bots}"
        if cache_key in UserRankPagination.CACHE:
            cached_data, timestamp = UserRankPagination.CACHE[cache_key]
            if time.time() - timestamp < UserRankPagination.CACHE_TTL:
                return cached_data

        # Fetch from API
        try:
            params = {
                "page": api_page,
                "time_period": time_period,
                "include_bots": "1" if include_bots else "0"
            }
            if requester_id:
                params["requester_id"] = requester_id

            async with aiohttp.ClientSession() as session:
                async with session.get(UserRankPagination.API_BASE_URL, params=params, headers={
                    "User-Agent": CLYPPYIO_USER_AGENT,
                    'X-API-Key': getenv('clyppy_post_key'),
                    'Content-Type': 'application/json'
                }) as response:
                    if response.status == 200:
                        data = await response.json()

                        # Cache the result
                        UserRankPagination.CACHE[cache_key] = (data, time.time())

                        return data
                    else:
                        return {
                            "success": False,
                            "error": f"API returned status {response.status}"
                        }
        except Exception as e:
            logger.error(f"_fetch_api_page failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @staticmethod
    async def fetch_ranking_data(user_id: str = None, page: int = 1,
                                  time_period: str = "all",
                                  requester_id: str = None,
                                  include_bots: bool = False) -> Dict[str, Any]:
        """
        Fetch user ranking from API with caching.
        Handles conversion from display page (10 per page) to API page (100 per page).

        Args:
            user_id: Discord user ID (optional)
            page: Display page number (1-indexed)
            time_period: Time period filter ('today', 'week', 'month', 'all')
            requester_id: ID of the user making the request
            include_bots: Whether to include bot accounts in results

        Returns:
            API response dict with 'success', 'data', 'page', 'total_count', etc.
            Data is sliced to show only the 10 entries for this display page.
        """
        # Convert display page to API page and offset
        api_page, start_index = UserRankPagination._convert_display_page_to_api_page(page)

        # Fetch full API page
        data = await UserRankPagination._fetch_api_page(api_page, time_period, requester_id, include_bots)

        if not data.get("success"):
            return data

        # Slice to get only the entries for this display page
        data_copy = dict(data)
        data_copy["data"] = data["data"][start_index:start_index + ENTRIES_PER_PAGE]
        return data_copy

    @staticmethod
    async def find_user_page(user_id: str, time_period: str = "all",
                              requester_id: str = None, include_bots: bool = False) -> Optional[int]:
        """
        Find which display page the user appears on in the ranking.

        Args:
            user_id: Discord user ID to find
            time_period: Time period filter
            requester_id: ID of the user making the request
            include_bots: Whether to include bot accounts in results

        Returns:
            Display page number where user appears, or None if not found
        """
        # Fetch full API pages to search for user
        api_page = 1
        while True:
            data = await UserRankPagination._fetch_api_page(api_page, time_period, requester_id, include_bots)

            if not data.get("success", False):
                return None

            # Search for user_id in this page's data
            for idx, user in enumerate(data.get("data", [])):
                if user.get("user_id") == user_id:
                    # Calculate which display page this user is on
                    # API page has 100 entries, we show 10 per page
                    overall_rank = (api_page - 1) * 100 + idx + 1
                    display_page = (overall_rank + 9) // 10  # Ceiling division
                    return display_page

            # Check if there are more pages
            if not data.get("has_more", False):
                # User not found in ranking
                return None

            api_page += 1

            # Safety limit to avoid infinite loops
            if api_page > 100:
                return None

    @staticmethod
    def create_embed(ranking_data: List[Dict], page: int, total_pages: int,
                     user_id: str, time_period: str = "all", entries_per_page: int = 10,
                     top_user: Dict = None) -> Embed:
        time_period_display = {
            "today": "Today",
            "week": "This Week",
            "month": "This Month",
            "all": "All Time"
        }.get(time_period, "All Time")

        # Calculate start rank for this page
        start_rank = (page - 1) * entries_per_page

        # Find target user's rank and data
        target_user_rank = None
        target_user_name = None
        target_user_data = None

        for idx, user in enumerate(ranking_data):
            rank = start_rank + idx + 1
            if user.get("user_id") == user_id:
                target_user_rank = rank
                target_user_name = user.get("user_name", "Unknown User")
                target_user_data = user
                break

        # Create embed
        # Use gold color for top 10, blue otherwise
        if target_user_rank and target_user_rank <= 10:
            color = 0xFFD700  # Gold
        else:
            color = 0x5865F2  # Discord Blurple

        embed = Embed(
            title=f"📊 User Clip Ranking - {time_period_display}",
            color=color
        )

        if target_user_rank and target_user_name:
            embed.description = (
                f"Showing users ranked by unique clips embedded\n"
                f"**{target_user_name}** is ranked **#{target_user_rank}**"
            )
        else:
            embed.description = "Showing users ranked by unique clips embedded"

        top_clips = top_user.get("unique_clip_count", 0) if top_user else 0
        BAR_LEN = 16

        # Add field for each user on this page
        for idx, user in enumerate(ranking_data[:entries_per_page]):
            rank = start_rank + idx + 1
            user_name = user.get("user_name", "Unknown User")
            unique_clips = user.get("unique_clip_count", 0)
            total_embeds = user.get("total_embed_count", 0)
            rate = user.get("embeds_per_hour", 0)
            servers_used = user.get("servers_used", 0)

            # Highlight target user / mark bots
            if user.get("is_bot"):
                user_name = f"{user_name} 🤖"
            if user.get("user_id") == user_id:
                user_name = f"**{user_name}** ⭐"

            # Progress bar: 0 → unique_clips → top_clips
            if top_clips > 0:
                filled = max(1 if unique_clips > 0 else 0, round((unique_clips / top_clips) * BAR_LEN))
                bar = "█" * filled + "░" * (BAR_LEN - filled)
                bar_line = f"`{bar}` **{unique_clips:,}** clips\n"
            else:
                bar_line = ""

            value = f"{bar_line}"
            embed.add_field(
                name=f"#{rank} — {user_name}",
                value=value,
                inline=False
            )

        # Add footer
        embed.set_footer(text=f"Page {page} of {total_pages} • Updated every hour")

        return embed

    @staticmethod
    def create_buttons(page: int, total_pages: int,
                       state: UserRankPaginationState) -> List[ActionRow]:
        """
        Create navigation buttons for pagination.

        Args:
            page: Current page number
            total_pages: Total number of pages
            state: Pagination state to encode in button custom_ids

        Returns:
            List of ActionRow components with buttons
        """
        import time
        # Use compact format: ur_{action}_{user_id}_{tp}_{page}_{total_pages}_{bots}_{ts}
        # Time period codes: a=all, w=week, m=month, t=today
        tp_code = {"all": "a", "week": "w", "month": "m", "today": "t"}.get(state.time_period, "a")
        bots_flag = "1" if state.include_bots else "0"
        ts = str(int(time.time() * 1000) % 100000)  # Last 5 digits of millisecond timestamp

        # Create buttons with compact IDs
        buttons = [
            Button(
                style=ButtonStyle.PRIMARY,
                label="⏮️ First",
                custom_id=f"ur_f_{state.user_id}_{tp_code}_{page}_{total_pages}_{bots_flag}_{ts}",
                disabled=(page == 1)
            ),
            Button(
                style=ButtonStyle.PRIMARY,
                label="◀️ Prev",
                custom_id=f"ur_p_{state.user_id}_{tp_code}_{page}_{total_pages}_{bots_flag}_{ts}",
                disabled=(page == 1)
            ),
            Button(
                style=ButtonStyle.SECONDARY,
                label=f"Page {page}/{total_pages}",
                custom_id=f"ur_x_{state.user_id}_{tp_code}_{page}_{total_pages}_{bots_flag}_{ts}",
                disabled=True
            ),
            Button(
                style=ButtonStyle.PRIMARY,
                label="Next ▶️",
                custom_id=f"ur_n_{state.user_id}_{tp_code}_{page}_{total_pages}_{bots_flag}_{ts}",
                disabled=(page >= total_pages)
            ),
            Button(
                style=ButtonStyle.PRIMARY,
                label="Last ⏭️",
                custom_id=f"ur_l_{state.user_id}_{tp_code}_{page}_{total_pages}_{bots_flag}_{ts}",
                disabled=(page >= total_pages)
            ),
        ]

        return [ActionRow(*buttons)]
