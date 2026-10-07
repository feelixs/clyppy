# Self-Hosting CLYPPY

Run your own standalone CLYPPY instance. See the main [README](../README.md#-self-hosting)
for what self-host mode does and doesn't support.

## Quick start

```bash
cp .env.example .env        # fill in CLYPP_TOKEN; keep CONTRIB_INSTANCE=1
docker compose -f selfhost/docker-compose.yml up -d --build
```

This starts two containers:

- **bot** — the CLYPPY bot itself, built from the repo's `Dockerfile`
- **bgutil-provider** — [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
  which generates YouTube "PO tokens" so yt-dlp can keep downloading YouTube
  without being flagged as a bot. The client plugin ships in `requirements.txt`
  and auto-connects to it on `127.0.0.1:4416` (that's why both services use
  host networking). You can run the bot without it, but expect more YouTube
  "Sign in to confirm you're not a bot" failures.

## Cookies (optional, strongly recommended for YouTube)

Some platforms serve certain videos only to logged-in users — most notably
YouTube ("Sign in to confirm you're not a bot"), plus age-gated or otherwise
restricted content. yt-dlp can present *your* browser session's cookies to get
through, and the bot picks them up automatically.

### 1. Export cookies from your browser

Export a **Netscape-format `cookies.txt`** from a browser profile that's
logged in to YouTube. Two easy options:

- A browser extension such as **"Get cookies.txt LOCALLY"** (Chrome/Firefox) —
  export while on youtube.com.
- yt-dlp itself, from a machine with Firefox installed:
  ```bash
  yt-dlp --cookies-from-browser firefox --cookies cookies.txt --skip-download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  ```

> **Tip:** yt-dlp's wiki recommends exporting from a browser profile you don't
> actively use afterward (e.g. a dedicated Firefox profile or a throwaway
> Google account) — active browsing rotates the session tokens and invalidates
> the exported file faster.

### 2. Give the file to the bot

Drop it at:

```
selfhost/cookies/cookies.txt
```

The compose file mounts `selfhost/cookies/` read-only into the container and
points `COOKIE_FILE` at it. On each download the bot hands yt-dlp a throwaway
copy (YouTube gets a dedicated writable copy inside the container), so your
original file is never modified. If the folder is empty, the bot just runs
cookie-less.

This folder is gitignored — **never commit a real cookies.txt**, it's
equivalent to your logged-in session.

### Alternative: mount a Firefox profile directly

If the machine running Docker has a logged-in Firefox profile, you can skip
the export and let yt-dlp read it live. Remove the `COOKIE_FILE` environment
variable in `docker-compose.yml` and mount the profile instead:

```yaml
    volumes:
      - ~/.mozilla/firefox:/firefox-profile:ro
    environment:
      - COOKIE_DIR=/firefox-profile
```

The bot looks for a `*.default-release` profile inside `COOKIE_DIR`.

### Refreshing

Cookies expire. If YouTube embeds start failing with login/bot-check errors
again, re-export and replace `selfhost/cookies/cookies.txt`, then restart the
bot container:

```bash
docker compose -f selfhost/docker-compose.yml restart bot
```
