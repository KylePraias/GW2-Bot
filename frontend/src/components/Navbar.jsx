import React, { useState, useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Menu, X, Swords } from "lucide-react";
import { DISCORD_INVITE_URL } from "../data";

const LINKS = [
  { to: "/", label: "Home" },
  { to: "/how-to-add", label: "How to Add" },
  { to: "/features", label: "Features" },
  { to: "/commands", label: "Commands" },
];

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const location = useLocation();

  useEffect(() => setOpen(false), [location.pathname]);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      data-testid="navbar"
      className={`fixed top-0 inset-x-0 z-50 transition-all duration-500 ${
        scrolled
          ? "bg-stone-950/80 backdrop-blur-xl border-b border-amber-500/10"
          : "bg-transparent border-b border-transparent"
      }`}
    >
      <nav className="max-w-7xl mx-auto px-6 lg:px-8 flex items-center justify-between h-20">
        <Link
          to="/"
          data-testid="nav-logo"
          className="flex items-center gap-3 group"
        >
          <span className="grid place-items-center w-10 h-10 rounded-md bg-red-900/80 border border-amber-500/30 ring-ember group-hover:bg-red-800 transition-colors">
            <Swords className="w-5 h-5 text-amber-400" />
          </span>
          <span className="font-cinzel text-lg tracking-widest text-stone-100">
            GW2 <span className="text-amber-500">ROSTER</span>
          </span>
        </Link>

        <div className="hidden md:flex items-center gap-9">
          {LINKS.map((l) => {
            const active = location.pathname === l.to;
            return (
              <Link
                key={l.to}
                to={l.to}
                data-testid={`nav-link-${l.label.toLowerCase().replace(/\s+/g, "-")}`}
                className={`font-cinzel text-sm tracking-widest uppercase transition-colors duration-300 ${
                  active
                    ? "text-amber-400"
                    : "text-stone-400 hover:text-amber-500"
                }`}
              >
                {l.label}
              </Link>
            );
          })}
          <a
            href={DISCORD_INVITE_URL}
            target="_blank"
            rel="noreferrer"
            data-testid="nav-add-bot-btn"
            className="font-cinzel text-sm tracking-widest uppercase px-5 py-2.5 rounded-md bg-red-900 hover:bg-red-800 text-amber-100 border border-amber-500/30 ring-ember transition-colors"
          >
            Add Bot
          </a>
        </div>

        <button
          data-testid="mobile-menu-toggle"
          className="md:hidden text-stone-200"
          onClick={() => setOpen((v) => !v)}
          aria-label="Toggle menu"
        >
          {open ? <X className="w-7 h-7" /> : <Menu className="w-7 h-7" />}
        </button>
      </nav>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="md:hidden overflow-hidden bg-stone-950/95 backdrop-blur-xl border-b border-amber-500/10"
          >
            <div className="px-6 py-6 flex flex-col gap-5">
              {LINKS.map((l) => (
                <Link
                  key={l.to}
                  to={l.to}
                  data-testid={`mobile-nav-link-${l.label.toLowerCase().replace(/\s+/g, "-")}`}
                  className="font-cinzel text-sm tracking-widest uppercase text-stone-300 hover:text-amber-500"
                >
                  {l.label}
                </Link>
              ))}
              <a
                href={DISCORD_INVITE_URL}
                target="_blank"
                rel="noreferrer"
                data-testid="mobile-nav-add-bot-btn"
                className="font-cinzel text-sm tracking-widest uppercase text-center px-5 py-3 rounded-md bg-red-900 text-amber-100 border border-amber-500/30"
              >
                Add Bot
              </a>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
