import discord
from discord.ext import commands, tasks
import json
import aiofiles
import os
from datetime import datetime, timedelta
from dateutil import parser
import pytz
from flask import Flask
from threading import Thread

# Flask dummy web server to keep Render free tier awake
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# Start Flask in background thread
Thread(target=run_flask).start()

# Load environment variables (set in Render dashboard)
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("TOKEN environment variable not set!")

GUILD_ID = int(os.getenv("GUILD_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
PING_ROLE_ID = int(os.getenv("PING_ROLE_ID"))
DEED_DURATION_HOURS = int(os.getenv("DEED_DURATION_HOURS", 168))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

DATA_FILE = 'holdings.json'
holdings = {}  # {holding_name: expiration_iso}

# Load holdings data
async def load_data():
    global holdings
    if os.path.exists(DATA_FILE):
        async with aiofiles.open(DATA_FILE, 'r') as f:
            data = await f.read()
            holdings = json.loads(data) if data else {}
    else:
        holdings = {}

# Save holdings data
async def save_data():
    async with aiofiles.open(DATA_FILE, 'w') as f:
        await f.write(json.dumps(holdings, indent=2))

@bot.event
async def on_ready():
    print(f'{bot.user} online!')
    await load_data()
    check_expirations.start()

@bot.command()
@commands.has_permissions(administrator=True)
async def renew(ctx, holding: str, days: int = 7, hours: int = 0, minutes: int = 0):
    """
    !renew <holding> [days] [hours] [minutes]
    Examples:
    !renew Avalon                     → default 7 days
    !renew Avalon 6                   → 6 days
    !renew Hammerhold 7 4             → 7 days + 4 hours
    !renew Granitevein 7 23 15        → 7 days + 23 hours + 15 minutes
    """
    if ctx.channel.id != CHANNEL_ID:
        return

    if days < 0 or hours < 0 or minutes < 0:
        await ctx.send("❌ Numbers cannot be negative.")
        return

    total_seconds = days * 86400 + hours * 3600 + minutes * 60

    if total_seconds < 3600:  # min 1 hour
        await ctx.send("❌ Minimum 1 hour countdown please.")
        return

    exp_time = datetime.now(pytz.UTC) + timedelta(seconds=total_seconds)

    h = holding.upper()
    holdings[h] = exp_time.isoformat()
    await save_data()

    embed = discord.Embed(title="✅ Deed Renewed", color=0x00ff00)
    embed.add_field(
        name=h,
        value=f"Expires: <t:{int(exp_time.timestamp())}:F> (<t:{int(exp_time.timestamp())}:R>)",
        inline=False
    )
    embed.set_footer(text=f"Set for: {days} days, {hours} hours, {minutes} minutes")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def remove(ctx, *, holding: str):
    """
    !remove <holding>
    Removes a holding from the tracker (e.g. if expired or lost)
    Example: !remove Avalon
    """
    if ctx.channel.id != CHANNEL_ID:
        return

    h = holding.upper()
    if h in holdings:
        del holdings[h]
        await save_data()
        await ctx.send(f"🗑️ **{h}** has been removed from the tracker.")
    else:
        await ctx.send(f"❌ **{h}** is not in the list.")

@bot.command()
@commands.has_permissions(administrator=True)
async def clearall(ctx):
    """
    !clearall
    Removes ALL holdings from the tracker (emergency only!)
    """
    if ctx.channel.id != CHANNEL_ID:
        return

    holdings.clear()
    await save_data()
    # Force reload to confirm
    await load_data()
    if not holdings:
        await ctx.send("🧹 All holdings have been cleared from the tracker. List is now empty!")
    else:
        await ctx.send("⚠️ List cleared, but something seems to remain. Try !fix or restart the service in Render if it persists.")

@bot.command()
@commands.has_permissions(administrator=True)
async def fix(ctx):
    """
    !fix
    Force-clear the list (emergency if clearall didn't fully work)
    """
    if ctx.channel.id != CHANNEL_ID:
        return

    holdings.clear()
    await save_data()
    await load_data()
    await ctx.send("Force-fix executed: list is now empty.")

@bot.command()
async def status(ctx, *, holding: str = None):
    if ctx.channel.id != CHANNEL_ID:
        return
    await load_data()
    embed = discord.Embed(title="📋 Holding Status", color=0x0099ff)
    if holding:
        h = holding.upper()
        if h in holdings:
            exp = parser.parse(holdings[h])
            delta = exp - datetime.now(pytz.UTC)
            embed.add_field(name=h, value=f"Time left: {str(delta).split('.')[0]}", inline=False)
        else:
            embed.add_field(name=h, value="❌ No data", inline=False)
    else:
        if not holdings:
            embed.description = "No active holdings at the moment."
        else:
            for h, exp_iso in holdings.items():
                exp = parser.parse(exp_iso)
                delta = exp - datetime.now(pytz.UTC)
                embed.add_field(name=h, value=f"{str(delta).split('.')[0]}", inline=False)
    await ctx.send(embed=embed)

@tasks.loop(hours=1)
async def check_expirations():
    await load_data()
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return
    role = channel.guild.get_role(PING_ROLE_ID)
    now = datetime.now(pytz.UTC)
    for holding, exp_iso in list(holdings.items()):
        exp = parser.parse(exp_iso)
        delta = exp - now
        if 0 < delta.total_seconds() <= 3600:  # <1h
            embed = discord.Embed(title="🚨 DEED EXPIRING SOON", description=f"{holding} expires in <1h! Renew NOW!", color=0xff0000)
            await channel.send(f"{role.mention if role else '@leaders'}", embed=embed)
        elif 6 * 3600 < delta.total_seconds() <= 24 * 3600:  # 6-24h
            embed = discord.Embed(title="⚠️ Deed Warning", description=f"{holding} expires in {str(delta).split('.')[0]}", color=0xffff00)
            await channel.send(embed=embed)

# Run the bot
bot.run(TOKEN)
