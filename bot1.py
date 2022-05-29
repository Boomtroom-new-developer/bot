import discord
from discord.ext import commands
from discord.utils import get
import youtube_dl
import asyncio
from functools import partial
from async_timeout import timeout
import itertools



user = {'BoomTroom#5895'}

prefix = "$"
bot = commands.Bot(command_prefix=prefix, help_command=None)

youtube_dl.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
    'options': '-vn',
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5" ## song will end if no this line
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester

        self.title = data.get('title')
        self.web_url = data.get('webpage_url')

        # YTDL info dicts (data) have other useful information you might want
        # https://github.com/rg3/youtube-dl/blob/master/README.md

    def __getitem__(self, item: str):
        """Allows us to access attributes similar to a dict.
        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)

    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        await ctx.send(f'```ini\n[Added {data["title"]} to the Queue.]\n```') #delete after can be added

        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']}

        return cls(discord.FFmpegPCMAudio(source, **ffmpeg_options), data=data, requester=ctx.author)

    @classmethod
    async def regather_stream(cls, data, *, loop):
        """Used for preparing a stream, instead of downloading.
        Since Youtube Streaming links expire."""
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(ytdl.extract_info, url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)

        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpeg_options), data=data, requester=requester)

class MusicPlayer:
    """A class which is assigned to each guild using the bot for Music.
    This class implements a queue and loop, which allows for different guilds to listen to different playlists
    simultaneously.
    When the bot disconnects from the Voice it's instance will be destroyed.
    """

    __slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current', 'np', 'volume')

    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None  # Now playing message
        self.volume = .5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        """Our main player loop."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                # Wait for the next song. If we timeout cancel the player and disconnect...
                async with timeout(300):  # 5 minutes...
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                del players[self._guild]
                return await self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
                # Source was probably a stream (not downloaded)
                # So we should regather to prevent stream expiration
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'There was an error processing your song.\n'
                                             f'```css\n[{e}]\n```')
                    continue

            source.volume = self.volume
            self.current = source

            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            self.np = await self._channel.send(f'**Now Playing:** `{source.title}` requested by '
                                               f'`{source.requester}`')
            await self.next.wait()

            # Make sure the FFmpeg process is cleaned up.
            source.cleanup()
            self.current = None

            try:
                # We are no longer playing this song...
                await self.np.delete()
            except discord.HTTPException:
                pass

    async def destroy(self, guild):
        """Disconnect and cleanup the player."""
        await self._guild.voice_client.disconnect()
        return self.bot.loop.create_task(self._cog.cleanup(guild))


@bot.event
async def on_ready():
    print(f"Success logged in as {bot.user}")

@bot.command()
async def send(ctx, *, arg):
    await ctx.channel.send(f"{arg}")

@bot.command()
async def logout(ctx):
    if ctx.author == 'BoomTroom#5895':
        bot.logout()

@bot.command()
async def play(ctx, search: str):
    
    bot = ctx.bot
    channel = ctx.author.voice.channel
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    
    
    await ctx.channel.send('Please wait')

    if voice_client == None:
        await ctx.channel.send("Joined")
        await channel.connect()
        voice_client = get(bot.voice_clients, guild=ctx.guild)

    await ctx.trigger_typing()

    _player = get_player(ctx)
    source = await YTDLSource.create_source(ctx, search, loop=bot.loop, download=False)

    await _player.queue.put(source)

players = {}
def get_player(ctx):
    try:
        player = players[ctx.guild.id]
    except:
        player = MusicPlayer(ctx)
        players[ctx.guild.id] = player
    
    return player


