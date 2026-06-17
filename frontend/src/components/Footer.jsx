import React from "react";
import { Link } from "react-router-dom";
import { Github, Swords } from "lucide-react";
import { GITHUB_URL, DISCORD_INVITE_URL } from "../data";

export default function Footer() {
  return (
    <footer
      data-testid="footer"
      className="border-t border-red-900/30 bg-stone-950"
    >
      <div className="max-w-7xl mx-auto px-6 lg:px-8 py-14">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-8">
          <div className="flex items-center gap-3">
            <span className="grid place-items-center w-10 h-10 rounded-md bg-red-900/80 border border-amber-500/30">
              <Swords className="w-5 h-5 text-amber-400" />
            </span>
            <div>
              <p className="font-cinzel tracking-widest text-stone-100">
                GW2 ROSTER BOT
              </p>
              <p className="text-stone-500 text-sm font-light">
                Automate your Guild Wars 2 Discord community.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              data-testid="footer-github-link"
              className="inline-flex items-center gap-2 px-5 py-3 rounded-md border border-amber-500/30 text-stone-200 hover:text-amber-400 hover:border-amber-500/60 transition-colors font-cinzel text-xs tracking-widest uppercase"
            >
              <Github className="w-4 h-4" />
              View on GitHub
            </a>
            <a
              href={DISCORD_INVITE_URL}
              target="_blank"
              rel="noreferrer"
              data-testid="footer-add-bot-link"
              className="inline-flex items-center gap-2 px-5 py-3 rounded-md bg-red-900 hover:bg-red-800 text-amber-100 border border-amber-500/30 transition-colors font-cinzel text-xs tracking-widest uppercase"
            >
              Add to Discord
            </a>
          </div>
        </div>

        <div className="mt-10 h-px gold-rule opacity-40" />

        <div className="mt-8 flex flex-col sm:flex-row gap-4 sm:items-center sm:justify-between text-stone-500 text-sm font-light">
          <nav className="flex flex-wrap gap-6">
            <Link to="/how-to-add" className="hover:text-amber-500 transition-colors">
              How to Add
            </Link>
            <Link to="/features" className="hover:text-amber-500 transition-colors">
              Features
            </Link>
            <Link to="/commands" className="hover:text-amber-500 transition-colors">
              Commands
            </Link>
          </nav>
          <p>
            Not affiliated with ArenaNet. Guild Wars 2 is a trademark of
            ArenaNet, LLC.
          </p>
        </div>
      </div>
    </footer>
  );
}
