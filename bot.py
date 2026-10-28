import discord
from discord import app_commands
from discord.ext import tasks
import aiohttp
import asyncio
import json
import os
import io
import logging
import secrets
from datetime import datetime, timedelta, timezone, time as dt_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logging.basicConfig(level=logging.INFO)

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "config.json")
# Update roster every 10 minutes (xx:00, xx:10, xx:20, xx:30, xx:40, xx:50)
ROSTER_TIMES  = [dt_time(hour=h, minute=m) for h in range(24) for m in range(0, 60, 10)]

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
# Leadership role management
# Any Discord member holding either the "Officer" or "Leader" rank role also
# gets given a separate "Leadership" role. This is an additional tag role —
# members keep their normal rank role too.
# ---------------------------------------------------------------------------

LEADERSHIP_ROLE_NAME    = "Leadership"
LEADERSHIP_ROLE_COLOUR  = 0x9b59b6
LEADERSHIP_SOURCE_RANKS = {"Leader", "Officer"}


async def ensure_leadership_role(discord_guild: discord.Guild) -> discord.Role:
    """
    Return the Discord 'Leadership' role, creating it if it does not exist.
    The role is not hoisted (members with it are not displayed separately in
    the member list), coloured 0x9b59b6, and positioned as the highest role
    in the server besides the bot's own role(s).
    """
    role = discord.utils.get(discord_guild.roles, name=LEADERSHIP_ROLE_NAME)
    if role:
        return role

    role = await discord_guild.create_role(
        name=LEADERSHIP_ROLE_NAME,
        colour=discord.Colour(LEADERSHIP_ROLE_COLOUR),
        hoist=False,
        reason="Leadership role auto-created by roster bot",
    )
    logging.info(f"Created Leadership role in guild {discord_guild.id}")

    # Position it directly below the bot's highest role, i.e. the highest
    # role in the server besides bot roles.
    bot_member  = discord_guild.get_member(discord_guild.me.id)
    bot_top_pos = max((r.position for r in bot_member.roles), default=1)
    target_pos  = max(bot_top_pos - 1, 1)

    try:
        await discord_guild.edit_role_positions({role: target_pos})
    except (discord.Forbidden, discord.HTTPException) as e:
        logging.warning(f"Could not position Leadership role in guild {discord_guild.id}: {e}")

    return role


async def sync_leadership_roles(discord_guild: discord.Guild):
    """
    Called on every roster update cycle. Ensures every member currently
    holding a 'Leader' or 'Officer' role also holds the 'Leadership' role,
    and removes 'Leadership' from anyone who no longer holds either rank.
    """
    leadership_role = await ensure_leadership_role(discord_guild)

    for member in discord_guild.members:
        member_role_names = {r.name for r in member.roles}
        should_have_leadership = bool(member_role_names & LEADERSHIP_SOURCE_RANKS)
        has_leadership = any(r.id == leadership_role.id for r in member.roles)

        try:
            if should_have_leadership and not has_leadership:
                await member.add_roles(leadership_role, reason="Holds Officer/Leader rank — granted Leadership role")
            elif not should_have_leadership and has_leadership:
                await member.remove_roles(leadership_role, reason="No longer holds Officer/Leader rank — removed Leadership role")
        except discord.Forbidden:
            logging.warning(f"Missing permissions to update Leadership role for {member.id} in guild {discord_guild.id}")
        except Exception as e:
            logging.warning(f"Failed to sync Leadership role for {member.id}: {e}")

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
# LFG helpers
# Stored as top-level keys in config.json:
# { "lfg_boards": { "<discord_guild_id>": {
#       "channel_id": int,            # channel holding the button + calendar
#       "announce_channel_id": int,   # "starting now" pings
#       "button_message_id": int,
#       "board_message_id": int,
#       "setup_by": int
#   } },
#   "lfgs": { "<discord_guild_id>": { "<lfg_id>": {
#       "id": str,
#       "host_id": int,
#       "name": str,
#       "unix_ts": int,
#       "max_slots": int,
#       "roles": ["dps", ...],
#       "signups": { "<role>": [user_id, ...] },
#       "emoji_ids": { "<role>": emoji_id }
#   } } } }
# ---------------------------------------------------------------------------

LFG_STANDARD_ROLES = ["dps", "qdps", "adps", "qheal", "aheal", "flex"]
LFG_SIMPLE_ROLES   = ["dps"]
LFG_MAX_CUSTOM     = 50
LFG_BOARD_MAX      = 25   # one dropdown option per LFG; Discord select menus hold at most 25

# Dropdown choices for the /lfg timezone picker (Discord allows max 25 options).
# Values are IANA names so daylight saving is handled automatically.
LFG_TZ_CHOICES = [
    ("PT – Pacific (Los Angeles, Vancouver)",   "America/Los_Angeles"),
    ("MT – Mountain (Denver, Edmonton)",        "America/Denver"),
    ("MST – Arizona (no DST)",                  "America/Phoenix"),
    ("CT – Central (Chicago, Winnipeg)",        "America/Chicago"),
    ("ET – Eastern (New York, Toronto)",        "America/New_York"),
    ("AT – Atlantic (Halifax)",                 "America/Halifax"),
    ("NT – Newfoundland (St. John's)",          "America/St_Johns"),
    ("AKT – Alaska (Anchorage)",                "America/Anchorage"),
    ("HST – Hawaii (Honolulu)",                 "Pacific/Honolulu"),
    ("BRT – Brazil (Sao Paulo)",                "America/Sao_Paulo"),
    ("UTC – Coordinated Universal Time",        "UTC"),
    ("GMT/BST – UK / Ireland (London)",         "Europe/London"),
    ("CET/CEST – Central Europe (Paris, Berlin)", "Europe/Paris"),
    ("EET/EEST – Eastern Europe (Athens, Helsinki)", "Europe/Athens"),
    ("MSK – Moscow",                            "Europe/Moscow"),
    ("IST – India (Kolkata)",                   "Asia/Kolkata"),
    ("CST – China / Singapore (Shanghai)",      "Asia/Shanghai"),
    ("JST – Japan / Korea (Tokyo)",             "Asia/Tokyo"),
    ("AEST/AEDT – Australia East (Sydney)",     "Australia/Sydney"),
    ("AWST – Australia West (Perth)",           "Australia/Perth"),
    ("NZST/NZDT – New Zealand (Auckland)",      "Pacific/Auckland"),
]
LFG_TZ_LABELS = {value: label for label, value in LFG_TZ_CHOICES}

