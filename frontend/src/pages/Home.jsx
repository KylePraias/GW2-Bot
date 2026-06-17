import React from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { Scroll, ShieldCheck, Search, ArrowRight } from "lucide-react";
import { AddBotButton, CopyInviteButton } from "../components/InviteButtons";
import { SectionHeader, fade } from "../components/SectionHeader";

const HERO_BG =
  "https://images.unsplash.com/photo-1709137405692-374c12e36609?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjAzNDR8MHwxfHNlYXJjaHwyfHxmYW50YXN5JTIwbGFuZHNjYXBlJTIwZGFya3xlbnwwfHx8fDE3ODE2NzIwOTl8MA&ixlib=rb-4.1.0&q=85";

const TEASERS = [
  {
    icon: Scroll,
    title: "Live Roster",
    desc: "Your full guild roster, posted and refreshed every hour automatically.",
  },
  {
    icon: ShieldCheck,
    title: "Verify Members",
    desc: "Members link their GW2 account to earn roles and matching nicknames.",
  },
  {
    icon: Search,
    title: "Item Lookup",
    desc: "Search any item with live trading post buy & sell prices.",
  },
];

export default function Home() {
  return (
    <div data-testid="home-page">
      {/* HERO */}
      <section className="relative min-h-[92vh] flex items-center overflow-hidden">
        <div className="absolute inset-0">
          <img
            src={HERO_BG}
            alt=""
            className="w-full h-full object-cover animate-slow-zoom"
          />
          <div className="absolute inset-0 bg-gradient-to-b from-black/70 via-stone-950/85 to-stone-950" />
          <div className="absolute inset-0 bg-gradient-to-r from-red-950/40 via-transparent to-transparent" />
          <div className="absolute inset-0 parchment-grain opacity-40" />
        </div>

        <div className="relative max-w-7xl mx-auto px-6 lg:px-8 w-full pt-28">
          <motion.p
            variants={fade}
            initial="hidden"
            animate="show"
            className="font-cinzel text-xs sm:text-sm tracking-[0.35em] uppercase text-amber-500 mb-6"
          >
            A Guild Wars 2 Discord Companion
          </motion.p>
          <motion.h1
            variants={fade}
            custom={1}
            initial="hidden"
            animate="show"
            className="font-display font-bold text-6xl sm:text-7xl lg:text-8xl text-stone-50 leading-[0.95] max-w-4xl text-glow-ember"
          >
            GW2 Roster Bot
          </motion.h1>
          <motion.p
            variants={fade}
            custom={2}
            initial="hidden"
            animate="show"
            className="mt-7 text-xl sm:text-2xl text-stone-300 font-light max-w-2xl leading-relaxed"
          >
            Automate your Guild Wars 2 Discord community — live rosters, account
            verification, item lookups and channel moderation, all in one bot.
          </motion.p>

          <motion.div
            variants={fade}
            custom={3}
            initial="hidden"
            animate="show"
            className="mt-10 flex flex-col sm:flex-row gap-4"
          >
            <AddBotButton testId="hero-add-bot-btn" />
            <CopyInviteButton testId="hero-copy-invite-btn" />
          </motion.div>

          <motion.div
            variants={fade}
            custom={4}
            initial="hidden"
            animate="show"
            className="mt-10 flex items-center gap-6 text-stone-500 text-sm font-light"
          >
            <span className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-flicker" />
              Built on the official GW2 API
            </span>
            <span className="hidden sm:inline">12 slash commands</span>
          </motion.div>
        </div>
      </section>

      {/* TEASER */}
      <section className="relative max-w-7xl mx-auto px-6 lg:px-8 py-24 lg:py-32">
        <SectionHeader
          overline="What it does"
          title="Everything your guild needs, on autopilot"
          sub="Set it up once and the bot quietly keeps your community organised — no spreadsheets, no manual updates."
        />

        <div className="mt-16 grid grid-cols-1 md:grid-cols-3 gap-6">
          {TEASERS.map((t, i) => (
            <motion.div
              key={t.title}
              variants={fade}
              custom={i}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true }}
              data-testid={`teaser-card-${i}`}
              className="group rounded-lg bg-stone-900/60 backdrop-blur-md border border-amber-500/20 hover:border-amber-500/50 p-8 transition-all duration-300 hover:shadow-[0_0_30px_rgba(153,27,27,0.18)]"
            >
              <span className="grid place-items-center w-12 h-12 rounded-md bg-red-900/60 border border-amber-500/30 mb-6 group-hover:bg-red-800/70 transition-colors">
                <t.icon className="w-6 h-6 text-amber-400" />
              </span>
              <h3 className="font-cinzel tracking-wide text-xl text-stone-100">
                {t.title}
              </h3>
              <p className="mt-3 text-stone-400 font-light leading-relaxed">
                {t.desc}
              </p>
            </motion.div>
          ))}
        </div>

        <div className="mt-12 flex flex-wrap gap-6">
          <Link
            to="/features"
            data-testid="home-features-link"
            className="inline-flex items-center gap-2 font-cinzel text-sm tracking-widest uppercase text-amber-400 hover:gap-3 transition-all"
          >
            Explore all features <ArrowRight className="w-4 h-4" />
          </Link>
          <Link
            to="/commands"
            data-testid="home-commands-link"
            className="inline-flex items-center gap-2 font-cinzel text-sm tracking-widest uppercase text-stone-400 hover:text-amber-400 hover:gap-3 transition-all"
          >
            Browse commands <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </section>

      {/* CTA */}
      <section className="relative max-w-7xl mx-auto px-6 lg:px-8 pb-28">
        <motion.div
          variants={fade}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true }}
          className="relative overflow-hidden rounded-2xl border border-amber-500/25 bg-gradient-to-br from-red-950/60 via-stone-900/80 to-stone-950 p-12 lg:p-20 text-center"
        >
          <div className="absolute inset-0 parchment-grain opacity-30" />
          <div className="relative">
            <h2 className="font-display font-bold text-4xl sm:text-5xl text-stone-50">
              Ready to rally your guild?
            </h2>
            <p className="mt-5 text-stone-400 font-light text-lg max-w-xl mx-auto">
              Add GW2 Roster Bot in seconds, then run{" "}
              <code className="font-mono text-amber-400">/setup</code> in your
              server to go live.
            </p>
            <div className="mt-9 flex flex-col sm:flex-row gap-4 justify-center">
              <AddBotButton testId="cta-add-bot-btn" />
              <Link
                to="/how-to-add"
                data-testid="cta-how-to-link"
                className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-md border border-amber-500/40 text-amber-400 hover:bg-amber-500/10 font-cinzel text-sm tracking-widest uppercase transition-all"
              >
                Read the setup guide
              </Link>
            </div>
          </div>
        </motion.div>
      </section>
    </div>
  );
}
