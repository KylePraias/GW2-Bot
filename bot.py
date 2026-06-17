import discord
from discord import app_commands
from discord.ext import tasks
import aiohttp
import asyncio
import json
import os
import io
import logging
from datetime import datetime, timezone, time as dt_time

logging.basicConfig(level=logging.INFO)

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CONFIG_FILE   = "/data/config.json"
# Update roster at the top of every hour
ROSTER_TIMES  = [dt_time(hour=h, minute=0) for h in range(24)]

# In-memory item cache: list of {"id": int, "name": str}
item_cache: list[dict] = []
item_cache_ready = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}

def save_config(config: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, CONFIG_FILE)

def get_guild_config(guild_id: int) -> dict | None:
    return load_config().get(str(guild_id))

def set_guild_config(guild_id: int, data: dict):
    config = load_config()
    config[str(guild_id)] = data
    save_config(config)

def delete_guild_config(guild_id: int):
    config = load_config()
    config.pop(str(guild_id), None)
    save_config(config)

# ---------------------------------------------------------------------------
# Report system config helpers
# Stored as a top-level key in config.json:
# { "reports": { "<discord_guild_id>": {
#       "button_channel_id": int, "report_channel_id": int,
#       "button_message_id": int, "setup_by": int
#   } } }
# ---------------------------------------------------------------------------

def get_report_config(guild_id: int) -> dict | None:
    config = load_config()
    return config.get("reports", {}).get(str(guild_id))

def set_report_config(guild_id: int, data: dict):
    config = load_config()
    config.setdefault("reports", {})[str(guild_id)] = data
    save_config(config)

def delete_report_config(guild_id: int):
    config = load_config()
    config.get("reports", {}).pop(str(guild_id), None)
    save_config(config)

# ---------------------------------------------------------------------------
# Verified accounts helpers
# ---------------------------------------------------------------------------

def get_verified(guild_id: int) -> dict:
    cfg = get_guild_config(guild_id)
    if not cfg:
        return {}
    return cfg.get("verified", {})

def link_account(guild_id: int, gw2_name: str, discord_user_id: int):
    cfg = get_guild_config(guild_id)
    if cfg is None:
        return
    cfg.setdefault("verified", {})[gw2_name] = discord_user_id
    set_guild_config(guild_id, cfg)

def unlink_account(guild_id: int, gw2_name: str):
    cfg = get_guild_config(guild_id)
    if cfg is None:
        return
    cfg.get("verified", {}).pop(gw2_name, None)
    set_guild_config(guild_id, cfg)

def find_gw2_names_for_discord_user(guild_id: int, discord_user_id: int) -> list[str]:
    """Find all GW2 account names linked to a given Discord user ID."""
    verified = get_verified(guild_id)
    return [gw2_name for gw2_name, uid in verified.items() if uid == discord_user_id]

def find_gw2_name_for_discord_user(guild_id: int, discord_user_id: int) -> str | None:
    """Find the first GW2 account name linked to a given Discord user ID."""
    names = find_gw2_names_for_discord_user(guild_id, discord_user_id)
    return names[0] if names else None


def build_nickname(gw2_names: list[str]) -> str | None:
    """Build a Discord nickname from a list of GW2 account names. Returns None if empty."""
    if not gw2_names:
        return None
    # Join all account names with " / ", truncate to Discord's 32 char limit
    nick = " / ".join(gw2_names)
    if len(nick) > 32:
        nick = nick[:29] + "..."
    return nick


def get_role_names(cfg: dict) -> tuple[str, str]:
    """Returns (member_role_name, guest_role_name), defaulting to 'Member' and 'Guest'."""
    return cfg.get("member_role_name", "Member"), cfg.get("guest_role_name", "Guest")

# ---------------------------------------------------------------------------
# Autodelete channel helpers
# Stored as a top-level key in config.json:
# { "autodelete": { "<discord_guild_id>": [channel_id, ...] } }
# ---------------------------------------------------------------------------

def get_autodelete_channels(guild_id: int) -> list[int]:
    config = load_config()
    return config.get("autodelete", {}).get(str(guild_id), [])

def add_autodelete_channel(guild_id: int, channel_id: int):
    config = load_config()
    ad = config.setdefault("autodelete", {})
    channels = ad.setdefault(str(guild_id), [])
    if channel_id not in channels:
        channels.append(channel_id)
    save_config(config)

def remove_autodelete_channel(guild_id: int, channel_id: int):
    config = load_config()
    ad = config.get("autodelete", {})
    channels = ad.get(str(guild_id), [])
    if channel_id in channels:
        channels.remove(channel_id)
    save_config(config)

# ---------------------------------------------------------------------------
# GW2 API — roster
# ---------------------------------------------------------------------------

async def fetch_roster(api_key: str, gw2_guild_id: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://api.guildwars2.com/v2/guild/{gw2_guild_id}",
            headers=headers
        ) as resp:
            resp.raise_for_status()
            guild_data = await resp.json()

        async with session.get(
            f"https://api.guildwars2.com/v2/guild/{gw2_guild_id}/members",
            headers=headers
        ) as resp:
            resp.raise_for_status()
            members = await resp.json()

    return guild_data, members

# ---------------------------------------------------------------------------
# GW2 API — item cache (loads in background on startup)
# ---------------------------------------------------------------------------

