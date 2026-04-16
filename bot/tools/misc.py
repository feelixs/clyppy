from interactions import SlashContext, Member
from bot.tools.dl import DownloadManager
import logging


class Tools:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.dl = DownloadManager(self)

    async def send_error_message(self, msg_embed, dm_content, guild, ctx, bot, delete_after_on_reply=None):
        if isinstance(ctx, SlashContext):
            return
        err = ""
        if guild.id == ctx.author.id:
            pass  # don't use 'dm' setting if we're already in a dm, just reply
        elif bot.guild_settings.is_dm_on_error(guild.id):
            await self.send_dm_err_msg(ctx, guild, dm_content)
            return

        if error_channel_id := bot.guild_settings.get_error_channel(guild.id):
            if error_channel := bot.get_channel(error_channel_id):
                try:
                    await error_channel.send(embed=msg_embed)
                    return
                except Exception as e:
                    err += f"An error occurred when trying to message the channel <#{error_channel_id}>\n"
                    self.logger.warning(f"Cannot send to error channel {error_channel_id} in guild {guild.id}: {e}")
            else:
                err += (f"Could not find the channel <#{error_channel_id}>. "
                        f"Please reset the `error_channel` with `/setup`\n")

        await ctx.reply(err, embed=msg_embed, delete_after=delete_after_on_reply)

    async def send_dm_err_msg(self, ctx, guild, content):
        try:
            await ctx.author.send(f"{content}\n\n"
                                  f"This error occurred while trying to embed the clip in {guild.name}. "
                                  f"You're receiving this message because that server has the 'dm' setting "
                                  f"enabled for one of its `/settings`")
        except Exception as e:
            if isinstance(ctx.author, Member):
                ctx.author.username = ctx.author.nick
            self.logger.info(f"Failed to send DM to {ctx.author.username} ({ctx.author.id}) {str(e)}")
