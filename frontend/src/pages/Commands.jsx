import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Shield, ShieldAlert } from "lucide-react";
import { SectionHeader, fade } from "../components/SectionHeader";
import { MEMBER_COMMANDS, ADMIN_COMMANDS } from "../data";

function CommandRow({ cmd, i, admin }) {
  return (
    <motion.div
      variants={fade}
      custom={i}
      initial="hidden"
      animate="show"
      data-testid={`command-${cmd.name.replace("/", "")}`}
      className="group rounded-lg bg-stone-900/60 backdrop-blur-md border border-amber-500/20 hover:border-amber-500/50 p-6 transition-all duration-300 hover:shadow-[0_0_24px_rgba(153,27,27,0.15)]"
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <code className="font-mono text-lg text-amber-500 group-hover:text-amber-400 transition-colors">
          {cmd.name}
        </code>
        {cmd.args && (
          <code className="font-mono text-sm text-stone-500">
            {cmd.args
              .split(" ")
              .map((a) => `<${a}>`)
              .join(" ")}
          </code>
        )}
        {admin && (
          <span className="ml-auto font-cinzel text-[10px] tracking-[0.25em] uppercase text-red-400/80 border border-red-900/60 rounded px-2 py-0.5">
            Admin
          </span>
        )}
      </div>
      <p className="mt-3 text-stone-400 font-light leading-relaxed">
        {cmd.desc}
      </p>
    </motion.div>
  );
}

export default function Commands() {
  const [tab, setTab] = useState("member");
  const tabs = [
    { id: "member", label: "Member", icon: Shield, count: MEMBER_COMMANDS.length },
    { id: "admin", label: "Admin", icon: ShieldAlert, count: ADMIN_COMMANDS.length },
  ];
  const list = tab === "member" ? MEMBER_COMMANDS : ADMIN_COMMANDS;

  return (
    <div data-testid="commands-page" className="pt-32">
      <section className="max-w-5xl mx-auto px-6 lg:px-8 pb-28">
        <SectionHeader
          overline="Reference"
          title="Complete command list"
          sub="Twelve slash commands. Member commands are open to everyone; admin commands require server administrator permissions."
        />

        {/* Tabs */}
        <div
          data-testid="commands-tabs"
          className="mt-14 inline-flex p-1.5 rounded-lg bg-stone-900/70 border border-amber-500/20"
        >
          {tabs.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                data-testid={`tab-${t.id}`}
                className={`relative flex items-center gap-2 px-5 sm:px-7 py-2.5 rounded-md font-cinzel text-xs tracking-widest uppercase transition-colors ${
                  active ? "text-amber-100" : "text-stone-400 hover:text-amber-400"
                }`}
              >
                {active && (
                  <motion.span
                    layoutId="tab-pill"
                    className="absolute inset-0 rounded-md bg-red-900 border border-amber-500/30"
                    transition={{ type: "spring", stiffness: 400, damping: 32 }}
                  />
                )}
                <t.icon className="relative w-4 h-4" />
                <span className="relative">{t.label}</span>
                <span className="relative text-amber-500/80">({t.count})</span>
              </button>
            );
          })}
        </div>

        <div className="mt-10">
          <AnimatePresence mode="wait">
            <motion.div
              key={tab}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.3 }}
              className="grid grid-cols-1 gap-4"
            >
              {list.map((cmd, i) => (
                <CommandRow
                  key={cmd.name}
                  cmd={cmd}
                  i={i}
                  admin={tab === "admin"}
                />
              ))}
            </motion.div>
          </AnimatePresence>
        </div>

        <p className="mt-10 text-stone-500 text-sm font-light text-center">
          Arguments shown in{" "}
          <code className="font-mono text-stone-400">&lt;angle brackets&gt;</code>{" "}
          are filled in by Discord when you run the command.
        </p>
      </section>
    </div>
  );
}
