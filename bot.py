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
# Update roster every 10 minutes (xx:00, xx:10, xx:20, xx:30, xx:40, xx:50)
ROSTER_TIMES  = [dt_time(hour=h, minute=m) for h in range(24) for m in range(0, 60, 10)]

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


def get_guest_role_name(cfg: dict) -> str:
    """Returns the guest role name to remove on verification, defaulting to 'Guest'."""
    return cfg.get("guest_role_name", "Guest")

# ---------------------------------------------------------------------------
# Guild rank role management
# ---------------------------------------------------------------------------

# The canonical GW2 guild ranks the bot manages, in hierarchy order (highest first).
# The bot will ONLY ever create, assign, or remove roles with these exact names.
# Any other Discord role (pronouns, content, etc.) is never touched.
GUILD_RANKS = ["Leader", "Officer", "Content Officer", "Founding Member", "Member"]

# Map rank name -> its position index in GUILD_RANKS (0 = highest)
RANK_PRIORITY: dict[str, int] = {r: i for i, r in enumerate(GUILD_RANKS)}


def highest_rank(gw2_names: list[str], members: list[dict]) -> str | None:
    """
    Given a list of GW2 account names and the full member list, return the
    highest guild rank held across all those accounts.  Invited members are
    excluded.  Returns None if none of the names are full guild members.
    """
    rank_by_name = {
        m["name"]: m["rank"]
        for m in members
        if m.get("rank", "").lower() != "invited"
    }
    best: str | None = None
    best_priority = len(GUILD_RANKS)  # lower is better
    for name in gw2_names:
        rank = rank_by_name.get(name)
        if rank and rank in RANK_PRIORITY:
            p = RANK_PRIORITY[rank]
            if p < best_priority:
                best_priority = p
                best = rank
    return best


async def ensure_rank_role(discord_guild: discord.Guild, rank_name: str) -> discord.Role:
    """
    Return the Discord role with the given rank name, creating it if it does
    not exist.  Newly created roles are positioned so that higher ranks sit
    above lower ones, directly below the bot's own top role.
    """
    role = discord.utils.get(discord_guild.roles, name=rank_name)
    if role:
        return role

    # Create the role with no colour (plain)
    role = await discord_guild.create_role(
        name=rank_name,
        reason="GW2 rank role auto-created by roster bot",
    )
    logging.info(f"Created rank role '{rank_name}' in guild {discord_guild.id}")

    # Position roles in rank order just below the bot's highest role
    bot_member   = discord_guild.get_member(discord_guild.me.id)
    bot_top_pos  = max((r.position for r in bot_member.roles), default=1)

    # Build desired positions: highest rank gets the highest position (just below bot)
    all_rank_roles = []
    for r_name in reversed(GUILD_RANKS):   # reversed = lowest rank first
        r = discord.utils.get(discord_guild.roles, name=r_name)
        if r:
            all_rank_roles.append(r)

    # Assign positions starting just above @everyone, up toward the bot
    base = 1
    positions = {}
    for idx, r in enumerate(all_rank_roles):
        positions[r] = base + idx

    try:
        await discord_guild.edit_role_positions(positions)
    except (discord.Forbidden, discord.HTTPException) as e:
        logging.warning(f"Could not reorder rank roles in guild {discord_guild.id}: {e}")

    return role


async def apply_rank_role(
    discord_guild: discord.Guild,
    discord_member: discord.Member,
    rank_name: str | None,
    guest_role_name: str,
):
    """
    Ensure discord_member holds exactly the correct rank role and no other
    guild rank roles.  Also removes the guest role if present.

    - rank_name: the GW2 rank to assign, or None if the member is not in the
      guild (roles are removed but nothing is added).
    """
    guest_role = discord.utils.get(discord_guild.roles, name=guest_role_name)

    # Collect which rank roles the member currently holds
    current_rank_roles = [r for r in discord_member.roles if r.name in GUILD_RANKS]

    # The target role object (create if needed)
    target_role = await ensure_rank_role(discord_guild, rank_name) if rank_name else None
    target_role_id = target_role.id if target_role else None

    roles_to_add    = []
    roles_to_remove = []

    # Remove all wrong rank roles (compare by ID, not object identity)
    for r in current_rank_roles:
        if r.id != target_role_id:
            roles_to_remove.append(r)

    # Add the correct rank role if not already held (compare by ID)
    if target_role and not any(r.id == target_role_id for r in discord_member.roles):
        roles_to_add.append(target_role)

    # Remove guest role if present (compare by ID)
    if guest_role and any(r.id == guest_role.id for r in discord_member.roles):
        roles_to_remove.append(guest_role)

    try:
        if roles_to_remove:
            await discord_member.remove_roles(*roles_to_remove, reason="GW2 rank sync")
        if roles_to_add:
            await discord_member.add_roles(*roles_to_add, reason="GW2 rank sync")
    except discord.Forbidden:
        logging.warning(f"Missing permissions to update rank roles for {discord_member.id} in guild {discord_guild.id}")
    except Exception as e:
        logging.warning(f"Failed to update rank roles for {discord_member.id}: {e}")

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
# Giveaway helpers
# Stored as a top-level key in config.json:
# { "giveaways": { "<discord_guild_id>": { "<name>": {
#       "channel_id": int,
#       "message_id": int | None,
#       "min_gold": int,
#       "created_at": str (ISO 8601 UTC),
#       "entries": [gw2_name, ...],
#       "closed": bool
#   } } } }
# ---------------------------------------------------------------------------

def get_all_giveaways(guild_id: int) -> dict:
    config = load_config()
    return config.get("giveaways", {}).get(str(guild_id), {})

def get_giveaway(guild_id: int, name: str) -> dict | None:
    return get_all_giveaways(guild_id).get(name)

def save_giveaway(guild_id: int, name: str, data: dict):
    config = load_config()
    config.setdefault("giveaways", {}).setdefault(str(guild_id), {})[name] = data
    save_config(config)

def delete_giveaway(guild_id: int, name: str):
    config = load_config()
    config.get("giveaways", {}).get(str(guild_id), {}).pop(name, None)
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