async def build_item_cache():
    global item_cache, item_cache_ready
    logging.info("Building GW2 item cache...")

    try:
        async with aiohttp.ClientSession() as session:
            # Get total page count
            async with session.get(
                "https://api.guildwars2.com/v2/items?page=0&page_size=200"
            ) as resp:
                resp.raise_for_status()
                total_pages = int(resp.headers.get("X-Page-Total", 1))
                first_page  = await resp.json()

            all_items = list(first_page)

            # Fetch remaining pages concurrently in batches of 10
            sem = asyncio.Semaphore(10)

            async def fetch_page(page_num):
                async with sem:
                    async with session.get(
                        f"https://api.guildwars2.com/v2/items?page={page_num}&page_size=200"
                    ) as r:
                        if r.status == 200:
                            return await r.json()
                        return []

            tasks_list = [fetch_page(p) for p in range(1, total_pages)]
            results    = await asyncio.gather(*tasks_list, return_exceptions=True)

            for result in results:
                if isinstance(result, list):
                    all_items.extend(result)

        # Store only id and name, filter out items with no name
        item_cache = [
            {"id": item["id"], "name": item["name"]}
            for item in all_items
            if item.get("name")
        ]
        item_cache.sort(key=lambda x: x["name"])
        item_cache_ready = True
        logging.info(f"Item cache ready — {len(item_cache)} items loaded.")

    except Exception as e:
        logging.error(f"Failed to build item cache: {e}")


async def fetch_item_details(item_id: int) -> dict | None:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://api.guildwars2.com/v2/items/{item_id}"
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            return None