TZ_ALIASES = {
    "UTC": "UTC", "GMT": "UTC", "Z": "UTC",
    "EST": "America/New_York", "EDT": "America/New_York", "ET": "America/New_York",
    "CST": "America/Chicago", "CDT": "America/Chicago", "CT": "America/Chicago",
    "MST": "America/Denver", "MDT": "America/Denver", "MT": "America/Denver",
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles", "PT": "America/Los_Angeles",
    "AKST": "America/Anchorage", "AKDT": "America/Anchorage",
    "HST": "Pacific/Honolulu",
    "AST": "America/Halifax", "ADT": "America/Halifax",
    "NST": "America/St_Johns", "NDT": "America/St_Johns",
    "BST": "Europe/London", "CET": "Europe/Paris", "CEST": "Europe/Paris",
    "AEST": "Australia/Sydney", "AEDT": "Australia/Sydney",
}

_lfg_lock: asyncio.Lock | None = None


def get_lfg_lock() -> asyncio.Lock:
    global _lfg_lock
    if _lfg_lock is None:
        _lfg_lock = asyncio.Lock()
    return _lfg_lock


def get_guild_lfgs(guild_id: int) -> dict:
    return load_config().get("lfgs", {}).get(str(guild_id), {})


def get_lfg(guild_id: int, lfg_id: str) -> dict | None:
    return get_guild_lfgs(guild_id).get(str(lfg_id))


def save_lfg(guild_id: int, lfg_id: str, data: dict):
    config = load_config()
    config.setdefault("lfgs", {}).setdefault(str(guild_id), {})[str(lfg_id)] = data
    save_config(config)


def delete_lfg(guild_id: int, lfg_id: str):
    config = load_config()
    guild_lfgs = config.get("lfgs", {}).get(str(guild_id), {})
    guild_lfgs.pop(str(lfg_id), None)
    save_config(config)


def sorted_lfg_entries(guild_id: int) -> list[tuple[str, dict]]:
    """Active LFGs for a guild as (lfg_id, lfg) pairs, soonest first."""
    return sorted(
        get_guild_lfgs(guild_id).items(),
        key=lambda kv: int(kv[1].get("unix_ts", 0)),
    )


def get_lfg_board(guild_id: int) -> dict | None:
    return load_config().get("lfg_boards", {}).get(str(guild_id))


def set_lfg_board(guild_id: int, data: dict):
    config = load_config()
    config.setdefault("lfg_boards", {})[str(guild_id)] = data
    save_config(config)


def occupied_user_ids(lfg: dict) -> set[int]:
    """Host always occupies a slot; reactors are unique across all roles."""
    ids = {int(lfg["host_id"])}
    for users in lfg.get("signups", {}).values():
        ids.update(int(uid) for uid in users)
    return ids


def format_lfg_role_lines(lfg: dict) -> list[str]:
    signups   = lfg.get("signups", {})
    emoji_ids = lfg.get("emoji_ids", {})
    lines = []
    for role in lfg["roles"]:
        eid = emoji_ids.get(role)
        prefix = f"<:{role}:{eid}> {role}:" if eid else f"{role}:"
        users = signups.get(role, [])
        if users:
            lines.append(f"{prefix} " + " ".join(f"<@{uid}>" for uid in users))
        else:
            lines.append(prefix)
    return lines


def build_lfg_text(lfg: dict) -> str:
    occupied = occupied_user_ids(lfg)
    lines = [
        f"**{lfg['name']}**",
        f"Host: <@{lfg['host_id']}>",
        f"Time: <t:{lfg['unix_ts']}:F> (<t:{lfg['unix_ts']}:R>)",
        "",
        *format_lfg_role_lines(lfg),
        "",
        f"**Slots:** {len(occupied)}/{lfg['max_slots']}",
    ]
    return "\n".join(lines)


def parse_lfg_datetime(date_str: str, time_str: str, tz_str: str) -> datetime:
    date_str = date_str.strip()
    time_str = " ".join(time_str.strip().upper().replace(".", "").split())
    tz_str   = tz_str.strip()

    parsed_date = None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d %b %Y", "%b %d %Y", "%d %B %Y", "%B %d %Y"):
        try:
            parsed_date = datetime.strptime(date_str, fmt)
            break
        except ValueError:
            continue
    if parsed_date is None:
        raise ValueError("Could not parse date. Use `YYYY-MM-DD` or `MM/DD/YYYY`.")

    parsed_time = None
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I %p", "%I:%M%p", "%I%p"):
        try:
            parsed_time = datetime.strptime(time_str, fmt)
            break
        except ValueError:
            continue
    if parsed_time is None:
        raise ValueError("Could not parse time. Use `20:00` or `8:00 PM`.")

    alias = TZ_ALIASES.get(tz_str.upper())
    try:
        tz = ZoneInfo(alias) if alias else ZoneInfo(tz_str)
    except ZoneInfoNotFoundError:
        raise ValueError(
            f"Unknown timezone `{tz_str}`. Try `EST`, `PST`, `UTC`, or an IANA name like `America/New_York`."
        )

    return datetime(
        parsed_date.year, parsed_date.month, parsed_date.day,
        parsed_time.hour, parsed_time.minute, parsed_time.second,
        tzinfo=tz,
    )


