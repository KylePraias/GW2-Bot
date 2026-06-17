import React from "react";
import { motion } from "framer-motion";
import { MousePointerClick, ShieldCheck, Terminal, UserCheck } from "lucide-react";
import { SectionHeader, fade } from "../components/SectionHeader";
import { AddBotButton, InviteUrlBox } from "../components/InviteButtons";

const STEPS = [
  {
    icon: MousePointerClick,
    title: "Click “Add to Discord”",
    body: "Hit the button below (or copy the invite link). Discord opens the authorization screen in a new tab.",
  },
  {
    icon: ShieldCheck,
    title: "Authorize the permissions",
    body: "Pick the server you manage and approve the requested permissions — the bot needs to manage roles, nicknames and messages to do its job.",
  },
  {
    icon: UserCheck,
    title: "Create a “Member” role",
    body: "Make sure your server has a role named Member, and drag the bot’s own role above it so it can assign roles to verified players.",
  },
  {
    icon: Terminal,
    title: "Run /setup",
    body: "In your server, run /setup with your GW2 API key (guilds permission), your GW2 Guild ID and the channel for the roster. The first roster posts instantly and refreshes hourly.",
  },
];

export default function HowToAdd() {
  return (
    <div data-testid="how-to-add-page" className="pt-32">
      <section className="max-w-7xl mx-auto px-6 lg:px-8">
        <SectionHeader
          overline="Setup Guide"
          title="Add the bot in four steps"
          sub="It takes about two minutes. You'll need admin rights on your Discord server and a GW2 API key from the guild leader."
        />

        <div className="mt-10">
          <InviteUrlBox />
        </div>

        <div className="mt-20 relative">
          {/* vertical line */}
          <div className="absolute left-[27px] top-2 bottom-2 w-px bg-gradient-to-b from-amber-500/50 via-red-900/40 to-transparent md:left-[31px]" />

          <div className="space-y-10">
            {STEPS.map((s, i) => (
              <motion.div
                key={s.title}
                variants={fade}
                custom={i}
                initial="hidden"
                whileInView="show"
                viewport={{ once: true }}
                data-testid={`step-${i + 1}`}
                className="relative flex gap-6 md:gap-8"
              >
                <div className="relative z-10 shrink-0">
                  <span className="grid place-items-center w-14 h-14 md:w-16 md:h-16 rounded-full bg-stone-900 border border-amber-500/40 ring-ember">
                    <s.icon className="w-6 h-6 md:w-7 md:h-7 text-amber-400" />
                  </span>
                </div>
                <div className="flex-1 rounded-lg bg-stone-900/60 backdrop-blur-md border border-amber-500/20 p-7 hover:border-amber-500/50 transition-colors">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="font-cinzel text-xs tracking-[0.3em] text-amber-500">
                      STEP {String(i + 1).padStart(2, "0")}
                    </span>
                  </div>
                  <h3 className="font-display font-bold text-2xl text-stone-100">
                    {s.title}
                  </h3>
                  <p className="mt-3 text-stone-400 font-light leading-relaxed">
                    {s.body}
                  </p>
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        <motion.div
          variants={fade}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true }}
          className="my-20 rounded-xl border border-amber-500/25 bg-stone-900/50 p-8 lg:p-10"
        >
          <h3 className="font-cinzel tracking-widest text-amber-500 text-sm uppercase">
            Before you start — checklist
          </h3>
          <ul className="mt-5 grid sm:grid-cols-2 gap-x-10 gap-y-3 text-stone-300 font-light">
            <li className="flex gap-3">
              <span className="text-amber-500">›</span> Administrator access on
              your Discord server
            </li>
            <li className="flex gap-3">
              <span className="text-amber-500">›</span> A GW2 API key with{" "}
              <code className="font-mono text-amber-400">guilds</code> &{" "}
              <code className="font-mono text-amber-400">account</code>{" "}
              permissions
            </li>
            <li className="flex gap-3">
              <span className="text-amber-500">›</span> Your GW2 Guild ID (UUID)
            </li>
            <li className="flex gap-3">
              <span className="text-amber-500">›</span> A role named{" "}
              <code className="font-mono text-amber-400">Member</code> in your
              server
            </li>
          </ul>
          <p className="mt-6 text-stone-500 text-sm font-light">
            The API key must belong to the guild leader so the bot can read your
            full roster.
          </p>
        </motion.div>

        <div className="pb-28 flex justify-center">
          <AddBotButton testId="howto-add-bot-btn" />
        </div>
      </section>
    </div>
  );
}