async def fetch_tp_prices(item_id: int) -> dict | None:
    """Fetch trading post buy/sell prices for an item. Returns None if not tradable."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://api.guildwars2.com/v2/commerce/prices/{item_id}"
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            return None  # 404 = not on trading post


def search_items(query: str, limit: int = 25) -> list[dict]:
    """Return up to `limit` items whose names contain the query (case-insensitive)."""
    q = query.lower()
    results = []
    for item in item_cache:
        if q in item["name"].lower():
            results.append(item)
            if len(results) >= limit:
                break
    return results

# ---------------------------------------------------------------------------
# Rarity colours for embeds
# ---------------------------------------------------------------------------

RARITY_COLORS = {
    "Junk":       0xAAAAAA,
    "Basic":      0xFFFFFF,
    "Fine":       0x62A4DA,
    "Masterwork": 0x1a9306,
    "Rare":       0xFCD00B,
    "Exotic":     0xFFA405,
    "Ascended":   0xFF69B4,
    "Legendary":  0x4C139D,
}

WIKI_BASE = "https://wiki.guildwars2.com/wiki/"

def item_to_embed(item: dict, prices: dict | None = None) -> discord.Embed:
    name     = item.get("name", "Unknown Item")
    rarity   = item.get("rarity", "Basic")
    itype    = item.get("type", "")
    desc     = item.get("description", "")
    icon_url = item.get("icon", "")
    level    = item.get("level", 0)
    vendor   = item.get("vendor_value", 0)

    color = RARITY_COLORS.get(rarity, 0xFFFFFF)
    embed = discord.Embed(title=name, color=color)

    # Type and rarity
    embed.add_field(name="Rarity", value=rarity, inline=True)
    if itype:
        subtype = item.get("details", {}).get("type", "")
        type_str = f"{itype} — {subtype}" if subtype else itype
        embed.add_field(name="Type", value=type_str, inline=True)
    if level:
        embed.add_field(name="Required Level", value=str(level), inline=True)

    # Description / flavour text
    if desc:
        # Strip GW2 colour tags like <c=@flavour>...</c>
        import re
        clean_desc = re.sub(r"<[^>]+>", "", desc)
        if clean_desc:
            embed.add_field(name="Description", value=clean_desc[:1024], inline=False)

    # Stats (weapons/armour)
    details = item.get("details", {})
    attributes = details.get("infix_upgrade", {}).get("attributes", [])
    if attributes:
        stats_str = "\n".join(f"+{a['modifier']} {a['attribute']}" for a in attributes)
        embed.add_field(name="Stats", value=stats_str, inline=False)

    # Defense (armour)
    defense = details.get("defense")
    if defense:
        embed.add_field(name="Defense", value=str(defense), inline=True)

    # Damage (weapons)
    min_power = details.get("min_power")
    max_power = details.get("max_power")
    if min_power and max_power:
        embed.add_field(name="Damage", value=f"{min_power}–{max_power}", inline=True)

    # Vendor value (convert copper to g/s/c)
    if vendor:
        gold   = vendor // 10000
        silver = (vendor % 10000) // 100
        copper = vendor % 100
        parts  = []
        if gold:   parts.append(f"{gold}g")
        if silver: parts.append(f"{silver}s")
        if copper: parts.append(f"{copper}c")
        embed.add_field(name="Vendor Value", value=" ".join(parts) or "0c", inline=True)

    # Trading post prices
    if prices:
        def fmt_copper(copper: int) -> str:
            gold   = copper // 10000
            silver = (copper % 10000) // 100
            c      = copper % 100
            parts  = []
            if gold:   parts.append(f"{gold}g")
            if silver: parts.append(f"{silver}s")
            if c:      parts.append(f"{c}c")
            return " ".join(parts) or "0c"

        sells = prices.get("sells", {})
        buys  = prices.get("buys", {})
        sell_price = sells.get("unit_price", 0)
        buy_price  = buys.get("unit_price", 0)
        sell_qty   = sells.get("quantity", 0)
        buy_qty    = buys.get("quantity", 0)

        if sell_price:
            embed.add_field(
                name="Sell (lowest offer)",
                value=f"{fmt_copper(sell_price)}\n_{sell_qty:,} listed_",
                inline=True
            )
        if buy_price:
            embed.add_field(
                name="Buy (highest bid)",
                value=f"{fmt_copper(buy_price)}\n_{buy_qty:,} orders_",
                inline=True
            )

    # Icon
    if icon_url:
        embed.set_thumbnail(url=icon_url)

    # Wiki link
    wiki_name = name.replace(" ", "_")
    embed.add_field(
        name="Wiki",
        value=f"[{name} on the GW2 Wiki]({WIKI_BASE}{wiki_name})",
        inline=False
    )

    embed.set_footer(text=f"Item ID: {item.get('id', '?')} • {rarity}")
    return embed

# ---------------------------------------------------------------------------
# Roster helpers
# ---------------------------------------------------------------------------

def build_roster_text(guild_data: dict, members: list, verified: dict | None = None) -> tuple[list[str], str]:
    """
    Returns (discord_chunks, plain_text).
    discord_chunks is a list of strings each under 1900 chars, ready to post as separate messages.
    plain_text is the full untruncated roster for download.
    """
    guild_name     = guild_data.get("name", "Unknown Guild")
    tag            = guild_data.get("tag", "")
    now            = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    verified_names = set(verified.keys()) if verified else set()

    by_rank: dict[str, list] = {}
    for m in members:
        by_rank.setdefault(m["rank"], []).append(m["name"])
    for rank in by_rank:
        by_rank[rank].sort()

    # Plain text (full, no limit)
    plain_lines = [
        f"[{tag}] {guild_name} — Guild Roster",
        f"Members: {len(members)}",
        f"Updated: {now}",
        "",
    ]
    for rank, names in sorted(by_rank.items()):
        plain_lines.append(f"== {rank} ({len(names)}) ==")
        for name in names:
            suffix = " (Verified)" if name in verified_names else ""
            plain_lines.append(f"  {name}{suffix}")
        plain_lines.append("")
    plain_text = "\n".join(plain_lines)

    # Discord lines — split into chunks under 1900 chars
    all_lines = [
        f"**[{tag}] {guild_name} — Roster ({len(members)} members)**",
        f"-# Last updated: {now}\n"
    ]
    for rank, names in sorted(by_rank.items()):
        all_lines.append(f"**{rank}** ({len(names)})")
        for name in names:
            suffix = " ✅" if name in verified_names else ""
            all_lines.append(f"  • {name}{suffix}")
        all_lines.append("")

    chunks = []
    current = ""
    for line in all_lines:
        addition = line + "\n"
        if len(current) + len(addition) > 1900:
            if current.strip():
                chunks.append(current.strip())
            current = addition
        else:
            current += addition
    if current.strip():
        chunks.append(current.strip())

    return chunks, plain_text

# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class RosterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Download roster (.txt)",
        style=discord.ButtonStyle.secondary,
        emoji="⬇️",
        custom_id="download_roster",
    )
    async def download_roster(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_config(interaction.guild_id)
        if not cfg:
            await interaction.response.send_message("❌ Roster is not configured for this server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            guild_data, members = await fetch_roster(cfg["api_key"], cfg["gw2_guild_id"])
            verified = cfg.get("verified", {})
            _chunks, plain_text = build_roster_text(guild_data, members, verified)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to fetch roster: {e}", ephemeral=True)
            return

        file = discord.File(
            fp=io.BytesIO(plain_text.encode()),
            filename=f"{guild_data.get('name', 'roster').replace(' ', '_')}_roster.txt"
        )
        await interaction.followup.send("Here's the current roster:", file=file, ephemeral=True)

# ---------------------------------------------------------------------------
# Report / feedback system
# ---------------------------------------------------------------------------

REPORT_CATEGORIES = [
    ("Member Conduct", "⚠️"),
    ("Suggestion", "💡"),
    ("Event Feedback", "🎉"),
    ("Other", "📝"),
]

class ReportModal(discord.ui.Modal, title="Submit a Report"):
    def __init__(self, category: str, anonymous: bool):
        super().__init__()
        self.category  = category
        self.anonymous = anonymous

    message = discord.ui.TextInput(
        label="Your message",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your report or feedback here...",
        max_length=1000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        rcfg = get_report_config(interaction.guild_id)
        if not rcfg:
            await interaction.response.send_message("❌ The report system is not configured for this server.", ephemeral=True)
            return

        report_channel = interaction.client.get_channel(rcfg["report_channel_id"])
        if report_channel is None:
            await interaction.response.send_message("❌ The report channel could not be found. Contact an admin.", ephemeral=True)
            return

        submitter = "Anonymous" if self.anonymous else interaction.user.display_name

        embed = discord.Embed(
            title=f"New Report — {self.category}",
            description=self.message.value,
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Submitted by", value=submitter, inline=True)
        embed.add_field(name="Category", value=self.category, inline=True)

        try:
            await report_channel.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to post in the report channel.", ephemeral=True)
            return

        logging.info(
            f"Report submitted in guild {interaction.guild_id} — category={self.category}, "
            f"anonymous={self.anonymous}, by={interaction.user.id}"
        )
        await interaction.response.send_message("✅ Your report has been submitted. Thank you!", ephemeral=True)


class ReportCategorySelect(discord.ui.Select):
    def __init__(self, anonymous: bool):
        self.anonymous = anonymous
        options = [
            discord.SelectOption(label=name, emoji=emoji)
            for name, emoji in REPORT_CATEGORIES
        ]
        super().__init__(placeholder="Choose a category...", options=options, custom_id="report_category_select")

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        await interaction.response.send_modal(ReportModal(category=category, anonymous=self.anonymous))


class ReportCategoryView(discord.ui.View):
    """Ephemeral, short-lived view shown after the user picks anonymous/named."""
    def __init__(self, anonymous: bool):
        super().__init__(timeout=180)
        self.add_item(ReportCategorySelect(anonymous=anonymous))


class ReportView(discord.ui.View):
    """Persistent view attached to the report button message."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Submit a Report",
        style=discord.ButtonStyle.primary,
        emoji="📨",
        custom_id="open_report_form",
    )
    async def open_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        rcfg = get_report_config(interaction.guild_id)
        if not rcfg:
            await interaction.response.send_message("❌ The report system is not configured for this server.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Would you like to include your server nickname, or remain anonymous?",
            view=AnonymityChoiceView(),
            ephemeral=True
        )


class AnonymityChoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Use my nickname", style=discord.ButtonStyle.secondary, emoji="🙋")
    async def named(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choose a category for your report:",
            view=ReportCategoryView(anonymous=False),
            ephemeral=True
        )

    @discord.ui.button(label="Stay anonymous", style=discord.ButtonStyle.secondary, emoji="🕶️")
    async def anonymous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choose a category for your report:",
            view=ReportCategoryView(anonymous=True),
            ephemeral=True
        )

# ---------------------------------------------------------------------------
# Post or edit roster message
# ---------------------------------------------------------------------------

async def post_or_edit_roster(channel: discord.TextChannel, cfg: dict, guild_id: int):
    guild_data, members = await fetch_roster(cfg["api_key"], cfg["gw2_guild_id"])
    verified = cfg.get("verified", {})
    chunks, _ = build_roster_text(guild_data, members, verified)

    # cfg["message_ids"] is the new field — list of message IDs in order.
    # cfg["message_id"] (old single-message field) is migrated on first run.
    existing_ids: list[int] = cfg.get("message_ids", [])
    if not existing_ids and cfg.get("message_id"):
        existing_ids = [cfg["message_id"]]

    needed   = len(chunks)
    have     = len(existing_ids)
    final_view = RosterView()

    # Edit or post messages for each chunk
    new_ids: list[int] = []
    for i, chunk in enumerate(chunks):
        view = final_view if i == needed - 1 else None
        if i < have:
            # Try to edit existing message
            try:
                msg = await channel.fetch_message(existing_ids[i])
                await msg.edit(content=chunk, view=view)
                new_ids.append(msg.id)
                logging.info(f"Edited roster message {msg.id} (chunk {i+1}/{needed}) in #{channel.name}")
            except discord.NotFound:
                # Message gone — post fresh
                msg = await channel.send(content=chunk, view=view)
                new_ids.append(msg.id)
                logging.info(f"Re-posted roster message {msg.id} (chunk {i+1}/{needed}) in #{channel.name}")
        else:
            # Need a new message
            msg = await channel.send(content=chunk, view=view)
            new_ids.append(msg.id)
            logging.info(f"Posted new roster message {msg.id} (chunk {i+1}/{needed}) in #{channel.name}")

    # Delete any leftover messages if roster shrank
    for old_id in existing_ids[needed:]:
        try:
            old_msg = await channel.fetch_message(old_id)
            await old_msg.delete()
            logging.info(f"Deleted surplus roster message {old_id} in #{channel.name}")
        except discord.NotFound:
            pass

    # Save updated message IDs
    cfg["message_ids"] = new_ids
    cfg.pop("message_id", None)  # remove old single-id field
    set_guild_config(guild_id, cfg)
    return members

# ---------------------------------------------------------------------------
# Sync verifications
# ---------------------------------------------------------------------------

async def sync_verifications(discord_guild: discord.Guild, cfg: dict, members: list):
    current_gw2_names = {m["name"] for m in members}
    verified = cfg.get("verified", {})

    to_unlink = [gw2_name for gw2_name in verified if gw2_name not in current_gw2_names]

    for gw2_name in to_unlink:
        discord_user_id = verified[gw2_name]
        logging.info(f"{gw2_name} left the GW2 guild — unlinking Discord user {discord_user_id}")

        try:
            pass  # Roles are intentionally left unchanged when accounts are unlinked
        except Exception:
            pass

        # Update nickname to reflect remaining accounts, or reset if none left
        remaining = find_gw2_names_for_discord_user(discord_guild.id, discord_user_id)
        # Remove this account from remaining (it's still in verified at this point)
        remaining = [n for n in remaining if n != gw2_name]
        try:
            member_obj = await discord_guild.fetch_member(discord_user_id)
            nick = build_nickname(remaining)
            await member_obj.edit(nick=nick, reason="GW2 account left guild — nickname updated")
        except (discord.NotFound, discord.Forbidden):
            pass
        except Exception as e:
            logging.warning(f"Nickname update failed during sync for {discord_user_id}: {e}")

        unlink_account(discord_guild.id, gw2_name)

    if to_unlink:
        logging.info(f"Unlinked {len(to_unlink)} accounts that left the GW2 guild: {to_unlink}")

# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # needed to detect non-command messages
bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


@bot.event
async def on_ready():
    bot.add_view(RosterView())
    bot.add_view(ReportView())
    await tree.sync()
    logging.info(f"Logged in as {bot.user} ({bot.user.id})")
    if not roster_loop.is_running():
        roster_loop.start()
    # Build item cache in background — bot is usable immediately
    asyncio.create_task(build_item_cache())


@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from bots (including ourselves)
    if message.author.bot:
        return
    # Ignore DMs
    if not message.guild:
        return

    autodelete_channels = get_autodelete_channels(message.guild.id)
    if message.channel.id not in autodelete_channels:
        return

    # Allow slash commands — they start with "/" and Discord sends them as
    # interaction payloads, so by the time on_message fires the content is
    # empty for slash commands. Delete anything with actual text content.
    if message.content or message.attachments or message.embeds:
        try:
            await message.delete()
            logging.info(
                f"Auto-deleted message from {message.author} "
                f"in #{message.channel.name} ({message.guild.name})"
            )
        except discord.Forbidden:
            logging.warning(f"Missing permissions to delete message in #{message.channel.name}")
        except discord.NotFound:
            pass  # Already deleted

# ---------------------------------------------------------------------------
# /setup
# ---------------------------------------------------------------------------

