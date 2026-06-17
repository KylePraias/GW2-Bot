import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, ExternalLink } from "lucide-react";
import { SectionHeader, fade } from "../components/SectionHeader";
import { AddBotButton } from "../components/InviteButtons";

const FAQS = [
  {
    q: "How do I create a GW2 API key?",
    a: [
      "Sign in at the official Guild Wars 2 account page and open the “Applications” tab.",
      "Click “New Key”, give it a name (e.g. “Roster Bot”), and tick the guilds and account permissions.",
      "Click “Create API Key”, then copy the long key it generates — you'll paste it into /setup.",
      "Important: the key must belong to the guild leader, otherwise the bot can't read your full member roster.",
    ],
    link: {
      label: "Open the GW2 Applications page",
      url: "https://account.arena.net/applications",
    },
  },
  {
    q: "How do I find my GW2 Guild ID?",
    a: [
      "Your Guild ID is a UUID that looks like 116E0C0E-0035-44A9-BB22-4AE3E23127E5.",
      "Easiest way: visit the GW2 API guild search endpoint in your browser and type your guild name.",
      "Go to https://api.guildwars2.com/v2/guild/search?name=Your+Guild+Name (replace spaces with +).",
      "The browser returns the Guild ID in quotes — copy it and paste it into /setup as the gw2_guild_id.",
    ],
    link: {
      label: "Open the GW2 guild search endpoint",
      url: "https://api.guildwars2.com/v2/guild/search?name=",
    },
  },
  {
    q: "What permissions does the bot need?",
    a: [
      "The invite link already requests everything required: Manage Roles, Manage Nicknames, Manage Messages, Send Messages and Read Message History.",
      "Manage Roles lets it grant the Member role on verification. Manage Nicknames keeps names in sync with GW2 accounts.",
      "Manage Messages powers the channel auto-delete moderation feature.",
      "You can leave the default permissions checked when authorizing.",
    ],
    link: null,
  },
  {
    q: "Why does verification say my account isn't in the guild?",
    a: [
      "Use your full GW2 account name including the four numbers, e.g. PlayerName.1234.",
      "Make sure you're actually a current member of the guild the bot is configured for.",
      "The roster syncs every hour, so a very recent join may take a little while to appear.",
      "Names are case-sensitive — copy it exactly as it appears in-game.",
    ],
    link: null,
  },
  {
    q: "I need a “Member” role — how do I set it up?",
    a: [
      "In Server Settings → Roles, create a role named exactly Member (capital M).",
      "Drag the bot's own role above the Member role in the list so it has permission to assign it.",
      "Optionally create a Guest role too — the bot removes it automatically once a player verifies.",
    ],
    link: null,
  },
];

function FaqItem({ item, index, open, onToggle }) {
  return (
    <motion.div
      variants={fade}
      custom={index}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true }}
      data-testid={`faq-item-${index}`}
      className="rounded-lg bg-stone-900/60 backdrop-blur-md border border-amber-500/20 hover:border-amber-500/40 transition-colors overflow-hidden"
    >
      <button
        onClick={onToggle}
        data-testid={`faq-question-${index}`}
        className="w-full flex items-center justify-between gap-4 text-left px-6 py-5"
      >
        <span className="font-cinzel tracking-wide text-base sm:text-lg text-stone-100">
          {item.q}
        </span>
        <ChevronDown
          className={`w-5 h-5 shrink-0 text-amber-500 transition-transform duration-300 ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3 }}
            className="overflow-hidden"
          >
            <div
              data-testid={`faq-answer-${index}`}
              className="px-6 pb-6 pt-0 space-y-3 text-stone-400 font-light leading-relaxed"
            >
              {item.a.map((line, i) => (
                <p key={i} className="flex gap-3">
                  <span className="text-amber-500/70 select-none">›</span>
                  <span className="break-words">{line}</span>
                </p>
              ))}
              {item.link && (
                <a
                  href={item.link.url}
                  target="_blank"
                  rel="noreferrer"
                  data-testid={`faq-link-${index}`}
                  className="inline-flex items-center gap-2 mt-2 font-cinzel text-xs tracking-widest uppercase text-amber-400 hover:gap-3 transition-all"
                >
                  {item.link.label} <ExternalLink className="w-4 h-4" />
                </a>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export default function Faq() {
  const [open, setOpen] = useState(0);

  return (
    <div data-testid="faq-page" className="pt-32">
      <section className="max-w-4xl mx-auto px-6 lg:px-8 pb-28">
        <SectionHeader
          overline="Questions & Answers"
          title="Setup help & FAQ"
          sub="Everything you need to get your GW2 API key, find your Guild ID and run /setup without a hitch."
        />

        <div className="mt-14 space-y-4">
          {FAQS.map((item, i) => (
            <FaqItem
              key={item.q}
              item={item}
              index={i}
              open={open === i}
              onToggle={() => setOpen(open === i ? -1 : i)}
            />
          ))}
        </div>

        <motion.div
          variants={fade}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true }}
          className="mt-16 flex flex-col items-center gap-5 text-center"
        >
          <p className="text-stone-400 font-light">
            Got your API key and Guild ID ready?
          </p>
          <AddBotButton testId="faq-add-bot-btn" />
        </motion.div>
      </section>
    </div>
  );
}
