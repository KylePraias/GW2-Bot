# GW2 Roster Bot — Marketing Site PRD

## Problem Statement
Build a small multi-page website for users of a Guild Wars 2 Discord bot. It must explain how to add the bot (including the invite URL), summarise what the bot does, and list all commands.

Discord invite URL: https://discord.com/oauth2/authorize?client_id=1513337321284702430&permissions=402761728&integration_type=0&scope=bot+applications.commands
GitHub repo: https://github.com/KylePraias/GW2-Bot

## Source of Truth
The Python bot lives in `/app/bot.py` (discord.py). Site content (features + commands) is derived from it and stored in `/app/frontend/src/data.js`.

## Tech Stack
- Frontend: React 18 (CRA) + react-router-dom v6 + TailwindCSS + Framer Motion + lucide-react + sonner
- Backend: minimal FastAPI (`/api/health`) — site is static, no DB used
- Theme: Dark, Guild Wars 2-inspired (stone-950 base, red-900 ember brand, amber-500 gold). Fonts: Cormorant Garamond (display), Cinzel (labels), Outfit (body), JetBrains Mono (commands).

## User Choices
- Bot name on site: "GW2 Roster Bot"
- Dark GW2-inspired visual style
- Multiple pages
- Copy-to-clipboard invite + big "Add to Discord" button
- GitHub link in footer

## Implemented (2026-06-17)
- Home: cinematic hero, Add to Discord + Copy Invite URL buttons, feature teasers, CTA
- How to Add (`/how-to-add`): invite URL box w/ copy, 4-step setup timeline, prerequisites checklist
- Features (`/features`): bento grid of 6 capabilities
- Commands (`/commands`): Member (3) / Admin (9) tabs, 12 commands total
- Global sticky navbar (responsive + mobile menu) and footer with GitHub + trademark disclaimer
- All interactive elements have data-testid; toasts via sonner

## Backlog / P1-P2
- Optional: command search/filter on Commands page
- Optional: live preview screenshots/GIFs of the bot in action
- Optional: FAQ page (API key help, GW2 Guild ID lookup)

## Next Action Items
- Optional formal QA pass via testing agent
- Consider deployment when user is ready
