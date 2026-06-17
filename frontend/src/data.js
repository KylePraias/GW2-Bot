import {
  Scroll,
  ShieldCheck,
  Users,
  Search,
  Trash2,
  Database,
} from "lucide-react";

export const DISCORD_INVITE_URL =
  "https://discord.com/oauth2/authorize?client_id=1513337321284702430&permissions=402761728&integration_type=0&scope=bot+applications.commands";

export const GITHUB_URL = "https://github.com/KylePraias/GW2-Bot";

export const FEATURES = [
  {
    icon: Scroll,
    title: "Live Guild Roster",
    desc: "Posts your full guild roster to a channel and silently re-edits it every hour on the hour. Splits across multiple messages when your guild gets large, with a one-click .txt export.",
    span: "lg:col-span-2",
  },
  {
    icon: ShieldCheck,
    title: "Account Verification",
    desc: "Members link their GW2 account name, instantly receive the Member role and get their nickname set to match — all validated against the live API roster.",
    span: "",
  },
  {
    icon: Users,
    title: "Multiple Accounts",
    desc: "One Discord user can link several GW2 accounts. Nicknames combine them automatically.",
    span: "",
  },
  {
    icon: Search,
    title: "Item Search + Trading Post",
    desc: "Search any GW2 item with live autocomplete. See stats, rarity, vendor value and real-time trading post buy & sell prices, with a direct wiki link.",
    span: "lg:col-span-2",
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
    desc: "Link your GW2 account and receive the Member role. Your nickname is set automatically.",
  },
  {
    name: "/item",
    args: "name",
    desc: "Search for any GW2 item and view its stats, rarity and live trading post prices. Requires the verified Member role.",
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
    args: "api_key gw2_guild_id channel",
    desc: "Configure the bot for this server and post the first live roster.",
  },
  {
    name: "/unsetup",
    args: "",
    desc: "Remove the bot configuration for this server.",
  },
  {
    name: "/forceverify",
    args: "member gw2_username",
    desc: "Manually verify a Discord user as a guild member.",
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
    name: "/addautodelete",
    args: "channel",
    desc: "Add a channel to the auto-delete list (bot commands only).",
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
];
