from interactions.api.events.discord import GuildJoin, GuildLeft, MessageCreate
from interactions import (Extension, Embed, slash_command, SlashContext, SlashCommandOption, OptionType, listen,
                          Permissions, Task, IntervalTrigger, ComponentContext, Message, SlashCommandChoice,
                          component_callback, Button, ButtonStyle, Activity, ActivityType, TYPE_ALL_CHANNEL, GuildForum,
                          GuildCategory, Member, ActionRow)

from bot.env import (SUPPORT_SERVER_URL, MONTHLY_WINNER_CHANNEL_ID, MONTHLY_WINNER_TOKENS, POSSIBLE_ON_ERRORS,
                     POSSIBLE_EMBED_BUTTONS, APPUSE_LOG_WEBHOOK, VERSION, EMBED_TXT_COMMAND, BUY_TOKENS_URL, CLYPPY_VOTE_URL,
                     is_contrib_instance, log_api_bypass, CLYPPYBOT_ID)
from bot.io import (get_clip_info, callback_clip_delete_msg, add_reqqed_by, subtract_tokens, refresh_clip,
                    preview_backup, create_backup, register_download, get_token_cost, log_guild_event,
                    log_guild_install, log_guild_left)
from bot.env import (BUY_TOKENS_URL, CLYPPY_VOTE_URL, EMBED_TOKEN_COST, EMBED_W_TOKEN_MAX_LEN,
                     EMBED_TOTAL_MAX_LENGTH, MAX_VIDEO_LEN_SEC)
from bot.errors import friendly_yt_dlp_error_message, VideoTooLong, VideoLongerThanMaxLength
from bot.classes import BaseMisc, send_webhook, maybe_refresh_youtube_cookies, tryremove
from bot.platforms.clyppyio import ClyppyioMisc
from bot.utils.pagination import ServerRankPagination, ServerRankPaginationState, UserRankPagination, UserRankPaginationState
from bot.tools.converter import SUPPORTED_FORMATS, convert_media, AUDIO_FORMATS, ffprobe_video_metadata
from bot.tools.embedder import publish_interaction
from bot.task_queue import SlashCommandTask, process_queued_tasks
from bot.types import COLOR_GREEN, COLOR_RED
from bot.db import VALID_QUICKEMBED_PLATFORMS
from bot.io.io import fetch_previous_vote_winner

from random import choice as random_choice
from datetime import datetime, timezone
from re import compile, search as re_search
from typing import Tuple, Optional
import traceback
import logging
import asyncio
import aiohttp
import json
import base64
import time
import os


_DOWNLOAD_FILETYPE_CHOICES = [
    SlashCommandChoice(name=ext, value=ext) for ext in SUPPORTED_FORMATS.keys()
]


def random_greeting() -> str:
    return random_choice(["hi", "hello", "sup", "hola", "how's it going,"])


def format_count(count: int) -> str:
    """Format a number with commas (e.g., 1004690 -> '1,004,690')"""
    return f"{count:,} clips"


def compute_platform(url: str, bot) -> Tuple[Optional[BaseMisc], Optional[str]]:
    """Determine the platform and clip ID from the URL"""
    for this_platform in bot.platform_list:
        if slug := this_platform.parse_clip_url(url):
            return this_platform, slug

    return None, None


