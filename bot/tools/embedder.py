from interactions import (Permissions, Embed, Message, Button, ButtonStyle, SlashContext,
                          TYPE_THREAD_CHANNEL, ActionRow, MessageFlags, errors)
from interactions.api.events import MessageCreate

from bot.errors import VideoTooLong, NoDuration, UnknownError, DefinitelyNoDuration, NSFWEmbed
from bot.io import get_aiohttp_session, is_404, fetch_video_status, get_clip_info, subtract_tokens, push_interaction_error
from bot.env import (DL_SERVER_ID, DOWNLOAD_THIS_WEBHOOK_ID, POSSIBLE_EMBED_BUTTONS, is_contrib_instance,
                     log_api_bypass, LOGGER_WEBHOOK_ID, TOPGG_VOTE_LINK, TOPGG_REVIEW_LINK)
from bot.types import DownloadResponse, LocalFileInfo, GuildType, DiscordAttachmentId
from bot.task_queue import QuickembedTask
from bot.tools.converter import ffprobe_video_metadata

from datetime import datetime, timezone, timedelta
from typing import List, Union, Tuple
from pathlib import Path
import traceback
import asyncio
import time
import re
import os

INVALID_VIEW_ON_PLATFORMS = ['discord']
INVALID_DL_PLATFORMS = ['discord', 'rule34', 'base']


async def publish_interaction(interaction_data, apikey, logger, edit_id=None, edit_type=None):
    if is_contrib_instance(logger):
        log_api_bypass(logger, "https://clyppy.io/api/publish/", "POST", {
            "edit": edit_type is not None,
            "edit_type": edit_type
        })
        return {"success": True, "id": "test_video_id"}

    try:
        url = 'https://clyppy.io/api/publish/'
        headers = {
            'X-API-Key': apikey,
            'Content-Type': 'application/json'
        }
        if edit_type is None:
            # publish new interaction
            j = interaction_data
        elif edit_type == "response_time":
            if edit_id is None:
                raise Exception("both edit_id and edit_type must be defined, or both None")
            j = {'edit': True, 'id': edit_id, 'response_time_seconds': interaction_data['response_time'], 'msg_id': interaction_data['msg_id']}
        else:
            raise Exception("Invalid call to publish_interaction()")
        async with get_aiohttp_session() as session:
            async with session.post(url, json=j, headers=headers) as response:
                if response.status == 201:  # Successfully created
                    return await response.json()
                else:
                    error_data = await response.json()
                    logger.info(error_data)
                    raise Exception(f"Failed to publish interaction: {error_data.get('error', 'Unknown error')}")
    except:
        logger.info(traceback.format_exc())


