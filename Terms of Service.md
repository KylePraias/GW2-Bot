# Terms of Service — GW2 Roster Bot

**Last updated:** [2026/06/17]

These Terms of Service ("Terms") govern your use of the GW2 Roster Bot on Discord. By adding the Bot to a server or using any of its commands, you agree to these Terms. If you do not agree, do not add or use the Bot.

## 1. Description of Service

The Bot is a third-party Discord application that connects to the official Guild Wars 2 API to:

- Post and automatically update a server's Guild Wars 2 guild roster on an hourly basis.
- Let members link ("verify") their Guild Wars 2 account to their Discord account, which automatically assigns a **Member** role and updates their server nickname.
- Automatically unlink an account and revert role/nickname changes if that character leaves the in-game guild.
- Provide item and trading post lookups via the `/item` command.
- Optionally enforce a bot-commands-only policy in specific channels by deleting other messages sent there.

## 2. Not Affiliated With ArenaNet

The Bot is an independent, fan-made tool. It is **not affiliated with, endorsed by, or sponsored by ArenaNet, NCSOFT, or Guild Wars 2**. All Guild Wars 2 game data, item information, and trademarks belong to their respective owners. The Bot uses ArenaNet's public API in accordance with its terms of use.

## 3. Eligibility

You must meet Discord's own minimum age requirement (13, or higher where required by local law) and comply with [Discord's Terms of Service](https://discord.com/terms) and [Community Guidelines](https://discord.com/guidelines) to use the Bot.

## 4. Server Administrator Responsibilities

A server administrator who runs `/setup` is responsible for:

- Providing a valid Guild Wars 2 API key that they are authorized to use, scoped appropriately (the `guilds` permission), and for that key's leadership/guild access remaining valid.
- Providing an accurate Guild Wars 2 guild ID.
- Keeping a Discord role named **Member** (and optionally **Guest**) present on the server, since the Bot relies on these role names to function.
- Ensuring the Bot's Discord role has sufficient permissions (manage roles, manage nicknames, manage messages where relevant) and is positioned correctly in the role hierarchy.
- Removing the configuration via `/unsetup` if the Bot should no longer operate on that server, or if the API key needs to be rotated or revoked.

The administrator who runs `/setup`, along with any server administrator, may remove the configuration at any time.

## 5. Member Responsibilities

When using `/verify`, you agree that:

- The Guild Wars 2 account name you provide is your own account and accurately identifies a character or account that is a current member of the linked in-game guild.
- You will not attempt to link an account name that does not belong to you or that has already been linked to another Discord user.
- You understand that linking your account will automatically change your server nickname and grant you the **Member** role, and that unlinking (via an administrator's `/unlink` or `/unlinkall`, or automatically if your character leaves the in-game guild) will revert these changes.

## 6. Acceptable Use

You agree not to:

- Use the Bot to harass, impersonate, or misrepresent another player's Guild Wars 2 account.
- Attempt to overload, exploit, reverse-engineer, or interfere with the Bot's operation or its access to the Discord or Guild Wars 2 APIs.
- Use the Bot in a manner that violates Discord's Terms of Service, Guild Wars 2's terms, or any applicable law.

Violation of these Terms may result in a server administrator unlinking your account, removing roles granted by the Bot, or removing the Bot from the server.

## 7. Automated Actions Disclaimer

The Bot automatically modifies server roles and nicknames based on verification status and Guild Wars 2 guild membership data retrieved from ArenaNet's API. These changes happen without manual review. The Bot's operator is not responsible for:

- Delays, errors, or omissions in the Guild Wars 2 API data the Bot relies on.
- Role or nickname changes resulting from inaccurate information provided during `/verify` or `/forceverify`.
- Temporary unavailability of roster updates or verification due to Discord or Guild Wars 2 API outages.

## 8. No Warranty

The Bot is provided "as is" and "as available," without warranties of any kind, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, accuracy, or uninterrupted availability. Guild Wars 2 roster, item, and pricing data reflect ArenaNet's API at the time of the request and may be incomplete or delayed.

## 9. Limitation of Liability

To the fullest extent permitted by law, the Bot's operator is not liable for any indirect, incidental, or consequential damages arising from your use of, or inability to use, the Bot — including loss of roles, nickname changes, or reliance on roster or item data.

## 10. Termination

The Bot's operator may suspend or discontinue the Bot, for a specific server or generally, at any time and without notice. A server administrator may remove the Bot or its configuration (`/unsetup`) at any time.

## 11. Changes to These Terms

These Terms may be updated from time to time. Continued use of the Bot after changes take effect constitutes acceptance of the revised Terms. Material changes will be reflected by updating the "Last updated" date above.

## 12. Privacy

Use of the Bot is also governed by the [Privacy Policy](#), which explains what data is collected and how it is used and stored.

## 13. Contact

Questions about these Terms can be directed to:

**[kylepraias@gmail.com]**