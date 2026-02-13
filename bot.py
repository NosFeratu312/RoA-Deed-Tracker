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

# Flask dummy web server om Render free tier wakker te houden
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# Start Flask in een background thread
Thread(target=run_flask).start()

# Laad environment variables (ingesteld in Render dashboard)
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

# Laad holdings data
async def load_data():
    global holdings
    if os.path.exists(DATA_FILE):
        async with aiofiles.open(DATA_FILE, 'r') as f:
            data = await f.read()
            holdings = json.loads(data) if data else {}

# Sla holdings data op
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
    !renew <holding> [dagen] [uren] [minuten]
    Voorbeelden:
    !renew Avalon                     → standaard 7 dagen
    !renew Avalon 6                   → 6 dagen
    !renew Hammerhold 7 4             → 7 dagen + 4 uur
    !renew Granitevein 7 23 15        → 7 dagen + 23 uur + 15 minuten
    """
    if ctx.channel.id != CHANNEL_ID:
        return

    if days < 0 or hours < 0 or minutes < 0:
        await ctx.send("❌ Getallen mogen niet negatief zijn.")
        return

    total_seconds = days * 86400 + hours * 3600 + minutes * 60

    if total_seconds < 3600:  # minimaal 1 uur
        await ctx.send("❌ Minimaal 1 uur countdown aub.")
        return

    exp_time = datetime.now(pytz.UTC) + timedelta(seconds=total_seconds)

    h = holding.upper()
    holdings[h] = exp_time.isoformat()
    await save_data()

    embed = discord.Embed(title="✅ Deed vernieuwd", color=0x00ff00)
    embed.add_field(
        name=h,
        value=f"Verloopt: <t:{int(exp_time.timestamp())}:F> (<t:{int(exp_time.timestamp())}:R>)",
        inline=False
    )
    embed.set_footer(text=f"Ingevoerd: {days} dagen, {hours} uur, {minutes} minuten")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def remove(ctx, *, holding: str):
    """
    !remove <holding>
    Verwijdert een holding uit de tracker (bijv. als verlopen of verloren)
    Voorbeeld: !remove Avalon
    """
    if ctx.channel.id != CHANNEL_ID:
        return

    h = holding.upper()
    if h in holdings:
        del holdings[h]
        await save_data()
        await ctx.send(f"🗑️ **{h}** is verwijderd uit de tracker.")
    else:
        await ctx.send(f"❌ **{h}** staat niet in de lijst.")

@bot.command()
@commands.has_permissions(administrator=True)
async def clearall(ctx):
    """
    !clearall
    Verwijdert ALLE holdings uit de tracker (noodgeval!)
    """
    if ctx.channel.id != CHANNEL_ID:
        return

    if holdings:
        holdings.clear()
        await save_data()
        await ctx.send("🧹 Alle holdings zijn verwijderd uit de tracker.")
    else:
        await ctx.send("De lijst was al leeg.")

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
            embed.description = "Geen holdings actief op dit moment."
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

# Start de bot
bot.run(TOKEN)
