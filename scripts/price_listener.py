"""Instant #buy-sell price checks: stays connected to Discord and answers a "PC:" line within seconds.

Same replies as price_check.py (raw TCGplayer prices, free data), but event-driven instead of a
5-minute poll. Needs an always-on machine (it holds a live connection), plus:
    pip install discord.py
    DISCORD_READ_BOT_TOKEN, BUYSELL_CHANNEL_ID, WEBHOOK_BUY_SELL in the environment
    the bot's "Message Content Intent" switched on in the Discord developer portal
Run it from a checkout of this repo; it pulls the nightly price data once an hour.
If you run this, disable the 5-minute "Buy-sell price checks" workflow so replies aren't doubled.
"""
import asyncio
import os
import subprocess

import discord

from common import ROOT, load_config, log
from price_check import LINE, reply

TOKEN = os.environ.get("DISCORD_READ_BOT_TOKEN", "").strip()
CHAN = os.environ.get("BUYSELL_CHANNEL_ID", "").strip()

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


async def refresh_data():
    while True:
        await asyncio.sleep(3600)
        try:
            subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only", "-q"], timeout=120, check=False)
        except Exception as e:
            log(f"[listener] git pull failed: {e}")


@client.event
async def on_ready():
    log(f"[listener] connected as {client.user}; watching channel {CHAN}")
    client.loop.create_task(refresh_data())


@client.event
async def on_message(m):
    if m.author.bot or m.webhook_id or str(m.channel.id) != CHAN:
        return
    cfg = load_config()
    for x in list(LINE.finditer(m.content or ""))[:3]:
        try:
            await asyncio.to_thread(reply, cfg, {"author": {"id": str(m.author.id)}}, x.group("q"))
        except Exception as e:
            log(f"[listener] reply failed: {e}")


if __name__ == "__main__":
    if not TOKEN or not CHAN:
        raise SystemExit("DISCORD_READ_BOT_TOKEN / BUYSELL_CHANNEL_ID not set")
    client.run(TOKEN)