@bot.command()
async def stop(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        embed = discord.Embed(
            title='bot music',
            description='play youtube music',
            color=discord.Color.from_rgb(255,0,0)
            )
        embed.add_field(name='run command by',value=f'{ctx.author}',inline=True)
        embed.add_field(name='Error',value='bot is not connect to voice channel',inline=True)
        embed.set_thumbnail(url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')
        embed.set_footer(text=f'bot by {user}',icon_url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')

        await ctx.channel.send(embed=embed, delete_after=10)
        return

    if voice_client.channel != ctx.author.voice.channel:
        embed = discord.Embed(
            title='bot music',
            description='play youtube music',
            color=discord.Color.from_rgb(248,255,0)
            )
        
        

        embed.add_field(name='run command by',value=f'{ctx.author}',inline=True)
        embed.add_field(name='Error',value='the bot is currently connected to {0}'.format(voice_client.channel),inline=True)
        embed.set_thumbnail(url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')
        embed.set_footer(text=f'bot by {user}',icon_url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')

        await ctx.channel.send(embed=embed, delete_after=10)
        return

    voice_client.stop()

@bot.command()
async def leave(ctx):
    
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    
    if voice_client == None:
        embed = discord.Embed(
            title='bot music',
            description='play youtube music',
            color=discord.Color.from_rgb(255,0,0)
            )
        embed.add_field(name='run command by',value=f'{ctx.author}',inline=True)
        embed.add_field(name='Error',value='bot is not connect to voice channel',inline=True)
        embed.set_thumbnail(url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')
        embed.set_footer(text=f'bot by {user}',icon_url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')

        await ctx.channel.send(embed=embed, delete_after=10)
        return

    if voice_client.channel != ctx.author.voice.channel:
        embed = discord.Embed(
            title='bot music',
            description='play youtube music',
            color=discord.Color.from_rgb(248,255,0)
            )
        
        

        embed.add_field(name='run command by',value=f'{ctx.author}',inline=True)
        embed.add_field(name='Error',value='the bot is currently connected to {0}'.format(voice_client.channel),inline=True)
        embed.set_thumbnail(url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')
        embed.set_footer(text=f'bot by {user}',icon_url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')
        await ctx.channel.send(embed=embed, delete_after=10)
        return
        
    del players[ctx.guild.id]
    await ctx.voice_client.disconnect()

@bot.command()
async def pause(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        embed = discord.Embed(
            title='bot music',
            description='play youtube music',
            color=discord.Color.from_rgb(255,0,0)
            )
        embed.add_field(name='run command by',value=f'{ctx.author}',inline=True)
        embed.add_field(name='Error',value='bot is not connect to voice channel',inline=True)
        embed.set_thumbnail(url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')
        embed.set_footer(text=f'bot by {user}',icon_url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')

        await ctx.channel.send(embed=embed, delete_after=10)
        return

    if voice_client.channel != ctx.author.voice.channel:
        embed = discord.Embed(
            title='bot music',
            description='play youtube music',
            color=discord.Color.from_rgb(248,255,0)
            )
        
        

        embed.add_field(name='run command by',value=f'{ctx.author}',inline=True)
        embed.add_field(name='Error',value='the bot is currently connected to {0}'.format(voice_client.channel),inline=True)
        embed.set_thumbnail(url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')
        embed.set_footer(text=f'bot by {user}',icon_url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')

        await ctx.channel.send(embed=embed, delete_after=10)
        return

@bot.command()
async def resume(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        embed = discord.Embed(
            title='bot music',
            description='play youtube music',
            color=discord.Color.from_rgb(255,0,0)
            )
        embed.add_field(name='run command by',value=f'{ctx.author}',inline=True)
        embed.add_field(name='Error',value='bot is not connect to voice channel',inline=True)
        embed.set_thumbnail(url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')
        embed.set_footer(text=f'bot by {user}',icon_url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')

        await ctx.channel.send(embed=embed, delete_after=10)
        return

    if voice_client.channel != ctx.author.voice.channel:
        embed = discord.Embed(
            title='bot music',
            description='play youtube music',
            color=discord.Color.from_rgb(248,255,0)
            )
        
        

        embed.add_field(name='run command by',value=f'{ctx.author}',inline=True)
        embed.add_field(name='Error',value='the bot is currently connected to {0}'.format(voice_client.channel),inline=True)
        embed.set_thumbnail(url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')
        embed.set_footer(text=f'bot by {user}',icon_url='https://www.pngall.com/wp-content/uploads/8/Vector-Sound-PNG-Image-File.png')

        await ctx.channel.send(embed=embed, delete_after=10)
        return

    voice_client.resume()

@bot.command()
async def skip(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    if voice_client == None or not voice_client.is_connected():
        await ctx.channel.send("Bot is not connected to vc", delete_after=10)
        return

    if voice_client.is_paused():
        pass
    elif not voice_client.is_playing():
        return

    voice_client.stop()
    await ctx.send(f'**`{ctx.author}`**: Skipped the song!')


@bot.command()
async def queueList(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    if voice_client == None or not voice_client.is_connected():
        await ctx.channel.send("Bot is not connected to vc", delete_after=10)
        return
    
    player = get_player(ctx)
    if player.queue.empty():
        return await ctx.send('There are currently no more queued songs')
    
    # 1 2 3
    upcoming = list(itertools.islice(player.queue._queue,0,player.queue.qsize()))
    fmt = '\n'.join(f'**`{_["title"]}`**' for _ in upcoming)
    embed = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt)
    await ctx.send(embed=embed)

@bot.command()
async def volume(ctx, volume: int):
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    if voice_client == None or not voice_client.is_connected():
        await ctx.channel.send("Bot is not connected to vc", delete_after=10)
        return

    if volume < 0 or volume > 100:
        return await ctx.send('Volume must be between 0 and 100')

    voice_client.source.volume = volume / 100
    await ctx.send(f'**`{ctx.author}`**: Set the volume to **{volume}%**')

@bot.command()
async def nowPlaying(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    if voice_client == None or not voice_client.is_connected():
        await ctx.channel.send("Bot is not connected to vc", delete_after=10)
        return

    player = get_player(ctx)
    if player.current == None:
        return await ctx.send('There are currently no songs playing')

    embed = discord.Embed(title=f'Now Playing - {player.current["title"]}', description=player.current["description"])
    embed.set_thumbnail(url=player.current["thumbnail"])
    embed.set_footer(text=f'Requested by {player.current["requester"]}',icon_url=player.current["requester_avatar"])
    await ctx.send(embed=embed)

@bot.command()
async def delete(ctx, amount: int):
    await ctx.channel.purge(limit=amount)

@bot.command()
async def kick(ctx, member: discord.Member, *, reason=None):
    await member.kick(reason=reason)
    await ctx.send(f'**`{ctx.author}`**: Kicked **{member}**')

@bot.command()
async def ban(ctx, member: discord.Member, *, reason=None):
    await member.ban(reason=reason)
    await ctx.send(f'**`{ctx.author}`**: Banned **{member}**')

@bot.command()
async def unban(ctx, *, member):
    banned_users = await ctx.guild.bans()
    member_name, member_discriminator = member.split('#')

    for ban_entry in banned_users:
        user = ban_entry.user

        if (user.name, user.discriminator) == (member_name, member_discriminator):
            await ctx.guild.unban(user)
            await ctx.send(f'**`{ctx.author}`**: Unbanned **{user}**')
            return

@bot.command()
async def mute(ctx, member: discord.Member):
    role = get(ctx.guild.roles, name='Muted')
    await member.add_roles(role)
    await ctx.send(f'**`{ctx.author}`**: Muted **{member}**')

@bot.command()
async def unmute(ctx, member: discord.Member):
    role = get(ctx.guild.roles, name='Muted')
    await member.remove_roles(role)
    await ctx.send(f'**`{ctx.author}`**: Unmuted **{member}**')

@bot.command()
async def kickban(ctx, member: discord.Member, *, reason=None):
    await member.kick(reason=reason)
    await member.ban(reason=reason)
    await ctx.send(f'**`{ctx.author}`**: Kicked and banned **{member}**')


@bot.command()
async def unbanall(ctx, *, reason=None):
    for member in ctx.guild.members:
        await member.unban(reason=reason)
    await ctx.send(f'**`{ctx.author}`**: Unbanned everyone')

@bot.command()
async def clear(ctx, amount: int):
    await ctx.channel.purge(limit=amount)

@bot.command()
async def help(ctx):
    embed = discord.Embed(title='Help', description='This is a list of all the commands you can use in this bot', color=0x00ff00)
    embed.add_field(name='!play', value='Plays a song from youtube', inline=False)
    embed.add_field(name='!skip', value='Skips the current song', inline=False)
    embed.add_field(name='!queue', value='Shows the current queue', inline=False)
    embed.add_field(name='!volume', value='Changes the volume of the music', inline=False)
    embed.add_field(name='!nowplaying', value='Shows the current song playing', inline=False)
    embed.add_field(name='!delete', value='Deletes the amount of messages specified', inline=False)
    embed.add_field(name='!kick', value='Kicks the specified member', inline=False)
    embed.add_field(name='!ban', value='Bans the specified member', inline=False)
    embed.add_field(name='!unban', value='Unbans the specified member', inline=False)
    embed.add_field(name='!kickban', value='Kicks and bans the specified member', inline=False)
    embed.add_field(name='!unbanall', value='Unbans everyone', inline=False)
    embed.add_field(name='!mute', value='Mutes the specified member', inline=False)
    embed.add_field(name='!unmute', value='Unmutes the specified member', inline=False)
    embed.add_field(name='!clear', value='Clears the amount of messages specified', inline=False)
    embed.add_field(name='!help', value='Shows this message', inline=False)
    await ctx.send(embed=embed)

bot.run("OTY4MTM3NjUwNzE4NTMxNjQ0.Gi1q_f.dAWnZ6IWzzMcG4UyVJIZIJJRm8wUoQSNmyX3wE")

