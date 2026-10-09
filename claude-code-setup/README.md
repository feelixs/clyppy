# CLYPPY Self-Host Setup — Instructions for Claude Code

You (Claude Code, or any coding agent) are reading this because a user asked you to
help them self-host the CLYPPY Discord bot from this repository. This file tells you
everything you need: the setup steps, which parts only the human can do, and a
debugging knowledge base collected from real self-host support cases.

## What you're setting up

A standalone CLYPPY instance in **self-host mode** (`CONTRIB_INSTANCE=1`). In this
mode the bot runs entirely without CLYPPY's private infrastructure:

- All calls to the hosted APIs (clyppy.io) are **stubbed** — the bot logs
  `[CONTRIB MODE] Would call ...` instead of calling them. Those log lines are
  **normal**, not errors.
- Videos small enough for Discord's bot upload limit (20 MiB, more on boosted
  servers) are uploaded directly to Discord.
- Redirect-capable platforms (TikTok, Instagram, Twitter/X, …) work at any size —
  the bot sends a resolver/CDN link that Discord embeds natively.
- Oversized non-redirect videos get a friendly "too large" message. There is no CDN.
- VIP tokens, `/backup`, `/download`, and clip analytics are unavailable/stubbed.

## Setup checklist

Work through these in order. Steps marked **(human)** need the user to do something
in a browser you may not have access to — give them exact instructions and wait.

1. **Prerequisites** — verify Docker is installed and running (`docker version`).
   On Windows, Docker Desktop with the WSL 2 backend is the normal setup.

2. **Create the env file:**
   ```bash
   cp .env.example .env
   ```
   Set `CLYPP_TOKEN` (next step) and keep `CONTRIB_INSTANCE=1`. Everything else is
   optional. Never commit `.env`.

3. **(human) Create the Discord application** at
   https://discord.com/developers/applications → New Application. In the **Bot**
   tab: click *Reset Token*, copy the token into `.env` as `CLYPP_TOKEN`.

4. **(human) Enable the Message Content intent** — Bot tab → *Privileged Gateway
   Intents* → toggle **Message Content Intent** ON. Without it the bot exits on
   startup with `You have requested privileged intents that have not been enabled
   or approved`.

5. **(human) Invite the bot** — in the *Installation* tab, add the `bot` scope with
   these permissions (or just Administrator to rule out permission issues):
   - Send Messages
   - Embed Links
   - Attach Files
   - **Read Message History** ← easy to miss; without it auto-embed silently
     ignores every message (see debugging section)
   - Send Messages in Threads

6. **(optional but strongly recommended) Cookies for YouTube** — follow
   [selfhost/README.md](../selfhost/README.md): export a Netscape-format
   `cookies.txt` from a logged-in browser profile and drop it at
   `selfhost/cookies/cookies.txt`. Without cookies, expect intermittent YouTube
   "Sign in to confirm you're not a bot" failures. **Never commit a real
   cookies.txt** — the folder is gitignored; treat the file like a session token.

7. **Start it:**
   ```bash
   docker compose -f selfhost/docker-compose.yml up -d --build
   ```
   This starts the bot plus `bgutil-provider` (generates YouTube PO tokens; the
   yt-dlp plugin in requirements.txt auto-connects to it on `127.0.0.1:4416`).

8. **Verify:** `docker compose -f selfhost/docker-compose.yml logs -f bot` should
   show a successful gateway connection. In Discord, `/help` should respond, and
   posting a TikTok link in a channel the bot can see should produce an embed
   reply within a few seconds.

## Debugging knowledge base

Real failure modes from self-host support, most common first. General rule: find
the container with `docker ps` (don't reuse stale container IDs — a rebuild changes
them), then `docker logs <id>`.

### Bot won't start / exits immediately
- `You have requested privileged intents...` → Message Content Intent toggle
  (checklist step 4).
- `401 Unauthorized` from the gateway → bad/reset token in `.env`. Remember the
  Dockerfile copies `.env` at **build** time — after editing `.env`, rebuild
  (`up -d --build`), not just restart.

