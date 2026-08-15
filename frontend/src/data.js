import {
  Scroll,
  ShieldCheck,
  Users,
  Search,
  Trash2,
  Database,
  Flag,
  Gift,
} from "lucide-react";

export const DISCORD_INVITE_URL =
  "https://discord.com/oauth2/authorize?client_id=1513337321284702430&permissions=402761728&integration_type=0&scope=bot+applications.commands";

export const GITHUB_URL = "https://github.com/KylePraias/GW2-Bot";

export const FEATURES = [
  {
    icon: Scroll,
    title: "Live Guild Roster",
    desc: "Posts your full guild roster to a channel and silently re-edits it every 10 minutes. Tracks join dates, recently-left members (shown for 24 hours), and splits across multiple messages for large guilds — with a one-click .txt export.",
    span: "lg:col-span-2",
  },
  {
    icon: ShieldCheck,
    title: "Rank-Based Verification",
    desc: "Members link their GW2 account name and instantly receive their exact guild rank role — Leader, Officer, Content Officer, Founding Member, or Member — validated live against the API. Rank roles stay in sync automatically as members are promoted or leave.",
    span: "",
  },
  {
    icon: Users,
    title: "Multiple Accounts",
    desc: "One Discord user can link several GW2 accounts. The bot assigns the highest rank held across all accounts and combines them in the nickname automatically.",
    span: "",
  },
  {
    icon: Search,
    title: "Item Search + Trading Post",
    desc: "Search any GW2 item with live autocomplete. See stats, rarity, vendor value and real-time trading post buy & sell prices, with a direct wiki link.",
    span: "lg:col-span-2",
  },
  {
    icon: Flag,
    title: "Anonymous Report System",
    desc: "Post a persistent report button in any channel. Members submit categorised reports or feedback — anonymously or with their nickname — which are forwarded to a private staff channel.",
    span: "",
  },
  {
    icon: Gift,
    title: "Guild Bank Giveaways",
    desc: "Run donation-based giveaways tracked against the guild bank log. Set a minimum gold donation, let the bot maintain the live entry list, then announce randomly drawn winners at any time.",
    span: "",
  },
  {
    icon: Trash2,
    title: "Channel Auto-Moderation",
    desc: "Lock channels down to bot commands only — any stray message is deleted instantly.",
    span: "",
  },
  {
    icon: Database,
    title: "Persistent Config",
    desc: "Every setting, link and verification survives redeploys via a persistent volume. Set it once and forget it.",
    span: "lg:col-span-2",
  },
];

export const MEMBER_COMMANDS = [
  {
    name: "/verify",
    args: "gw2_username",
    desc: "Link your GW2 account and receive your guild rank role. Your nickname is set automatically.",
  },
  {
    name: "/item",
    args: "name",
    desc: "Search for any GW2 item and view its stats, rarity and live trading post prices. Requires a verified guild rank role.",
  },
  {
    name: "/viewmyaccounts",
    args: "",
    desc: "View every GW2 account currently linked to your Discord profile.",
  },
];

export const ADMIN_COMMANDS = [
  {
    name: "/setup",
    args: "api_key gw2_guild_id channel guest_role",
    desc: "Configure the bot for this server, set the guest role to remove on verification, and post the first live roster.",
  },
  {
    name: "/unsetup",
    args: "",
    desc: "Remove the bot configuration for this server.",
  },
  {
    name: "/viewsetup",
    args: "",
    desc: "View a full summary of the bot's current configuration, including roster channel, autodelete channels, and report setup.",
  },
  {
    name: "/forceverify",
    args: "member gw2_username",
    desc: "Manually verify a Discord user as a guild member and assign their rank role.",
  },
  {
    name: "/unlink",
    args: "member gw2_username",
    desc: "Unlink one specific GW2 account from a Discord user.",
  },
  {
    name: "/unlinkall",
    args: "member",
    desc: "Unlink every GW2 account from a Discord user.",
  },
  {
    name: "/viewaccounts",
    args: "member",
    desc: "View all GW2 accounts linked to a given Discord user.",
  },
  {
    name: "/setupautodelete",
    args: "channel",
    desc: "Add a channel to the auto-delete list (bot commands only — all other messages are deleted instantly).",
  },
  {
    name: "/removeautodelete",
    args: "channel",
    desc: "Remove a channel from the auto-delete list.",
  },
  {
    name: "/viewautodelete",
    args: "",
    desc: "View every channel currently on the auto-delete list.",
  },
  {
    name: "/setupreport",
    args: "button_channel report_channel",
    desc: "Post a persistent report button in a channel. Submitted reports and feedback are forwarded to the specified staff channel.",
  },
  {
    name: "/viewreport",
    args: "",
    desc: "View the current report system configuration.",
  },
  {
    name: "/removereport",
    args: "",
    desc: "Remove the report system and delete the button message.",
  },
  {
    name: "/setupgiveaway",
    args: "channel name min_gold",
    desc: "Create a new giveaway tracked by guild bank donations. Members who donate the minimum gold are added to the entry list automatically every 10 minutes.",
  },
  {
    name: "/announcewinner",
    args: "name num_winners",
    desc: "Close a giveaway and randomly draw the specified number of winners, announcing them in the giveaway channel.",
  },
  {
    name: "/removegiveaway",
    args: "name",
    desc: "Delete a giveaway and remove its entry list message from the channel.",
  },
];