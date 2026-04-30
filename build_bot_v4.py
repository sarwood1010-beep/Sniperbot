#!/usr/bin/env python3
"""Run this script on your droplet to generate bot.py v4 - Polymarket US API"""

code = '''import os, discord, json, asyncio
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timezone
from polymarket_us import PolymarketUS

load_dotenv()
TOKEN = os.environ.get("DISCORD_TOKEN")
ALERTS = int(os.environ.get("ALERTS_CHANNEL_ID", 0))
TRADES = int(os.environ.get("TRADES_CHANNEL_ID", 0))
OWNER = int(os.environ.get("AUTHORIZED_USER_ID", 0))

client = PolymarketUS(
    key_id=os.environ.get("POLYMARKET_US_KEY_ID"),
    secret_key=os.environ.get("POLYMARKET_US_SECRET"),
)

scanning = True
config = {
    "scan_interval": 120,
    "near_expiry_hours": 48,
    "min_edge": 0.02,
    "dry_run": True,
}
alerted = set()

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


def fetch_markets(limit=50):
    try:
        result = client.markets.list({"limit": limit, "active": True})
        markets = result.get("markets", [])
        return [m for m in markets if not m.get("closed", False)]
    except Exception as e:
        print(f"Fetch error: {e}")
        return []


def get_prices(m):
    sides = m.get("marketSides", [])
    prices = {}
    for s in sides:
        desc = s.get("description", "Unknown")
        price = s.get("price", "?")
        try:
            price = float(price)
        except:
            price = None
        is_long = s.get("long", True)
        prices[desc] = {"price": price, "long": is_long}
    return prices


def get_orderbook(slug):
    try:
        book = client.markets.orderbook(slug)
        return book
    except:
        return None


def hours_until(end_date):
    if not end_date:
        return 99999
    try:
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        return (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
    except:
        return 99999


def find_near_expiry(markets):
    signals = []
    for m in markets:
        hours = hours_until(m.get("endDate"))
        if hours <= 0 or hours > config["near_expiry_hours"]:
            continue
        prices = get_prices(m)
        for name, info in prices.items():
            p = info["price"]
            if p is None:
                continue
            if p >= 0.85 or p <= 0.15:
                edge = min(p, 1 - p)
                if edge >= config["min_edge"]:
                    signals.append({
                        "type": "near-expiry",
                        "market": m.get("question", "?"),
                        "slug": m.get("slug", "?"),
                        "side": name,
                        "price": p,
                        "hours": hours,
                        "category": m.get("category", "?"),
                    })
    return signals


def find_spread_opps(markets):
    signals = []
    for m in markets:
        slug = m.get("slug", "")
        book = get_orderbook(slug)
        if not book:
            continue
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if bids and asks:
            try:
                best_bid = float(bids[0].get("price", 0))
                best_ask = float(asks[0].get("price", 1))
                spread = best_ask - best_bid
                if spread >= 0.05:
                    signals.append({
                        "type": "wide-spread",
                        "market": m.get("question", "?"),
                        "slug": slug,
                        "bid": best_bid,
                        "ask": best_ask,
                        "spread": spread,
                        "category": m.get("category", "?"),
                    })
            except:
                pass
    return signals


@tree.command(name="status", description="Bot status")
async def cmd_status(interaction: discord.Interaction):
    if interaction.user.id != OWNER:
        return await interaction.response.send_message("Unauthorized", ephemeral=True)
    mode = "SCANNING" if scanning else "PAUSED"
    dry = "DRY RUN" if config["dry_run"] else "LIVE"
    await interaction.response.send_message(
        f"**Sniper Bot v4 - Polymarket US**\\n"
        f"Status: {mode} | {dry}\\n"
        f"Scan interval: {config['scan_interval']}s\\n"
        f"Near-expiry window: {config['near_expiry_hours']}h\\n"
        f"Min edge: {config['min_edge']:.0%}\\n"
        f"Alerts sent: {len(alerted)}"
    )


@tree.command(name="markets", description="Top US markets")
async def cmd_markets(interaction: discord.Interaction):
    if interaction.user.id != OWNER:
        return await interaction.response.send_message("Unauthorized", ephemeral=True)
    await interaction.response.defer()
    markets = fetch_markets(20)
    if not markets:
        return await interaction.followup.send("No markets found.")
    embed = discord.Embed(title="Polymarket US - Active Markets", color=0x3B82F6)
    seen_questions = set()
    count = 0
    for m in markets:
        q = m.get("question", "?")
        if q in seen_questions or count >= 8:
            continue
        seen_questions.add(q)
        count += 1
        prices = get_prices(m)
        price_str = "  ".join([f"{n}: {i['price']:.3f}" for n, i in prices.items() if i['price'] is not None])
        hours = hours_until(m.get("endDate"))
        expiry = f"{hours:.0f}h" if hours < 168 else m.get("endDate", "?")[:10]
        cat = m.get("category", "?")
        embed.add_field(
            name=f"[{cat}] {q[:70]}",
            value=f"{price_str}\\nExpires: {expiry}",
            inline=False,
        )
    await interaction.followup.send(embed=embed)


@tree.command(name="book", description="View orderbook for a market")
@app_commands.describe(slug="Market slug")
async def cmd_book(interaction: discord.Interaction, slug: str):
    if interaction.user.id != OWNER:
        return await interaction.response.send_message("Unauthorized", ephemeral=True)
    await interaction.response.defer()
    book = get_orderbook(slug)
    if not book:
        return await interaction.followup.send(f"Could not fetch orderbook for {slug}")
    bids = book.get("bids", [])[:5]
    asks = book.get("asks", [])[:5]
    embed = discord.Embed(title=f"Orderbook: {slug[:50]}", color=0x3B82F6)
    bid_str = "\\n".join([f"${b.get('price','?')} x {b.get('size','?')}" for b in bids]) or "Empty"
    ask_str = "\\n".join([f"${a.get('price','?')} x {a.get('size','?')}" for a in asks]) or "Empty"
    embed.add_field(name="Bids", value=bid_str, inline=True)
    embed.add_field(name="Asks", value=ask_str, inline=True)
    await interaction.followup.send(embed=embed)


@tree.command(name="scan", description="Run signal scan now")
async def cmd_scan(interaction: discord.Interaction):
    if interaction.user.id != OWNER:
        return await interaction.response.send_message("Unauthorized", ephemeral=True)
    await interaction.response.defer()
    markets = fetch_markets(50)
    ne = find_near_expiry(markets)
    total = len(ne)
    if total == 0:
        return await interaction.followup.send("No signals found. Try /set near_expiry_hours 72 or /set min_edge 0.01 to widen the net.")
    embed = discord.Embed(title=f"Scan: {total} signals found", color=0xF59E0B)
    for s in ne[:8]:
        embed.add_field(
            name=f"NEAR-EXPIRY: {s['side']} @ {s['price']:.3f}",
            value=f"[{s['category']}] {s['market'][:60]}\\n{s['hours']:.1f}h left",
            inline=False,
        )
    await interaction.followup.send(embed=embed)


@tree.command(name="pause", description="Pause scanning")
async def cmd_pause(interaction: discord.Interaction):
    global scanning
    if interaction.user.id != OWNER:
        return await interaction.response.send_message("Unauthorized", ephemeral=True)
    scanning = False
    await interaction.response.send_message("Paused.")


@tree.command(name="resume", description="Resume scanning")
async def cmd_resume(interaction: discord.Interaction):
    global scanning
    if interaction.user.id != OWNER:
        return await interaction.response.send_message("Unauthorized", ephemeral=True)
    scanning = True
    await interaction.response.send_message("Resumed.")


@tree.command(name="config", description="View config")
async def cmd_config(interaction: discord.Interaction):
    if interaction.user.id != OWNER:
        return await interaction.response.send_message("Unauthorized", ephemeral=True)
    lines = [f"**{k}:** {v}" for k, v in config.items()]
    await interaction.response.send_message("\\n".join(lines))


@tree.command(name="set", description="Update config value")
@app_commands.describe(key="Config key", value="New value")
async def cmd_set(interaction: discord.Interaction, key: str, value: str):
    if interaction.user.id != OWNER:
        return await interaction.response.send_message("Unauthorized", ephemeral=True)
    if key not in config:
        keys = ", ".join(config.keys())
        return await interaction.response.send_message(f"Unknown key. Valid: {keys}")
    old = config[key]
    if isinstance(old, bool):
        config[key] = value.lower() in ("true", "1", "yes")
    elif isinstance(old, float):
        config[key] = float(value)
    elif isinstance(old, int):
        config[key] = int(value)
    else:
        config[key] = value
    await interaction.response.send_message(f"**{key}:** {old} -> {config[key]}")


@tree.command(name="update", description="Pull latest bot from GitHub and restart")
async def cmd_update(interaction: discord.Interaction):
    if interaction.user.id != OWNER:
        return await interaction.response.send_message("Unauthorized", ephemeral=True)
    await interaction.response.defer()
    try:
        import urllib.request
        url = "https://api.github.com/repos/sarwood1010-beep/Sniperbot/issues"
        r = urllib.request.urlopen(url)
        issues = json.loads(r.read().decode())
        if not issues:
            return await interaction.followup.send("No issues found in repo.")
        latest = issues[0]
        body = latest.get("body", "")
        title = latest.get("title", "unknown")
        with open("build_bot_latest.py", "w") as f:
            f.write(body)
        exec(open("build_bot_latest.py").read())
        await interaction.followup.send(f"Updated from: {title}. Restarting...")
        import subprocess
        subprocess.Popen(["sudo", "systemctl", "restart", "sniper-bot"])
    except Exception as e:
        await interaction.followup.send(f"Update failed: {e}")


async def scanner_loop():
    await bot.wait_until_ready()
    ch = bot.get_channel(ALERTS)
    while not bot.is_closed():
        if scanning and ch:
            try:
                markets = fetch_markets(50)
                for s in find_near_expiry(markets):
                    sig_id = f"ne-{s['slug']}-{s['side']}"
                    if sig_id not in alerted:
                        alerted.add(sig_id)
                        embed = discord.Embed(title="NEAR-EXPIRY SIGNAL", color=0xF59E0B)
                        embed.add_field(name="Market", value=s["market"][:100], inline=False)
                        embed.add_field(name="Side", value=s["side"], inline=True)
                        embed.add_field(name="Price", value=f"{s['price']:.3f}", inline=True)
                        embed.add_field(name="Hours Left", value=f"{s['hours']:.1f}h", inline=True)
                        embed.add_field(name="Category", value=s["category"], inline=True)
                        await ch.send(embed=embed)
            except Exception as e:
                print(f"Scanner error: {e}")
        await asyncio.sleep(config["scan_interval"])


@bot.event
async def on_ready():
    await tree.sync()
    print(f"Bot ready as {bot.user}")
    ch = bot.get_channel(ALERTS)
    if ch:
        await ch.send(
            "**Sniper Bot v4 online! (Polymarket US)**\\n"
            "Commands: /markets /book /scan /status /config /set /pause /resume /update"
        )
    bot.loop.create_task(scanner_loop())


bot.run(TOKEN)
'''

with open("bot.py", "w") as f:
    f.write(code)
print("bot.py v4 created successfully!")