### Slash commands work, but posting links does nothing (auto-embed silent)
Auto-embed ("quickembed") exits **silently by design** when a precondition fails —
check these in order:

1. **Channel permissions.** The handler returns early if the bot lacks
   `Embed Links`, `Send Messages`, `Read Message History`, or (in threads)
   `Send Messages in Threads` in that specific channel. `Read Message History`
   is the one people usually miss. Check the channel-level overrides too, not
   just the server role.
2. **Platform not enabled for quickembed.** Default-enabled platforms are
   **Instagram, TikTok, Twitch, Kick, Medal** only. Others (YouTube, Twitter/X,
   Reddit, …) must be enabled per server with `/settings` → quickembed platforms
   (`all` enables everything). `/embed <url>` works for every platform regardless.
3. **The download actually failed.** Failures in the pipeline are logged but not
   posted to the channel. Grep the logs:
   ```bash
   docker logs <id> 2>&1 | grep -E "Error in AutoEmbed|Error in processing this clip link|provider.*returned"
   ```

### TikTok embeds fail
TikTok is resolved through third-party "fixer" providers (`tnktok.com`, then
`www.tikwm.com`) — the bot probes them in order and uses the first that 302s to a
TikTok CDN URL. If the logs show `all N providers failed`:
- For **one specific video**: the video is usually dead/private/region-locked —
  verify the link opens in a browser.
- For **every video**: it's a provider outage. These services come and go; check
  whether `curl -sI "https://tnktok.com/generate/video/<video_id>.mp4"` returns a
  302 from another network. New providers can be added to `_TIKTOK_PROVIDERS` in
  `bot/platforms/tiktok.py`.

### YouTube: "Sign in to confirm you're not a bot"
- Make sure `bgutil-provider` is running (`docker ps`) and the bot log doesn't
  complain about reaching `127.0.0.1:4416`. Both services use host networking;
  on Docker Desktop (Windows/macOS) host networking needs to be enabled in
  *Settings → Resources → Network* on older versions.
- Add or refresh cookies (checklist step 6). Cookies expire — if YouTube worked
  and then stopped, re-export `cookies.txt` and restart the bot container.
- Export cookies from a browser profile you **don't keep using** — active browsing
  rotates the session tokens and invalidates the export faster.

### "Too large" errors
Discord's bot upload cap is 20 MiB (50 MiB at server boost tier 2, 100 MiB at
tier 3 — detected automatically). In self-host mode, non-redirect videos above the
cap are rejected with a friendly message; that's intended behavior, not a bug.

### Settings don't persist across restarts
Guild settings live in `guild_settings.db` under the `data/` directory, which the
compose file mounts as a volume (`../data:/app/data`). If you run the container
manually without that volume, settings reset on every restart.

### Where things live
| What | Where |
|---|---|
| Compose file (bot + PO-token provider) | `selfhost/docker-compose.yml` |
| Cookie docs & folder | `selfhost/README.md`, `selfhost/cookies/` |
| Guild settings DB (persistent volume) | `data/guild_settings.db` |
| Platform handlers (one per site) | `bot/platforms/*.py` |
| Auto-embed pipeline | `bot/tools/embedder.py` |
| API stubs / contrib-mode gates | `bot/io/io.py` (`is_contrib_instance`) |

## Scope notes for the agent

- Everything here targets **self-host mode only**. The hosted CLYPPY bot has been
  discontinued; the hosted APIs return `410 Gone`. If the user's instance tries to
  call clyppy.io for real, `CONTRIB_INSTANCE` isn't set to `1` — fix that first.
- Don't ask the user for, or try to configure, any CLYPPY API keys, CDN
  credentials, or webhook IDs — self-host mode needs none of them. Optional `.env`
  entries you can safely ignore unless the user wants them: logging webhooks.
- Treat `cookies.txt` and the bot token as secrets: never print their contents,
  commit them, or send them anywhere.