def resolve_lfg_emojis(role_names: list[str]) -> dict[str, discord.Emoji]:
    found: dict[str, discord.Emoji] = {}
    missing: list[str] = []
    for name in role_names:
        emoji = None
        for guild in bot.guilds:
            emoji = discord.utils.get(guild.emojis, name=name)
            if emoji:
                break
        if emoji:
            found[name] = emoji
        else:
            missing.append(f":{name}:")
    if missing:
        raise ValueError(
            "Missing custom emoji(s) the bot can use: " + ", ".join(missing)
            + ". Add them to a server the bot is in and try again."
        )
    return found

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
# LFG views
# ---------------------------------------------------------------------------

class LfgDetailsModal(discord.ui.Modal, title="Create LFG"):
    event_name = discord.ui.TextInput(
        label="Event name",
        placeholder="e.g. Friday Raid, HT CM, WvW zerg",
        max_length=100,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        view = LfgWhenView(name=self.event_name.value.strip())
        await interaction.response.send_message(view.content(), view=view, ephemeral=True)


class LfgWhenView(discord.ui.View):
    """Click-only date / time / timezone picker (no typing)."""

    DATES_PER_PAGE = 25   # Discord select menus hold at most 25 options
    MAX_DATE_PAGES = 12   # ~300 days ahead
    SLOTS_PER_PAGE = 24   # 24 x 15 min = 6 hours per page
    TIME_PAGES     = 4    # 4 x 6 hours = full day

    def __init__(self, name: str):
        super().__init__(timeout=300)
        self.name       = name
        self.tz:        str | None = None
        self.date                  = None      # datetime.date
        self.slot:      int | None = None      # minutes since midnight
        self.date_page: int = 0
        self.time_page: int = 3                # start on the 6 PM - midnight page
        self._refresh()

    # ----- helpers -----------------------------------------------------

    def _today(self):
        tz = ZoneInfo(self.tz) if self.tz else ZoneInfo("UTC")
        return datetime.now(tz).date()

    @staticmethod
    def _fmt_date(d) -> str:
        return f"{d:%a %b} {d.day}, {d.year}"

    @staticmethod
    def _fmt_slot(minutes: int) -> str:
        h, m = divmod(minutes, 60)
        return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"

    @staticmethod
    def _utc_offset(tz_name: str) -> str:
        off = datetime.now(ZoneInfo(tz_name)).utcoffset()
        total = int(off.total_seconds() // 60)
        sign = "+" if total >= 0 else "-"
        h, m = divmod(abs(total), 60)
        return f"Currently UTC{sign}{h}" + (f":{m:02d}" if m else "")

    def content(self) -> str:
        date_s = self._fmt_date(self.date) if self.date else "—"
        time_s = self._fmt_slot(self.slot) if self.slot is not None else "—"
        tz_s   = LFG_TZ_LABELS.get(self.tz, "—") if self.tz else "—"
        return (
            f"**{self.name}** — pick when it starts.\n"
            f"🌍 Timezone: **{tz_s}**\n"
            f"📅 Date: **{date_s}**\n"
            f"🕒 Time: **{time_s}**\n"
            "-# Choose your timezone first so the date list starts from your \"today\"."
        )

    def _refresh(self):
        """Rebuild option lists (with defaults) and button states from current state."""
        # Timezone
        self.tz_select.options = [
            discord.SelectOption(
                label=label,
                value=value,
                description=self._utc_offset(value),
                default=(value == self.tz),
            )
            for label, value in LFG_TZ_CHOICES
        ]

        # Dates (25 per page, starting from "today" in the chosen timezone)
        today = self._today()
        base  = today + timedelta(days=self.date_page * self.DATES_PER_PAGE)
        date_opts = []
        for i in range(self.DATES_PER_PAGE):
            d = base + timedelta(days=i)
            label = self._fmt_date(d)
            if d == today:
                label += " (today)"
            elif d == today + timedelta(days=1):
                label += " (tomorrow)"
            date_opts.append(
                discord.SelectOption(
                    label=label,
                    value=d.isoformat(),
                    default=(self.date is not None and d == self.date),
                )
            )
        self.date_select.options = date_opts
        self.date_select.placeholder = (
            f"📅 Date ({self._fmt_date(base)} – "
            f"{self._fmt_date(base + timedelta(days=self.DATES_PER_PAGE - 1))})"
        )

        # Time (15-minute slots, 6 hours per page)
        start = self.time_page * self.SLOTS_PER_PAGE * 15
        slots = [start + 15 * i for i in range(self.SLOTS_PER_PAGE)]
        self.time_select.options = [
            discord.SelectOption(
                label=self._fmt_slot(s), value=str(s), default=(self.slot == s)
            )
            for s in slots
        ]
        self.time_select.placeholder = (
            f"🕒 Time ({self._fmt_slot(slots[0])} – {self._fmt_slot(slots[-1])})"
        )

        # Buttons
        self.prev_dates.disabled = self.date_page <= 0
        self.next_dates.disabled = self.date_page >= self.MAX_DATE_PAGES - 1
        self.prev_times.disabled = self.time_page <= 0
        self.next_times.disabled = self.time_page >= self.TIME_PAGES - 1
        self.confirm_button.disabled = not (self.date and self.slot is not None and self.tz)

    async def _update(self, interaction: discord.Interaction):
        self._refresh()
        await interaction.response.edit_message(content=self.content(), view=self)

    # ----- components --------------------------------------------------

    @discord.ui.select(placeholder="🌍 Timezone", min_values=1, max_values=1, options=[], row=0)
    async def tz_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.tz = select.values[0]
        await self._update(interaction)

    @discord.ui.select(placeholder="📅 Date", min_values=1, max_values=1, options=[], row=1)
    async def date_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.date = datetime.strptime(select.values[0], "%Y-%m-%d").date()
        await self._update(interaction)

    @discord.ui.select(placeholder="🕒 Time", min_values=1, max_values=1, options=[], row=2)
    async def time_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.slot = int(select.values[0])
        await self._update(interaction)

    @discord.ui.button(label="◀ Dates", style=discord.ButtonStyle.secondary, row=3)
    async def prev_dates(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.date_page = max(0, self.date_page - 1)
        await self._update(interaction)

    @discord.ui.button(label="Dates ▶", style=discord.ButtonStyle.secondary, row=3)
    async def next_dates(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.date_page = min(self.MAX_DATE_PAGES - 1, self.date_page + 1)
        await self._update(interaction)

    @discord.ui.button(label="◀ Times", style=discord.ButtonStyle.secondary, row=3)
    async def prev_times(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.time_page = max(0, self.time_page - 1)
        await self._update(interaction)

    @discord.ui.button(label="Times ▶", style=discord.ButtonStyle.secondary, row=3)
    async def next_times(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.time_page = min(self.TIME_PAGES - 1, self.time_page + 1)
        await self._update(interaction)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, row=4, disabled=True)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (self.date and self.slot is not None and self.tz):
            await interaction.response.send_message(
                "❌ Pick a date, time, and timezone first.", ephemeral=True
            )
            return

        hour, minute = divmod(self.slot, 60)
        dt = datetime(
            self.date.year, self.date.month, self.date.day,
            hour, minute,
            tzinfo=ZoneInfo(self.tz),
        )
        if dt.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "❌ That time is already in the past. Pick a later date or time.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            content=(
                f"**{self.name}** at {discord.utils.format_dt(dt, style='F')}.\n"
                "How many people is this for?"
            ),
            view=LfgSizeView(
                name=self.name,
                unix_ts=int(dt.timestamp()),
            ),
        )
        self.stop()


class LfgSizeView(discord.ui.View):
    def __init__(self, name: str, unix_ts: int):
        super().__init__(timeout=180)
        self.name       = name
        self.unix_ts    = unix_ts

    async def _ask_roles(self, interaction: discord.Interaction, max_slots: int, *, edit: bool):
        view = LfgRolesView(
            name=self.name,
            unix_ts=self.unix_ts,
            max_slots=max_slots,
        )
        content = (
            f"Party size: **{max_slots}**.\n"
            "Use standard squad roles (`dps`, `qdps`, `adps`, `qheal`, `aheal`, `flex`), "
            "or DPS only?"
        )
        if edit:
            await interaction.response.edit_message(content=content, view=view)
        else:
            await interaction.response.send_message(content=content, view=view, ephemeral=True)

    @discord.ui.button(label="5 person", style=discord.ButtonStyle.primary)
    async def size_five(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._ask_roles(interaction, 5, edit=True)

    @discord.ui.button(label="10 person", style=discord.ButtonStyle.primary)
    async def size_ten(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._ask_roles(interaction, 10, edit=True)

    @discord.ui.button(label="Custom", style=discord.ButtonStyle.secondary)
    async def size_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            LfgCustomSizeModal(name=self.name, unix_ts=self.unix_ts)
        )


class LfgCustomSizeModal(discord.ui.Modal, title="Custom party size"):
    def __init__(self, name: str, unix_ts: int):
        super().__init__()
        self.name       = name
        self.unix_ts    = unix_ts

    amount = discord.ui.TextInput(
        label="Number of people (1–50)",
        placeholder="e.g. 15",
        max_length=2,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.amount.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("❌ Enter a whole number from 1 to 50.", ephemeral=True)
            return
        max_slots = int(raw)
        if not 1 <= max_slots <= LFG_MAX_CUSTOM:
            await interaction.response.send_message("❌ Party size must be between 1 and 50.", ephemeral=True)
            return

        view = LfgRolesView(
            name=self.name,
            unix_ts=self.unix_ts,
            max_slots=max_slots,
        )
        await interaction.response.send_message(
            f"Party size: **{max_slots}**.\n"
            "Use standard squad roles (`dps`, `qdps`, `adps`, `qheal`, `aheal`, `flex`), "
            "or DPS only?",
            view=view,
            ephemeral=True,
        )


class LfgRolesView(discord.ui.View):
    def __init__(self, name: str, unix_ts: int, max_slots: int):
        super().__init__(timeout=180)
        self.name      = name
        self.unix_ts   = unix_ts
        self.max_slots = max_slots
        self._posted   = False

    async def _post(self, interaction: discord.Interaction, standard: bool):
        if self._posted:
            await interaction.response.send_message("This LFG was already created.", ephemeral=True)
            return
        self._posted = True

        await interaction.response.edit_message(content="Creating LFG…", view=None)

        guild_id = interaction.guild_id
        board = get_lfg_board(guild_id) if guild_id else None
        if not board:
            await interaction.edit_original_response(
                content="❌ LFG isn't set up on this server. Ask an admin to run `/setuplfg`."
            )
            return

        roles = LFG_STANDARD_ROLES if standard else LFG_SIMPLE_ROLES
        try:
            emojis = resolve_lfg_emojis(roles)
        except ValueError as e:
            self._posted = False
            await interaction.edit_original_response(content=f"❌ {e}")
            return

        calendar_full = False
        async with get_lfg_lock():
            if len(get_guild_lfgs(guild_id)) >= LFG_BOARD_MAX:
                calendar_full = True
            else:
                lfg_id = secrets.token_hex(4)
                lfg = {
                    "id":        lfg_id,
                    "host_id":   interaction.user.id,
                    "name":      self.name,
                    "unix_ts":   self.unix_ts,
                    "max_slots": self.max_slots,
                    "roles":     roles,
                    "signups":   {role: [] for role in roles},
                    "emoji_ids": {role: emoji.id for role, emoji in emojis.items()},
                }
                save_lfg(guild_id, lfg_id, lfg)

        if calendar_full:
            await interaction.edit_original_response(
                content=f"❌ The calendar is full ({LFG_BOARD_MAX} active LFGs). Try again once one has started."
            )
            return

        await refresh_lfg_board(guild_id)
        await interaction.edit_original_response(
            content=f"✅ **{self.name}** is on the calendar in <#{board['channel_id']}>."
        )
        logging.info(
            f"User {interaction.user.id} created LFG '{self.name}' (id {lfg_id}, "
            f"{self.max_slots} slots, roles={roles}) in guild {guild_id}"
        )

    @discord.ui.button(label="Standard squad roles", style=discord.ButtonStyle.success)
    async def standard_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._post(interaction, standard=True)

    @discord.ui.button(label="DPS only", style=discord.ButtonStyle.secondary)
    async def dps_only(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._post(interaction, standard=False)


# ---------------------------------------------------------------------------
# LFG calendar, "Create LFG" button, and per-LFG detail view
# ---------------------------------------------------------------------------

def _lfg_user_current_role(lfg: dict, user_id: int) -> str | None:
    for role, users in lfg.get("signups", {}).items():
        if any(int(uid) == user_id for uid in users):
            return role
    return None


def _can_delete_lfg(lfg: dict, user) -> bool:
    """The host can delete their own LFG; server administrators can delete any."""
    perms = getattr(user, "guild_permissions", None)
    return int(lfg["host_id"]) == user.id or bool(perms and perms.administrator)


def _lfg_option_description(lfg: dict) -> str:
    dt = datetime.fromtimestamp(int(lfg["unix_ts"]), tz=timezone.utc)
    slots = f"{len(occupied_user_ids(lfg))}/{lfg['max_slots']} slots"
    return f"{dt:%a %b} {dt.day}, {dt:%H:%M} UTC · {slots}"[:100]


def build_lfg_board_embed(entries: list[tuple[str, dict]]) -> discord.Embed:
    """The 'calendar': every active LFG in chronological order."""
    embed = discord.Embed(title="📅 Upcoming LFGs", color=0x5865F2)
    if not entries:
        embed.description = "No LFGs scheduled right now.\nClick **Create LFG** above to start one."
        return embed

    embed.description = "Pick an LFG from the dropdown below to view it and join with a role."
    for _lfg_id, lfg in entries[:LFG_BOARD_MAX]:
        occupied = occupied_user_ids(lfg)
        embed.add_field(
            name=lfg["name"][:100],
            value=(
                f"🕒 <t:{lfg['unix_ts']}:F> (<t:{lfg['unix_ts']}:R>)\n"
                f"👤 <@{lfg['host_id']}> · **{len(occupied)}/{lfg['max_slots']}** slots"
            ),
            inline=False,
        )
    return embed


def build_lfg_detail_text(lfg: dict, user_id: int) -> str:
    current = _lfg_user_current_role(lfg, user_id)
    if current:
        status = f"✅ You're signed up as **{current}**."
    elif int(lfg["host_id"]) == user_id:
        status = "You're the host, so a slot is reserved for you. Pick a role to list yourself under it."
    else:
        status = "Pick a role below to join."
    return f"{build_lfg_text(lfg)}\n\n{status}"


class LfgCreateView(discord.ui.View):
    """Persistent 'Create LFG' button posted by /setuplfg."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create LFG",
        style=discord.ButtonStyle.success,
        emoji="➕",
        custom_id="lfg_create",
    )
    async def create_lfg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild_id or not get_lfg_board(interaction.guild_id):
            await interaction.response.send_message(
                "❌ LFG isn't set up on this server. Ask an admin to run `/setuplfg`.",
                ephemeral=True,
            )
            return
        if len(get_guild_lfgs(interaction.guild_id)) >= LFG_BOARD_MAX:
            await interaction.response.send_message(
                f"❌ The calendar is full ({LFG_BOARD_MAX} active LFGs). Try again once one has started.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(LfgDetailsModal())


class LfgBoardSelect(discord.ui.Select):
    """Dropdown under the calendar; one option per active LFG."""

    def __init__(self, entries: list[tuple[str, dict]]):
        options = [
            discord.SelectOption(
                label=lfg["name"][:100],
                value=lfg_id,
                description=_lfg_option_description(lfg),
            )
            for lfg_id, lfg in entries[:LFG_BOARD_MAX]
        ]
        if not options:
            # Only used when registering the persistent view at startup.
            options = [discord.SelectOption(label="No LFGs", value="none")]
        super().__init__(
            custom_id="lfg_board_select",
            placeholder="📅 Select an LFG to view or join…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        lfg_id   = self.values[0]
        lfg      = get_lfg(guild_id, lfg_id) if guild_id else None

        if not lfg:
            await interaction.response.send_message(
                "❌ That LFG no longer exists (it may have started or been deleted).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                build_lfg_detail_text(lfg, interaction.user.id),
                view=LfgDetailView(lfg_id, lfg, interaction.user),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        # Re-render the calendar: resets the dropdown selection and drops stale entries.
        if guild_id:
            asyncio.create_task(refresh_lfg_board(guild_id))


class LfgBoardView(discord.ui.View):
    """Persistent view holding the calendar dropdown."""

    def __init__(self, entries: list[tuple[str, dict]] | None = None):
        super().__init__(timeout=None)
        self.add_item(LfgBoardSelect(entries or []))


class LfgRoleButton(discord.ui.Button):
    def __init__(self, role: str, selected: bool, emoji_id, row: int):
        super().__init__(
            label=role,
            style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
            emoji=discord.PartialEmoji(name=role, id=int(emoji_id)) if emoji_id else None,
            row=row,
        )
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        await lfg_signup(interaction, self.view.lfg_id, self.role)


class LfgLeaveButton(discord.ui.Button):
    def __init__(self, row: int, disabled: bool):
        super().__init__(label="Leave", style=discord.ButtonStyle.secondary, row=row, disabled=disabled)

    async def callback(self, interaction: discord.Interaction):
        await lfg_signup(interaction, self.view.lfg_id, None)


class LfgDeleteButton(discord.ui.Button):
    def __init__(self, row: int):
        super().__init__(label="Delete LFG", style=discord.ButtonStyle.danger, emoji="🗑️", row=row)

    async def callback(self, interaction: discord.Interaction):
        lfg = get_lfg(interaction.guild_id, self.view.lfg_id)
        if not lfg:
            await interaction.response.edit_message(
                content="❌ That LFG no longer exists (it may have started or been deleted).",
                view=None,
            )
            return
        if not _can_delete_lfg(lfg, interaction.user):
            await interaction.response.send_message(
                "❌ Only the host (or a server admin) can delete this LFG.", ephemeral=True
            )
            return

        host_id = int(lfg["host_id"])
        others = {
            int(uid)
            for users in lfg.get("signups", {}).values()
            for uid in users
            if int(uid) != host_id
        }
        warning = (
            f" This will remove **{len(others)}** signed-up "
            f"member{'s' if len(others) != 1 else ''} from it."
            if others else ""
        )
        await interaction.response.edit_message(
            content=f"🗑️ Delete **{lfg['name']}**?{warning} This can't be undone.",
            view=LfgDeleteConfirmView(self.view.lfg_id),
        )


class LfgDetailView(discord.ui.View):
    """Ephemeral view shown to one user after they pick an LFG from the calendar."""

    def __init__(self, lfg_id: str, lfg: dict, user):
        super().__init__(timeout=600)
        self.lfg_id = lfg_id

        current   = _lfg_user_current_role(lfg, user.id)
        emoji_ids = lfg.get("emoji_ids", {})
        roles     = lfg["roles"]
        for i, role in enumerate(roles):
            self.add_item(LfgRoleButton(role, current == role, emoji_ids.get(role), row=i // 5))

        action_row = (len(roles) - 1) // 5 + 1
        self.add_item(LfgLeaveButton(row=action_row, disabled=current is None))
        if _can_delete_lfg(lfg, user):
            self.add_item(LfgDeleteButton(row=action_row))


class LfgDeleteConfirmView(discord.ui.View):
    def __init__(self, lfg_id: str):
        super().__init__(timeout=120)
        self.lfg_id = lfg_id

    @discord.ui.button(label="Yes, delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild_id
        lfg      = None
        denied   = False
        async with get_lfg_lock():
            lfg = get_lfg(guild_id, self.lfg_id)
            if lfg and not _can_delete_lfg(lfg, interaction.user):
                denied = True
            elif lfg:
                delete_lfg(guild_id, self.lfg_id)

        if denied:
            await interaction.response.send_message(
                "❌ Only the host (or a server admin) can delete this LFG.", ephemeral=True
            )
            return
        if not lfg:
            await interaction.response.edit_message(
                content="❌ That LFG no longer exists (it may have started or been deleted).",
                view=None,
            )
            await refresh_lfg_board(guild_id)
            return

        await interaction.response.edit_message(content=f"🗑️ **{lfg['name']}** was deleted.", view=None)
        await refresh_lfg_board(guild_id)
        logging.info(f"User {interaction.user.id} deleted LFG '{lfg['name']}' ({self.lfg_id}) in guild {guild_id}")

    @discord.ui.button(label="Keep it", style=discord.ButtonStyle.secondary)
    async def keep(self, interaction: discord.Interaction, button: discord.ui.Button):
        lfg = get_lfg(interaction.guild_id, self.lfg_id)
        if not lfg:
            await interaction.response.edit_message(
                content="❌ That LFG no longer exists (it may have started or been deleted).",
                view=None,
            )
            return
        await interaction.response.edit_message(
            content=build_lfg_detail_text(lfg, interaction.user.id),
            view=LfgDetailView(self.lfg_id, lfg, interaction.user),
        )


async def lfg_signup(interaction: discord.Interaction, lfg_id: str, role: str | None):
    """Join `role` on an LFG, or leave it when role is None. Updates the detail view and the calendar."""
    guild_id = interaction.guild_id
    user_id  = interaction.user.id
    lfg      = None
    notice   = None
    changed  = False

    async with get_lfg_lock():
        lfg = get_lfg(guild_id, lfg_id)
        if lfg is None:
            pass
        elif int(lfg["unix_ts"]) <= datetime.now(timezone.utc).timestamp():
            notice = "❌ This LFG has already started."
        else:
            signups = lfg.setdefault("signups", {r: [] for r in lfg["roles"]})
            current = _lfg_user_current_role(lfg, user_id)

            if role is None:
                if current:
                    signups[current] = [u for u in signups[current] if int(u) != user_id]
                    changed = True
            elif current != role:
                occupied = occupied_user_ids(lfg)   # the host always occupies a slot
                if user_id not in occupied and len(occupied) >= int(lfg["max_slots"]):
                    notice = "❌ This LFG is full."
                else:
                    if current:
                        signups[current] = [u for u in signups[current] if int(u) != user_id]
                    signups.setdefault(role, []).append(user_id)
                    changed = True

            if changed:
                lfg["signups"] = signups
                save_lfg(guild_id, lfg_id, lfg)

    if lfg is None:
        await interaction.response.edit_message(
            content="❌ That LFG no longer exists (it may have started or been deleted).",
            view=None,
        )
        await refresh_lfg_board(guild_id)
        return

    content = build_lfg_detail_text(lfg, user_id)
    if notice:
        content += f"\n\n{notice}"
    await interaction.response.edit_message(
        content=content,
        view=LfgDetailView(lfg_id, lfg, interaction.user),
    )
    if changed:
        await refresh_lfg_board(guild_id)

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
intents.reactions = True
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
    bot.add_view(LfgCreateView())
    bot.add_view(LfgBoardView())
    await tree.sync()
    logging.info(f"Logged in as {bot.user} ({bot.user.id})")
    if not roster_loop.is_running():
        roster_loop.start()
    if not lfg_loop.is_running():
        lfg_loop.start()
    # Clean up verified accounts for members who left while the bot was offline
    asyncio.create_task(_cleanup_departed_members())
    asyncio.create_task(_lfg_startup())


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
# LFG calendar refresh and start announcements
# ---------------------------------------------------------------------------

_lfg_board_locks: dict[int, asyncio.Lock] = {}


async def _get_channel(channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return channel


async def refresh_lfg_board(guild_id: int):
    """Re-render the calendar message (embed + dropdown) from the stored LFGs."""
    lock = _lfg_board_locks.setdefault(guild_id, asyncio.Lock())
    async with lock:
        board = get_lfg_board(guild_id)
        if not board:
            return
        channel = await _get_channel(board["channel_id"])
        if channel is None:
            logging.warning(f"LFG calendar channel {board['channel_id']} not found in guild {guild_id}")
            return

        # Snapshot taken inside the per-guild lock, so a slow edit can never
        # overwrite a newer one with stale data.
        entries = sorted_lfg_entries(guild_id)
        embed   = build_lfg_board_embed(entries)
        view    = LfgBoardView(entries) if entries else None

        try:
            await channel.get_partial_message(board["board_message_id"]).edit(embed=embed, view=view)
        except discord.NotFound:
            # Someone deleted the calendar message: post a fresh one.
            try:
                msg = await channel.send(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException) as e:
                logging.warning(f"Could not repost LFG calendar in guild {guild_id}: {e}")
                return
            board["board_message_id"] = msg.id
            set_lfg_board(guild_id, board)
        except (discord.Forbidden, discord.HTTPException) as e:
            logging.warning(f"Could not update LFG calendar in guild {guild_id}: {e}")


async def refresh_all_lfg_boards():
    for guild_id_str in list(load_config().get("lfg_boards", {}).keys()):
        if guild_id_str.isdigit():
            await refresh_lfg_board(int(guild_id_str))


async def _announce_channel_for(guild_id: int):
    board = get_lfg_board(guild_id)
    if not board:
        return None
    channel = await _get_channel(board["announce_channel_id"])
    if channel is None:
        logging.warning(f"LFG announce channel {board['announce_channel_id']} not found in guild {guild_id}; using calendar channel")
        channel = await _get_channel(board["channel_id"])
    return channel


async def announce_lfg_start(guild_id: int, lfg: dict):
    channel = await _announce_channel_for(guild_id)
    if channel is None:
        logging.warning(f"LFG '{lfg.get('name')}' started in guild {guild_id} but no channel to announce in")
        return

    role_block = "\n".join(format_lfg_role_lines(lfg))
    try:
        await channel.send(
            f"**{lfg['name']}** is starting now!\n"
            f"Host: <@{lfg['host_id']}>\n\n"
            f"{role_block}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        logging.warning(f"Could not send LFG start message for '{lfg.get('name')}': {e}")
        return

    logging.info(f"LFG '{lfg['name']}' started in guild {guild_id}")


async def process_due_lfgs():
    now = datetime.now(timezone.utc).timestamp()
    due: list[tuple[int, str, dict]] = []
    async with get_lfg_lock():
        config = load_config()
        for guild_id_str, lfgs in list(config.get("lfgs", {}).items()):
            if not guild_id_str.isdigit():
                continue
            for lfg_id, lfg in list(lfgs.items()):
                if int(lfg.get("unix_ts", 0)) <= now:
                    due.append((int(guild_id_str), lfg_id, lfg))
                    delete_lfg(int(guild_id_str), lfg_id)

    touched: set[int] = set()
    for guild_id, lfg_id, lfg in due:
        touched.add(guild_id)
        try:
            await announce_lfg_start(guild_id, lfg)
        except Exception as e:
            logging.error(f"Failed to start LFG {lfg_id} in guild {guild_id}: {e}")

    for guild_id in touched:
        try:
            await refresh_lfg_board(guild_id)
        except Exception as e:
            logging.error(f"Failed to refresh LFG calendar in guild {guild_id}: {e}")


async def migrate_legacy_lfgs():
    """
    LFGs created before the calendar existed were keyed by their message id and
    carried a channel_id. Re-key them by a plain id, keep their signups, and
    delete the old posts so they show up on the calendar instead.
    """
    to_delete: list[tuple[int, int]] = []
    touched: set[int] = set()
    async with get_lfg_lock():
        config = load_config()
        for guild_id_str, lfgs in config.get("lfgs", {}).items():
            if not guild_id_str.isdigit():
                continue
            for key, lfg in lfgs.items():
                if isinstance(lfg, dict) and "id" not in lfg:
                    channel_id = lfg.pop("channel_id", None)
                    lfg["id"] = key
                    if channel_id and key.isdigit():
                        to_delete.append((int(channel_id), int(key)))
                    touched.add(int(guild_id_str))
        if touched:
            save_config(config)

    for channel_id, message_id in to_delete:
        channel = await _get_channel(channel_id)
        if channel is None:
            continue
        try:
            await channel.get_partial_message(message_id).delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    if touched:
        logging.info(f"Migrated legacy LFGs in {len(touched)} guild(s) to the calendar format.")


async def _lfg_startup():
    try:
        await migrate_legacy_lfgs()
        await refresh_all_lfg_boards()
    except Exception as e:
        logging.error(f"LFG startup tasks failed: {e}")


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

VERIFY_INSTRUCTIONS = (
    "Use your full Guild Wars 2 account name (including the numbers, e.g. PlayerName.1234) "
    "to gain access to the server. **This is case sensitive.**"
)


async def verify_gw2_account(interaction: discord.Interaction, gw2_username: str):
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


class VerifyModal(discord.ui.Modal, title="Verify your GW2 account"):
    def __init__(self):
        super().__init__()
        self.gw2_name = discord.ui.TextInput(
            placeholder="e.g. PlayerName.1234 (include the numbers)",
            max_length=50,
            required=True,
            **({} if hasattr(discord.ui, "Label") else {"label": "Full GW2 account name (case sensitive)"}),
        )
        if hasattr(discord.ui, "Label") and hasattr(discord.ui, "TextDisplay"):
            # discord.py 2.6+: instructions above the box, input wrapped in a Label.
            self.add_item(discord.ui.TextDisplay(VERIFY_INSTRUCTIONS))
            self.add_item(discord.ui.Label(
                text="Full GW2 account name (case sensitive)",
                component=self.gw2_name,
            ))
        else:
            # Older discord.py: the label and placeholder carry the key points on their own.
            self.add_item(self.gw2_name)

    async def on_submit(self, interaction: discord.Interaction):
        await verify_gw2_account(interaction, self.gw2_name.value)


@tree.command(name="verify", description="Verify your GW2 account to receive your guild rank role.")
async def verify(interaction: discord.Interaction):
    if not interaction.guild_id or not get_guild_config(interaction.guild_id):
        await interaction.response.send_message(
            "❌ This bot has not been set up yet. An admin needs to run `/setup` first.",
            ephemeral=True,
        )
        return
    await interaction.response.send_modal(VerifyModal())

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

    # ── LFG ──────────────────────────────────────────────────────────────
    lfg_board = get_lfg_board(guild_id)
    if lfg_board:
        lfg_channel  = interaction.guild.get_channel(lfg_board.get("channel_id", 0))
        lfg_announce = interaction.guild.get_channel(lfg_board.get("announce_channel_id", 0))
        embed.add_field(
            name="📅 LFG",
            value=(
                f"**Calendar channel:** {lfg_channel.mention if lfg_channel else '⚠️ Channel not found'}\n"
                f"**Announce channel:** {lfg_announce.mention if lfg_announce else '⚠️ Channel not found'}\n"
                f"**Active LFGs:** {len(get_guild_lfgs(guild_id))}"
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="📅 LFG",
            value="Not configured. Run `/setuplfg` to enable.",
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
            if discord_guild:
                await sync_leadership_roles(discord_guild)
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


@tasks.loop(seconds=30)
async def lfg_loop():
    await process_due_lfgs()


@lfg_loop.before_loop
async def before_lfg_loop():
    await bot.wait_until_ready()


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


# ---------------------------------------------------------------------------
# /setuplfg
# ---------------------------------------------------------------------------

@tree.command(name="setuplfg", description="[Admin] Set up the LFG button and calendar.")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    channel          = "Channel where the Create LFG button and the LFG calendar will be posted",
    announce_channel = "Channel where 'starting now' pings are posted",
)
async def setuplfg(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    announce_channel: discord.TextChannel,
):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    me = guild.me
    problems = []
    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        problems.append(f"• {channel.mention}: I need **View Channel**, **Send Messages** and **Embed Links**.")
    aperms = announce_channel.permissions_for(me)
    if not (aperms.view_channel and aperms.send_messages):
        problems.append(f"• {announce_channel.mention}: I need **View Channel** and **Send Messages**.")
    if problems:
        await interaction.followup.send(
            "❌ I'm missing permissions:\n" + "\n".join(problems), ephemeral=True
        )
        return

    # Re-running /setuplfg moves the button + calendar; active LFGs are kept.
    old = get_lfg_board(guild.id)
    if old:
        old_channel = guild.get_channel(old.get("channel_id", 0))
        if old_channel:
            for key in ("button_message_id", "board_message_id"):
                message_id = old.get(key)
                if not message_id:
                    continue
                try:
                    await old_channel.get_partial_message(message_id).delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

    entries = sorted_lfg_entries(guild.id)
    intro = discord.Embed(
        title="🗓️ Looking for Group",
        description=(
            "Click **Create LFG** to schedule a group.\n"
            "Every upcoming LFG is listed in the calendar below. Pick one from the dropdown "
            "to see who's in and join with your role."
        ),
        color=0x5865F2,
    )
    try:
        button_msg = await channel.send(embed=intro, view=LfgCreateView())
        board_msg  = await channel.send(
            embed=build_lfg_board_embed(entries),
            view=LfgBoardView(entries) if entries else None,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ I don't have permission to post in {channel.mention}.", ephemeral=True
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Failed to post in {channel.mention}: {e}", ephemeral=True)
        return

    set_lfg_board(guild.id, {
        "channel_id":          channel.id,
        "announce_channel_id": announce_channel.id,
        "button_message_id":   button_msg.id,
        "board_message_id":    board_msg.id,
        "setup_by":            interaction.user.id,
    })

    note = ""
    if channel.permissions_for(guild.default_role).send_messages:
        note = (
            f"\n⚠️ Everyone can currently send messages in {channel.mention}, so chat will push the "
            f"button and calendar up. Consider denying **Send Messages** for @everyone there."
        )
    await interaction.followup.send(
        f"✅ LFG set up. The button and calendar are in {channel.mention}; "
        f"'starting now' pings go to {announce_channel.mention}.{note}",
        ephemeral=True,
    )
    logging.info(f"Admin {interaction.user.id} set up LFG in guild {guild.id}")


# ---------------------------------------------------------------------------
# /removelfg
# ---------------------------------------------------------------------------

@tree.command(name="removelfg", description="[Admin] Remove the LFG button and calendar, and delete all active LFGs.")
@app_commands.default_permissions(administrator=True)
async def removelfg(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    guild_id = interaction.guild_id
    board    = get_lfg_board(guild_id)
    active   = get_guild_lfgs(guild_id)
    if not board and not active:
        await interaction.followup.send("The LFG system is not set up for this server.", ephemeral=True)
        return

    # Hold the calendar lock so an in-flight refresh can't repost the message we're deleting.
    async with _lfg_board_locks.setdefault(guild_id, asyncio.Lock()):
        async with get_lfg_lock():
            config = load_config()
            config.get("lfg_boards", {}).pop(str(guild_id), None)
            config.get("lfgs", {}).pop(str(guild_id), None)
            save_config(config)

        if board:
            channel = interaction.guild.get_channel(board.get("channel_id", 0))
            if channel:
                for key in ("button_message_id", "board_message_id"):
                    message_id = board.get(key)
                    if not message_id:
                        continue
                    try:
                        await channel.get_partial_message(message_id).delete()
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        pass

    logging.info(f"Admin {interaction.user.id} removed the LFG system in guild {guild_id} ({len(active)} active LFG(s) deleted)")
    await interaction.followup.send(
        f"✅ LFG system removed: the button and calendar are deleted"
        + (f", along with {len(active)} active LFG{'s' if len(active) != 1 else ''}." if active else "."),
        ephemeral=True,
    )


bot.run(DISCORD_TOKEN)