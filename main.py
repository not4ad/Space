import os
import asyncio
import discord
from discord.ext import commands
import yt_dlp
import traceback
import requests
import json
from flask import Flask
from threading import Thread

# --- Keep Alive for Replit ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
# -----------------------------

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True # You had this, so I've re-enabled it for on_member_join

# Use your original Bot definition
bot = commands.Bot(command_prefix='_hidden_prefix_', intents=intents)

# In-memory storage for guild queues
music_queues = {} 

# --- yt-dlp and FFmpeg Options ---
YTDL_OPTIONS = {
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
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt',
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# --- YTDLSource Class (Handles searching and streaming) ---
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('webpage_url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
                data = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=not stream))
        except Exception as e:
            print(f"Error extracting info from yt-dlp: {e}")
            return None

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else yt_dlp.YoutubeDL(YTDL_OPTIONS).prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data)

# --- Playback Logic ---
async def play_next(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id in music_queues and music_queues[guild_id]:
        player = music_queues[guild_id].pop(0)
        interaction.guild.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(interaction), bot.loop))
        await interaction.channel.send(f'🎶 Now playing: **{player.title}**')
    else:
        await interaction.channel.send('✅ Queue finished.')

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.event
async def on_member_join(member):
    """This is your original welcome message event."""
    print(f'{member.name} has joined the server: {member.guild.name}')
    welcome_message = f"Hello {member.name}! 🎉 Welcome to **{member.guild.name}**!"
    try:
        await member.send(welcome_message)
    except discord.Forbidden:
        print(f"Could not send welcome DM to {member.name}.")

@bot.event
async def on_voice_state_update(member, before, after):
    """Handles auto-disconnecting when the bot is alone."""
    voice_client = member.guild.voice_client
    if voice_client and len(voice_client.channel.members) == 1 and voice_client.channel.members[0] == bot.user:
        await asyncio.sleep(60) # Wait 60 seconds
        if voice_client.is_connected() and len(voice_client.channel.members) == 1:
            await voice_client.disconnect()
            if member.guild.id in music_queues:
                music_queues[member.guild.id].clear()

# --- Music Commands ---
@bot.tree.command(name="join", description="Joins your current voice channel.")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("You are not connected to a voice channel.", ephemeral=True)
        return
    channel = interaction.user.voice.channel
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.move_to(channel)
    else:
        await channel.connect()
    await interaction.response.send_message(f"✅ Joined **{channel.name}**!")

@bot.tree.command(name="play", description="Plays a song from YouTube or adds it to the queue.")
@discord.app_commands.describe(query="The name or URL of the song.")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    voice_client = interaction.guild.voice_client
    if not voice_client:
        if interaction.user.voice:
            await interaction.user.voice.channel.connect()
            voice_client = interaction.guild.voice_client
        else:
            await interaction.followup.send("You're not in a voice channel, please join one first.", ephemeral=True)
            return

    player = await YTDLSource.from_url(query, loop=bot.loop, stream=True)
    if player is None:
        await interaction.followup.send(f"❌ Could not find or process the song: `{query}`. It might be age-restricted or private.")
        return

    guild_id = interaction.guild.id
    if guild_id not in music_queues:
        music_queues[guild_id] = []

    if voice_client.is_playing() or voice_client.is_paused():
        music_queues[guild_id].append(player)
        await interaction.followup.send(f'✅ Queued: **{player.title}**')
    else:
        voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(interaction), bot.loop))
        await interaction.followup.send(f'🎶 Now playing: **{player.title}**')

@bot.tree.command(name="skip", description="Skips the current song.")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_playing():
        return await interaction.response.send_message("I am not playing anything.", ephemeral=True)
    voice_client.stop()
    await interaction.response.send_message("⏭️ Song skipped!")

@bot.tree.command(name="stop", description="Stops the music and clears the queue.")
async def stop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id in music_queues:
        music_queues[guild_id].clear()
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.stop()
    await interaction.response.send_message("⏹️ Music stopped and queue cleared.")

@bot.tree.command(name="queue", description="Shows the current music queue.")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id not in music_queues or not music_queues[guild_id]:
        return await interaction.response.send_message("The queue is empty.", ephemeral=True)

    queue_list = "\n".join([f"{i+1}. {player.title}" for i, player in enumerate(music_queues[guild_id][:10])])
    footer = f"\n...and {len(music_queues[guild_id]) - 10} more." if len(music_queues[guild_id]) > 10 else ""
    await interaction.response.send_message(f"**Current Queue:**\n{queue_list}{footer}")

@bot.tree.command(name="nowplaying", description="Shows the currently playing song.")
async def nowplaying(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.source:
        await interaction.response.send_message(f"🎶 Now playing: **{voice_client.source.title}**")
    else:
        await interaction.response.send_message("Not playing anything right now.", ephemeral=True)

@bot.tree.command(name="leave", description="Leaves the voice channel.")
async def leave(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        guild_id = interaction.guild.id
        if guild_id in music_queues:
            music_queues[guild_id].clear()
        await voice_client.disconnect()
        await interaction.response.send_message("👋 Left the voice channel.")
    else:
        await interaction.response.send_message("I am not in a voice channel.", ephemeral=True)

# --- Your Other Commands ---
@bot.tree.command(name='generateplanet', description='Generates a description of a unique, fictional exoplanet.')
async def generate_planet(interaction: discord.Interaction):
    await interaction.response.defer()
    prompt_text = "Generate a detailed description of a unique, fictional exoplanet. Include its name, size, atmosphere, surface, climate, and any unique life forms. Make it sound fantastical and intriguing."
    try:
        planet_description = await generate_planet_with_llm(prompt_text)
        await interaction.followup.send(content=f"✨ A new world has been discovered!\n\n{planet_description[:1800]}")
    except Exception as e:
        await interaction.followup.send(content=f"🔭 An error occurred while generating a planet: {e}")

async def generate_planet_with_llm(prompt):
    gemini_api_key = os.getenv('GEMINI_API_KEY')
    if not gemini_api_key:
        return "ERROR: GEMINI_API_KEY environment variable not set."

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={gemini_api_key}"
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        response = requests.post(api_url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        result = response.json()
        return result['candidates'][0]['content']['parts'][0]['text']
    except requests.exceptions.RequestException as e:
        print(f"Gemini API request failed: {e}")
        return "Failed to contact the planet generation service."
    except (KeyError, IndexError):
        print(f"Malformed response from Gemini API: {response.text}")
        return "Received a malformed response from the planet generation service."

# --- Run the bot ---
keep_alive()
bot_token = os.getenv('DISCORD_BOT_TOKEN')
if not bot_token:
    print("ERROR: DISCORD_BOT_TOKEN not found in secrets.")
else:
    bot.run(bot_token)