@tree.command(name="setup", description="[Admin] Configure the GW2 roster bot.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    api_key      = "Your GW2 API key (guilds permission, must be guild leader)",
    gw2_guild_id = "Your GW2 Guild ID (UUID)",
    channel      = "Channel to post the daily roster in",
    member_role  = "Role to grant on verification (default: role named 'Member')",
    guest_role   = "Role to remove on verification (default: role named 'Guest')",
)
async def setup(
    interaction: discord.Interaction,
    api_key: str,
    gw2_guild_id: str,
    channel: discord.TextChannel,
    member_role: discord.Role | None = None,
    guest_role: discord.Role | None = None,
):
    await interaction.response.defer(ephemeral=True)

    try:
        guild_data, members = await fetch_roster(api_key, gw2_guild_id)
    except aiohttp.ClientResponseError as e:
        if e.status == 403:
            await interaction.followup.send("❌ Access denied. Check guilds permission and guild leader status.", ephemeral=True)
        elif e.status == 404:
            await interaction.followup.send("❌ Guild not found. Double-check your GW2 Guild ID.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ GW2 API error: HTTP {e.status}", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"❌ Unexpected error: {e}", ephemeral=True)
        return

    existing_cfg     = get_guild_config(interaction.guild_id)
    existing_verified = existing_cfg.get("verified", {}) if existing_cfg else {}

    # Preserve existing role names if not specified this time, otherwise default to "Member"/"Guest"
    existing_member_name = existing_cfg.get("member_role_name", "Member") if existing_cfg else "Member"
    existing_guest_name  = existing_cfg.get("guest_role_name", "Guest") if existing_cfg else "Guest"

    member_role_name = member_role.name if member_role else existing_member_name
    guest_role_name  = guest_role.name if guest_role else existing_guest_name

    # Verify the Member role actually exists on this server before saving.
    # Guest role is optional in general usage, but if a name is set (explicitly
    # or via default), it must exist too — otherwise fail loudly now rather
    # than silently later.
    resolved_member_role = member_role or discord.utils.get(interaction.guild.roles, name=member_role_name)
    resolved_guest_role  = guest_role or discord.utils.get(interaction.guild.roles, name=guest_role_name)

    if not resolved_member_role:
        await interaction.followup.send(
            f"❌ Setup failed: no role named **{member_role_name}** exists on this server. "
            f"Create the role first, or pick an existing one with the `member_role` option.",
            ephemeral=True
        )
        return

    if not resolved_guest_role:
        await interaction.followup.send(
            f"❌ Setup failed: no role named **{guest_role_name}** exists on this server. "
            f"Create the role first, or pick an existing one with the `guest_role` option.",
            ephemeral=True
        )
        return

    cfg = {
        "api_key":          api_key,
        "gw2_guild_id":     gw2_guild_id,
        "channel_id":       channel.id,
        "setup_by":         interaction.user.id,
        "verified":         existing_verified,
        "member_role_name": member_role_name,
        "guest_role_name":  guest_role_name,
    }
    set_guild_config(interaction.guild_id, cfg)

    try:
        await post_or_edit_roster(channel, cfg, interaction.guild_id)
    except Exception as e:
        await interaction.followup.send(f"✅ Config saved, but failed to post initial roster: {e}", ephemeral=True)
        return

    gw2_name = guild_data.get("name", gw2_guild_id)
    await interaction.followup.send(
        f"✅ Done! **{gw2_name}** roster ({len(members)} members) posted in {channel.mention}.\n"
        f"Updates every hour on the hour. Members can use `/verify` to link their GW2 account.\n"
        f"Verification grants the **{member_role_name}** role and removes **{guest_role_name}**.\n"
        f"Use `/unsetup` to remove this configuration.",
        ephemeral=True
    )

# ---------------------------------------------------------------------------
# /verify
# ---------------------------------------------------------------------------

@tree.command(name="verify", description="Verify your GW2 account to receive the Member role.")
@app_commands.describe(gw2_username="Your full GW2 account name, including the numbers (e.g. PlayerName.1234)")
async def verify(interaction: discord.Interaction, gw2_username: str):
    await interaction.response.defer(ephemeral=True)

    cfg = get_guild_config(interaction.guild_id)
    if not cfg:
        await interaction.followup.send("❌ This bot has not been set up yet. An admin needs to run `/setup` first.", ephemeral=True)
        return

    discord_guild = interaction.guild
    member_role_name, guest_role_name = get_role_names(cfg)
    member_role = discord.utils.get(discord_guild.roles, name=member_role_name)
    guest_role  = discord.utils.get(discord_guild.roles, name=guest_role_name)

    if not member_role:
        await interaction.followup.send(f"❌ Could not find a role named **{member_role_name}** on this server. Ask an admin to create it.", ephemeral=True)
        return

    verified = get_verified(interaction.guild_id)
    gw2_username = gw2_username.strip()

    if gw2_username in verified:
        await interaction.followup.send("❌ That GW2 account is already linked to another Discord user.", ephemeral=True)
        return

    try:
        _, members = await fetch_roster(cfg["api_key"], cfg["gw2_guild_id"])
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch the guild roster: {e}", ephemeral=True)
        return

    if gw2_username not in {m["name"] for m in members}:
        await interaction.followup.send(
            f"❌ **{gw2_username}** is not a member of the guild. "
            "Check your full account name including the numbers (e.g. `PlayerName.1234`).",
            ephemeral=True
        )
        return

    try:
        if member_role not in interaction.user.roles:
            await interaction.user.add_roles(member_role, reason="GW2 guild membership verified")
        if guest_role and guest_role in interaction.user.roles:
            await interaction.user.remove_roles(guest_role, reason="GW2 guild membership verified")
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to manage roles. Make sure my role is above Member and Guest.", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to assign roles: {e}", ephemeral=True)
        return

    link_account(interaction.guild_id, gw2_username, interaction.user.id)

    # Set nickname to all linked accounts
    all_names = find_gw2_names_for_discord_user(interaction.guild_id, interaction.user.id)
    nick = build_nickname(all_names)
    try:
        await interaction.user.edit(nick=nick, reason="GW2 verification — nickname updated")
    except discord.Forbidden:
        logging.warning(f"Could not set nickname for {interaction.user.id} — missing permissions")
    except Exception as e:
        logging.warning(f"Nickname set failed for {interaction.user.id}: {e}")

    logging.info(f"Verified {gw2_username} as Discord user {interaction.user.id}")

    await interaction.followup.send(
        f"✅ Verified! Welcome, **{gw2_username}**. You've been given the **{member_role_name}** role "
        f"and your server nickname has been updated.",
        ephemeral=True
    )

