import React from "react";
import { motion } from "framer-motion";
import { Copy, Check, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { DISCORD_INVITE_URL } from "../data";

export function AddBotButton({ testId = "add-bot-btn", className = "" }) {
  return (
    <a
      href={DISCORD_INVITE_URL}
      target="_blank"
      rel="noreferrer"
      data-testid={testId}
      className={`group inline-flex items-center justify-center gap-3 px-8 py-4 rounded-md bg-red-900 hover:bg-red-800 text-amber-100 border border-amber-500/40 ring-ember font-cinzel text-sm tracking-widest uppercase transition-all duration-300 hover:-translate-y-0.5 ${className}`}
    >
      Add to Discord
      <ExternalLink className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
    </a>
  );
}

export function CopyInviteButton({ testId = "copy-invite-btn", className = "" }) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(DISCORD_INVITE_URL);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = DISCORD_INVITE_URL;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    setCopied(true);
    toast.success("Invite link copied to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      onClick={handleCopy}
      data-testid={testId}
      className={`group inline-flex items-center justify-center gap-3 px-8 py-4 rounded-md bg-transparent border border-amber-500/40 text-amber-400 hover:bg-amber-500/10 font-cinzel text-sm tracking-widest uppercase transition-all duration-300 hover:-translate-y-0.5 ${className}`}
    >
      {copied ? (
        <>
          <Check className="w-4 h-4" /> Copied
        </>
      ) : (
        <>
          <Copy className="w-4 h-4" /> Copy Invite URL
        </>
      )}
    </button>
  );
}

export function InviteUrlBox() {
  const [copied, setCopied] = React.useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(DISCORD_INVITE_URL);
    } catch {
      /* ignore */
    }
    setCopied(true);
    toast.success("Invite link copied to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      className="flex items-stretch gap-2 max-w-2xl"
    >
      <code
        data-testid="invite-url-text"
        className="flex-1 min-w-0 truncate font-mono text-xs sm:text-sm text-stone-400 bg-stone-900/70 border border-amber-500/20 rounded-md px-4 py-3"
      >
        {DISCORD_INVITE_URL}
      </code>
      <button
        onClick={handleCopy}
        data-testid="invite-url-copy-btn"
        aria-label="Copy invite URL"
        className="shrink-0 grid place-items-center w-12 rounded-md bg-red-900 hover:bg-red-800 text-amber-100 border border-amber-500/30 transition-colors"
      >
        {copied ? <Check className="w-5 h-5" /> : <Copy className="w-5 h-5" />}
      </button>
    </motion.div>
  );
}