async def fetch_guild_log(api_key: str, gw2_guild_id: str) -> list[dict]:
    """Fetch the guild log from the GW2 API. Returns a list of log entries."""
    headers = {"Authorization": f"Bearer {api_key}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://api.guildwars2.com/v2/guild/{gw2_guild_id}/log",
            headers=headers
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


def build_giveaway_text(name: str, entries: list[str], min_gold: int, created_at: str, closed: bool = False) -> str:
    """Build the Discord message content for a giveaway entry list."""
    status = "🔒 CLOSED" if closed else "🎉 Active"
    lines = [
        f"**{status} — {name}**",
        f"Minimum donation: **{min_gold} gold** to the guild bank",
        f"Started: {created_at}",
        f"",
        f"**Entries ({len(entries)})**",
    ]
    if entries:
        for i, gw2_name in enumerate(sorted(entries), 1):
            lines.append(f"{i}. {gw2_name}")
    else:
        lines.append("*No entries yet.*")
    if not closed:
        lines.append("")
        lines.append(f"-# Updates every 10 minutes. Donate at least {min_gold} gold to the guild bank to enter.")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Join-date and recently-left tracking helpers
# ---------------------------------------------------------------------------

RECENTLY_LEFT_TTL_HOURS = 24

def fmt_date(dt: datetime) -> str:
    """Format a datetime as '5 Aug 2026, 14:30 UTC'."""
    return dt.strftime("%-d %b %Y, %H:%M UTC")


def update_member_join_dates(cfg: dict, current_members: list) -> dict:
    """
    Compare current API members against stored join_dates.
    Any member who is now in the guild (rank != "invited") and doesn't yet
    have a join date gets stamped with the current UTC time.
    Members who are only "invited" are deliberately excluded.
    If a member in recently_left reappears as a full member, they are treated
    as a brand-new join: their recently_left entry is cleared and they receive
    a fresh join date.

    Returns the updated join_dates dict (also mutates cfg in-place).
    """
    join_dates: dict[str, str]     = cfg.setdefault("join_dates", {})
    recently_left: dict[str, dict] = cfg.setdefault("recently_left", {})
    now_str = fmt_date(datetime.now(timezone.utc))

    for m in current_members:
        name = m["name"]
        rank = m.get("rank", "")
        if rank.lower() == "invited":
            continue
        # Rejoining after leaving — fresh membership, remove from recently left
        if name in recently_left:
            recently_left.pop(name)
            join_dates[name] = now_str
        elif name not in join_dates:
            join_dates[name] = now_str

    return join_dates


def update_recently_left(cfg: dict, current_members: list) -> dict:
    """
    Detect members who were previously tracked (had a join date or were in
    recently_left) but are no longer present in the API response at all.
    Records their departure timestamp and prunes entries older than 24 hours.

    Returns the updated recently_left dict (also mutates cfg in-place).
    Stored format per entry:
        { "left_at": "2026-08-05 14:30 UTC", "joined_at": "2026-07-01 10:00 UTC" | null }
    """
    recently_left: dict[str, dict] = cfg.setdefault("recently_left", {})
    join_dates: dict[str, str]     = cfg.get("join_dates", {})
    now_utc = datetime.now(timezone.utc)
    now_str = fmt_date(now_utc)

    # Names currently visible in the API (any rank, including invited)
    current_names = {m["name"] for m in current_members}

    # Any name that has a join date but is no longer in the API has left/been kicked
    for name, joined_at in list(join_dates.items()):
        if name not in current_names and name not in recently_left:
            recently_left[name] = {
                "left_at":  now_str,
                "joined_at": joined_at,
            }

    # Prune entries older than 24 hours.
    # Dates are stored as "5 Aug 2026, 14:30 UTC" — parse with strptime.
    cutoff = now_utc.timestamp() - RECENTLY_LEFT_TTL_HOURS * 3600
    for name in list(recently_left.keys()):
        left_at_str = recently_left[name].get("left_at", "")
        try:
            left_dt = datetime.strptime(left_at_str, "%d %b %Y, %H:%M UTC").replace(tzinfo=timezone.utc)
            if left_dt.timestamp() < cutoff:
                recently_left.pop(name)
                join_dates.pop(name, None)
        except ValueError:
            pass  # Malformed entry — leave it for next cycle

    # Clean up join_dates entries for people in recently_left so we don't
    # double-detect them as new leavers on the next cycle
    for name in recently_left:
        join_dates.pop(name, None)

    return recently_left


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

def build_roster_text(
    guild_data: dict,
    members: list,
    verified: dict | None = None,
    join_dates: dict | None = None,
    recently_left: dict | None = None,
) -> tuple[list[str], str]:
    """
    Returns (discord_chunks, plain_text).
    discord_chunks is a list of strings each under 1900 chars, ready to post as separate messages.
    plain_text is the full untruncated roster for download.

    join_dates    — dict mapping gw2_name -> "D Mon YYYY, HH:MM UTC" join timestamp.
                    Members in the 'invited' rank are shown without a date.
    recently_left — dict mapping gw2_name -> {"left_at": str, "joined_at": str|None}.
                    Shown at the bottom of the roster for up to 24 hours.

    Rank display order: Guild Leader → Officer → Content Officer → Member → Invited → Recently Left.
    Any ranks not in the known order are appended alphabetically after the known ones.
    """
    guild_name     = guild_data.get("name", "Unknown Guild")
    tag            = guild_data.get("tag", "")
    now            = fmt_date(datetime.now(timezone.utc))
    verified_names = set(verified.keys()) if verified else set()
    join_dates     = join_dates or {}
    recently_left  = recently_left or {}

    # Rank display order — known ranks first, anything else sorted alphabetically after
    RANK_ORDER = ["Leader", "Officer", "Content Officer", "Founding Member", "Member", "Invited"]

    by_rank: dict[str, list] = {}
    for m in members:
        by_rank.setdefault(m["rank"], []).append(m["name"])
    for rank in by_rank:
        by_rank[rank].sort()

    def sorted_ranks(rank_dict: dict) -> list[str]:
        known   = [r for r in RANK_ORDER if r in rank_dict]
        unknown = sorted(r for r in rank_dict if r not in RANK_ORDER)
        return known + unknown

    # Count only full members (not invited)
    full_members = [m for m in members if m.get("rank", "").lower() != "invited"]
    invited      = [m for m in members if m.get("rank", "").lower() == "invited"]

    def plain_join(name: str, is_invited: bool) -> str:
        if is_invited:
            return ""
        date = join_dates.get(name)
        return f"\n    Joined: {date}" if date else "\n    Joined: Unknown"

    def discord_join(name: str, is_invited: bool) -> str:
        if is_invited:
            return ""
        date = join_dates.get(name)
        return f"\n    Joined: {date}" if date else "\n    Joined: Unknown"

    # ------------------------------------------------------------------ #
    # Plain text (full, no limit)                                         #
    # ------------------------------------------------------------------ #
    plain_lines = [
        f"[{tag}] {guild_name} — Guild Roster",
        f"Members: {len(full_members)} ({len(invited)} invited)",
        f"Updated: {now}",
        "",
    ]
    for rank in sorted_ranks(by_rank):
        names  = by_rank[rank]
        is_inv = rank.lower() == "invited"
        plain_lines.append(f"== {rank} ({len(names)}) ==")
        for name in names:
            verified_suffix = " (Verified)" if name in verified_names else ""
            join_suffix     = plain_join(name, is_inv)
            plain_lines.append(f"  {name}{verified_suffix}{join_suffix}")
        plain_lines.append("")

    if recently_left:
        plain_lines.append(f"== Recently Left ({len(recently_left)}) ==")
        for name, info in sorted(recently_left.items()):
            left_at   = info.get("left_at", "Unknown")
            joined_at = info.get("joined_at", "Unknown")
            plain_lines.append(f"  {name}")
            plain_lines.append(f"    Joined: {joined_at}")
            plain_lines.append(f"    Left:   {left_at}")
        plain_lines.append("")

    plain_text = "\n".join(plain_lines)

    # ------------------------------------------------------------------ #
    # Discord lines — split into chunks under 1900 chars                  #
    # ------------------------------------------------------------------ #
    all_lines = [
        f"**[{tag}] {guild_name} — Roster ({len(full_members)} members)**",
        f"-# Last updated: {now}\n"
    ]
    for rank in sorted_ranks(by_rank):
        names  = by_rank[rank]
        is_inv = rank.lower() == "invited"
        all_lines.append(f"**{rank}** ({len(names)})")
        for name in names:
            verified_suffix = " ✅" if name in verified_names else ""
            join_suffix     = discord_join(name, is_inv)
            all_lines.append(f"  • {name}{verified_suffix}{join_suffix}")
        all_lines.append("")

    if recently_left:
        all_lines.append(f"**Recently Left** ({len(recently_left)})")
        for name, info in sorted(recently_left.items()):
            left_at   = info.get("left_at", "Unknown")
            joined_at = info.get("joined_at", "Unknown")
            all_lines.append(f"  • ~~{name}~~")
            all_lines.append(f"    Joined: {joined_at}")
            all_lines.append(f"    Left: {left_at}")
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
            verified      = cfg.get("verified", {})
            join_dates    = cfg.get("join_dates", {})
            recently_left = cfg.get("recently_left", {})
            _chunks, plain_text = build_roster_text(
                guild_data, members, verified, join_dates, recently_left
            )
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

    # Update tracking data before building the roster text so the display
    # reflects the current state. Both helpers mutate cfg in-place and
    # persist their changes via set_guild_config at the end of this function.
    recently_left = update_recently_left(cfg, members)
    join_dates    = update_member_join_dates(cfg, members)

    chunks, _ = build_roster_text(guild_data, members, verified, join_dates, recently_left)

    # cfg["message_ids"] is the new field — list of message IDs in order.
    # cfg["message_id"] (old single-message field) is migrated on first run.
    existing_ids: list[int] = cfg.get("message_ids", [])
    if not existing_ids and cfg.get("message_id"):
        existing_ids = [cfg["message_id"]]

    # Last-sent content per chunk — used to skip edits when nothing changed.
    # Chunk 0 (contains the "Last updated" timestamp) is always edited.
    # All other chunks are only edited if their content has changed.
    last_contents: list[str] = cfg.get("last_chunk_contents", [])

    needed     = len(chunks)
    have       = len(existing_ids)
    final_view = RosterView()
    new_ids: list[int] = []
    new_contents: list[str] = []
    first_edit = True  # used to throttle — only sleep before 2nd+ actual edit

    for i, chunk in enumerate(chunks):
        view = final_view if i == needed - 1 else None

        if i < have:
            # Chunk 0 always gets edited (timestamp). Chunks 1+ skipped if unchanged.
            if i > 0 and i < len(last_contents) and chunk == last_contents[i]:
                new_ids.append(existing_ids[i])
                new_contents.append(chunk)
                continue

            if not first_edit:
                await asyncio.sleep(2)
            first_edit = False

            try:
                msg = await channel.fetch_message(existing_ids[i])
                await msg.edit(content=chunk, view=view)
                new_ids.append(msg.id)
                new_contents.append(chunk)
                logging.info(f"Edited roster message {msg.id} (chunk {i+1}/{needed}) in #{channel.name}")
            except discord.NotFound:
                if not first_edit:
                    await asyncio.sleep(2)
                msg = await channel.send(content=chunk, view=view)
                new_ids.append(msg.id)
                new_contents.append(chunk)
                logging.info(f"Re-posted roster message {msg.id} (chunk {i+1}/{needed}) in #{channel.name}")
        else:
            if not first_edit:
                await asyncio.sleep(2)
            first_edit = False
            msg = await channel.send(content=chunk, view=view)
            new_ids.append(msg.id)
            new_contents.append(chunk)
            logging.info(f"Posted new roster message {msg.id} (chunk {i+1}/{needed}) in #{channel.name}")

    # Delete any leftover messages if roster shrank
    for old_id in existing_ids[needed:]:
        try:
            old_msg = await channel.fetch_message(old_id)
            await old_msg.delete()
            logging.info(f"Deleted surplus roster message {old_id} in #{channel.name}")
        except discord.NotFound:
            pass

    # Save updated message IDs and last-sent content
    cfg["message_ids"]         = new_ids
    cfg["last_chunk_contents"] = new_contents
    cfg.pop("message_id", None)  # remove old single-id field
    set_guild_config(guild_id, cfg)
    return members

# ---------------------------------------------------------------------------
# Sync verifications
# ---------------------------------------------------------------------------

async def sync_verifications(discord_guild: discord.Guild, cfg: dict, members: list):
    """
    Called on every roster update cycle.
    1. Unlinks GW2 accounts that have left the guild and updates their nickname.
    2. For every verified Discord member still in the guild, ensures they hold
       the correct rank role (and only that rank role).
    """
    current_gw2_names = {m["name"] for m in members}
    verified          = cfg.get("verified", {})
    guest_role_name   = get_guest_role_name(cfg)

    # ── Step 1: unlink accounts that left the guild ──────────────────────
    to_unlink = [gw2_name for gw2_name in verified if gw2_name not in current_gw2_names]

    for gw2_name in to_unlink:
        discord_user_id = verified[gw2_name]
        logging.info(f"{gw2_name} left the GW2 guild — unlinking Discord user {discord_user_id}")

        remaining = find_gw2_names_for_discord_user(discord_guild.id, discord_user_id)
        remaining = [n for n in remaining if n != gw2_name]

        try:
            member_obj = await discord_guild.fetch_member(discord_user_id)
            nick = build_nickname(remaining)
            await member_obj.edit(nick=nick, reason="GW2 account left guild — nickname updated")

            # Recalculate rank from remaining accounts; remove all rank roles if none left
            new_rank = highest_rank(remaining, members) if remaining else None
            await apply_rank_role(discord_guild, member_obj, new_rank, guest_role_name)

        except discord.NotFound:
            pass  # Member left the Discord server
        except discord.Forbidden:
            logging.warning(f"Missing permissions to update member {discord_user_id}")
        except Exception as e:
            logging.warning(f"Failed to update member {discord_user_id} after GW2 unlink: {e}")

        unlink_account(discord_guild.id, gw2_name)

    if to_unlink:
        logging.info(f"Unlinked {len(to_unlink)} accounts that left the GW2 guild: {to_unlink}")

    # ── Step 2: sync rank roles for all currently verified members ───────
    # Build a map of discord_user_id -> [gw2_names] for everyone still verified
    verified_now = cfg.get("verified", {})  # reload after unlinking
    discord_to_gw2: dict[int, list[str]] = {}
    for gw2_name, discord_id in verified_now.items():
        discord_to_gw2.setdefault(discord_id, []).append(gw2_name)

    for discord_user_id, gw2_names in discord_to_gw2.items():
        try:
            member_obj = await discord_guild.fetch_member(discord_user_id)
        except (discord.NotFound, discord.HTTPException):
            continue

        rank = highest_rank(gw2_names, members)
        await apply_rank_role(discord_guild, member_obj, rank, guest_role_name)

# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # needed to detect non-command messages
bot  = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


def migrate_date_formats():
    """
    One-time migration: reformat any join_dates / recently_left timestamps
    still stored in the old '%Y-%m-%d %H:%M UTC' style (e.g. '2026-08-05 01:00 UTC')
    to the current '%-d %b %Y, %H:%M UTC' style (e.g. '5 Aug 2026, 01:00 UTC').
    Safe to run on every startup — already-migrated dates are left untouched.
    """
    OLD_FMT = "%Y-%m-%d %H:%M UTC"
    config  = load_config()
    changed = False

    for guild_id_str, cfg in config.items():
        if not guild_id_str.isdigit() or not isinstance(cfg, dict):
            continue

        for name, date_str in list(cfg.get("join_dates", {}).items()):
            try:
                dt = datetime.strptime(date_str, OLD_FMT).replace(tzinfo=timezone.utc)
                cfg["join_dates"][name] = fmt_date(dt)
                changed = True
            except ValueError:
                pass  # Already in new format or unrecognised — leave it

        for name, info in list(cfg.get("recently_left", {}).items()):
            if not isinstance(info, dict):
                continue
            for key in ("left_at", "joined_at"):
                val = info.get(key)
                if not val:
                    continue
                try:
                    dt = datetime.strptime(val, OLD_FMT).replace(tzinfo=timezone.utc)
                    info[key] = fmt_date(dt)
                    changed = True
                except ValueError:
                    pass

    if changed:
        save_config(config)
        logging.info("Migrated stored dates from old format to new format.")


@bot.event
async def on_ready():
    migrate_date_formats()
    bot.add_view(RosterView())
    bot.add_view(ReportView())
    await tree.sync()
    logging.info(f"Logged in as {bot.user} ({bot.user.id})")
    if not roster_loop.is_running():
        roster_loop.start()
    # Build item cache in background — bot is usable immediately
    asyncio.create_task(build_item_cache())
    # Clean up verified accounts for members who left while the bot was offline
    asyncio.create_task(_cleanup_departed_members())


async def _cleanup_departed_members():
    """
    On startup, unlink any verified GW2 accounts whose Discord user is no
    longer a member of the server.  Catches anyone who left while the bot
    was offline (including the member who triggered this fix).
    """
    config = load_config()
    for guild_id_str, cfg in config.items():
        if not guild_id_str.isdigit() or not isinstance(cfg, dict):
            continue
        verified = cfg.get("verified", {})
        if not verified:
            continue

        discord_guild = bot.get_guild(int(guild_id_str))
        if not discord_guild:
            continue

        # Fetch the full member list so we have accurate data
        try:
            member_ids = {m.id for m in discord_guild.members}
        except Exception as e:
            logging.warning(f"Could not fetch members for guild {guild_id_str} during cleanup: {e}")
            continue

        to_unlink = [
            gw2_name for gw2_name, discord_id in verified.items()
            if discord_id not in member_ids
        ]

        for gw2_name in to_unlink:
            unlink_account(int(guild_id_str), gw2_name)

        if to_unlink:
            logging.info(
                f"Startup cleanup — unlinked {len(to_unlink)} GW2 account(s) "
                f"for departed Discord members in guild {guild_id_str}: {to_unlink}"
            )


@bot.event
async def on_member_remove(member: discord.Member):
    """Unlink all GW2 accounts belonging to a Discord member who left the server."""
    if member.bot:
        return

    guild_id  = member.guild.id
    gw2_names = find_gw2_names_for_discord_user(guild_id, member.id)
    if not gw2_names:
        return

    for gw2_name in gw2_names:
        unlink_account(guild_id, gw2_name)

    logging.info(
        f"Discord member {member.id} left guild {guild_id} — "
        f"unlinked GW2 account(s): {gw2_names}"
    )


@bot.event
async def on_message(message: discord.Message):
    # Ignore bots and DMs
    if message.author.bot or not message.guild:
        return

    autodelete_channels = get_autodelete_channels(message.guild.id)
    if message.channel.id in autodelete_channels:
        asyncio.create_task(_autodelete(message))


async def _autodelete(message: discord.Message):
    """Delete a message with up to 3 retries on transient HTTP errors."""
    for attempt in range(3):
        try:
            await message.delete()
            logging.info(
                f"Auto-deleted message {message.id} from {message.author} "
                f"in #{message.channel.name} ({message.guild.name})"
            )
            return
        except discord.NotFound:
            return  # Already gone — fine
        except discord.Forbidden:
            logging.warning(
                f"Missing Manage Messages permission in #{message.channel.name} "
                f"({message.guild.name})"
            )
            return  # Retrying won't help
        except discord.HTTPException as e:
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
            else:
                logging.warning(
                    f"Auto-delete failed after 3 attempts for message "
                    f"{message.id} in #{message.channel.name}: {e}"
                )

# ---------------------------------------------------------------------------
# /setup
# ---------------------------------------------------------------------------

@tree.command(name="setup", description="[Admin] Configure the GW2 roster bot.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    api_key      = "Your GW2 API key (guilds permission, must be guild leader)",
    gw2_guild_id = "Your GW2 Guild ID (UUID)",
    channel      = "Channel to post the daily roster in",
    guest_role   = "Role to remove when a member verifies (default: role named 'Guest')",
)
async def setup(
    interaction: discord.Interaction,
    api_key: str,
    gw2_guild_id: str,
    channel: discord.TextChannel,
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

    existing_cfg           = get_guild_config(interaction.guild_id)
    existing_verified      = existing_cfg.get("verified", {}) if existing_cfg else {}
    existing_join_dates    = existing_cfg.get("join_dates", {}) if existing_cfg else {}
    existing_recently_left = existing_cfg.get("recently_left", {}) if existing_cfg else {}
    existing_guest_name    = existing_cfg.get("guest_role_name", "Guest") if existing_cfg else "Guest"

    guest_role_name = guest_role.name if guest_role else existing_guest_name

    resolved_guest_role = guest_role or discord.utils.get(interaction.guild.roles, name=guest_role_name)
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
        "guest_role_name":  guest_role_name,
        "join_dates":       existing_join_dates,
        "recently_left":    existing_recently_left,
    }
    set_guild_config(interaction.guild_id, cfg)

    try:
        await post_or_edit_roster(channel, cfg, interaction.guild_id)
    except Exception as e:
        await interaction.followup.send(f"✅ Config saved, but failed to post initial roster: {e}", ephemeral=True)
        return

    gw2_name = guild_data.get("name", gw2_guild_id)
    rank_list = ", ".join(GUILD_RANKS)
    await interaction.followup.send(
        f"✅ Done! **{gw2_name}** roster ({len(members)} members) posted in {channel.mention}.\n"
        f"Updates every 10 minutes. Members can use `/verify` to link their GW2 account.\n"
        f"On verification, the **{guest_role_name}** role is removed and the member's guild rank role is assigned automatically.\n"
        f"Managed rank roles: {rank_list}.\n"
        f"Use `/unsetup` to remove this configuration.",
        ephemeral=True
    )

# ---------------------------------------------------------------------------
# /verify
# ---------------------------------------------------------------------------

@tree.command(name="verify", description="Verify your GW2 account to receive your guild rank role.")
@app_commands.describe(gw2_username="Your full GW2 account name, including the numbers (e.g. PlayerName.1234)")
async def verify(interaction: discord.Interaction, gw2_username: str):
    await interaction.response.defer(ephemeral=True)

    cfg = get_guild_config(interaction.guild_id)
    if not cfg:
        await interaction.followup.send("❌ This bot has not been set up yet. An admin needs to run `/setup` first.", ephemeral=True)
        return

    discord_guild   = interaction.guild
    guest_role_name = get_guest_role_name(cfg)

    verified     = get_verified(interaction.guild_id)
    gw2_username = gw2_username.strip()

    if gw2_username in verified:
        if verified[gw2_username] == interaction.user.id:
            await interaction.followup.send(f"❌ **{gw2_username}** is already linked to your account.", ephemeral=True)
        else:
            await interaction.followup.send("❌ That GW2 account is already linked to another Discord user.", ephemeral=True)
        return

    try:
        _, members = await fetch_roster(cfg["api_key"], cfg["gw2_guild_id"])
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch the guild roster: {e}", ephemeral=True)
        return

    member_data = {m["name"]: m for m in members}
    if gw2_username not in member_data or member_data[gw2_username].get("rank", "").lower() == "invited":
        await interaction.followup.send(
            f"❌ **{gw2_username}** is not a member of the guild. "
            "Check your full account name including the numbers (e.g. `PlayerName.1234`).",
            ephemeral=True
        )
        return

    # Link the account first so highest_rank can see all accounts for this user
    link_account(interaction.guild_id, gw2_username, interaction.user.id)

    all_gw2_names = find_gw2_names_for_discord_user(interaction.guild_id, interaction.user.id)
    rank = highest_rank(all_gw2_names, members)

    try:
        await apply_rank_role(discord_guild, interaction.user, rank, guest_role_name)
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I don't have permission to manage roles. Make sure my role is above the guild rank roles.",
            ephemeral=True
        )
        return
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to assign roles: {e}", ephemeral=True)
        return

    # Set nickname to all linked accounts
    nick = build_nickname(all_gw2_names)
    try:
        await interaction.user.edit(nick=nick, reason="GW2 verification — nickname updated")
    except discord.Forbidden:
        logging.warning(f"Could not set nickname for {interaction.user.id} — missing permissions")
    except Exception as e:
        logging.warning(f"Nickname set failed for {interaction.user.id}: {e}")

    logging.info(f"Verified {gw2_username} as Discord user {interaction.user.id} with rank {rank}")

    await interaction.followup.send(
        f"✅ Verified! Welcome, **{gw2_username}**. You've been given the **{rank}** role "
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

    discord_guild   = interaction.guild
    guest_role_name = get_guest_role_name(cfg)

    verified     = get_verified(interaction.guild_id)
    gw2_username = gw2_username.strip()

    if gw2_username in verified:
        if verified[gw2_username] == member.id:
            await interaction.followup.send(f"❌ **{gw2_username}** is already linked to {member.mention}.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ **{gw2_username}** is already linked to another Discord user.", ephemeral=True)
        return

    try:
        _, members = await fetch_roster(cfg["api_key"], cfg["gw2_guild_id"])
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch the guild roster: {e}", ephemeral=True)
        return

    member_data = {m["name"]: m for m in members}
    if gw2_username not in member_data or member_data[gw2_username].get("rank", "").lower() == "invited":
        await interaction.followup.send(
            f"❌ **{gw2_username}** is not a member of the guild. "
            "Check the account name including the numbers (e.g. `PlayerName.1234`).",
            ephemeral=True
        )
        return

    # Link first so highest_rank sees all accounts for this user
    link_account(interaction.guild_id, gw2_username, member.id)

    all_gw2_names = find_gw2_names_for_discord_user(interaction.guild_id, member.id)
    rank = highest_rank(all_gw2_names, members)

    try:
        await apply_rank_role(discord_guild, member, rank, guest_role_name)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to manage roles.", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to assign roles: {e}", ephemeral=True)
        return

    # Set nickname
    nick = build_nickname(all_gw2_names)
    try:
        await member.edit(nick=nick, reason=f"Force-verified by admin {interaction.user}")
    except discord.Forbidden:
        logging.warning(f"Could not set nickname for {member.id} — missing permissions")
    except Exception as e:
        logging.warning(f"Nickname set failed for {member.id}: {e}")

    logging.info(f"Admin {interaction.user.id} force-verified {gw2_username} as Discord user {member.id} with rank {rank}")

    await interaction.followup.send(
        f"✅ {member.mention} has been verified as **{gw2_username}** and given the **{rank}** role.",
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
    guest_role_name = get_guest_role_name(item_cfg) if item_cfg else "Guest"
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
        f"**Report system configuration:**\n"
        f"• Button posted in: {button_channel.mention if button_channel else 'Unknown channel'}\n"
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

@tree.command(name="unsetup", description="[Admin] Remove the GW2 roster bot configuration for this server.")
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
# /viewsetup
# ---------------------------------------------------------------------------

@tree.command(name="viewsetup", description="[Admin] View a full summary of the bot's configuration for this server.")
@app_commands.default_permissions(administrator=True)
async def viewsetup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    guild_id = interaction.guild_id
    cfg      = get_guild_config(guild_id)
    rcfg     = get_report_config(guild_id)
    ad_ids   = get_autodelete_channels(guild_id)

    embed = discord.Embed(
        title="⚙️ Bot Configuration",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )

    # ── Roster / GW2 setup ──────────────────────────────────────────────
    if cfg:
        setup_by_id = cfg.get("setup_by")
        if setup_by_id:
            try:
                setup_user   = await interaction.guild.fetch_member(setup_by_id)
                setup_by_str = setup_user.mention
            except (discord.NotFound, discord.HTTPException):
                setup_by_str = f"Unknown user ({setup_by_id})"
        else:
            setup_by_str = "Unknown"

        roster_channel   = interaction.guild.get_channel(cfg.get("channel_id", 0))
        guest_role_name  = get_guest_role_name(cfg)
        guest_role       = discord.utils.get(interaction.guild.roles, name=guest_role_name)

        # Check which rank roles currently exist on the server
        existing_rank_roles = [
            discord.utils.get(interaction.guild.roles, name=r) for r in GUILD_RANKS
        ]
        rank_role_lines = []
        for r_name, r_obj in zip(GUILD_RANKS, existing_rank_roles):
            rank_role_lines.append(f"  {r_obj.mention if r_obj else f'⚠️ `{r_name}` (not yet created)'}")

        # ── Discord server stats (no API call needed) ────────────────────
        discord_members     = [m for m in interaction.guild.members if not m.bot]
        discord_total       = len(discord_members)
        verified_discord_ids = set(cfg.get("verified", {}).values())
        verified_in_discord = sum(1 for m in discord_members if m.id in verified_discord_ids)
        discord_verified_pct = (
            f"{verified_in_discord / discord_total * 100:.0f}%" if discord_total else "N/A"
        )

        # ── Live roster stats from the GW2 API ──────────────────────────
        try:
            _, live_members = await fetch_roster(cfg["api_key"], cfg["gw2_guild_id"])

            full_members   = [m for m in live_members if m.get("rank", "").lower() != "invited"]
            invited_members = [m for m in live_members if m.get("rank", "").lower() == "invited"]
            full_names     = {m["name"] for m in full_members}

            verified       = cfg.get("verified", {})
            verified_count = len(verified)
            # How many verified GW2 accounts belong to someone who is still in the guild
            verified_in_guild = sum(1 for gw2_name in verified if gw2_name in full_names)
            unverified_count  = len(full_members) - verified_in_guild
            verification_pct  = (
                f"{verified_in_guild / len(full_members) * 100:.0f}%" if full_members else "N/A"
            )

            # Counts by rank
            rank_counts: dict[str, int] = {}
            for m in full_members:
                rank_counts[m["rank"]] = rank_counts.get(m["rank"], 0) + 1

            RANK_ORDER_DISPLAY = [
                "Leader", "Officer", "Content Officer",
                "Founding Member", "Member",
            ]
            rank_lines = []
            for rank in RANK_ORDER_DISPLAY:
                if rank in rank_counts:
                    rank_lines.append(f"  {rank}: **{rank_counts[rank]}**")
            for rank, count in sorted(rank_counts.items()):
                if rank not in RANK_ORDER_DISPLAY:
                    rank_lines.append(f"  {rank}: **{count}**")

            recently_left     = cfg.get("recently_left", {})
            recently_left_count = len(recently_left)
            recently_left_names = (
                ", ".join(sorted(recently_left.keys())) if recently_left else "None"
            )
            # Truncate if the list would be too long for an embed field
            if len(recently_left_names) > 900:
                recently_left_names = recently_left_names[:897] + "..."

            next_run = roster_loop.next_iteration
            next_run_str = (
                discord.utils.format_dt(next_run, style="R")
                if next_run else "Unknown"
            )

            embed.add_field(
                name="📋 Roster",
                value=(
                    f"**Set up by:** {setup_by_str}\n"
                    f"**Roster channel:** {roster_channel.mention if roster_channel else '⚠️ Channel not found'}\n"
                    f"**GW2 Guild ID:** `{cfg.get('gw2_guild_id', 'Unknown')}`\n"
                    f"**API key:** `{cfg.get('api_key', 'Unknown')}`\n"
                    f"**Next auto-update:** {next_run_str}"
                ),
                inline=False,
            )

            embed.add_field(
                name=f"👥 Guild Members ({len(full_members)})",
                value="\n".join(rank_lines) if rank_lines else "No members found",
                inline=True,
            )

            embed.add_field(
                name=f"📩 Invited ({len(invited_members)})",
                value=(
                    f"Pending acceptance\n"
                    f"*(not counted in member total)*"
                ),
                inline=True,
            )

            embed.add_field(name="\u200b", value="\u200b", inline=True)  # spacer

            embed.add_field(
                name="🔗 Verification",
                value=(
                    f"**Removes on verify:** {guest_role.mention if guest_role else f'⚠️ `{guest_role_name}` not found'}\n"
                    f"**Assigns rank role:** matching guild rank (auto-created if missing)\n"
                    f"**Managed rank roles:**\n" + "\n".join(rank_role_lines) + "\n"
                    f"**Discord members (excl. bots):** {discord_total}\n"
                    f"**Verified in Discord:** {verified_in_discord} / {discord_total} ({discord_verified_pct})\n"
                    f"**Verified (in guild):** {verified_in_guild} / {len(full_members)} ({verification_pct})\n"
                    f"**Unverified guild members:** {unverified_count}\n"
                    f"**Total linked accounts:** {verified_count}"
                ),
                inline=False,
            )

            embed.add_field(
                name=f"🚪 Recently Left ({recently_left_count})",
                value=recently_left_names,
                inline=False,
            )

        except Exception as e:
            # API fetch failed — fall back to config-only data
            verified_count = len(cfg.get("verified", {}))
            recently_left_count = len(cfg.get("recently_left", {}))

            embed.add_field(
                name="📋 Roster",
                value=(
                    f"**Set up by:** {setup_by_str}\n"
                    f"**Roster channel:** {roster_channel.mention if roster_channel else '⚠️ Channel not found'}\n"
                    f"**GW2 Guild ID:** `{cfg.get('gw2_guild_id', 'Unknown')}`\n"
                    f"**API key:** `{cfg.get('api_key', 'Unknown')}`\n"
                    f"⚠️ Could not fetch live roster data: {e}"
                ),
                inline=False,
            )
            embed.add_field(
                name="🔗 Verification",
                value=(
                    f"**Removes on verify:** {guest_role.mention if guest_role else f'⚠️ `{guest_role_name}` not found'}\n"
                    f"**Assigns rank role:** matching guild rank (auto-created if missing)\n"
                    f"**Discord members (excl. bots):** {discord_total}\n"
                    f"**Verified in Discord:** {verified_in_discord} / {discord_total} ({discord_verified_pct})\n"
                    f"**Linked accounts (cached):** {verified_count}\n"
                    f"**Recently left (tracked):** {recently_left_count}"
                ),
                inline=False,
            )
    else:
        embed.add_field(
            name="📋 Roster",
            value="Not configured. Run `/setup` to get started.",
            inline=False,
        )

    # ── Report system ────────────────────────────────────────────────────
    if rcfg:
        button_channel = interaction.guild.get_channel(rcfg.get("button_channel_id", 0))
        report_channel = interaction.guild.get_channel(rcfg.get("report_channel_id", 0))
        embed.add_field(
            name="📨 Report System",
            value=(
                f"**Button channel:** {button_channel.mention if button_channel else '⚠️ Channel not found'}\n"
                f"**Reports sent to:** {report_channel.mention if report_channel else '⚠️ Channel not found'}"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="📨 Report System",
            value="Not configured. Run `/setupreport` to enable.",
            inline=False,
        )

    # ── Auto-delete channels ─────────────────────────────────────────────
    if ad_ids:
        lines = []
        for cid in ad_ids:
            ch = interaction.guild.get_channel(cid)
            lines.append(ch.mention if ch else f"⚠️ Unknown channel ({cid})")
        embed.add_field(
            name=f"🗑️ Auto-delete Channels ({len(ad_ids)})",
            value="\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="🗑️ Auto-delete Channels",
            value="None configured. Run `/setupautodelete` to add one.",
            inline=False,
        )

    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed, ephemeral=True)
    logging.info(f"Admin {interaction.user.id} viewed setup in guild {guild_id}")


# ---------------------------------------------------------------------------
# Giveaway update
# ---------------------------------------------------------------------------

async def update_giveaway(discord_guild_id: int, name: str, giveaway: dict):
    """
    Fetch the guild log, find new qualifying donations for this giveaway,
    update the entry list, and edit the Discord message.
    """
    cfg = get_guild_config(discord_guild_id)
    if not cfg:
        return

    api_key      = cfg.get("api_key")
    gw2_guild_id = cfg.get("gw2_guild_id")
    if not api_key or not gw2_guild_id:
        return

    channel = bot.get_channel(giveaway["channel_id"])
    if not channel:
        return

    # created_at is stored as ISO 8601 internally for comparison
    created_dt = datetime.fromisoformat(giveaway["created_at"])
    min_copper = giveaway["min_gold"] * 10_000
    entries    = set(giveaway.get("entries", []))

    try:
        log_entries = await fetch_guild_log(api_key, gw2_guild_id)
    except Exception as e:
        logging.warning(f"Failed to fetch guild log for giveaway '{name}': {e}")
        return

    for entry in log_entries:
        # Only guild stash entries
        if entry.get("type") != "stash":
            continue
        # Withdrawals have negative coins or operation == "withdraw"; skip them.
        # Some API responses omit "operation" entirely for deposits, so we check
        # the operation only if it's present.
        op = entry.get("operation")
        if op is not None and op != "deposit":
            continue
        # Item donations have a non-zero item_id — coin deposits use item_id: 0
        if entry.get("item_id", 0) != 0:
            continue
        coins = entry.get("coins", 0)
        if not isinstance(coins, int) or coins <= 0:
            continue

        # Only count entries after the giveaway was created
        ts_str = entry.get("time", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            logging.warning(f"Giveaway '{name}' — could not parse timestamp: {ts_str!r}")
            continue
        if ts <= created_dt:
            continue

        # Check minimum donation threshold
        if coins < min_copper:
            continue

        gw2_name = entry.get("user", "")
        if gw2_name and gw2_name not in entries:
            logging.info(f"Giveaway '{name}' — adding entry: {gw2_name} ({coins} copper)")
            entries.add(gw2_name)

    # Persist updated entries
    giveaway["entries"] = sorted(entries)
    save_giveaway(discord_guild_id, name, giveaway)

    # Edit the Discord message — display_created_at is a human-readable version
    display_created_at = fmt_date(created_dt)
    content = build_giveaway_text(
        name, giveaway["entries"], giveaway["min_gold"],
        display_created_at, closed=giveaway.get("closed", False)
    )
    msg_id = giveaway.get("message_id")
    if msg_id:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(content=content)
        except discord.NotFound:
            msg = await channel.send(content)
            giveaway["message_id"] = msg.id
            save_giveaway(discord_guild_id, name, giveaway)
        except Exception as e:
            logging.warning(f"Failed to edit giveaway message for '{name}': {e}")
    else:
        msg = await channel.send(content)
        giveaway["message_id"] = msg.id
        save_giveaway(discord_guild_id, name, giveaway)


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
        except aiohttp.ClientResponseError as e:
            if e.status == 403:
                logging.error(
                    f"GW2 API 403 Forbidden for guild {guild_id} — API key is invalid, "
                    f"missing 'guilds' scope, or account is no longer guild leader. "
                    f"Run /setup again with a valid API key."
                )
                try:
                    await channel.send(
                        "⚠️ **Roster update failed** — the GW2 API key is no longer valid (403 Forbidden).\n"
                        "This usually means the key was regenerated, the account is no longer guild leader, "
                        "or the key is missing the `guilds` permission.\n"
                        "An admin needs to run `/setup` again with a valid API key.",
                    )
                except Exception:
                    pass
            else:
                logging.error(f"GW2 API error for guild {guild_id}: HTTP {e.status} — {e}")
        except Exception as e:
            logging.error(f"Failed to update roster for guild {guild_id}: {e}")

    # Update all active giveaways across all guilds
    full_config = load_config()
    for guild_id_str, guild_giveaways in full_config.get("giveaways", {}).items():
        if not guild_id_str.isdigit():
            continue
        for giveaway_name, giveaway in guild_giveaways.items():
            if giveaway.get("closed"):
                continue
            try:
                await update_giveaway(int(guild_id_str), giveaway_name, giveaway)
            except Exception as e:
                logging.error(f"Failed to update giveaway '{giveaway_name}' in guild {guild_id_str}: {e}")


# ---------------------------------------------------------------------------
# /setupgiveaway  /announcewinner  /removegiveaway
# ---------------------------------------------------------------------------

@tree.command(name="setupgiveaway", description="[Admin] Create a new giveaway tracked by guild bank donations.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel  = "Channel to post the giveaway entry list in",
    name     = "Unique name for this giveaway (e.g. 'summer2026')",
    min_gold = "Minimum whole gold donation required to enter (e.g. 1, 5, 10)",
)
async def setupgiveaway(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    name: str,
    min_gold: int,
):
    await interaction.response.defer(ephemeral=True)

    if min_gold < 1:
        await interaction.followup.send("❌ Minimum gold must be at least 1.", ephemeral=True)
        return

    cfg = get_guild_config(interaction.guild_id)
    if not cfg:
        await interaction.followup.send("❌ Bot is not set up yet. Run `/setup` first.", ephemeral=True)
        return

    existing = get_giveaway(interaction.guild_id, name)
    if existing:
        await interaction.followup.send(
            f"❌ A giveaway named **{name}** already exists. Choose a unique name or remove the existing one first.",
            ephemeral=True
        )
        return

    created_at = datetime.now(timezone.utc).isoformat()
    giveaway = {
        "channel_id": channel.id,
        "message_id": None,
        "min_gold":   min_gold,
        "created_at": created_at,
        "entries":    [],
        "closed":     False,
    }
    save_giveaway(interaction.guild_id, name, giveaway)

    # Post the initial message immediately
    content = build_giveaway_text(name, [], min_gold, fmt_date(datetime.now(timezone.utc)))
    msg = await channel.send(content)
    giveaway["message_id"] = msg.id
    save_giveaway(interaction.guild_id, name, giveaway)

    await interaction.followup.send(
        f"✅ Giveaway **{name}** created in {channel.mention}!\n"
        f"Members who donate at least **{min_gold} gold** to the guild bank after "
        f"{fmt_date(datetime.now(timezone.utc))} will be automatically added to the entry list.\n"
        f"The list updates every 10 minutes.",
        ephemeral=True
    )
    logging.info(f"Admin {interaction.user.id} created giveaway '{name}' in guild {interaction.guild_id}")


@tree.command(name="announcewinner", description="[Admin] Close a giveaway and announce the winners.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    name       = "The unique name of the giveaway to close",
    num_winners = "How many winners to draw",
)
async def announcewinner(
    interaction: discord.Interaction,
    name: str,
    num_winners: int,
):
    await interaction.response.defer(ephemeral=True)

    if num_winners < 1:
        await interaction.followup.send("❌ Number of winners must be at least 1.", ephemeral=True)
        return

    giveaway = get_giveaway(interaction.guild_id, name)
    if not giveaway:
        await interaction.followup.send(f"❌ No giveaway named **{name}** found.", ephemeral=True)
        return

    if giveaway.get("closed"):
        await interaction.followup.send(f"❌ Giveaway **{name}** is already closed.", ephemeral=True)
        return

    channel = bot.get_channel(giveaway["channel_id"])
    if not channel:
        await interaction.followup.send("❌ The giveaway channel no longer exists.", ephemeral=True)
        return

    # Do one final entry update before closing
    cfg = get_guild_config(interaction.guild_id)
    if cfg:
        try:
            await update_giveaway(interaction.guild_id, name, giveaway)
            # Reload after update
            giveaway = get_giveaway(interaction.guild_id, name)
        except Exception as e:
            logging.warning(f"Final entry update failed before announcing winners for '{name}': {e}")

    entries = giveaway.get("entries", [])

    # Draw winners
    import random
    pool         = list(entries)
    actual_count = min(num_winners, len(pool))
    winners      = random.sample(pool, actual_count) if pool else []

    # Mark closed and edit the entry list message
    giveaway["closed"] = True
    save_giveaway(interaction.guild_id, name, giveaway)

    closed_content = build_giveaway_text(
        name, entries, giveaway["min_gold"], giveaway["created_at"], closed=True
    )
    msg_id = giveaway.get("message_id")
    if msg_id:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(content=closed_content)
        except (discord.NotFound, Exception):
            pass

    # Build winner announcement
    verified = get_verified(interaction.guild_id)
    # Reverse map: gw2_name -> discord_user_id
    gw2_to_discord = {gw2: uid for gw2, uid in verified.items()}

    if not winners:
        announcement = (
            f"🎉 **{name} — Giveaway Closed**\n\n"
            f"No winners could be drawn — there were no eligible entries."
        )
    else:
        winner_lines = []
        for gw2_name in winners:
            discord_uid = gw2_to_discord.get(gw2_name)
            if discord_uid:
                try:
                    discord_member = await interaction.guild.fetch_member(discord_uid)
                    discord_str    = f"{discord_member.mention} ({discord_member.display_name})"
                except (discord.NotFound, discord.HTTPException):
                    discord_str = "*Discord account not found*"
            else:
                discord_str = "*Not verified*"
            winner_lines.append(f"🏆 **{gw2_name}** — {discord_str}")

        drawn_str   = f"{actual_count} winner{'s' if actual_count != 1 else ''}"
        entries_str = f"{len(entries)} entr{'ies' if len(entries) != 1 else 'y'}"
        announcement = (
            f"🎉 **{name} — Giveaway Winners!**\n"
            f"Drawn from {entries_str}:\n\n"
            + "\n".join(winner_lines)
        )

        if actual_count < num_winners:
            announcement += f"\n\n*Note: Only {actual_count} winner(s) drawn — not enough entries for {num_winners}.*"

    await channel.send(announcement)
    await interaction.followup.send(
        f"✅ Giveaway **{name}** closed. {len(winners)} winner(s) announced in {channel.mention}.",
        ephemeral=True
    )
    logging.info(
        f"Admin {interaction.user.id} announced {len(winners)} winner(s) "
        f"for giveaway '{name}' in guild {interaction.guild_id}: {winners}"
    )


@tree.command(name="removegiveaway", description="[Admin] Delete a giveaway and remove its message.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(name="The unique name of the giveaway to delete")
async def removegiveaway(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)

    giveaway = get_giveaway(interaction.guild_id, name)
    if not giveaway:
        await interaction.followup.send(f"❌ No giveaway named **{name}** found.", ephemeral=True)
        return

    # Delete the Discord message
    channel = bot.get_channel(giveaway["channel_id"])
    if channel and giveaway.get("message_id"):
        try:
            msg = await channel.fetch_message(giveaway["message_id"])
            await msg.delete()
        except (discord.NotFound, discord.Forbidden, Exception):
            pass

    delete_giveaway(interaction.guild_id, name)

    await interaction.followup.send(
        f"✅ Giveaway **{name}** has been deleted.",
        ephemeral=True
    )
    logging.info(f"Admin {interaction.user.id} deleted giveaway '{name}' in guild {interaction.guild_id}")


bot.run(DISCORD_TOKEN)