# ---------------------------------------------------------------------------
# /forceverify  — admin only, verify another user manually
# ---------------------------------------------------------------------------

@tree.command(name="forceverify", description="[Admin] Manually verify a Discord user as a GW2 guild member.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    member       = "The Discord user to verify",
    gw2_username = "Their full GW2 account name, including the numbers (e.g. PlayerName.1234)"
)
async def forceverify(interaction: discord.Interaction, member: discord.Member, gw2_username: str):
    await interaction.response.defer(ephemeral=True)

    cfg = get_guild_config(interaction.guild_id)
    if not cfg:
        await interaction.followup.send("❌ Bot is not set up yet. Run `/setup` first.", ephemeral=True)
        return

    discord_guild = interaction.guild
    member_role_name, guest_role_name = get_role_names(cfg)
    member_role = discord.utils.get(discord_guild.roles, name=member_role_name)
    guest_role  = discord.utils.get(discord_guild.roles, name=guest_role_name)

    if not member_role:
        await interaction.followup.send(f"❌ Could not find a role named **{member_role_name}** on this server.", ephemeral=True)
        return

    verified = get_verified(interaction.guild_id)
    gw2_username = gw2_username.strip()

    # Check if the GW2 account is already linked to someone else
    if gw2_username in verified:
        await interaction.followup.send(
            f"❌ **{gw2_username}** is already linked to another Discord user.",
            ephemeral=True
        )
        return

    # Check the account is actually in the guild
    try:
        _, members = await fetch_roster(cfg["api_key"], cfg["gw2_guild_id"])
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch the guild roster: {e}", ephemeral=True)
        return

    if gw2_username not in {m["name"] for m in members}:
        await interaction.followup.send(
            f"❌ **{gw2_username}** is not a member of the guild. "
            "Check the account name including the numbers (e.g. `PlayerName.1234`).",
            ephemeral=True
        )
        return

    # Assign roles
    try:
        if member_role not in member.roles:
            await member.add_roles(member_role, reason=f"Force-verified by admin {interaction.user}")
        if guest_role and guest_role in member.roles:
            await member.remove_roles(guest_role, reason=f"Force-verified by admin {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to manage roles.", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to assign roles: {e}", ephemeral=True)
        return

    # Set nickname
    try:
        await member.edit(nick=gw2_username, reason=f"Force-verified by admin {interaction.user}")
    except discord.Forbidden:
        logging.warning(f"Could not set nickname for {member.id} — missing permissions")
    except Exception as e:
        logging.warning(f"Nickname set failed for {member.id}: {e}")

    link_account(interaction.guild_id, gw2_username, member.id)

    # Update nickname to reflect all linked accounts
    all_names = find_gw2_names_for_discord_user(interaction.guild_id, member.id)
    nick = build_nickname(all_names)
    try:
        await member.edit(nick=nick, reason=f"Force-verified by admin {interaction.user}")
    except discord.Forbidden:
        logging.warning(f"Could not set nickname for {member.id} — missing permissions")
    except Exception as e:
        logging.warning(f"Nickname set failed for {member.id}: {e}")

    logging.info(f"Admin {interaction.user.id} force-verified {gw2_username} as Discord user {member.id}")

    await interaction.followup.send(
        f"✅ {member.mention} has been verified as **{gw2_username}** and given the **{member_role_name}** role.",
        ephemeral=True
    )


# ---------------------------------------------------------------------------
# /item  — search GW2 items with autocomplete
# ---------------------------------------------------------------------------

@tree.command(name="item", description="Search for a GW2 item and view its details.")
@app_commands.describe(name="Start typing an item name to search")
async def item_search(interaction: discord.Interaction, name: str):
    # Check guest role before deferring so ephemeral state is set correctly
    item_cfg = get_guild_config(interaction.guild_id)
    _, guest_role_name = get_role_names(item_cfg) if item_cfg else (None, "Guest")
    guest_role = discord.utils.get(interaction.guild.roles, name=guest_role_name)
    if guest_role and guest_role in interaction.user.roles:
        await interaction.response.send_message(
            "❌ You need to verify your GW2 account first. Use `/verify` to get access.",
            ephemeral=True
        )
        return

    # Defer publicly — result will be visible to everyone in the channel
    await interaction.response.defer(ephemeral=False)

    if not item_cache_ready:
        await interaction.followup.send("⏳ Item database is still loading, please try again in a moment.", ephemeral=True)
        return

    item_id   = None
    item_data = None

    if "|" in name:
        parts = name.rsplit("|", 1)
        try:
            item_id   = int(parts[1])
            item_data = await fetch_item_details(item_id)
        except (ValueError, IndexError):
            pass

    if item_data is None:
        matches = search_items(name, limit=1)
        if not matches:
            await interaction.followup.send(f"❌ No items found matching **{name}**.", ephemeral=True)
            return
        item_data = await fetch_item_details(matches[0]["id"])

    if item_data is None:
        await interaction.followup.send("❌ Could not fetch item details. Try again in a moment.", ephemeral=True)
        return

    prices = await fetch_tp_prices(item_data["id"])
    embed  = item_to_embed(item_data, prices)
    await interaction.followup.send(embed=embed)


@item_search.autocomplete("name")
async def item_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not item_cache_ready or len(current) < 2:
        return []

    matches = search_items(current, limit=25)
    return [
        app_commands.Choice(name=item["name"][:100], value=f"{item['name']}|{item['id']}")
        for item in matches
    ]

# ---------------------------------------------------------------------------
# helpers shared by unlink commands
# ---------------------------------------------------------------------------

async def _remove_member_roles(discord_guild: discord.Guild, member: discord.Member, reason: str):
    """Clear nickname when all accounts are unlinked. Roles are intentionally left unchanged."""
    try:
        await member.edit(nick=None, reason=reason)
    except discord.Forbidden:
        pass

# ---------------------------------------------------------------------------
# /unlink  — admin: unlink a specific GW2 account from a user
# ---------------------------------------------------------------------------

@tree.command(name="unlink", description="[Admin] Unlink a specific GW2 account from a Discord user.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    member       = "The Discord user to unlink",
    gw2_username = "The specific GW2 account name to unlink"
)
async def unlink(interaction: discord.Interaction, member: discord.Member, gw2_username: str):
    await interaction.response.defer(ephemeral=True)

    gw2_username = gw2_username.strip()
    verified     = get_verified(interaction.guild_id)

    if gw2_username not in verified or verified[gw2_username] != member.id:
        await interaction.followup.send(
            f"❌ **{gw2_username}** is not linked to {member.mention}.",
            ephemeral=True
        )
        return

    unlink_account(interaction.guild_id, gw2_username)

    # Only remove Member role if they have no remaining linked accounts
    remaining = find_gw2_names_for_discord_user(interaction.guild_id, member.id)
    if not remaining:
        try:
            await _remove_member_roles(interaction.guild, member, reason=f"Unlinked by admin {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to manage roles.", ephemeral=True)
            return
    else:
        # Update nickname to reflect remaining accounts
        nick = build_nickname(remaining)
        try:
            await member.edit(nick=nick, reason=f"Account unlinked by admin {interaction.user}")
        except discord.Forbidden:
            logging.warning(f"Could not update nickname for {member.id}")
        except Exception as e:
            logging.warning(f"Nickname update failed for {member.id}: {e}")

    logging.info(f"Admin {interaction.user.id} unlinked {gw2_username} from Discord user {member.id}")
    remaining_msg = f" They still have {len(remaining)} linked account(s)." if remaining else " They have no remaining linked accounts."
    await interaction.followup.send(
        f"✅ Unlinked **{gw2_username}** from {member.mention}.{remaining_msg}",
        ephemeral=True
    )

# ---------------------------------------------------------------------------
# /unlinkall  — admin: unlink all GW2 accounts from a user
# ---------------------------------------------------------------------------

@tree.command(name="unlinkall", description="[Admin] Unlink all GW2 accounts from a Discord user.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(member="The Discord user to fully unlink")
async def unlinkall(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)

    gw2_names = find_gw2_names_for_discord_user(interaction.guild_id, member.id)
    if not gw2_names:
        await interaction.followup.send(
            f"❌ {member.mention} has no linked GW2 accounts.",
            ephemeral=True
        )
        return

    for name in gw2_names:
        unlink_account(interaction.guild_id, name)

    try:
        await _remove_member_roles(interaction.guild, member, reason=f"All accounts unlinked by admin {interaction.user}")
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to manage roles.", ephemeral=True)
        return

    logging.info(f"Admin {interaction.user.id} unlinked all accounts from Discord user {member.id}: {gw2_names}")
    names_list = ", ".join(f"**{n}**" for n in gw2_names)
    await interaction.followup.send(
        f"✅ Unlinked {len(gw2_names)} account(s) from {member.mention}: {names_list}. ",
        ephemeral=True
    )

# ---------------------------------------------------------------------------
# /viewmyaccounts  — any user: see their own linked accounts
# ---------------------------------------------------------------------------

@tree.command(name="viewmyaccounts", description="View your linked GW2 accounts.")
async def viewmyaccounts(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    gw2_names = find_gw2_names_for_discord_user(interaction.guild_id, interaction.user.id)
    if not gw2_names:
        await interaction.followup.send(
            "You don't have any linked GW2 accounts. Use `/verify` to link one.",
            ephemeral=True
        )
        return

    names_list = "\n".join(f"• **{n}**" for n in gw2_names)
    await interaction.followup.send(
        f"Your linked GW2 account(s):\n{names_list}",
        ephemeral=True
    )

# ---------------------------------------------------------------------------
# /viewaccounts  — admin: see linked accounts for any user
# ---------------------------------------------------------------------------

@tree.command(name="viewaccounts", description="[Admin] View all linked GW2 accounts for a Discord user.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(member="The Discord user to look up")
async def viewaccounts(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)

    gw2_names = find_gw2_names_for_discord_user(interaction.guild_id, member.id)
    if not gw2_names:
        await interaction.followup.send(
            f"{member.mention} has no linked GW2 accounts.",
            ephemeral=True
        )
        return

    names_list = "\n".join(f"• **{n}**" for n in gw2_names)
    await interaction.followup.send(
        f"{member.mention} has {len(gw2_names)} linked GW2 account(s):\n{names_list}",
        ephemeral=True
    )

# ---------------------------------------------------------------------------
# /addautodelete  /viewautodelete  /removeautodelete
# ---------------------------------------------------------------------------

@tree.command(name="setupautodelete", description="[Admin] Add a channel to the auto-delete list (bot commands only).")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="The channel to restrict to bot commands only")
async def setupautodelete(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)

    channels = get_autodelete_channels(interaction.guild_id)
    if channel.id in channels:
        await interaction.followup.send(
            f"❌ {channel.mention} is already in the auto-delete list.",
            ephemeral=True
        )
        return

    add_autodelete_channel(interaction.guild_id, channel.id)
    await interaction.followup.send(
        f"✅ {channel.mention} added to auto-delete. Any non-bot-command messages sent there will be deleted instantly.",
        ephemeral=True
    )
    logging.info(f"Admin {interaction.user.id} added #{channel.name} to autodelete in guild {interaction.guild_id}")


@tree.command(name="viewautodelete", description="[Admin] View all channels on the auto-delete list.")
@app_commands.default_permissions(administrator=True)
async def viewautodelete(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    channel_ids = get_autodelete_channels(interaction.guild_id)
    if not channel_ids:
        await interaction.followup.send("No channels are currently on the auto-delete list.", ephemeral=True)
        return

    lines = []
    for cid in channel_ids:
        ch = interaction.guild.get_channel(cid)
        lines.append(f"• {ch.mention if ch else f'Unknown channel ({cid})'}")

    channel_list = "\n".join(lines)
    await interaction.followup.send(
        f"**Auto-delete channels ({len(channel_ids)}):**\n{channel_list}",
        ephemeral=True
    )


@tree.command(name="removeautodelete", description="[Admin] Remove a channel from the auto-delete list.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="The channel to remove from the auto-delete list")
async def removeautodelete(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)

    channels = get_autodelete_channels(interaction.guild_id)
    if channel.id not in channels:
        await interaction.followup.send(
            f"❌ {channel.mention} is not on the auto-delete list.",
            ephemeral=True
        )
        return

    remove_autodelete_channel(interaction.guild_id, channel.id)
    await interaction.followup.send(
        f"✅ {channel.mention} removed from auto-delete. Messages will no longer be deleted there.",
        ephemeral=True
    )
    logging.info(f"Admin {interaction.user.id} removed #{channel.name} from autodelete in guild {interaction.guild_id}")


# ---------------------------------------------------------------------------
# /setupreport  /viewreport  /removereport
# ---------------------------------------------------------------------------

@tree.command(name="setupreport", description="[Admin] Set up the report/feedback button system.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    button_channel = "Channel where the report button message will be posted",
    report_channel = "Channel where submitted reports will be sent",
)
async def setupreport(interaction: discord.Interaction, button_channel: discord.TextChannel, report_channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="📨 Submit a Report or Feedback",
        description="Click the button below to submit a report, suggestion, or feedback. ",
        color=0x5865F2,
    )

    try:
        msg = await button_channel.send(embed=embed, view=ReportView())
    except discord.Forbidden:
        await interaction.followup.send(f"❌ I don't have permission to send messages in {button_channel.mention}.", ephemeral=True)
        return

    set_report_config(interaction.guild_id, {
        "button_channel_id": button_channel.id,
        "report_channel_id": report_channel.id,
        "button_message_id": msg.id,
        "setup_by":           interaction.user.id,
    })

    logging.info(f"Admin {interaction.user.id} set up reports in guild {interaction.guild_id}")
    await interaction.followup.send(
        f"✅ Report system set up. Button posted in {button_channel.mention}, "
        f"reports will be sent to {report_channel.mention}.",
        ephemeral=True
    )


@tree.command(name="viewreport", description="[Admin] View the current report system configuration.")
@app_commands.default_permissions(administrator=True)
async def viewreport(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    rcfg = get_report_config(interaction.guild_id)
    if not rcfg:
        await interaction.followup.send("The report system is not set up for this server.", ephemeral=True)
        return

    button_channel = interaction.guild.get_channel(rcfg["button_channel_id"])
    report_channel = interaction.guild.get_channel(rcfg["report_channel_id"])

    await interaction.followup.send(
        f"**Report system configuration:**\\n"
        f"• Button posted in: {button_channel.mention if button_channel else 'Unknown channel'}\\n"
        f"• Reports sent to: {report_channel.mention if report_channel else 'Unknown channel'}",
        ephemeral=True
    )


@tree.command(name="removereport", description="[Admin] Remove the report system entirely.")
@app_commands.default_permissions(administrator=True)
async def removereport(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    rcfg = get_report_config(interaction.guild_id)
    if not rcfg:
        await interaction.followup.send("The report system is not set up for this server.", ephemeral=True)
        return

    button_channel = interaction.guild.get_channel(rcfg["button_channel_id"])
    if button_channel:
        try:
            msg = await button_channel.fetch_message(rcfg["button_message_id"])
            await msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    delete_report_config(interaction.guild_id)
    logging.info(f"Admin {interaction.user.id} removed the report system in guild {interaction.guild_id}")
    await interaction.followup.send("✅ Report system removed.", ephemeral=True)


# ---------------------------------------------------------------------------
# /unsetup
# ---------------------------------------------------------------------------

@tree.command(name="unsetup", description="[Admin]Remove the GW2 roster bot configuration for this server.")
@app_commands.default_permissions(administrator=True)
async def unsetup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    cfg = get_guild_config(interaction.guild_id)
    if not cfg:
        await interaction.followup.send("Nothing to remove — bot isn't configured here.", ephemeral=True)
        return

    is_admin    = interaction.user.guild_permissions.administrator
    is_setupper = interaction.user.id == cfg.get("setup_by")
    if not is_admin and not is_setupper:
        await interaction.followup.send("❌ Only the person who ran `/setup` or a server administrator can remove the configuration.", ephemeral=True)
        return

    delete_guild_config(interaction.guild_id)
    await interaction.followup.send("✅ Configuration removed.", ephemeral=True)

# ---------------------------------------------------------------------------
# Roster update loop
# ---------------------------------------------------------------------------

@tasks.loop(time=ROSTER_TIMES)
async def roster_loop():
    config = load_config()
    for guild_id_str, cfg in config.items():
        # Skip non-guild top-level keys like "autodelete"
        if not guild_id_str.isdigit():
            continue
        guild_id      = int(guild_id_str)
        channel       = bot.get_channel(cfg["channel_id"])
        discord_guild = bot.get_guild(guild_id)

        if channel is None:
            logging.warning(f"Channel {cfg['channel_id']} not found for guild {guild_id}")
            continue

        try:
            members = await post_or_edit_roster(channel, cfg, guild_id)
            if discord_guild and members:
                await sync_verifications(discord_guild, cfg, members)
        except Exception as e:
            logging.error(f"Failed to update roster for guild {guild_id}: {e}")


bot.run(DISCORD_TOKEN)