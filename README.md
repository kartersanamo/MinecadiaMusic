# Minecadia Music Bot

Discord bot for music playback on Minecadia: `/music` panel, Lavalink, and the web dashboard.

## What it does

- `/music` — Components V2 in-channel music player panel
- Lavalink playback (YouTube, SoundCloud, etc.)
- Web dashboard at `MUSIC_PUBLIC_BASE_URL` (default `https://music.kartersanamo.com`)
- **Discord Activity** — launch the dashboard inside Discord from the panel (**Launch Dashboard** button)

## Discord Activity setup (Developer Portal)

Required once per application (`DISCORD_CLIENT_ID` in `.env`):

1. Open [Discord Developer Portal](https://discord.com/developers/applications) → your Minecadia Music app.
2. **Activities → Settings**: enable **Activities**.
3. **Activities → URL Mappings**: add mapping prefix `/` → target `music.kartersanamo.com` (host only, no `https://`).
4. **OAuth2**: keep redirect `https://music.kartersanamo.com/oauth/callback` (used by dashboard OAuth and Activities).
5. **Installation**: ensure **Guild Install** is enabled.
6. **Bot → OAuth2 URL Generator** (or re-invite the bot): include permission **Set Voice Channel Status** (`SET_VOICE_CHANNEL_STATUS`) so the bot can show a hint when it joins voice.
7. **Distribution** (optional): submit the Activity for public use; until approved, only the app’s developer team can launch it in production.

After portal setup, restart MinecadiaMusic. Users can launch the dashboard in two ways:

- **App Launcher → Minecadia Music** — uses the global Entry Point command (registered automatically on bot startup)
- **`/music` panel → Launch Dashboard** — launches directly from the in-channel panel

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # add DISCORD_TOKEN, DB_*, Lavalink, OAuth
python main.py         # or ./run.sh
```

## Lavalink

Install and run Lavalink (shared on port 2333):

```bash
./scripts/install-lavalink-native.sh
./scripts/run-lavalink-native.sh
```

Or use `docker/lavalink-compose.yml`. See `lavalink/application.yml.example`.

## Config

- `.env` — token, database, Lavalink, OAuth (see `.env.example`)
- `assets/config.json` — roles, music settings, presence

## Deploy (production)

1. Create a **new Discord application** for this bot; enable Server Members Intent and voice permissions.
2. Copy Lavalink/OAuth vars from the old Utilities `.env` if migrating.
3. Stop Utilities (or anything on port 8790), start MinecadiaMusic with `./run.sh`.
4. Restart the bot — slash commands sync on startup (`DISCORD_GUILD_ID` enables instant guild sync).
5. Restart Utilities and run **`/utilities-sync`** to remove the old `/music` command from that app.
6. Add a tmux pane for MinecadiaMusic alongside the other bots.
7. nginx (`nginx/music.kartersanamo.com.conf`) still proxies to `127.0.0.1:8790`.

## Admin commands

- `/music-reload` — reload a cog