class AutoEmbedder:
    def __init__(self, bot, platform_tools, logger):
        self.api_key = os.getenv('clyppy_post_key')
        self.bot = bot
        self.too_large_clips = []
        self.logger = logger
        self.platform_tools = platform_tools
        self.clip_id_msg_timestamps = {}
        self.embedding_timestamps = {}  # Track when clips were added to currently_embedding

    @staticmethod
    def get_words(text: str) -> List[str]:
        # Extract URLs from Discord markdown links. Handles both forms:
        #   [text](https://url)           — regular markdown link
        #   [text](<https://url>)         — angle-bracket form (suppresses Discord preview)
        text = re.sub(r'\[.*?]\(<?(https?://[^\s)>]+)>?\)', r'\1', text)
        return re.split(r"[ \n]+", text)

    def get_next_clip_link_loc(self, words: List[str], n=0, print=True) -> Tuple[bool, int]:
        for i in range(n, len(words)):
            word = words[i]
            if self.platform_tools.is_clip_link(word):
                if print:
                    self.logger.info(f"Found clip link: {word}")
                return True, i
        return False, 0

    def _get_num_clip_links(self, words: List[str]):
        n = 0
        for word in words:
            if self.platform_tools.is_clip_link(word):
                n += 1
        return n

    async def on_message_create(self, event: MessageCreate):
        try:
            if event.message.guild is None:
                # if we're in dm context, set the guild id to the author id
                guild = GuildType(event.message.author.id, event.message.author.username, True)
            else:
                guild = GuildType(event.message.guild.id, event.message.guild.name, False)
                # if we're in guild ctx, we need to verify clyppy has the right perms
                if Permissions.EMBED_LINKS not in event.message.channel.permissions_for(event.message.guild.me):
                    return 1
                if Permissions.SEND_MESSAGES not in event.message.channel.permissions_for(event.message.guild.me):
                    return 1
                if Permissions.READ_MESSAGE_HISTORY not in event.message.channel.permissions_for(event.message.guild.me):
                    return 1
                if Permissions.SEND_MESSAGES_IN_THREADS not in event.message.channel.permissions_for(event.message.guild.me):
                    if isinstance(event.message.channel, TYPE_THREAD_CHANNEL):
                        return 1

            if event.message.author.id == self.bot.user.id:
                return 1  # don't respond to the bot's own messages
            elif event.message.author.id == LOGGER_WEBHOOK_ID:
                return 1  # logger webhook

            # Check if quickembeds are enabled for this platform in this channel/guild
            channel_id = event.message.channel.id if hasattr(event.message.channel, 'id') else None
            if not self.bot.guild_settings.is_platform_quickembed_enabled(
                    guild.id, self.platform_tools.platform_name, channel_id):
                # quickembeds not enabled for this platform
                return 1

            words = self.get_words(event.message.content)
            num_links = self._get_num_clip_links(words)

            # If shutting down, queue tasks instead of processing
            if self.bot.is_shutting_down:
                self.logger.info(f"Bot is shutting down, queueing {num_links} clip(s) from message {event.message.id}")
                if num_links >= 1:
                    for word in words:
                        if self.platform_tools.is_clip_link(word):
                            task = QuickembedTask(
                                message_id=event.message.id,
                                channel_id=event.message.channel.id,
                                guild_id=guild.id,
                                guild_name=guild.name,
                                is_dm=guild.is_dm,
                                clip_url=word,
                                author_id=event.message.author.id,
                                author_username=event.message.author.username
                            )
                            self.bot.task_queue.add_quickembed(task)
                return 1

            if num_links == 1:
                contains_clip_link, index = self.get_next_clip_link_loc(words, 0)
                if not contains_clip_link:
                    return 1
                await self._process_clip_one_at_a_time(
                    clip_link=words[index],
                    respond_to=event.message,
                    guild=guild,
                    channel=event.message.channel
                )
            elif num_links > 1:
                next_link_exists = True
                index = -1  # we will +1 in the next step (setting it to 0 for the start)
                while next_link_exists:
                    next_link_exists, index = self.get_next_clip_link_loc(words, index + 1)
                    if not next_link_exists:
                        return 1
                    await self._process_clip_one_at_a_time(
                        clip_link=words[index],
                        respond_to=event.message,
                        guild=guild,
                        channel=event.message.channel
                    )
        except NSFWEmbed:
            pass
        except Exception as e:
            self.logger.info(f"Error in AutoEmbed on_message_create: {event.message.content}\n{traceback.format_exc()}")

    async def _process_clip_one_at_a_time(self, clip_link: str, respond_to: Message, guild: GuildType, channel):
        parsed_id = self.platform_tools.parse_clip_url(clip_link)
        if self.platform_tools.is_nsfw is None:
            self.platform_tools.is_nsfw = await self.platform_tools.check_url_is_nsfw(clip_link)

        if not guild.is_dm:
            if isinstance(channel, TYPE_THREAD_CHANNEL):
                if self.platform_tools.is_nsfw:
                    # GuildPublicThread has no attribute nsfw
                    if not channel.parent_channel.nsfw:
                        raise NSFWEmbed
            elif not channel.nsfw and self.platform_tools.is_nsfw:
                # only allow nsfw in nsfw channels
                raise NSFWEmbed

        self.clip_id_msg_timestamps[respond_to.id] = datetime.now().timestamp()

        # Convert DiscordAttachmentId to string if needed
        if isinstance(parsed_id, DiscordAttachmentId):
            parsed_id = parsed_id.to_string()
        elif not isinstance(parsed_id, str):
            self.logger.error(f"parse_clip_url returned unexpected type {type(parsed_id)}: {parsed_id}")
            return

        # Clean up stale entries (clips that have been processing for > 5 minutes)
        await self._cleanup_stale_downloads()

        if parsed_id in self.bot.currently_embedding:
            await self._wait_for_download(parsed_id)
        else:
            self.bot.currently_embedding.append(parsed_id)
            self.embedding_timestamps[parsed_id] = time.time()

        clip = None
        handled = False
        err_msg = "Unknown error in quickembed"
        exc_name = "None"
        try:
            clip = await self.platform_tools.get_clip(clip_link, extended_url_formats=True, basemsg=respond_to)
            await self.process_clip_link(
                clip=clip,
                clip_link=clip_link,
                respond_to=respond_to,
                guild=guild,
                try_send_files=True
            )
        except VideoTooLong:
            self.logger.info(f"VideoTooLong was reported for {clip_link}")
            err_msg = "Video was too long"
            handled = True
            exc_name = "VideoTooLong"
        except (NoDuration, DefinitelyNoDuration) as e:
            self.logger.info(f"NoDuration was reported for {clip_link}")
            err_msg = "No duration found for this clip"
            handled = True
            exc_name = type(e).__name__
        except Exception as e:
            err_msg = str(e)
            exc_name = type(e).__name__
            self.logger.info(f"Error in processing this clip link one at a time: {clip_link} - {e}")
        finally:
            try:
                while parsed_id in self.bot.currently_embedding:
                    self.bot.currently_embedding.remove(parsed_id)
            except ValueError:
                pass
            try:
                del self.embedding_timestamps[parsed_id]
            except KeyError:
                pass
            try:
                del self.clip_id_msg_timestamps[respond_to.id]
            except KeyError:
                pass

            if exc_name != "None":
                exception = {
                    'name': exc_name,
                    'msg': err_msg
                }
                await push_interaction_error(
                    parent_msg=respond_to,
                    clip=clip,
                    platform_name=self.platform_tools.platform_name,
                    clip_url=clip_link,
                    error_info=exception,
                    handled=handled,
                    logger=self.logger
                )

    async def _cleanup_stale_downloads(self, max_age_seconds: float = 300):
        """Remove clips from currently_embedding that have been processing for too long (default 5 minutes)"""
        current_time = time.time()
        stale_clips = []

        for clip_id in list(self.bot.currently_embedding):
            # Convert DiscordAttachmentId to string if somehow one got in here
            if isinstance(clip_id, DiscordAttachmentId):
                self.logger.warning(f"Found DiscordAttachmentId in currently_embedding, converting: {clip_id}")
                clip_id = clip_id.to_string()
            # Skip other non-string entries (defensive programming)
            elif not isinstance(clip_id, str):
                self.logger.warning(f"Non-string entry in currently_embedding: {type(clip_id)} - {clip_id}")
                continue

            if clip_id in self.embedding_timestamps:
                age = current_time - self.embedding_timestamps[clip_id]
                if age > max_age_seconds:
                    stale_clips.append(clip_id)
                    self.logger.warning(f"Cleaning up stale download: {clip_id} (age: {age:.1f}s)")

        for clip_id in stale_clips:
            try:
                while clip_id in self.bot.currently_embedding:
                    self.bot.currently_embedding.remove(clip_id)
            except ValueError:
                pass
            try:
                del self.embedding_timestamps[clip_id]
            except KeyError:
                pass

    async def _wait_for_download(self, clip_id: str, timeout: float = 60):
        start_time = time.time()
        while clip_id in self.bot.currently_embedding:
            if time.time() - start_time > timeout:
                self.logger.error(f"Timeout waiting for clip {clip_id}. Forcing cleanup and retrying...")
                # Force cleanup this clip and let the caller retry
                try:
                    while clip_id in self.bot.currently_embedding:
                        self.bot.currently_embedding.remove(clip_id)
                    del self.embedding_timestamps[clip_id]
                except (ValueError, KeyError):
                    pass
                raise TimeoutError(f"Waiting for clip {clip_id} download timed out")
            await asyncio.sleep(0.1)

    async def send_welcome_dm_if_first_time(self, user):
        """Send welcome DM to user if this is their first embed. Fire-and-forget, never blocks."""
        try:
            if user.bot:
                self.logger.info(f"User {user.id} - {user.username} is a bot. not sendin them a welcome dm")
                return

            # Check if user has already received welcome DM
            if self.bot.guild_settings.has_received_welcome_dm(user.id):
                self.logger.info(f"User {user.id} - {user.username} has already received welcome DM, skipping")
                return

            # Record that we're sending (or attempting to send) the DM
            # Do this before sending to prevent duplicate attempts if DM fails
            self.bot.guild_settings.record_welcome_dm_sent(user.id)

            # Try to send the DM
            try:
                msg = (f"**Welcome to Clyppy!** 🎬\n\n"
                       f"Thanks for using Clyppy to embed your clips! "
                       f"I will automatically embed links from certain platforms, if your server admin has setup `quickembeds` using my `/settings` command. "
                       f"Otherwise, all platforms are embeddable via the `/embed` command.\n"
                       f"All your embedded videos are automatically saved to your personal clip library.\n\n"
                       f"You can view and manage all your clips at any time using the link below. To set your link library to private, click the link below.\n\n"
                       f"If you're enjoying Clyppy, leaving a review or vote on top.gg helps others discover it — it only takes a second!")
                await user.send(content=msg, components=[
                    ActionRow(
                        Button(style=ButtonStyle(ButtonStyle.LINK), label="View My Profile", url="https://clyppy.io/profile/clips"),
                        Button(style=ButtonStyle(ButtonStyle.LINK), label="Leave a Review", url=TOPGG_REVIEW_LINK),
                        Button(style=ButtonStyle(ButtonStyle.LINK), label="Vote on top.gg", url=TOPGG_VOTE_LINK),
                    )
                ])
                self.logger.info(f"Successfully sent welcome DM to user {user.id} ({user.username})")
            except errors.Forbidden:
                # User has DMs disabled
                self.logger.info(f"Could not send welcome DM to user {user.id} ({user.username}): DMs disabled")
            except Exception as e:
                # Other DM failure (Discord API error, etc.)
                self.logger.error(f"Failed to send welcome DM to user {user.id} ({user.username}): {e}")

        except Exception as e:
            # Catch-all to ensure this never crashes the main embed flow
            self.logger.error(f"Error in send_welcome_dm_if_first_time for user {user.id}: {e}")

    async def process_clip_link(
            self, clip: 'BaseClip',
            clip_link: str, respond_to: Union[Message, SlashContext],
            guild: GuildType, try_send_files = True,
            extend_with_ai = False
    ) -> None:
        # get_clip will have used the VIP tokens if they were needed for this clip
        try:
            await self._process_clip(
                clip=clip,
                clip_link=clip_link,
                respond_to=respond_to,
                guild=guild,
                try_send_files=try_send_files,
                extend_with_ai=extend_with_ai
            )
        except Exception as e:
            # this is where we refund the tokens
            username = respond_to.author.username or f"User_{respond_to.author.id}"
            if extend_with_ai:
                # video extension -> always 10 tokens cost
                self.logger.info(f"The AI extend failed, so we should refund 10 VIP tokens to {username} <{respond_to.author.id}>")
                asyncio.create_task(subtract_tokens(
                    user=respond_to.author,
                    amt=-10,
                    clip_url=clip.url,
                    reason="Token Refund",
                    description=f"The AI extend failed for {clip.url}"
                ))
            else:
                # normal embed
                self.logger.info(f"The clip failed to embed, so we should refund {clip.tokens_used} VIP tokens to {username} <{respond_to.author.id}>")
                asyncio.create_task(subtract_tokens(
                    user=respond_to.author,
                    amt=-1 * clip.tokens_used,
                    clip_url=clip.url,
                    reason="Token Refund",
                    description=f"The embed failed for {clip.url}"
                ))
            raise e

    async def _process_clip(
            self,
            clip: 'BaseClip', clip_link: str,
            respond_to: Union[Message, SlashContext], guild: GuildType,
            try_send_files=True, extend_with_ai=False):
        if guild.is_dm:  # dm gives error (nonetype has no attribute 'permissions_for')
            has_file_perms = True
        elif getattr(respond_to, '_restored_task', False):
            # Restored task - assume permissions are valid since interaction was deferred successfully
            has_file_perms = True
        else:
            has_file_perms = Permissions.ATTACH_FILES in respond_to.channel.permissions_for(respond_to.guild.me)

        will_send_files = has_file_perms and try_send_files
        if clip is None:
            self.logger.info(f"Failed to fetch clip: **Invalid Clip Link** {clip_link}")
            # should silently fail
            return None
        elif clip.clyppy_id is None:
            await clip.compute_clyppy_id()

        if str(guild.id) == str(DL_SERVER_ID) and isinstance(respond_to, Message) and int(respond_to.author) == DOWNLOAD_THIS_WEBHOOK_ID:
            # if we're in video dl server -> StoredVideo obj for this clip probably already exists
            # also checks against the 'download this' webhook id, so the 'video refresher' webhook will trigger the normal block (create/refresh StoredVideo clip)
            # TODO: decrement the tokens used for this clip by parsing the <@user_id> from the message
            the_file = f'https://cdn.clyppy.io/temp/{clip.service}_{clip.clyppy_id}.mp4'
            file_not_exists, _ = await is_404(the_file)
            if file_not_exists:
                self.logger.info("(is_dl_server) YTDLP is manually downloading this clip to be uploaded to the server")
                await respond_to.reply("(is_dl_server) YTDLP is manually downloading this clip to be uploaded to the server")
                response: LocalFileInfo = await self.bot.tools.dl.download_clip(
                    clip=clip,
                    can_send_files=False,
                    skip_upload=True
                )
                success, uploaded_url = await self.bot.cdn_client.cdn_upload_video(
                    file_path=response.local_file_path,
                    storage_type="temp",
                    content_type="video/mp4"
                )
                if not success:
                    self.logger.error(f"(is_dl_server) CDN upload failed for {clip_link}")
                    asyncio.create_task(respond_to.reply(f"Upload failed for {clip_link}"))
                    return None
                asyncio.create_task(respond_to.reply(f"Success for {clip_link}, uploaded to -> {uploaded_url}"))
                return None
            else:
                self.logger.info(f"(is_dl_server) Video file `{the_file}` already exists on the server! Cancelling")
                asyncio.create_task(respond_to.reply("(is_dl_server) Video file already exists on the server!"))
                return None
        else:
            # proceed normally

            if extend_with_ai:
                # these should always be re-generated - even for duplicate video ids
                video_doesnt_exist = True
            else:
                status = await fetch_video_status(clip.clyppy_id)
                video_doesnt_exist = not status['exists']

            if video_doesnt_exist:
                response: DownloadResponse = await self.bot.tools.dl.download_clip(
                    clip=clip,
                    can_send_files=will_send_files,
                    extend_with_ai=extend_with_ai
                )
            else:
                self.logger.info(f" {clip.clyppy_url} - Video already exists!")
                info = await get_clip_info(clip.clyppy_id)
                # if not await author_has_enough_tokens(respond_to, ...):  # todo if i ever care
                #    raise VideoTooLong(...duration)
                response: DownloadResponse = DownloadResponse(
                    remote_url=info['url'],
                    local_file_path=None,
                    duration=info['duration'],
                    clyppy_object_is_stored_as_redirect=info['is_redirect'],
                    width=info['width'],
                    height=info['height'],
                    filesize=info['file_size'],
                    video_name=info['title'],
                    can_be_discord_uploaded=False
                )

        # send embed
        try:
            comp = []
            # refer to: ["all", "view", "dl", "none"]
            if self.platform_tools.platform_name.lower() in INVALID_VIEW_ON_PLATFORMS:
                btn_idx = 2
            else:
                btn_idx = self.bot.guild_settings.get_embed_buttons(guild.id)
            if btn_idx <= 1:
                comp.append(Button(
                    style=ButtonStyle.LINK,
                    label=f"View On {self.platform_tools.platform_name}" if self.platform_tools.platform_name != "base" else "View Source",
                    url=clip.url if clip.share_url is None else clip.share_url
                ))
            if (btn_idx == 0 or btn_idx == 2) and self.platform_tools.platform_name.lower() not in INVALID_DL_PLATFORMS:
                comp.append(Button(
                    style=ButtonStyle.SECONDARY,
                    label="Download",
                    custom_id=f"embdl-{clip.clyppy_id}"
                ))

            if guild.is_dm:
                chn = "{dm}"
                chnid = 0
            else:
                chn = respond_to.channel.name
                chnid = respond_to.channel.id

            if clip.service == 'medal':
                expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=10)
                expires_at = expires_at.timestamp()
            elif clip.service == 'twitch':
                expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=10)
                expires_at = expires_at.timestamp()
            elif clip.service == 'instagram':
                expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)
                expires_at = expires_at.timestamp()
            elif clip.service == 'tiktok':
                expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)
                expires_at = expires_at.timestamp()
            elif clip.service == 'youtube':
                expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=6)
                expires_at = expires_at.timestamp()
            else:
                expires_at = None

            if clip.title is not None:
                title_str = clip.title[:100]
            elif response.video_name is not None:
                title_str = response.video_name[:100]
            else:
                title_str = None

            thumb_url = None
            uploading_to_discord = response.can_be_discord_uploaded and has_file_perms
            clip_webp = None
            local_video_path = None
            if response.remote_url is None and not uploading_to_discord and video_doesnt_exist:
                self.logger.info("The remote url was None for a new video create but we're not uploading to Discord!")
                raise UnknownError
            elif not uploading_to_discord and response.local_file_path is not None:
                # if we actually downloaded this file locally, create its thumbnail
                try:
                    clip_webp = await clip.create_first_frame_webp(response.local_file_path)
                    status, thumb_url = await self.bot.cdn_client.upload_webp(clip_webp)
                    if not status:
                        self.logger.info(f"Uploading {clip_webp} failed (status = False)")
                        thumb_url = None
                except Exception as e:
                    self.logger.info(f"Exception in creating/uploading webp thumbnail for {clip.url}: {str(e)}")
                    # keep thumb_url as None
            elif not uploading_to_discord and response.local_file_path is None and response.remote_url:
                if clip.service == 'twitch':
                    try:
                        # perhaps the thumbnail is hosted on twitch (for pre-v2 clips)
                        thumb_url = await clip.get_thumbnail()
                    except Exception as e:
                        self.logger.info(f"Failed to get twitch thumbnail for {clip.url}: {str(e)}")
                        thumb_url = None

                potential_local_file = f'{clip.service}_{clip.clyppy_id}.mp4' if clip.service != 'base' else f'{clip.clyppy_id}.mp4'
                local_video_path = potential_local_file if Path(potential_local_file).exists() else None
                get_thumb_for_redirects_platforms = ['twitch']
                if not thumb_url and (not response.clyppy_object_is_stored_as_redirect or clip.service in get_thumb_for_redirects_platforms):
                    # can't get thumbnails for redirect objects usually
                    self.logger.info(f"[{clip.clyppy_id}] Thumbnail url is None, downloading thumbnail from remote cdn url...")
                    try:
                        clip_webp = await clip.download_first_frame_webp(response.remote_url, f'{clip.clyppy_id}.webp')
                        status, thumb_url = await self.bot.cdn_client.upload_webp(clip_webp)
                        if not status:
                            self.logger.info(f"[thumbnail] Uploading {clip_webp} failed (status = False)")
                            thumb_url = None
                    except Exception as e:
                        self.logger.info(f"Failed to fetch or upload thumbnail for {clip.url}: {str(e)}")
                        thumb_url = None
                elif local_video_path:
                    # for redirect files we may have still downloaded its video to check length, just not uploaded it to the server
                    self.logger.info(f"[{clip.clyppy_id}] Thumbnail url is None but local file exists, extracting thumbnail...")
                    try:
                        clip_webp = await clip.create_first_frame_webp(local_video_path, f'{clip.clyppy_id}.webp')
                        status, thumb_url = await self.bot.cdn_client.upload_webp(clip_webp)
                        if not status:
                            self.logger.info(f"[thumbnail] Uploading {clip_webp} failed (status = False)")
                            thumb_url = None
                    except Exception as e:
                        self.logger.info(f"Failed to create or upload thumbnail for {clip.url}: {str(e)}")
                        thumb_url = None
            # Final-pass metadata fallback: if yt-dlp didn't give us a real duration but we have
            # a local file on disk, ffprobe it as ground truth before publishing to the server.
            # This prevents StoredVideo rows from being created with duration=0 (which would also
            # break backup proration math). Width/height are only patched in if they're also missing.
            if local_video_path and (not response.duration or response.duration == 0):
                try:
                    probed = await ffprobe_video_metadata(local_video_path)
                    if probed is not None:
                        if probed['duration'] > 0 and (not response.duration or response.duration == 0):
                            self.logger.info(f"[{clip.clyppy_id}] ffprobe filled in duration={probed['duration']} (yt-dlp gave 0)")
                            response.duration = probed['duration']
                        if probed['width'] and (not response.width or response.width == 0):
                            response.width = probed['width']
                        if probed['height'] and (not response.height or response.height == 0):
                            response.height = probed['height']
                except Exception as e:
                    self.logger.warning(f"[{clip.clyppy_id}] ffprobe fallback failed: {e}")

            if clip_webp:
                try:
                    Path(clip_webp).unlink()
                except Exception as e:
                    self.logger.info(f"Failed to delete {clip_webp}: {str(e)}")
            if local_video_path:
                try:
                    Path(local_video_path).unlink()
                except Exception as e:
                    self.logger.info(f"Failed to delete local video {local_video_path}: {str(e)}")

            interaction_data = {
                'is_redirect': response.clyppy_object_is_stored_as_redirect,
                'edit': False,  # create new BotInteraction obj
                'create_new_video': video_doesnt_exist,
                'server_name': guild.name,
                'channel_name': chn,
                'thumbnail': thumb_url,
                'title': title_str,
                'user_name': respond_to.author.username or f"User_{respond_to.author.id}",
                'server_id': str(guild.id),
                'channel_id': str(chnid),
                'user_id': str(respond_to.author.id),
                'embedded_url': clip_link,
                'remote_file_url': response.remote_url,
                'remote_video_height': response.height,
                'remote_video_width': response.width,
                'url_platform': self.platform_tools.platform_name,
                'response_time_seconds': 0,
                'total_servers_now': len(self.bot.guilds),
                'generated_id': clip.clyppy_id,
                'original_id': clip.id,
                'video_file_size': response.filesize,
                'uploaded_to_discord': uploading_to_discord,
                'video_file_dur': response.duration,
                'expires_at_timestamp': expires_at,
                'is_extended': extend_with_ai,
                'broadcaster_username': response.broadcaster_username,
                'video_uploader_username': response.video_uploader_username,
                'user_is_bot':  respond_to.author.bot,
                'tokens_used': clip.tokens_used,
                'interaction_type': 'embed' if isinstance(respond_to, SlashContext) else 'quickembed',
            }

            try:
                clip.is_discord_attachment = uploading_to_discord
                try:
                    result = await publish_interaction(interaction_data, apikey=self.api_key, logger=self.logger)
                except Exception as e:
                    self.logger.info(f"Failed to post interaction to API: {e}\ninteraction_data: {interaction_data}")
                    raise

                self.logger.info(f"got back from server {result}")
                if result['success']:
                    # sometimes the server will generate a new and improved clyppy id
                    # to bypass invalid discord caches of old clyppy urls
                    if result['video_page_id']:
                        new_id = result["video_page_id"]
                        if new_id != clip.clyppy_id:
                            self.logger.info(f"Overwriting clyppy url {clip.clyppy_url} with https://clyppy.io/{new_id}")
                            clip.clyppy_id = new_id  # clyppy_url is a property() that pulls from clyppy_id

                    # Send welcome DM if this is user's first embed (fire-and-forget, never blocks)
                    asyncio.create_task(self.send_welcome_dm_if_first_time(respond_to.author))
                else:
                    self.logger.info(f"Failed to publish interaction, got back from server {result}")
                    return None

                btns_is_none = self.bot.guild_settings.get_embed_buttons(guild.id)
                btns_is_none = POSSIBLE_EMBED_BUTTONS[btns_is_none] == "none"
                if not btns_is_none:
                    dctx = ""
                    if uploading_to_discord:
                        dctx = "d-"

                    info_button = Button(
                        style=ButtonStyle.SECONDARY,
                        label="ⓘ Info",
                        custom_id=f"ibtn-{dctx}{clip.clyppy_id}"
                    )
                    comp = [info_button] + comp
                    comp = ActionRow(*comp)

                # send message
                # Check if it's a SlashContext (or MinimalContext with send method)
                auto_delete_mode = self.bot.guild_settings.get_auto_delete(guild.id)
                if isinstance(respond_to, SlashContext) or (hasattr(respond_to, 'send') and not hasattr(respond_to, 'reply')):
                    # slash command
                    if uploading_to_discord:
                        bot_message = await respond_to.send(file=response.local_file_path, components=comp)
                    else:
                        bot_message = await respond_to.send(clip.clyppy_url, components=comp)
                else:
                    # message
                    msg_content = f'<@!{respond_to.author.id}> ' if auto_delete_mode == 'true' else ''
                    try:
                        if uploading_to_discord:
                            bot_message = await respond_to.reply(msg_content, file=response.local_file_path, components=comp)
                        else:
                            bot_message = await respond_to.reply(f'{msg_content}{clip.clyppy_url}', components=comp)
                    except Exception as e:
                        self.logger.info(f"Error replying to message: {str(e)} - sending to channel instead")
                        auto_delete_mode = 'false'
                        # assume message to reply to was deleted
                        if uploading_to_discord:
                            bot_message = await respond_to.channel.send(
                                content=f'<@!{respond_to.author.id}>',
                                file=response.local_file_path,
                                components=comp
                            )
                        else:
                            bot_message = await respond_to.channel.send(
                                content=f'<@!{respond_to.author.id}> {clip.clyppy_url}',
                                components=comp
                            )

                my_response_time = 0
                if isinstance(respond_to, Message):
                    # don't publish response_time on /embeds, we could but we need a way to pull timestamp from SlashContext
                    respond_to_utc = self.clip_id_msg_timestamps[respond_to.id]
                    my_response_time = round((datetime.now().timestamp() - respond_to_utc), 2)
                    self.logger.info(f"Successfully embedded clip {clip.id} in {guild.name} - #{chn} in {my_response_time} seconds")

                    try:
                        i = respond_to.guild.me
                        can_manage = respond_to.channel.permissions_for(i).VIEW_CHANNEL and i.has_permission(Permissions.MANAGE_MESSAGES)
                        if auto_delete_mode == 'true' and can_manage:
                            asyncio.create_task(respond_to.delete())  # delete the parent message
                        elif auto_delete_mode == 'embeds' and can_manage:
                            existing_flags = int(getattr(respond_to, 'flags', 0) or 0)
                            new_flags = existing_flags | int(MessageFlags.SUPPRESS_EMBEDS)
                            asyncio.create_task(respond_to.edit(flags=new_flags))
                    except Exception as e:
                        self.logger.warning(f"Error while applying auto_delete '{auto_delete_mode}' to parent message: {str(e)}")

                if result['success']:
                    # Handle both Message objects and dict responses (from restored tasks)
                    msg_id = bot_message.id if hasattr(bot_message, 'id') else bot_message.get('id', 0)
                    await publish_interaction(
                        interaction_data={'response_time': my_response_time, 'msg_id': msg_id},
                        apikey=self.api_key,
                        edit_id=result['id'],
                        edit_type='response_time',
                        logger=self.logger
                    )
                else:
                    self.logger.info(f"Failed to publish BotInteraction to server for {clip.id} ({guild.name} - #{chn})")
            except Exception as e:
                # Handle error
                self.logger.info(f"Could not send interaction: {e}")
                raise

        except errors.HTTPException as e:
            self.logger.info(f"Unknown HTTPException in _process_this_clip_link: {traceback.format_exc()}")
            emb = Embed(title="**Oops...**",
                        description=f"I messed up while trying to fetch this clip:\n{clip_link} "
                                    f"\n\nPlease try linking it again.\n"
                                    "If the issue keeps on happening, you should report this error to us!")
            asyncio.create_task(self.bot.tools.send_error_message(
                ctx=respond_to,
                msg_embed=emb,
                dm_content=f"Failed to fetch clip {clip_link}",
                guild=guild,
                bot=self.bot,
                delete_after_on_reply=60
            ))
            raise
        except Exception:
            self.logger.info(f"Unknown Exception in _process_this_clip_link: {traceback.format_exc()}")
            emb = Embed(title="**Oops...**",
                        description=f"I messed up while trying to fetch this clip:\n{clip_link} "
                                    f"\n\nPlease try linking it again.\n"
                                    "If the issue keeps on happening, you should report this error to us!")
            asyncio.create_task(self.bot.tools.send_error_message(
                ctx=respond_to,
                msg_embed=emb,
                dm_content=f"Failed to fetch clip {clip_link}",
                guild=guild,
                bot=self.bot,
                delete_after_on_reply=60
            ))
            raise
