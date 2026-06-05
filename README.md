# Minecadia Music Bot

Discord bot for music playback on Minecadia: `/music` panel, Lavalink, and the web dashboard.

## What it does

- `/music` — Components V2 in-channel music player panel
- Lavalink playback (YouTube, SoundCloud, etc.)
- Web dashboard at `MUSIC_PUBLIC_BASE_URL` (default `https://music.kartersanamo.com`)

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