class Base(Extension):
    def __init__(self, bot):
        self.bot = bot
        self.ready = False
        self.logger = logging.getLogger(__name__)
        self.save_task = Task(self.db_save_task, IntervalTrigger(seconds=60 * 30))  # save db every 30 minutes
        self.cookie_refresh_task = Task(self.refresh_cookies_task, IntervalTrigger(seconds=60 * 60))  # refresh cookies every
        self.status_update_task = Task(self.update_status, IntervalTrigger(seconds=60 * 5))  # cycle status every 5 minutes
        self._status_cycle_idx = 0
        self.monthly_winner_task = Task(self.check_monthly_winner, IntervalTrigger(seconds=60 * 60))  # check every hour
        self.last_winner_month = None
        self._last_discordforge_post = float('-inf')  # discordforge.org allows 1 stats post / 5 min
        self.base_embedder = self.bot.base_embedder.embedder

    @staticmethod
    def _sanitize_url(url: str) -> str:
        # Remove Discord's url: prefix if present
        if url.startswith("url:"):
            url = url[4:]

        # Extract URL from Discord markdown link format [text](url)
        md_match = re_search(r'\[.*?]\((https?://[^\s)]+)\)', url)
        if md_match:
            url = md_match.group(1)

        # trim off extra characters at start or beginning
        while url.startswith('*') or url.startswith('[') or url.startswith('`'):
            url = url[1:]
        while url.endswith('*') or url.endswith(']') or url.endswith('`') or url.endswith(')'):
            url = url[:-1]
        if not url.startswith(("http://", "https://")):
            # Users sometimes paste extra text around the link (e.g. the whole
            # "/embed url: https://..." command into the url field). If a URL
            # is embedded in the input, use it instead of prepending a scheme
            # to text that isn't a hostname.
            embedded = re_search(r'https?://\S+', url)
            if embedded:
                url = embedded.group(0)
            else:
                url = "https://" + url
        if url.startswith("http://"):
            url = "https://" + url[7:]  # Upgrade http to https
        return url

    async def _resolve_clyppy_embed_link(self, url: str) -> Optional[str]:
        """If url is a clyppy.io/e/{id} link, resolve it to the original source URL.
        Returns the original embedded_url, or None if the clip wasn't found."""
        if not ClyppyioMisc.is_embed_link(url):
            return None
        clip_id = self.bot.clyppyio.parse_clip_url(url)
        if not clip_id:
            return None
        clip_info = await get_clip_info(clip_id, ctx_type='StoredVideo')
        if not clip_info.get('match'):
            return None
        return clip_info.get('embedded_url')

    def _get_first_clip_link(self, message_content: str) -> Optional[str]:
        """Extract the first valid clip link from a message"""
        words = self.base_embedder.get_words(message_content)
        for word in words:
            # Remove Discord's url: prefix if present
            if word.startswith("url:"):
                word = word[4:]
            for platform in self.bot.platform_embedders:
                if platform.embedder.platform_tools.is_clip_link(word):
                    return self._sanitize_url(word)  # Clean before returning
        return None

    @listen(MessageCreate)
    async def on_message_create(self, event: MessageCreate):
        if event.message.author.id == self.bot.user.id:
            # don't respond to bot's own messages
            return

        # check for greetings mentioning the bot
        msg = event.message.content
        if str(CLYPPYBOT_ID) in msg or "clyppy" in msg:
            msg_lower = msg.lower()
            self.logger.info(f"[greeting] Bot mentioned, checking: '{msg_lower}'")
            if re_search(fr'(?:hi|hello|sup|hola|hai|hey)\s*[,\.;:\-]?\s*(?:<@!?{CLYPPYBOT_ID}>|clyppy)', msg_lower):
                self.logger.info(f"[greeting] Matched! Replying to {event.message.author.username}")
                return await event.message.reply(f"{random_greeting()} <@{event.message.author.id}>")

        split = msg.split(' ')
        if msg.startswith(EMBED_TXT_COMMAND):
            # Check if it's ONLY ".embed" (reply-to mode)
            if msg.strip() == EMBED_TXT_COMMAND:
                # Fetch referenced message
                ref_msg = await event.message.fetch_referenced_message()
                if not ref_msg:
                    return await event.message.reply("Please reply to a message containing a clip link or use `.embed <URL>`")

                url = self._get_first_clip_link(event.message.content) or self._get_first_clip_link(ref_msg.content)
                if not url:
                    return await event.message.reply("No clip links found in either message")

                # Sanitize URL
                url = self._sanitize_url(url)
                for p in self.bot.platform_embedders:
                    if p.embedder.platform_tools.is_clip_link(url):
                        return await p.handle_message(event, skip_check=True, url=url)

                # No platform matched
                return await event.message.reply("Invalid or unsupported URL")

            # Original validation
            if len(split) <= 1:
                return await event.message.reply("Please provide a URL to embed like `.embed https://example.com`")
            # handle .embed command
            words = self.base_embedder.get_words(msg)  # Use msg instead of event.message.content
            for p in self.bot.platform_embedders:
                contains_clip_link, _ = p.embedder.get_next_clip_link_loc(
                    words=words,
                    n=0,
                    print=False
                )
                if contains_clip_link:
                    return await p.handle_message(event)

        # Check for text commands (with or without arguments)
        msg = msg.strip()
        TXT_COMMANDS_WITH_USER_ARG = {".profile", ".rank"}
        for txt_command, func in self.bot.base_embedder.OTHER_TXT_COMMANDS.items():
            if msg == txt_command:
                return await func(event.message)
            elif msg.startswith(txt_command + " ") and txt_command in TXT_COMMANDS_WITH_USER_ARG:
                arg = msg[len(txt_command):].strip()
                return await func(event.message, arg)

        # handle quickembed links -> both .embed and quickembed
        # will use the same function, and will both do checks to ensure if it should continue
        # but structuring like this will reduce unwanted calls handle_message()
        words = self.base_embedder.get_words(event.message.content)
        for p in self.bot.platform_embedders:
            if p.is_base:
                continue  # don't use autoembed on base embed (bot.base -> raw yt-dlp)
            contains_clip_link, _ = p.embedder.get_next_clip_link_loc(
                words=words,
                n=0,
                print=False
            )
            if contains_clip_link:
                return await p.handle_message(event)

    @component_callback(compile(r"rbtn-.*"))
    async def refresh_button_response(self, ctx: ComponentContext):
        await ctx.defer(ephemeral=True)

        clip_ctx = ctx.custom_id.split("-")
        clyppyid = clip_ctx[-1]
        resp = await refresh_clip(clyppyid, ctx.author.id)
        if resp['code'] == 200:
            await ctx.send("Clip refreshed successfully. It may take a few hours before it's viewable again in Discord.")
        elif resp['code'] == 402:
            await ctx.send("Uh oh... it seems you don't have enough tokens to refresh this clip.\n"
                           f"You have: `{resp['req_tokens']}`, while this clip requires: `{resp['tokens_needed']}`")
        elif resp['code'] == 202:
            # todo -> see if we can't provide a 'no-cache' header to discord?
            await ctx.send("Uh oh... it seems the clip has already been refreshed. Please check back in a few hours.\n")
        else:
            await ctx.send("Uh oh... an error occurred while refreshing the clip.\n"
                           f"Error code: `{resp['code']}`\n"
                           f"Message: `{resp['error']}`")

    @component_callback(compile(r"ibtn-.*"))
    async def info_button_response(self, ctx: ComponentContext):
        await ctx.defer(ephemeral=True)

        clip_ctx = ctx.custom_id.split("-")
        clyppyid = clip_ctx[-1]
        is_discord_uploaded = clip_ctx[1] == "d"  # was it a discord upload

        try:
            clyppy_cdn = False

            clip_info = await get_clip_info(clyppyid, ctx_type='BotInteraction' if is_discord_uploaded else 'StoredVideo')
            self.logger.info(f"@component_callback for button {ctx.custom_id} - clip_info: {clip_info}")
            if clip_info['match']:
                clip_url = clip_info['url']

                original = clip_info['requested_by']
                if original is not None:
                    original = int(original)

                cmp_url = 'https://clyppy.io/profile/clips'
                cpm_params = f'?msgid={ctx.message.id}&clipid={clyppyid}'
                buttons = [
                    Button(
                        style=ButtonStyle.DANGER,
                        label="X",
                        custom_id=f"ibtn-delete-d-{clyppyid}" if is_discord_uploaded else f"ibtn-delete-{clyppyid}"
                    ),
                    Button(style=ButtonStyle.LINK, label=f"View your clips", url=cmp_url + cpm_params)
                ]

                deleted = clip_info['is_deleted']
                dstr = clip_info['deleted_at_str']

                dyr = clip_info['duration']
                if dyr is None:
                    dyr = 0

                embed = Embed(title=f"{clip_info['title']}")
                if original is not None:
                    embed.add_field(name="Command", value=f"<@{original}> used `.embed` {clip_info['embedded_url']}")
                    #embed.add_field(name="Requested by", value=f'<@{original}>')
                else:
                    embed.add_field(name="Command", value=f"`.embed` {clip_info['embedded_url']}")

                if clip_info['platform'] != 'base':
                    embed.add_field(name="Platform", value=clip_info['platform'])

                embed.add_field(
                    name="Duration",
                    value=f"{dyr // 60}m {round(dyr % 60, 2)}s"
                )

                embed.add_field(name="Clip ID", value=clyppyid)

                if not is_discord_uploaded:
                    clyppy_cdn = 'https://clyppy.io/media/' in clip_url or 'https://cdn.clyppy.io' in clip_url
                    embed.add_field(
                        name="File Location",
                        value=clip_url if clyppy_cdn else f"Hosted on {clip_info['platform']}'s cdn"
                    )
                    if clyppy_cdn and not deleted:
                        expires_dt = None if clip_info['expires_at'] is None else datetime.fromtimestamp(clip_info['expires_at'], tz=timezone.utc)
                        if expires_dt is not None and expires_dt > datetime.now(timezone.utc):
                            exp_str = "Expires"
                        else:
                            exp_str = "Expired"
                            buttons.pop(-1)  # remove the "View your clips" button
                            buttons.append(Button(style=ButtonStyle.BLURPLE, label="File Expired - Refresh?", custom_id=f"rbtn-{clyppyid}"))
                        embed.add_field(name=exp_str, value=f"{clip_info['expiry_ts_str']}")
                    elif clyppy_cdn and deleted:
                        embed.add_field(name="Deleted", value=dstr if dstr is not None else "True")

                await ctx.send(embed=embed, components=buttons)

                if not is_discord_uploaded:
                    # from external/clyppy cdn
                    await send_webhook(
                        title=f'{"DM" if ctx.guild is None else ctx.guild.name}, {ctx.author.username} - \'info\' called on {clyppyid}',
                        load=f"response - success"
                             f"title: {clip_info['title']}\n"
                             f"url: {clip_info['embedded_url']}\n"
                             f"platform: {clip_info['platform']}\n"
                             f"duration: {dyr}\n"
                             f"file_location: {clip_info['url'] if clyppy_cdn else 'Hosted on ' + str(clip_info['platform']) + ' cdn'}\n"
                             f"expires: {clip_info['expiry_ts_str'] if clyppy_cdn else 'N/A'}"
                             f"deleted: {deleted}",
                        color=COLOR_GREEN,
                        url=APPUSE_LOG_WEBHOOK,
                        logger=self.logger
                    )
                else:
                    # uploaded to discord
                    await send_webhook(
                        title=f'{"DM" if ctx.guild is None else ctx.guild.name}, {ctx.author.username} - \'info\' called on {clyppyid}',
                        load=f"response - success"
                             f"title: {clip_info['title']}\n"
                             f"url: {clip_info['embedded_url']}\n"
                             f"platform: {clip_info['platform']}\n"
                             f"duration: {dyr}\n",
                        color=COLOR_GREEN,
                        url=APPUSE_LOG_WEBHOOK,
                        logger=self.logger
                    )
            else:
                await ctx.send(f"Uh oh... it seems the clip {clyppyid} doesn't exist!")
                await send_webhook(
                    title=f'{"DM" if ctx.guild is None else ctx.guild.name}, {ctx.author.username} - \'info\' called on {clyppyid}',
                    load=f"response - error clip not found",
                    color=COLOR_RED,
                    url=APPUSE_LOG_WEBHOOK,
                    logger=self.logger
                )
        except Exception as e:
            self.logger.info(f"@component_callback for button {ctx.custom_id} - Error: {e}")
            await ctx.send(f"Uh oh... an error occurred fetching the clip {clyppyid}")
            await send_webhook(
                title=f'{"DM" if ctx.guild is None else ctx.guild.name}, {ctx.author.username} - \'info\' called on {clyppyid}',
                load=f"response - unexpected error: {e}",
                color=COLOR_RED,
                url=APPUSE_LOG_WEBHOOK,
                logger=self.logger
            )

    @component_callback(compile(r"ibtn-delete-.*"))
    async def delete_button_response(self, ctx: ComponentContext):
        clip_ctx = ctx.custom_id.split("-")
        clyppyid = clip_ctx[-1]
        is_discord_uploaded = clip_ctx[-2] == "d"

        await ctx.send(
            content=f"Are you sure you want to continue? This will delete all CLYPPY embeds you\'ve requested of this clip.",
            ephemeral=True,
            components=[
                Button(
                    style=ButtonStyle.SUCCESS,
                    label="Confirm",
                    custom_id=f"ibtn-confirm-delete-d-{clyppyid}" if is_discord_uploaded else f"ibtn-confirm-delete-{clyppyid}"
                )
            ]
        )

    @component_callback(compile(r"ibtn-confirm-delete-.*"))
    async def confirm_delete_button_response(self, ctx: ComponentContext):
        await ctx.defer(ephemeral=True)
        clip_ctx = ctx.custom_id.split("-")
        clyppyid = clip_ctx[-1]
        is_discord_uploaded = clip_ctx[-2] == "d"

        success_codes = [200, 201, 404]  # all the status codes where we wouldn't want to re-add reqqed by on error

        self.logger.info(f"{ctx.message.id}, {ctx.id}, {ctx.message_id}")
        data = {"video_id": clyppyid, "user_id": ctx.author.id, "msg_id": ctx.message.id}

        cmp_url = 'https://clyppy.io/profile/clips'
        try:
            response = await callback_clip_delete_msg(
                data=data,
                key=os.getenv('clyppy_post_key'),
                ctx_type='BotInteraction' if is_discord_uploaded else 'StoredVideo'
            )
            self.logger.info(f"@component_callback for button {ctx.custom_id} - response: {response}")
            if response['code'] == 401:
                raise Exception(f"Unauthorized: User <@{ctx.author.id}> did not embed this clip!")
            elif response['code'] not in success_codes:
                raise Exception(f"Error: {response['code']}")
            elif response['ctx'] is not None:
                # maybe there's more than 1 message by this user of this clip
                delete_tasks = []
                for clip in response['ctx']:
                    try:
                        # clyppy uploads the clip to clyppyio with the serverid as the userid if it's uploaded inside that user's DM with CLYPPY
                        is_dm = str(clip['server_id']) == str(ctx.author.id)
                        if not clip['message_id']:
                            self.logger.warning(f"clip missing message_id: {clip.__dict__}")
                            continue
                        if is_dm:
                            chn = await ctx.author.fetch_dm(force=False)
                            msg: Message | None = await chn.fetch_message(clip['message_id'])
                        else:
                            chn: TYPE_ALL_CHANNEL | None = await self.bot.fetch_channel(clip['channel_id'])
                            if chn is None or isinstance(chn, GuildForum) or isinstance(chn, GuildCategory):
                                self.logger.warning(f"clip {clip['channel_id']} not found, or may be incorrect Discord channel type")
                                continue
                            msg: Message | None = await chn.fetch_message(clip['message_id'])

                        if msg is not None:
                            delete_tasks.append(asyncio.create_task(msg.delete()))
                    except Exception as e:
                        self.logger.info(f"@component_callback for button {ctx.custom_id} - Could not delete message {clip['message_id']} from channel {clip['channel_id']}: {str(e)}")
                await asyncio.gather(*delete_tasks)

        except Exception as e:
            self.logger.info(f"@component_callback for button {ctx.custom_id} - Error: {e}")
            await ctx.send(f"Uh oh... an error occurred deleting the clip {clyppyid}:\n{str(e)}", components=[Button(style=ButtonStyle.LINK, label=f"View your clips", url=cmp_url)])
            await send_webhook(
                title=f'{"DM" if ctx.guild is None else ctx.guild.name}, {ctx.author.username} - \'delete\' called on {clyppyid}',
                load=f"response - unexpected error: {e}",
                color=COLOR_RED,
                url=APPUSE_LOG_WEBHOOK,
                logger=self.logger
            )

            if 'Unauthorized' in str(e):
                return
            elif is_discord_uploaded:
                return

            try:
                await add_reqqed_by(data, key=os.getenv('clyppy_post_key'))
            except Exception as e:
                self.logger.info(f"@component_callback for button {ctx.custom_id} - Could not re-add reqqed by for user {ctx.author.id}")
            return

        await ctx.send("The clip has been deleted." if not is_discord_uploaded else "All embeds you've requested of this clip have been deleted.")
        await send_webhook(
            title=f'{"DM" if ctx.guild is None else ctx.guild.name}, {ctx.author.username} - \'delete\' called on {clyppyid}',
            load=f"response - success"
                 f"title: {clyppyid}",
            color=COLOR_GREEN,
            url=APPUSE_LOG_WEBHOOK,
            logger=self.logger
        )

    @component_callback(compile(r"dlbtn-info-.*"))
    async def dl_info_button(self, ctx: ComponentContext):
        await ctx.defer(ephemeral=True)
        parts = ctx.custom_id.split("-")  # dlbtn-info-{user_id}-{clyppy_id}
        clyppy_id = parts[-1]
        # Extract CDN URL from the message content (last line)
        cdn_url = None
        if ctx.message and ctx.message.content:
            for line in ctx.message.content.split("\n"):
                if line.startswith("http"):
                    cdn_url = line.strip()
        embed = Embed(title="Download Info")
        embed.add_field(name="Clip ID", value=clyppy_id)
        if cdn_url:
            embed.add_field(name="CDN URL", value=cdn_url)
        clip_info = await get_clip_info(clyppy_id, ctx_type='StoredVideo')
        if clip_info.get('match'):
            if clip_info.get('embedded_url'):
                embed.add_field(name="Original Link", value=clip_info['embedded_url'])
            if clip_info.get('expiry_ts_str'):
                embed.add_field(name="Expires", value=clip_info['expiry_ts_str'])
        embed.add_field(name="Backup", value=f"https://clyppy.io/{clyppy_id}/backup")
        await ctx.send(embed=embed)

    @component_callback(compile(r"dlbtn-delete-.*"))
    async def dl_delete_button(self, ctx: ComponentContext):
        parts = ctx.custom_id.split("-")  # dlbtn-delete-{user_id}-{clyppy_id}
        owner_id = int(parts[2])
        if ctx.author.id != owner_id:
            await ctx.send("Only the person who used `/download` can delete this.", ephemeral=True)
            return
        try:
            await ctx.message.delete()
        except Exception as e:
            self.logger.error(f"Failed to delete /download message: {e}")
            await ctx.send("Failed to delete the message.", ephemeral=True)

    @component_callback(compile(r"embdl-.*"))
    async def embed_download_button(self, ctx: ComponentContext):
        await ctx.defer(ephemeral=True)
        clyppy_id = ctx.custom_id.split("-", 1)[1]

        clip_info = await get_clip_info(clyppy_id, ctx_type='StoredVideo')
        if not clip_info.get('match') or not clip_info.get('embedded_url'):
            await ctx.send("Could not find the original clip. Please try again.", ephemeral=True)
            return

        embedded_url = clip_info['embedded_url']
        fallback_url = f"https://clyppy.io/clip-downloader?clip={embedded_url}"

        if ctx.guild is not None:
            file_ext = self.bot.guild_settings.get_default_download_filetype(ctx.guild.id)
        else:
            file_ext = 'mp4'

        try:
            await self._run_download_pipeline(ctx, embedded_url, file_ext)
        except Exception:
            await ctx.send(
                f"The inline download failed. Please visit {fallback_url} in your browser to continue.",
                ephemeral=True
            )

    @component_callback(compile(r"server_rank_.*"))
    async def server_rank_button(self, ctx: ComponentContext):
        """Handle server ranking pagination button clicks."""
        await ctx.defer(edit_origin=True)

        # Parse custom_id: server_rank_{action}_{encoded_state}
        parts = ctx.custom_id.split("_", 3)
        action = parts[2]  # first, prev, next, last
        encoded_state = parts[3]

        # Decode state
        state_json = base64.b64decode(encoded_state).decode('utf-8')
        state_dict = json.loads(state_json)
        state = ServerRankPaginationState(**state_dict)

        # Calculate new page
        if action == "first":
            new_page = 1
        elif action == "prev":
            new_page = max(1, state.page - 1)
        elif action == "next":
            new_page = min(state.total_pages, state.page + 1)
        elif action == "last":
            new_page = state.total_pages
        else:
            return  # Invalid action

        # Fetch new page data
        data = await ServerRankPagination.fetch_ranking_data(
            guild_id=state.guild_id,
            page=new_page,
            time_period=state.time_period
        )

        if not data["success"]:
            await ctx.send("Failed to load page. Please try again.", ephemeral=True)
            return

        # Update state
        state.page = new_page

        # Create new embed and buttons
        embed = ServerRankPagination.create_embed(
            ranking_data=data["data"],
            page=new_page,
            total_pages=state.total_pages,
            guild_id=state.guild_id,
            entries_per_page=state.entries_per_page
        )

        buttons = ServerRankPagination.create_buttons(
            page=new_page,
            total_pages=state.total_pages,
            state=state
        )

        # Update message
        await ctx.edit_origin(embed=embed, components=buttons)

    @component_callback(compile(r"ur_.*"))
    async def user_rank_button(self, ctx: ComponentContext):
        """Handle user ranking pagination button clicks."""
        await ctx.defer(edit_origin=True)

        # Parse compact custom_id: ur_{action}_{user_id}_{tp}_{page}_{total}_{bots}_{ts}
        parts = ctx.custom_id.split("_")
        action = parts[1]  # f, p, n, l
        user_id = parts[2]
        tp_code = parts[3]
        current_page = int(parts[4])
        total_pages = int(parts[5])
        include_bots = parts[6] == "1"

        # Decode time period
        time_period = {"a": "all", "w": "week", "m": "month", "t": "today"}.get(tp_code, "all")
        requester_id = str(ctx.author.id)

        # Calculate new page
        if action == "f":
            new_page = 1
        elif action == "p":
            new_page = max(1, current_page - 1)
        elif action == "n":
            new_page = min(total_pages, current_page + 1)
        elif action == "l":
            new_page = total_pages
        else:
            return

        # Fetch new page data
        data = await UserRankPagination.fetch_ranking_data(
            page=new_page,
            time_period=time_period,
            requester_id=requester_id,
            include_bots=include_bots
        )
        if not data.get("success"):
            await ctx.send("Failed to load page. Please try again.", ephemeral=True)
            return

        # Create state for buttons
        state = UserRankPaginationState(
            user_id=user_id,
            time_period=time_period,
            page=new_page,
            total_pages=total_pages,
            include_bots=include_bots
        )

        # Create new embed and buttons
        embed = UserRankPagination.create_embed(
            ranking_data=data["data"],
            page=new_page,
            total_pages=total_pages,
            user_id=user_id,
            time_period=time_period,
            top_user=data.get("top_user")
        )
        buttons = UserRankPagination.create_buttons(
            page=new_page,
            total_pages=total_pages,
            state=state
        )

        # Update message
        await ctx.edit_origin(embed=embed, components=buttons)

    @slash_command(name="save", description="Save Clyppy DB", scopes=[759798762171662399])
    async def save(self, ctx: SlashContext):
        await ctx.defer()
        await ctx.send("Saving DB...")
        await self.bot.guild_settings.save()
        await self.post_servers(len(self.bot.guilds))
        await ctx.send("You can now safely exit.")

    @slash_command(name="rank", description="View your voting rank for this month!")
    async def rank(self, ctx: SlashContext):
        await self.bot.base_embedder.rank_cmd(ctx)

    @slash_command(name="vote", description="Vote on Clyppy to gain exclusive rewards!")
    async def vote(self, ctx: SlashContext):
        await self.bot.base_embedder.vote_cmd(ctx)

    @slash_command(name="tokens", description="View your VIP tokens!")
    async def tokens(self, ctx: SlashContext):
        await ctx.defer()
        await self.bot.base_embedder.tokens_cmd(ctx)

    @slash_command(name="myclips", description="View your personal clip library")
    async def myclips(self, ctx: SlashContext):
        await self.bot.base_embedder.myclips_cmd(ctx)

    @slash_command(name="invite", description="Display a link to invite Clyppy to your server")
    async def invite(self, ctx: SlashContext):
        await self.bot.base_embedder.invite_cmd(ctx)

    @slash_command(name="profile",
                   sub_cmd_name="info",
                   sub_cmd_description="View your Clyppy profile",
                   options=[SlashCommandOption(
                       name="user",
                       description="User ID or username",
                       required=False,
                       type=OptionType.STRING)
                   ])
    async def profile(self, ctx: SlashContext, user: str = None):
        await self.bot.base_embedder.profile_cmd(ctx, user)

    @slash_command(name="profile",
                   sub_cmd_name="rank",
                   sub_cmd_description="View your ranking in clip embeds",
                   options=[
                       SlashCommandOption(
                           name="user",
                           description="User ID or username (defaults to yourself)",
                           required=False,
                           type=OptionType.STRING
                       ),
                       SlashCommandOption(
                           name="time_period",
                           description="Time period for ranking",
                           required=False,
                           type=OptionType.STRING,
                           choices=[
                               SlashCommandChoice(name="All Time", value="all"),
                               SlashCommandChoice(name="This Week", value="week"),
                               SlashCommandChoice(name="This Month", value="month"),
                               SlashCommandChoice(name="Today", value="today"),
                           ]
                       ),
                       SlashCommandOption(
                           name="bots",
                           description="Include bots in rankings (default: No)",
                           required=False,
                           type=OptionType.BOOLEAN
                       )
                   ])
    async def profile_rank(self, ctx: SlashContext, user: str = None, time_period: str = "all", bots: bool = False):
        await self.bot.base_embedder.profile_rank_cmd(ctx, user, time_period, bots)

    @slash_command(name="embed", description="Embed a video link in this chat",
                   options=[SlashCommandOption(
                       name="url",
                       description="The YouTube, Twitch, etc. link to embed",
                       required=True,
                       type=OptionType.STRING)
                   ])
    async def embed(self, ctx: SlashContext, url: str):
        # Defer IMMEDIATELY before any processing to ensure we respond within 3s
        await ctx.defer()

        self.logger.info(f"@slash_command for /embed - {ctx.author.id} - {url}")
        url = self._sanitize_url(url)

        # Check if bot is shutting down
        if self.bot.is_shutting_down:
            self.logger.info(f"Bot is shutting down, queueing /embed command for {url}")

            try:
                # Interaction already deferred at the top of this function

                task = SlashCommandTask(
                    interaction_id=int(ctx.id),
                    interaction_token=ctx.token,
                    channel_id=int(ctx.channel_id),
                    channel_name=ctx.channel.name if hasattr(ctx.channel, 'name') else 'unknown-channel',
                    guild_id=int(ctx.guild_id) if ctx.guild else None,
                    guild_name=ctx.guild.name if ctx.guild else None,
                    user_id=int(ctx.author.id),
                    user_username=ctx.author.username,
                    clip_url=url,
                    extend_with_ai=False
                )
                self.bot.task_queue.add_slash_command(task)
                self.logger.info(f"Successfully queued task for {url}")
            except Exception as e:
                self.logger.error(f"Failed to queue task during shutdown: {e}")
                self.logger.error(traceback.format_exc())
            # Don't send any response - the deferred state will be resumed on restart
            return

        # Clyppy /e/ links are already embedded — just resend the link
        if ClyppyioMisc.is_embed_link(url):
            await ctx.send(url)
            return

        for p in self.bot.platform_embedders:
            if slug := p.platform.parse_clip_url(url):
                await self.bot.base_embedder.command_embed(
                    ctx=ctx,
                    already_deferred=True,
                    url=url,
                    platform=p.platform,
                    slug=slug
                )
                return
        # incompatible (should never get here, since bot.base is a catch-all)
        await ctx.send("An unexpected error occurred.")
        raise Exception(f"Error in /embed - bot.base did not catch url {url}, exited returning None")


    async def _run_download_pipeline(self, ctx, url: str, file_ext: str, interaction_type: str = 'download'):
        """Core download pipeline: fetch → convert → upload to CDN → respond.

        Called by both the /download slash command and the embed Download button callback.
        Token refund and file cleanup are handled internally. Raises on failure so callers
        can display appropriate error messages.

        interaction_type: 'download' or 'giphify' — recorded on the BotInteraction row.
        """
        platform = None
        slug = None
        for p in self.bot.platform_embedders:
            if s := p.platform.parse_clip_url(url):
                platform = p.platform
                slug = s
                break
        if not platform or not slug:
            await ctx.send("Could not recognize that URL.")
            return

        if platform.is_nsfw is None:
            platform.is_nsfw = await platform.check_url_is_nsfw(url)
        if platform.is_nsfw:
            nsfw_allowed = ctx.guild is None or getattr(ctx.channel, 'nsfw', False)
            if not nsfw_allowed:
                await ctx.send(
                    "( ͡~ ͜ʖ ͡°) This platform is not allowed in this channel. "
                    "A server admin can enable it by going to `Edit Channel > Overview` and toggling `Age-Restricted Channel`."
                )
                return

        if self.bot.currently_embedding_users.count(ctx.author.id) >= 2:
            await ctx.send("You're already embedding 2 videos. Please wait for one to finish before trying again.")
            return

        self.bot.currently_embedding_users.append(ctx.author.id)
        clip = None
        original_path = None
        converted_path = None
        try:
            clip = await platform.get_clip(url=url, basemsg=ctx)
            if clip is None:
                await ctx.send("Failed to fetch clip from that URL.")
                return

            clip._clyppy_id_input = f"{clip.service}{clip.id}_{file_ext}"
            await clip.compute_clyppy_id()

            local_info = await self.bot.tools.dl.download_clip(
                clip=clip,
                can_send_files=False,
                skip_upload=True
            )
            original_path = local_info.local_file_path

            base_name = original_path.rsplit('.', 1)[0]
            converted_path = f"{base_name}_converted.{file_ext}"
            await convert_media(original_path, converted_path, file_ext)

            content_type = SUPPORTED_FORMATS[file_ext]
            success, cdn_url = await self.bot.cdn_client.cdn_upload_video(
                file_path=converted_path,
                storage_type="temp",
                content_type=content_type
            )
            if not success:
                raise RuntimeError("CDN upload failed")

            out_duration = float(local_info.duration or 0)
            out_width = 0 if file_ext in AUDIO_FORMATS else int(local_info.width or 0)
            out_height = 0 if file_ext in AUDIO_FORMATS else int(local_info.height or 0)
            try:
                out_file_size = os.path.getsize(converted_path)
            except OSError:
                out_file_size = 0
            probed = await ffprobe_video_metadata(converted_path)
            if probed is not None:
                if probed['duration'] > 0:
                    out_duration = probed['duration']
                if file_ext not in AUDIO_FORMATS:
                    if probed['width']:
                        out_width = probed['width']
                    if probed['height']:
                        out_height = probed['height']
            else:
                self.logger.warning(f"ffprobe returned no metadata for {converted_path}; using source values")

            try:
                reg_resp = await register_download(
                    clyppy_id=clip.clyppy_id,
                    service=clip.service,
                    title=(clip.title or getattr(local_info, 'video_name', None) or 'Clyppy Download'),
                    file_url=cdn_url,
                    file_size=out_file_size,
                    duration=out_duration,
                    width=out_width,
                    height=out_height,
                    file_ext=file_ext,
                    user_id=ctx.author.id,
                    username=ctx.author.username or f"User_{ctx.author.id}",
                    original_url=url,
                    guild_id=(ctx.guild.id if ctx.guild else None),
                    guild_name=(ctx.guild.name if ctx.guild else None),
                    channel_name=(getattr(ctx.channel, 'name', None) if ctx.channel else None),
                    is_nsfw=bool(platform.is_nsfw),
                    tokens_used=clip.tokens_used,
                )
                if not reg_resp.get('success'):
                    self.logger.error(
                        f"[/download] register_download FAILED for {clip.clyppy_id}: {reg_resp.get('error')}. "
                        f"CDN file at {cdn_url} will leak until manual cleanup."
                    )
            except Exception as reg_err:
                self.logger.error(
                    f"[/download] register_download raised for {clip.clyppy_id}: {reg_err}. "
                    f"CDN file at {cdn_url} will leak until manual cleanup."
                )

            try:
                interaction_data = {
                    'edit': False,
                    'create_new_video': False,
                    'interaction_type': interaction_type,
                    'server_name': (ctx.guild.name if ctx.guild else 'DM'),
                    'channel_name': (getattr(ctx.channel, 'name', None) or 'DM'),
                    'user_name': ctx.author.username or f"User_{ctx.author.id}",
                    'server_id': str(ctx.guild.id) if ctx.guild else str(ctx.author.id),
                    'channel_id': str(ctx.channel.id) if ctx.channel else '0',
                    'user_id': str(ctx.author.id),
                    'embedded_url': url,
                    'url_platform': platform.platform_name,
                    'title': clip.title or getattr(local_info, 'video_name', None) or ('Clyppy GIF' if interaction_type == 'giphify' else 'Clyppy Download'),
                    'generated_id': clip.clyppy_id,
                    'original_id': clip.id,
                    'video_file_size': out_file_size,
                    'video_file_dur': out_duration,
                    'remote_video_width': out_width,
                    'remote_video_height': out_height,
                    'uploaded_to_discord': False,
                    'is_redirect': False,
                    'is_extended': False,
                    'user_is_bot': ctx.author.bot,
                    'tokens_used': clip.tokens_used,
                    'response_time_seconds': 0,
                    'total_servers_now': len(self.bot.guilds),
                    'remote_file_url': cdn_url,
                    'video_uploader_username': getattr(clip, 'video_uploader_username', None),
                    'broadcaster_username': getattr(clip, 'broadcaster_username', None),
                    'expires_at_timestamp': None,
                    'thumbnail': None,
                }
                await publish_interaction(
                    interaction_data=interaction_data,
                    apikey=os.getenv('clyppy_post_key'),
                    logger=self.logger,
                )
            except Exception as pub_err:
                self.logger.warning(
                    f"[/{interaction_type}] publish_interaction failed for {clip.clyppy_id}: {pub_err}"
                )

            buttons = ActionRow(
                Button(style=ButtonStyle.LINK, label="Download", url=cdn_url),
                Button(style=ButtonStyle.SUCCESS, label="Back it up", custom_id=f"dlbtn-backup-{ctx.author.id}-{clip.clyppy_id}"),
                Button(style=ButtonStyle.SECONDARY, label="ⓘ Info", custom_id=f"dlbtn-info-{ctx.author.id}-{clip.clyppy_id}"),
                Button(style=ButtonStyle.DANGER, label="X", custom_id=f"dlbtn-delete-{ctx.author.id}-{clip.clyppy_id}"),
            )
            await ctx.send(
                content=f"Here's your converted `.{file_ext}` file. It will expire in 24 hours.\n{cdn_url}",
                components=buttons
            )

        except Exception:
            if clip and clip.tokens_used > 0:
                asyncio.create_task(subtract_tokens(
                    user=ctx.author,
                    amt=-1 * clip.tokens_used,
                    clip_url=url,
                    reason="Token Refund",
                    description=f"The /download conversion failed for {url}"
                ))
            raise
        finally:
            if original_path:
                tryremove(original_path)
            if converted_path:
                tryremove(converted_path)
            while ctx.author.id in self.bot.currently_embedding_users:
                self.bot.currently_embedding_users.remove(ctx.author.id)

    @slash_command(name="giphify", description="Convert a video to a GIF",
                   options=[SlashCommandOption(
                       name="url",
                       description="The video URL to convert to GIF",
                       required=True,
                       type=OptionType.STRING)
                   ])
    async def giphify(self, ctx: SlashContext, url: str):
        await ctx.defer()
        url = self._sanitize_url(url)

        # Resolve clyppy.io/e/ links to their original source URL for yt-dlp
        if ClyppyioMisc.is_embed_link(url):
            original_url = await self._resolve_clyppy_embed_link(url)
            if original_url:
                self.logger.info(f"/giphify: resolved clyppy /e/ link to original URL: {original_url}")
                url = original_url
            else:
                await ctx.send("Could not find the original video for this clyppy link.")
                return

        if self.bot.is_shutting_down:
            self.logger.info(f"Bot is shutting down, queueing /giphify for {url}")
            try:
                task = SlashCommandTask(
                    interaction_id=int(ctx.id),
                    interaction_token=ctx.token,
                    channel_id=int(ctx.channel_id),
                    channel_name=ctx.channel.name if hasattr(ctx.channel, 'name') else 'unknown-channel',
                    guild_id=int(ctx.guild_id) if ctx.guild else None,
                    guild_name=ctx.guild.name if ctx.guild else None,
                    user_id=int(ctx.author.id),
                    user_username=ctx.author.username,
                    clip_url=url,
                    extend_with_ai=False,
                    file_ext="gif",
                )
                self.bot.task_queue.add_slash_command(task)
                self.logger.info(f"Successfully queued giphify task for {url}")
            except Exception as e:
                self.logger.error(f"Failed to queue giphify task during shutdown: {e}")
                self.logger.error(traceback.format_exc())
            return

        await self._run_download_pipeline(ctx, url, "gif", interaction_type='giphify')

    @slash_command(name="download", description="Download and convert a video to a different format",
                   options=[
                       SlashCommandOption(
                           name="url",
                           description="The video URL to download",
                           required=True,
                           type=OptionType.STRING),
                       SlashCommandOption(
                           name="file_ext",
                           description="Output format (defaults to server setting, then mp4)",
                           required=False,
                           type=OptionType.STRING,
                           choices=_DOWNLOAD_FILETYPE_CHOICES)
                   ])
    async def download(self, ctx: SlashContext, url: str, file_ext: str = None):
        await ctx.defer()

        if file_ext is None:
            if ctx.guild is not None:
                file_ext = self.bot.guild_settings.get_default_download_filetype(ctx.guild.id)
            else:
                file_ext = 'mp4'
        self.logger.info(f"@slash_command for /download - {ctx.author.id} - {url} -> {file_ext}")
        url = self._sanitize_url(url)
        file_ext = file_ext.lower().strip().lstrip('.')

        # Resolve clyppy.io/e/ links to their original source URL for yt-dlp
        if ClyppyioMisc.is_embed_link(url):
            original_url = await self._resolve_clyppy_embed_link(url)
            if original_url:
                self.logger.info(f"/download: resolved clyppy /e/ link to original URL: {original_url}")
                url = original_url
            else:
                await ctx.send("Could not find the original video for this clyppy link.")
                return

        if file_ext not in SUPPORTED_FORMATS:
            await ctx.send(f"Unsupported format `{file_ext}`. Supported: {', '.join(SUPPORTED_FORMATS.keys())}")
            return

        if self.bot.is_shutting_down:
            self.logger.info(f"Bot is shutting down, queueing /download for {url} -> {file_ext}")
            try:
                task = SlashCommandTask(
                    interaction_id=int(ctx.id),
                    interaction_token=ctx.token,
                    channel_id=int(ctx.channel_id),
                    channel_name=ctx.channel.name if hasattr(ctx.channel, 'name') else 'unknown-channel',
                    guild_id=int(ctx.guild_id) if ctx.guild else None,
                    guild_name=ctx.guild.name if ctx.guild else None,
                    user_id=int(ctx.author.id),
                    user_username=ctx.author.username,
                    clip_url=url,
                    extend_with_ai=False,
                    file_ext=file_ext,
                )
                self.bot.task_queue.add_slash_command(task)
                self.logger.info(f"Successfully queued download task for {url} -> {file_ext}")
            except Exception as e:
                self.logger.error(f"Failed to queue download task during shutdown: {e}")
                self.logger.error(traceback.format_exc())
            return

        try:
            await self._run_download_pipeline(ctx, url, file_ext)
        except Exception as e:
            self.logger.error(f"Error in /download: {e}")
            self.logger.error(traceback.format_exc())
            friendly = friendly_yt_dlp_error_message(e)
            if friendly is not None:
                await ctx.send(friendly)
            elif isinstance(e, (VideoTooLong, VideoLongerThanMaxLength)):
                dur = e.video_dur if hasattr(e, 'video_dur') else 0
                dur_min = dur / 60
                buy_vote_buttons = [
                    Button(style=ButtonStyle.LINK, label="Buy Tokens", url=BUY_TOKENS_URL),
                    Button(style=ButtonStyle.LINK, label="Free Tokens (Vote)", url=CLYPPY_VOTE_URL),
                    Button(style=ButtonStyle.LINK, label="Free Tokens (Join Discord)", url=SUPPORT_SERVER_URL),
                ]
                if dur >= EMBED_TOTAL_MAX_LENGTH:
                    await ctx.send(
                        f"I can't process videos longer than {EMBED_TOTAL_MAX_LENGTH // (60 * 60)} hours total, "
                        f"even with Clyppy VIP Tokens. ({dur_min:.1f} minutes)"
                    )
                else:
                    user_tokens = await self.bot.base_embedder.fetch_tokens(ctx.author)
                    video_cost = get_token_cost(dur)
                    if 0 < user_tokens < video_cost:
                        await ctx.send(
                            f"This video is too long to download ({dur_min:.1f} minutes).\n"
                            f"You can normally use `/download` on videos under {MAX_VIDEO_LEN_SEC / 60:.0f} minutes, "
                            f"but every {EMBED_TOKEN_COST} token can add {EMBED_W_TOKEN_MAX_LEN / 60:.0f} minutes of video time.\n"
                            f"You have `{user_tokens}` tokens available.\n"
                            f"This video would cost `{video_cost}` VIP tokens.",
                            components=buy_vote_buttons
                        )
                    else:
                        await ctx.send(
                            f"This video is too long to download (longer than {MAX_VIDEO_LEN_SEC / 60:.0f} minutes).\n"
                            f"Voting with `/vote` will earn you tokens to unlock longer downloads "
                            f"({EMBED_W_TOKEN_MAX_LEN // 60} more minutes per token).",
                            components=buy_vote_buttons
                        )
            else:
                await ctx.send(f"Failed to convert: `{type(e).__name__}`. Please try again or report this in our support server if it keeps happening.")

    @staticmethod
    async def _run_backup_flow(ctx, clip_id: str):
        clip_id = clip_id.strip().strip('/')
        if '/' in clip_id:
            clip_id = clip_id.rstrip('/').split('/')[-1]

        preview = await preview_backup(user_id=ctx.author.id, clip_id=clip_id)
        if not preview.get('success'):
            err = preview.get('error', 'unknown')
            if err == 'clip_not_found':
                await ctx.send(
                    f"That clip doesn't exist in my database, or it wasn't embedded via `/embed`. "
                    f"Only `/embed` clips can be backed up."
                )
            elif err == 'no_cdn_file':
                await ctx.send(
                    f"That clip can't be backed up — it's hosted on the original platform's CDN, not on Clyppy's servers. "
                    f"Only clips that Clyppy has downloaded and stored can be backed up."
                )
            else:
                await ctx.send(f"Error: `{err}`. Please try again later.")
            return

        clip_title = preview.get('clip_title', 'Untitled')
        monthly_cost = preview.get('monthly_cost', 0)
        first_charge = preview.get('first_charge', 0)
        days_covered = preview.get('days_covered', 0)
        user_tokens = preview.get('user_tokens', 0)
        user_current_position = preview.get('user_current_position')
        active_sponsor = preview.get('active_sponsor')
        expires_iso = preview.get('clip_server_expires_at')

        # Case 3: user already in queue
        if user_current_position is not None:
            pos_label = "active sponsor" if user_current_position == 0 else f"reserve position `{user_current_position}`"
            await ctx.send(
                f"You're already backing up `{clip_title}` — {pos_label}.\n"
                f"You can cancel or change privacy at https://clyppy.io/profile/backups"
            )
            return

        # Compute relative expiry for display
        expires_display = ""
        if expires_iso:
            try:
                expires_dt = datetime.fromisoformat(expires_iso.replace('Z', '+00:00'))
                expires_display = f"<t:{int(expires_dt.timestamp())}:R>"
            except Exception:
                expires_display = expires_iso

        # Case 2: clip already has an active sponsor — joining reserve
        if active_sponsor is not None:
            sponsor_name = f"**{active_sponsor['username']}**" if not active_sponsor['is_anonymous'] and active_sponsor.get('username') else "an anonymous user"
            reserve_size = preview.get('reserve_size', 0)
            my_reserve_pos = reserve_size + 1

            msg_lines = [
                f"This clip (`{clip_title}`) is already backed up by {sponsor_name}.",
                f"It will stay available as long as they keep paying.",
                "",
                f"Would you like to join the reserve? If they ever stop backing up this clip",
                f"(cancel or run out of tokens), and you have enough tokens at that time,",
                f"the clip will stay available and you'll start paying.",
                "",
                f"You would join at reserve position `{my_reserve_pos}`.",
                f"Monthly cost (when active): `{monthly_cost}` tokens — billing starts only if promoted.",
            ]

            if user_tokens < monthly_cost:
                msg_lines.extend([
                    "",
                    f"⚠️ You have `{user_tokens}` tokens. You can still join the reserve, but if you're promoted "
                    f"while still below `{monthly_cost}`, you'll be skipped to the next user. "
                    f"If you're the only user in the reserve at that point, the clip will expire normally."
                ])

            buttons = [
                Button(style=ButtonStyle.SUCCESS, label="Join reserve (public)", custom_id=f"bkbtn-confirm-pub-{ctx.author.id}-{clip_id}"),
                Button(style=ButtonStyle.SUCCESS, label="Join reserve (anonymous)", custom_id=f"bkbtn-confirm-anon-{ctx.author.id}-{clip_id}"),
                Button(style=ButtonStyle.DANGER, label="Cancel", custom_id=f"bkbtn-cancel-{ctx.author.id}-{clip_id}"),
            ]
            await ctx.send(content="\n".join(msg_lines), components=buttons)
            return

        # Case 4: no active sponsor, user can't afford first charge
        if user_tokens < first_charge:
            buttons = [
                Button(style=ButtonStyle.LINK, label="Free Tokens (Vote)", url=CLYPPY_VOTE_URL),
                Button(style=ButtonStyle.LINK, label="Buy Tokens", url=BUY_TOKENS_URL),
            ]
            await ctx.send(
                f"You need `{first_charge}` VIP tokens to back up this clip. You have `{user_tokens}`.",
                components=buttons
            )
            return

        # Case 1: no active sponsor, user can afford
        msg_lines = [
            f"Are you sure you want to back up `{clip_title} ({clip_id})`?",
            f"Backing up will make it never expire as long as tokens are paid monthly.",
            "",
        ]
        if expires_display:
            msg_lines.append(f"Currently expires: {expires_display}")
        msg_lines.append(f"Monthly cost: `{monthly_cost}` tokens")
        if days_covered > 0:
            msg_lines.append(f"First month cost: `{first_charge}` tokens ({days_covered} days already covered)")
        else:
            msg_lines.append(f"First month cost: `{first_charge}` tokens")
        msg_lines.append(f"You have: `{user_tokens}` tokens")

        buttons = [
            Button(style=ButtonStyle.SUCCESS, label="Confirm (public)", custom_id=f"bkbtn-confirm-pub-{ctx.author.id}-{clip_id}"),
            Button(style=ButtonStyle.SUCCESS, label="Confirm (anonymous)", custom_id=f"bkbtn-confirm-anon-{ctx.author.id}-{clip_id}"),
            Button(style=ButtonStyle.DANGER, label="Cancel", custom_id=f"bkbtn-cancel-{ctx.author.id}-{clip_id}"),
        ]
        await ctx.send(content="\n".join(msg_lines), components=buttons)

    @slash_command(name="backup", description="Back up a clip so it never expires (costs VIP tokens monthly)",
                   options=[SlashCommandOption(
                       name="clip_id",
                       description="The Clyppy clip ID (10 chars, found in clyppy.io URL or Info button)",
                       required=True,
                       type=OptionType.STRING)
                   ])
    async def backup(self, ctx: SlashContext, clip_id: str):
        await ctx.defer(ephemeral=True)
        self.logger.info(f"@slash_command for /backup - {ctx.author.id} - {clip_id}")
        await self._run_backup_flow(ctx, clip_id)

    @component_callback(compile(r"dlbtn-backup-.*"))
    async def dl_backup_button(self, ctx: ComponentContext):
        parts = ctx.custom_id.split("-")  # dlbtn-backup-{user_id}-{clyppy_id}
        owner_id = int(parts[2])
        clip_id = parts[3]
        if ctx.author.id != owner_id:
            await ctx.send("Only the person who used `/download` can back this up.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        await self._run_backup_flow(ctx, clip_id)

    @component_callback(compile(r"bkbtn-confirm-(pub|anon)-.*"))
    async def backup_confirm_button(self, ctx: ComponentContext):
        parts = ctx.custom_id.split("-")  # bkbtn-confirm-pub-{user_id}-{clip_id}
        privacy = parts[2]  # "pub" or "anon"
        owner_id = int(parts[3])
        clip_id = "-".join(parts[4:])  # clip ids shouldn't contain dashes but be safe

        if ctx.author.id != owner_id:
            await ctx.send("Only the person who used `/backup` can confirm.", ephemeral=True)
            return

        await ctx.defer(edit_origin=True)
        is_anonymous = (privacy == "anon")
        result = await create_backup(
            user_id=ctx.author.id,
            username=ctx.author.username,
            clip_id=clip_id,
            is_anonymous=is_anonymous,
        )

        if not result.get('success'):
            err = result.get('error', 'unknown')
            err_messages = {
                'insufficient_tokens': "You don't have enough tokens to cover the first charge.",
                'already_exists': "You're already backing up this clip.",
                'clip_not_found': "That clip no longer exists.",
            }
            await ctx.edit_origin(content=f"❌ {err_messages.get(err, f'Error: `{err}`')}", components=[])
            return

        position = result.get('position', 0)
        charged = result.get('charged', 0)
        next_charge_at = result.get('next_charge_at')
        privacy_label = "anonymously" if is_anonymous else "publicly"

        if position == 0:
            lines = [f"✅ Backed up {privacy_label}. You're at position `0` (active)."]
            if charged > 0:
                lines.append(f"Charged: `{charged}` tokens.")
            else:
                lines.append(f"Charged: `0` tokens (existing expiry covered the month).")
            if next_charge_at:
                try:
                    dt = datetime.fromisoformat(next_charge_at.replace('Z', '+00:00'))
                    lines.append(f"Next charge: <t:{int(dt.timestamp())}:D>")
                except Exception:
                    pass
        else:
            lines = [
                f"✅ Joined reserve queue {privacy_label} at position `{position}`.",
                f"Billing starts only if you're promoted to active.",
            ]
        lines.append(f"Manage / edit privacy: https://clyppy.io/profile/backups")

        await ctx.edit_origin(content="\n".join(lines), components=[])

    @component_callback(compile(r"bkbtn-cancel-.*"))
    async def backup_cancel_button(self, ctx: ComponentContext):
        parts = ctx.custom_id.split("-")  # bkbtn-cancel-{user_id}-{clip_id}
        owner_id = int(parts[2])
        if ctx.author.id != owner_id:
            await ctx.send("Only the person who used `/backup` can cancel.", ephemeral=True)
            return
        await ctx.edit_origin(content="Cancelled.", components=[])

    @slash_command(name="help", description="Get help using Clyppy")
    async def help(self, ctx: SlashContext):
        await ctx.defer()
        await self.bot.base_embedder.send_help(ctx)

    @slash_command(name="setup", description="Display or change Clyppy's general settings",
                   options=[SlashCommandOption(name="error_channel", type=OptionType.CHANNEL,
                                               description="The channel where Clyppy should send error messages",
                                               required=False)])
    async def setup(self, ctx: SlashContext, error_channel=None):
        if ctx.guild is None:
            asyncio.create_task(ctx.send("This command is only available in servers."))
            return
        if ctx.guild.id == ctx.author.id:  # in case they patch the "dm guild is None" situation
            asyncio.create_task(ctx.send("This command is only available in servers."))
            return

        if not ctx.author.has_permission(Permissions.ADMINISTRATOR):
            asyncio.create_task(ctx.send("Only members with the **Administrator** permission can change Clyppy's settings."))
            return
        if error_channel is None:
            if (ec := self.bot.guild_settings.get_error_channel(ctx.guild.id)) == 0:
                cur_chn = ("Unconfigured\n\n"
                           "When not configured, Clyppy will send error messages to the same channel as the interaction.")
                asyncio.create_task(ctx.send("Current error channel: " + cur_chn))
                return
            else:
                try:
                    cur_chn = self.bot.get_channel(ec)
                    asyncio.create_task(ctx.send(f"Current error channel: {cur_chn.mention}"))
                    return
                except:
                    cur_chn = ("Channel not found - error channel was reset to **Unconfigured**\n\n"
                               "Make sure Clyppy has the `VIEW_CHANNELS` permission, and that the channel still exists."
                               "\nWhen not configured, Clyppy will send error messages to the same channel as the interaction.\n\n"
                               f"More info:\nTried to retrieve channel <#{ec}> but failed.")
                    self.bot.guild_settings.set_error_channel(ctx.guild.id, 0)
                    asyncio.create_task(ctx.send("Current error channel: " + cur_chn))
                    return

        await ctx.defer()
        if ctx.guild is None:
            asyncio.create_task(ctx.send("This command is only available in servers."))
            return

        if (e := self.bot.get_channel(error_channel)) is None:
            asyncio.create_task(ctx.send(f"Channel #{error_channel} not found.\n\n"
                                  f"Please make sure Clyppy has the `VIEW_CHANNELS` permission & try again."))
            return

        res = self.bot.guild_settings.set_error_channel(ctx.guild.id, e.id)
        if res:
            asyncio.create_task(ctx.send(f"Success! Error channel set to {e.mention}"))
        else:
            asyncio.create_task(ctx.send("An error occurred while setting the error channel. Please try again."))

    @slash_command(name="settings", description="Display or change Clyppy's miscellaneous settings",
                   options=[
                       SlashCommandOption(
                           name="quickembeds",
                           type=OptionType.STRING,
                           description="Platforms: 'all', 'none', 'reset', or comma-separated (e.g., 'twitch,kick')",
                           required=False
                       ),
                       SlashCommandOption(
                           name="channel",
                           type=OptionType.CHANNEL,
                           description="Apply quickembeds to specific channel (blank = server-wide)",
                           required=False
                       ),
                       SlashCommandOption(
                           name="on_error",
                           type=OptionType.STRING,
                           description="Choose what Clyppy should do upon error",
                           required=False),
                       SlashCommandOption(
                           name="embed_buttons",
                           type=OptionType.STRING,
                           description="Configure what buttons Clyppy shows when embedding clips",
                           required=False
                       ),
                       SlashCommandOption(
                           name="auto_delete",
                           type=OptionType.STRING,
                           description="What Clyppy does with the parent message after embedding",
                           required=False,
                           choices=[
                               SlashCommandChoice(name="true (delete the parent message)", value="true"),
                               SlashCommandChoice(name="false (leave parent message as-is)", value="false"),
                               SlashCommandChoice(name="embeds (suppress embeds on parent message)", value="embeds"),
                           ]
                       ),
                       SlashCommandOption(
                           name="default_download_filetype",
                           type=OptionType.STRING,
                           description="Default output format for /download when no file_ext is given",
                           required=False,
                           choices=_DOWNLOAD_FILETYPE_CHOICES
                       )
                   ])
    async def settings(self, ctx: SlashContext, quickembeds: str = None, channel = None,
                       on_error: str = None, embed_buttons: str = None, auto_delete: str = None,
                       default_download_filetype: str = None):
        await ctx.defer()
        if ctx.guild is None:
            await ctx.send("This command is only available in servers.")
            return
        if ctx.guild.id == ctx.author.id:
            await ctx.send("This command is only available in servers.")
            return

        if isinstance(ctx.author, Member) and not ctx.author.has_permission(Permissions.ADMINISTRATOR):
            await self._send_settings_help(ctx, ctx.guild.id, ctx.guild.name, prepend_admin=True, log=True)
            return

        target_channel_id = channel.id if channel else None
        target_channel_name = channel.name if channel else None

        if (on_error is None and embed_buttons is None and quickembeds is None and auto_delete is None
                and default_download_filetype is None):
            await self._send_settings_help(ctx, ctx.guild.id, ctx.guild.name, prepend_admin=False, log=True)
            return

        await self._apply_settings(
            ctx, ctx.guild.id, ctx.guild.name, target_channel_id, target_channel_name,
            quickembeds, on_error, embed_buttons, auto_delete, default_download_filetype, log=True,
        )

    async def _apply_settings(self, ctx: SlashContext, target_guild_id: int, target_guild_name: str,
                              target_channel_id: Optional[int], target_channel_name: Optional[str],
                              quickembeds: Optional[str], on_error: Optional[str], embed_buttons: Optional[str],
                              auto_delete: Optional[str], default_download_filetype: Optional[str], log: bool):
        current_qe_platforms, qe_is_default = self.bot.guild_settings.get_quickembed_platforms(
            target_guild_id, target_channel_id)
        chosen_qe = current_qe_platforms
        qe_scope = f"in **#{target_channel_name}**" if target_channel_id else "server-wide"

        if quickembeds is not None:
            if quickembeds.lower() == 'reset':
                if target_channel_id is None:
                    await ctx.send("Cannot reset server-wide settings. Use `quickembeds=none` to disable all platforms.")
                    return
                success = self.bot.guild_settings.delete_channel_quickembed_setting(target_guild_id, target_channel_id)
                if success:
                    await ctx.send(f"Channel override removed for **#{target_channel_name}**. Now using server-wide settings.")
                else:
                    await ctx.send("Error removing channel override.")
                return

            success, error_msg, valid_platforms = self.bot.guild_settings.set_quickembed_platforms(
                target_guild_id, quickembeds, target_channel_id)
            if not success:
                await ctx.send(f"Error setting quickembeds: {error_msg}")
                return
            chosen_qe = valid_platforms
            if log:
                await log_guild_event(
                    guild_id=target_guild_id,
                    event_type='quickembeds_changed',
                    data={'platforms': chosen_qe, 'channel_id': target_channel_id, 'user_id': ctx.author.id},
                )

        current_setting = self.bot.guild_settings.get_setting(target_guild_id)
        current_on_error = POSSIBLE_ON_ERRORS[int(current_setting[1])]

        on_error = on_error or current_on_error
        if on_error not in POSSIBLE_ON_ERRORS:
            await ctx.send(f"Option '{on_error}' not a valid **on_error** setting!\nMust be one of `{POSSIBLE_ON_ERRORS}`")
            return

        current_embed_setting: int = self.bot.guild_settings.get_embed_buttons(target_guild_id)
        current_embed_setting: str = POSSIBLE_EMBED_BUTTONS[current_embed_setting]
        embed_buttons = embed_buttons or current_embed_setting

        if embed_buttons not in POSSIBLE_EMBED_BUTTONS:
            await ctx.send(f"Option '{embed_buttons}' not a valid **embed_buttons** setting!\n"
                           f"Must be one of `{POSSIBLE_EMBED_BUTTONS}`")
            return

        embed_idx = POSSIBLE_EMBED_BUTTONS.index(embed_buttons)
        self.bot.guild_settings.set_embed_buttons(target_guild_id, embed_idx)

        if auto_delete is None:
            auto_delete = self.bot.guild_settings.get_auto_delete(target_guild_id)
        else:
            auto_delete = auto_delete.strip().lower()
            if auto_delete not in ('true', 'false', 'embeds'):
                await ctx.send(f"Option '{auto_delete}' not a valid **auto_delete** setting!\n"
                               f"Must be one of `true`, `false`, `embeds`")
                return
            self.bot.guild_settings.set_auto_delete(target_guild_id, auto_delete)

        if default_download_filetype is None:
            default_download_filetype = self.bot.guild_settings.get_default_download_filetype(target_guild_id)
        else:
            default_download_filetype = default_download_filetype.strip().lower().lstrip('.')
            if default_download_filetype not in SUPPORTED_FORMATS:
                await ctx.send(f"Option '{default_download_filetype}' not a valid **default_download_filetype** setting!\n"
                               f"Must be one of `{', '.join(SUPPORTED_FORMATS.keys())}`")
                return
            self.bot.guild_settings.set_default_download_filetype(target_guild_id, default_download_filetype)

        if not chosen_qe:
            qe_display = "none"
        elif set(chosen_qe) == set(VALID_QUICKEMBED_PLATFORMS):
            qe_display = "all"
        else:
            qe_display = ', '.join(chosen_qe)

        qe_scope_msg = f" ({qe_scope})" if quickembeds is not None else ""
        await ctx.send(
            f"Successfully changed settings for **{target_guild_name}**:\n\n"
            f"**quickembeds**: {qe_display}{' (default)' if qe_is_default else ''}{qe_scope_msg}\n"
            f"**on_error**: {on_error}\n"
            f"**embed_buttons**: {embed_buttons}\n"
            f"**auto_delete**: {auto_delete}\n"
            f"**default_download_filetype**: {default_download_filetype}\n\n"
        )
        if log:
            await send_webhook(
                title=f'{target_guild_name} - /settings called',
                load=f'user: {ctx.user.username}\n'
                     "Successfully changed settings:\n\n"
                     f"**quickembeds**: {qe_display}\n"
                     f"**on_error**: {on_error}\n"
                     f"**embed_buttons**: {embed_buttons}\n"
                     f"**auto_delete**: {auto_delete}\n"
                     f"**default_download_filetype**: {default_download_filetype}\n\n",
                color=COLOR_GREEN,
                url=APPUSE_LOG_WEBHOOK,
                logger=self.logger
            )

    async def _send_settings_help(self, ctx: SlashContext, target_guild_id: int, target_guild_name: str,
                                  prepend_admin: bool = False, log: bool = True):
        cs = self.bot.guild_settings.get_setting_str(target_guild_id)
        es = self.bot.guild_settings.get_embed_buttons(target_guild_id)
        qe_platforms, qe_is_default = self.bot.guild_settings.get_quickembed_platforms(target_guild_id)
        auto_delete = self.bot.guild_settings.get_auto_delete(target_guild_id)
        default_download_filetype = self.bot.guild_settings.get_default_download_filetype(target_guild_id)
        es = POSSIBLE_EMBED_BUTTONS[es]

        # Format quickembed display
        if not qe_platforms:
            qe = "none"
        elif 'all' in qe_platforms:
            qe = "all"
        else:
            qe = ', '.join(qe_platforms)

        # Use friendly platform names for display
        valid_platforms_str = ', '.join(p.platform_name for p in self.bot.platform_list if not p.is_nsfw)

        # Build channel overrides section
        overrides = self.bot.guild_settings.list_channel_overrides(target_guild_id)
        channel_overrides_section = ""
        if overrides:
            override_lines = []
            for channel_id, setting in overrides:
                try:
                    channel_obj = self.bot.get_channel(channel_id)
                    channel_name = f"**#{channel_obj.name}**" if channel_obj else f"channel `{channel_id}`"
                    # Parse setting for display
                    if setting == 'none':
                        platforms = 'none'
                    elif setting == 'all':
                        platforms = 'all'
                    else:
                        platforms = ', '.join(setting.split(','))
                    override_lines.append(f"  {channel_name}: {platforms}")
                except Exception:
                    pass
            if override_lines:
                channel_overrides_section = "\n\n**Channel Overrides:**\n" + "\n".join(override_lines)

        about = (
            '**Configurable Settings:**\n'
            'Below are the settings you can configure using this command. Each setting name is in **bold** '
            'followed by its available options.\n\n'
            '**quickembeds** Configure which platforms Clyppy automatically embeds:\n'
            ' - `all`: Enable for all platforms\n'
            ' - `none`: Disable all quickembeds (use `/embed` command instead)\n'
            ' - `reset`: Remove channel-specific override (use with `channel` parameter)\n'
            f' - Comma-separated list: e.g., `Twitch,Kick,Medal`\n'
            f' - Valid platforms: `None, All, {valid_platforms_str}, and more...`\n'
            ' - Use `channel` parameter to apply to specific channel (blank = server-wide)\n\n'
            '**on_error** Choose what Clyppy does when it encounters an error:\n'
            ' - `info`: Respond to the message with the error.\n'
            ' - `dm`: DM the message author about the error.\n\n'
            '**embed_buttons** Choose which buttons Clyppy shows under embedded videos:\n'
            ' - `none`: No buttons, just the video.\n'
            ' - `view`: A button to the original clip.\n'
            ' - `dl`: A button to download the original video file (on compatible clips).\n'
            ' - `all`: Shows all available buttons.\n\n'
            '**auto_delete** What Clyppy does with the parent message after embedding:\n'
            ' - `true`: Delete the parent message after embedding.\n'
            ' - `false`: Leave the parent message as-is.\n'
            ' - `embeds`: Suppress embeds on the parent message (requires Manage Messages permission).\n\n'
            '**default_download_filetype** Default output format for `/download` when no `file_ext` is given:\n'
            f' - One of: `{", ".join(SUPPORTED_FORMATS.keys())}`\n\n'
            f'**Current Settings:**\n**quickembeds** (server-wide): {qe}{" (default)" if qe_is_default else ""}'
            f'{channel_overrides_section}\n{cs}\n**embed_buttons**: {es}\n'
            f'**auto_delete**: {auto_delete}\n'
            f'**default_download_filetype**: {default_download_filetype}\n\n'
            f'Something missing? Please **[Suggest a Feature]({SUPPORT_SERVER_URL})**'
        )

        if prepend_admin:
            about = "**ONLY MEMBERS WITH THE ADMINISTRATOR PERMISSIONS CAN EDIT SETTINGS**\n\n" + about

        tutorial_embed = Embed(title=f"CLYPPY SETTINGS — {target_guild_name}", description=about)
        await ctx.send(embed=tutorial_embed)
        if log:
            await send_webhook(
                title=f'{target_guild_name} - /settings called',
                load=f'user: {ctx.user.username}\n'
                     f'**Current Settings:**\n**quickembeds**: {qe}\n{cs}\n**embed_buttons**: {es}\n\n',
                color=COLOR_GREEN,
                url=APPUSE_LOG_WEBHOOK,
                logger=self.logger
            )

    async def check_monthly_winner(self):
        if not self.ready:
            return

        now = datetime.now(tz=timezone.utc)
        current_month_key = now.strftime('%Y-%m')

        # Load persisted value on first run after restart
        if self.last_winner_month is None:
            self.last_winner_month = self.bot.guild_settings.get_bot_state('last_winner_month')

        # Only announce on the 1st of the month, and only once per month
        if now.day != 1 or self.last_winner_month == current_month_key:
            return

        self.logger.info("Monthly winner check triggered - it's the 1st of the month!")

        try:
            data = await fetch_previous_vote_winner()
            if not data.get('success'):
                self.logger.error(f"Failed to fetch previous vote winner: {data}")
                return

            winners = data.get('winners', [])
            vote_month = data.get('vote_month', '')
            if not winners:
                self.logger.info("No winners for the previous month (no votes)")
                self.last_winner_month = current_month_key
                self.bot.guild_settings.set_bot_state('last_winner_month', current_month_key)
                return

            # Parse month display
            try:
                month_dt = datetime.strptime(vote_month, '%Y-%m')
                month_display = month_dt.strftime('%B %Y')
            except Exception:
                month_display = vote_month

            winner_votes = winners[0]['monthly_votes']

            # Award tokens to all winners
            for winner in winners:
                try:
                    winner_user = await self.bot.fetch_user(winner['user_id'])
                    await subtract_tokens(
                        winner_user,
                        -MONTHLY_WINNER_TOKENS,
                        reason='Monthly Vote Champion Reward',
                        description=f'Won the {month_display} voting competition with {winner_votes} votes'
                    )
                    self.logger.info(f"Awarded {MONTHLY_WINNER_TOKENS} tokens to {winner['username']} ({winner['user_id']})")
                except Exception as e:
                    self.logger.error(f"Failed to award tokens to monthly winner {winner['user_id']}: {e}")

            # Send announcement
            try:
                server = self.bot.get_guild(1117149574730104872)
                if server is None:
                    self.logger.warning("Could not find support server for monthly winner announcement")
                    self.last_winner_month = current_month_key
                    self.bot.guild_settings.set_bot_state('last_winner_month', current_month_key)
                    return

                channel = server.get_channel(MONTHLY_WINNER_CHANNEL_ID)
                if channel is None:
                    channel = await server.fetch_channel(MONTHLY_WINNER_CHANNEL_ID)

                if channel is None:
                    self.logger.warning(f"Could not find channel {MONTHLY_WINNER_CHANNEL_ID} for monthly winner announcement")
                    self.last_winner_month = current_month_key
                    self.bot.guild_settings.set_bot_state('last_winner_month', current_month_key)
                    return

                if len(winners) == 1:
                    w = winners[0]
                    description = (
                        f"Congratulations to **{w['username']}** for being the top voter of **{month_display}**!\n\n"
                        f"They cast **{winner_votes}** vote{'s' if winner_votes != 1 else ''} and have been awarded "
                        f"**{MONTHLY_WINNER_TOKENS} VIP tokens**!\n\n"
                        f"Vote this month to claim the title next time!"
                    )
                    title = f"Monthly Voting Champion - {month_display}"
                else:
                    winner_list = ", ".join(f"**{w['username']}**" for w in winners)
                    description = (
                        f"Congratulations to our top voters of **{month_display}**!\n"
                        f"{winner_list} — **{winner_votes}** vote{'s' if winner_votes != 1 else ''} each\n\n"
                        f"Each winner has been awarded **{MONTHLY_WINNER_TOKENS} VIP tokens**!\n\n"
                        f"Vote this month to claim the title next time!"
                    )
                    title = f"Monthly Voting Champions - {month_display}"

                embed = Embed(title=title, description=description, color=0xFFD700)
                embed.set_footer(text=f"Use /rank to see your current standing")
                await channel.send(embed=embed, components=[
                    Button(style=ButtonStyle.LINK, label="Vote Now!", url=CLYPPY_VOTE_URL),
                ])
                self.logger.info(f"Sent monthly winner announcement for {month_display}")
            except Exception as e:
                self.logger.error(f"Failed to send monthly winner announcement: {e}")

            self.last_winner_month = current_month_key
            self.bot.guild_settings.set_bot_state('last_winner_month', current_month_key)
        except Exception as e:
            self.logger.error(f"Error in check_monthly_winner: {e}")

    async def db_save_task(self):
        if not self.ready:
            self.logger.info("Bot not ready, skipping database save task")
            return

        self.logger.info("Saving database to the server...")
        await self.bot.guild_settings.save()

    async def refresh_cookies_task(self):
        """Download cookies from felixcreations.com every 24 hours"""
        if not self.ready:
            self.logger.info("Bot not ready, skipping cookie refresh task")
            return

        if is_contrib_instance(self.logger):
            log_api_bypass(self.logger, "https://felixcreations.com/api/cookies/get", "GET")
            self.logger.info("[CONTRIB MODE] Cookie refresh bypassed")
            return

        self.logger.info("Downloading cookies from server...")

        # Check if API key is available
        api_key = os.getenv('clyppy_post_key')
        if not api_key:
            self.logger.warning("Cookie refresh skipped: clyppy_post_key not set")
            return

        try:
            async with aiohttp.ClientSession() as session:
                url = "https://felixcreations.com/api/cookies/get"
                headers = {'X-API-Key': api_key}
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        cookies_content = await response.text()
                        cookie_file_path = os.getenv('COOKIE_FILE', '/tmp/cookies.txt')
                        with open(cookie_file_path, 'w') as f:
                            f.write(cookies_content)

                        self.logger.info(f"Successfully updated cookies at {cookie_file_path}")
                        # If the user uploaded fresh YouTube cookies via
                        # merge_yt_cookies.sh, re-bootstrap our dedicated
                        # writable YouTube cookies file so it picks up the
                        # new session instead of compounding stale rotations.
                        maybe_refresh_youtube_cookies(cookie_file_path, self.logger)
                    else:
                        self.logger.warning(f"Failed to download cookies: HTTP {response.status}")
        except Exception as e:
            self.logger.error(f"Error downloading cookies: {e}")

    @listen()
    async def on_guild_join(self, event: GuildJoin):
        if self.ready:
            guild = event.guild
            self.logger.info(f'Joined new guild: {guild.name}')
            try:
                w = await guild.fetch_widget()
            except:
                w = None
            await send_webhook(
                title=f'Joined new guild: {guild.name}',
                load=f"id - {guild.id}\n"
                     f"large - {guild.large}\n"
                     f"members - {guild.member_count}\n"
                     f"widget - {w}\n",
                color=COLOR_GREEN,
                logger=self.logger,
                embed=False
            )
            await self.post_servers(len(self.bot.guilds))

            # Send welcome message
            welcome_msg = (
                "**Clyppy is ready!** "
                "I'll automatically embed **TikTok, Instagram, Twitch, Kick, and Medal** links shared in this server.\n\n"
                "Use `/settings quickembeds=all` to enable more platforms (YouTube, Twitter, Reddit...) "
                "or `/settings quickembeds=none` to disable.\n"
                "Try `/download` to convert and save any video as a file.\n"
                "Type `/help` to see everything I can do."
            )
            welcome_sent = False
            me = getattr(guild, 'me', None)

            def _writable(c):
                if isinstance(c, (GuildForum, GuildCategory)):
                    return False
                if not hasattr(c, 'send'):
                    return False
                if me is None:
                    return True
                try:
                    return Permissions.SEND_MESSAGES in c.permissions_for(me)
                except Exception:
                    return False

            # Prioritized list: system channel first (if writable), then every
            # other writable text channel by position. We try them all so a
            # single locked-down channel doesn't force a fall-through to the
            # owner DM (which usually fails — Discord users have DMs off).
            sc = getattr(guild, 'system_channel', None)
            candidates = []
            if sc is not None and _writable(sc):
                candidates.append(sc)
            others = [c for c in guild.channels if c is not sc and _writable(c)]
            others.sort(key=lambda c: getattr(c, 'position', 999))
            candidates.extend(others)

            for ch in candidates:
                try:
                    await ch.send(welcome_msg)
                    welcome_sent = True
                    break
                except Exception as e:
                    self.logger.debug(
                        f"Welcome send failed in #{getattr(ch, 'name', '?')} "
                        f"for {guild.name}: {e}"
                    )
                    continue

            if not welcome_sent:
                self.logger.warning(
                    f"Welcome msg failed in all {len(candidates)} channels for "
                    f"{guild.name}, trying owner DM"
                )
                try:
                    owner = await self.bot.fetch_user(guild.owner_id)
                    await owner.send(welcome_msg)
                    welcome_sent = True
                except Exception as e2:
                    self.logger.warning(f"Welcome msg DM also failed for {guild.name}: {e2}")

            await log_guild_install(
                guild_id=guild.id,
                guild_name=guild.name,
                member_count=guild.member_count,
                owner_id=getattr(guild, 'owner_id', None),
                welcome_sent=welcome_sent,
            )

    @listen()
    async def on_guild_left(self, event: GuildLeft):
        if self.ready:
            guild = event.guild
            self.logger.info(f'Left guild: {guild.name}')
            try:
                w = await guild.fetch_widget()
            except:
                w = None
            await send_webhook(
                title=f'Left guild: {guild.name}',
                load=f"id - {guild.id}\n"
                     f"large - {guild.large}\n"
                     f"members - {guild.member_count}\n"
                     f"widget - {w}\n",
                color=COLOR_RED,
                logger=self.logger,
                embed=False
            )
            await self.post_servers(len(self.bot.guilds))
            await log_guild_left(guild_id=guild.id, guild_name=guild.name)

    @listen()
    async def on_ready(self):
        if not self.ready:
            self.ready = True
            self.save_task.start()
            self.cookie_refresh_task.start()
            self.status_update_task.start()
            self.monthly_winner_task.start()
            # Download cookies immediately on startup
            await self.refresh_cookies_task()
            self.logger.info(f"bot logged in as {self.bot.user.username}")
            self.logger.info(f"total shards: {len(self.bot.shards)}")
            self.logger.info(f"my guilds: {len(self.bot.guilds)}")
            self.logger.info(f"CLYPPY VERSION: {VERSION}")
            if os.getenv("TEST") is not None:
                await self.post_servers(len(self.bot.guilds))

            # Register download pipeline for task queue restoration
            self.bot.download_runner = self._run_download_pipeline

            # Process queued tasks from previous session
            try:
                await process_queued_tasks(self.bot, self.bot.task_queue)
            except Exception as e:
                self.logger.error(f"Error processing queued tasks: {e}")
            self.logger.info("--------------")

    async def update_status(self):
        """Cycle per-shard presence between embed count and tagline+shard id"""
        show_count = self._status_cycle_idx % 2 == 0
        self._status_cycle_idx += 1
        try:
            #if show_count:
            #    async with aiohttp.ClientSession() as session:
            #        async with session.get("https://clyppy.io/api/stats/embeds-count/") as resp:
            #            if resp.status == 200:
            #                data = await resp.json()
            #                self.bot.cached_embed_count = data.get("count", 0)

            shards = getattr(self.bot, "shards", None) or [self.bot._connection_state]
            total = len(shards)
            for shard in shards:
                if show_count:
                    #text = format_count(getattr(self.bot, "cached_embed_count", 0))
                    text =  f"shard {shard.shard_id + 1}/{total}"
                else:
                    text = f"video embeds for you • /help"
                await shard.change_presence(activity=Activity(name=text, type=ActivityType.PLAYING))
            self.logger.info(f"Updated status (count={show_count}) across {total} shard(s)")
        except Exception as e:
            self.logger.warning(f"Failed to update status: {e}")

    async def post_servers(self, num: int):
        if os.getenv("TEST") is not None:
            return

        # Calculate total user count across all guilds
        total_users = sum(guild.member_count or 0 for guild in self.bot.guilds)
        ggt = os.getenv('GG_TOKEN')
        try:
            if not ggt:
                self.logger.info("GG_TOKEN env var unset, skipping stats post")
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                            url="https://top.gg/api/bots/1111723928604381314/stats", json={
                                'server_count': num,
                                'shard_count': self.bot.total_shards
                            },
                            headers={'Authorization': ggt}
                    ) as resp:
                        r = await resp.text()
                        self.logger.info(f"Successfully posted servers to topp.gg - response: {r}")
        except Exception as e:
            self.logger.info(f"Failed to post servers to top.gg: {type(e).__name__}: {str(e)}")

        blt = os.getenv('BOTLISTME_TOKEN')
        try:
            if not blt:
                self.logger.info("BOTLISTME_TOKEN env var unset, skipping stats post")
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                            url="https://api.botlist.me/api/v1/bots/1111723928604381314/stats",
                            json={
                                'server_count': str(num),
                                'shard_count': self.bot.total_shards
                            },
                            headers={'authorization': blt}
                    ) as resp:
                        r = await resp.json()
                        self.logger.info(f"Successfully posted servers to botlist.me - response: {r}")
        except Exception as e:
            self.logger.info(f"Failed to post servers to botlist.me: {type(e).__name__}: {str(e)}")

        dft = os.getenv('DISCORDFORGE_TOKEN')
        try:
            if not dft:
                self.logger.info("DISCORDFORGE_TOKEN env var unset, skipping stats post")
            elif (since_last := time.monotonic() - self._last_discordforge_post) < 300:
                self.logger.info(f"Skipping discordforge.org stats post (last one {since_last:.0f}s ago, limit is 1/5min)")
            else:
                self._last_discordforge_post = time.monotonic()
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                            url="https://discordforge.org/api/bots/stats",
                            json={
                                'server_count': num,
                                'shard_count': self.bot.total_shards,
                                'user_count': total_users,
                            },
                            headers={
                                'Authorization': dft,
                                'Content-Type': 'application/json',
                            }
                    ) as resp:
                        r = await resp.text()
                        self.logger.info(f"Successfully posted servers to discordforge.org - response: {r}")
        except Exception as e:
            self.logger.info(f"Failed to post servers to discordforge.org: {type(e).__name__}: {str(e)}")

        dlt = os.getenv('DISCORDBOTLIST_TOKEN')
        try:
            if not dlt:
                self.logger.info("DISCORDBOTLIST_TOKEN env var unset, skipping stats post")
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                            url="https://discordbotlist.com/api/v1/bots/1111723928604381314/stats",
                            json={
                                'users': total_users,
                                'guilds': num
                            },
                            headers={
                                'Authorization': dlt,
                                'Accept': 'application/json'
                            }
                    ) as resp:
                        r = await resp.json()
                        self.logger.info(f"Successfully posted servers to discordbotlist.com - response: {r}")
        except Exception as e:
            self.logger.info(f"Failed to post servers to discordbotlist.com: {type(e).__name__}: {str(e)}")
