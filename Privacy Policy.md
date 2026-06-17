# Privacy Policy — GW2 Roster Bot

**Last updated:** [DATE]

This Privacy Policy explains what information the GW2 Roster Bot ("the Bot") collects, how it is used, and how it is stored when the Bot is added to a Discord server.

## 1. Information We Collect

The Bot collects and stores the following information, all of which is tied to the Discord server (guild) that installs it:

- **Server configuration**: the server's Discord Guild ID, the channel ID used for posting the roster, and the channel IDs designated for the auto-delete (bot-commands-only) feature.
- **Guild Wars 2 API key**: provided by a server administrator via `/setup`. This key is used solely to call the official Guild Wars 2 API on the server's behalf (to fetch the guild roster) and is stored in the Bot's configuration file.
- **Guild Wars 2 guild ID**: the GW2 in-game guild's unique identifier, provided during `/setup`.
- **Discord user IDs**: stored when a member links their account via `/verify` or is linked by an admin via `/forceverify`.
- **Guild Wars 2 account names**: the in-game account name (e.g. `PlayerName.1234`) a member links to their Discord account.
- **Identity of the admin who ran `/setup`**: stored so that configuration removal (`/unsetup`) can be restricted to that admin or other server administrators.

The Bot does **not** collect or store:
- Message content, except transiently. In channels an admin designates with `/addautodelete`, the Bot reads a message only to determine whether to delete it; message content is never saved or logged anywhere.
- Direct messages. The Bot ignores DMs entirely.
- Any data from users who have not run `/verify` (beyond their public Discord member list, which Discord itself provides to any bot in the server).

## 2. How We Use This Information

Collected information is used only to operate the Bot's features:

- Fetching and posting an up-to-date Guild Wars 2 guild roster in the configured channel, once per hour.
- Verifying that a linked Guild Wars 2 account name is currently a member of the GW2 guild.
- Assigning or removing the **Member** Discord role and updating a verified member's server nickname to reflect their linked GW2 account(s).
- Automatically unlinking a Discord account and resetting its nickname if the linked GW2 character leaves the in-game guild.
- Enforcing the bot-commands-only auto-delete feature in channels an admin has designated.
- Looking up Guild Wars 2 item and trading post data via the `/item` command (this calls the public GW2 API and does not involve any personal data).

## 3. Data Storage & Retention

- All configuration and verification data is stored in a single configuration file on the server/host running the Bot. It is not stored in any third-party database or analytics service.
- Data for a given Discord server is retained until an administrator removes it (see Section 4) or until the Bot is removed from the server and its host data is deleted.
- A linked GW2 account is automatically removed from storage if that account leaves the in-game guild (detected during the hourly roster sync).

## 4. Your Choices & How to Remove Your Data

- Any member can view their own linked accounts at any time with `/viewmyaccounts`.
- Any member can ask a server administrator to unlink their account(s) with `/unlink` (single account) or `/unlinkall` (all accounts), which removes the stored Discord ID ↔ GW2 account name association and reverts role/nickname changes.
- A server administrator can remove all stored configuration for the server (API key, guild ID, channel settings, and all verification links) at any time with `/unsetup`.
- Removing the Bot from your server does not automatically delete stored data; use `/unsetup` first, or contact the Bot operator (Section 7) to request deletion.

## 5. Data Sharing

The Bot does not sell, rent, or share collected data with advertisers or unrelated third parties. Data is shared only as strictly necessary to provide the Bot's functionality:

- With **Discord**, via the Discord API, to read server membership, assign roles, and post messages — subject to [Discord's own Privacy Policy](https://discord.com/privacy).
- With **ArenaNet's official Guild Wars 2 API**, to fetch guild roster and item/trading-post data — subject to ArenaNet's terms.

## 6. Security

The Bot's host takes reasonable measures to protect stored data, but no system is completely secure. Notably, the Guild Wars 2 API key provided during `/setup` is stored in the configuration file in order to make scheduled API calls; administrators should treat this key as sensitive and use a GW2 API key scoped only to the permissions the Bot needs (`guilds`).

## 7. Children's Privacy

The Bot is intended for use within Discord servers and is subject to [Discord's Terms of Service](https://discord.com/terms), which require all users to be at least 13 years old (or the minimum age in their jurisdiction). The Bot does not knowingly collect data from anyone below this age beyond what Discord itself provides.

## 8. Changes to This Policy

This Privacy Policy may be updated from time to time. Material changes will be noted by updating the "Last updated" date above. Continued use of the Bot after changes take effect constitutes acceptance of the revised policy.

## 9. Contact

Questions about this policy or requests to review or delete your data can be directed to:

**[kylepraias@gmail.com]**