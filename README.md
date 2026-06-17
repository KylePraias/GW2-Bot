# GW2 Guild Discord Bot
 
A Discord bot for Guild Wars 2 guilds. Automatically posts and updates a live guild roster, supports GW2 account verification with role assignment, item lookups with trading post prices, and channel auto-moderation. Built with discord.py and hosted on Fly.io.
 
## Features
 
- **Live guild roster** — posts the full guild roster to a designated channel and edits it every hour, splitting across multiple messages if needed
- **GW2 account verification** — members verify their GW2 account name, receive the Member role, and get their nickname set automatically
- **Multiple account support** — one Discord account can be linked to multiple GW2 accounts
- **Item search** — search any GW2 item with autocomplete, showing stats, rarity, vendor value, and live trading post buy/sell prices
- **Channel auto-delete** — restrict channels to bot commands only, with any other messages deleted instantly
- **Persistent config** — all data survives redeployments via a Fly.io volume
## Commands
 
### Available to all members
| Command | Description |
|---|---|
| `/verify` | Link your GW2 account and receive the Member role |
| `/item` | Search for a GW2 item (requires verified Member role) |
| `/viewmyaccounts` | View your linked GW2 accounts |
 
### Admin only
| Command | Description |
|---|---|
| `/setup` | Configure the bot for this server |
| `/unsetup` | Remove the bot configuration for this server |
| `/forceverify` | Manually verify a Discord user as a guild member |
| `/unlink` | Unlink a specific GW2 account from a Discord user |
| `/unlinkall` | Unlink all GW2 accounts from a Discord user |
| `/viewaccounts` | View all linked GW2 accounts for a Discord user |
| `/addautodelete` | Add a channel to the auto-delete list |
| `/removeautodelete` | Remove a channel from the auto-delete list |
| `/viewautodelete` | View all auto-delete channels |
 
## Requirements
 
- Python 3.11+
- A Discord bot token
- A GW2 API key with `guilds` and `account` permissions (must belong to the guild leader)