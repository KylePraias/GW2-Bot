import React from "react";
import { motion } from "framer-motion";
import { SectionHeader, fade } from "../components/SectionHeader";
import { AddBotButton } from "../components/InviteButtons";
import { FEATURES } from "../data";

export default function Features() {
  return (
    <div data-testid="features-page" className="pt-32">
      <section className="max-w-7xl mx-auto px-6 lg:px-8">
        <SectionHeader
          overline="Capabilities"
          title="Built for Guild Wars 2 guilds"
          sub="Every feature talks directly to the official GW2 API, so what your members see is always live and accurate."
        />

        <div className="mt-16 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {FEATURES.map((f, i) => (
            <motion.div
              key={f.title}
              variants={fade}
              custom={i}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true }}
              data-testid={`feature-card-${i}`}
              className={`group relative overflow-hidden rounded-lg bg-stone-900/60 backdrop-blur-md border border-amber-500/20 hover:border-amber-500/50 p-8 transition-all duration-300 hover:shadow-[0_0_34px_rgba(153,27,27,0.18)] ${f.span}`}
            >
              <div className="absolute -right-10 -top-10 w-32 h-32 rounded-full bg-red-900/20 blur-2xl opacity-0 group-hover:opacity-100 transition-opacity" />
              <span className="relative grid place-items-center w-12 h-12 rounded-md bg-red-900/60 border border-amber-500/30 mb-6 group-hover:bg-red-800/70 transition-colors">
                <f.icon className="w-6 h-6 text-amber-400" />
              </span>
              <h3 className="relative font-cinzel tracking-wide text-xl text-stone-100">
                {f.title}
              </h3>
              <p className="relative mt-3 text-stone-400 font-light leading-relaxed">
                {f.desc}
              </p>
            </motion.div>
          ))}
        </div>

        <div className="my-24 flex flex-col items-center gap-6 text-center">
          <h3 className="font-display font-bold text-3xl sm:text-4xl text-stone-100">
            All of this, free and self-hostable
          </h3>
          <p className="text-stone-400 font-light max-w-xl">
            The bot is open source and stores its config on a persistent volume,
            so your data survives every redeploy.
          </p>
          <AddBotButton testId="features-add-bot-btn" />
        </div>
      </section>
    </div>
  );
}
