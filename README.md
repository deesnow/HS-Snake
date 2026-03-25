# 🐍 HS-Snake — Hearthstone Discord Bot

A Discord bot for Hearthstone: decode deck codes, visualise decks, search cards, look up legend ranks, and auto-detect deck codes posted anywhere in your server.

See [DESIGN.md](DESIGN.md) for the full architecture and task breakdown.

---

## Features

### Deck Commands

| Command | Description |
|---|---|
| `/deck <code>` | Decode a deck code and display a simple card list with rarity icons, mana cost, format label, and total dust cost |
| `/deckanalyze <code>` | Detailed grouped analysis: cards split by type (Minions, Spells, Weapons, Locations, Heroes), subtype/tribe info, and a mana curve bar chart |
| `/deckimage <code>` | Render a high-quality visual image of the deck with card thumbnails |

### Card Commands

| Command | Description |
|---|---|
| `/card <name>` | Look up a single card by name and display its full card image |
| `/cardsearch [name]` | Interactive card search with dropdown filters for **Mana Cost**, **Class**, and **Card Type**; paginated results (10 per page, up to 100); click any result to view its card image |

### Legend Rank Commands

| Command | Description |
|---|---|
| `/rankset <battletag> <region>` | Register a BattleTag for a region (EU / US / AP). Run once per region you play in |
| `/rankremove <region>` | Remove your BattleTag registration for a region |
| `/rank [mode] [region]` | Show your current legend rank across all registered regions. Optional filters for game mode (Standard, Wild, Classic, Battlegrounds, Battlegrounds Duo, Arena, Twist) and region |

### Auto-Detection

The bot can passively monitor channels for Hearthstone deck codes:

- When a deck code is detected in a watched channel the bot replies with a deck image automatically.
- **@mention + deck code** always triggers a reply, regardless of channel settings.
- Fully configurable per server — see Admin Commands below.

### Admin Commands (`/botadmin`)

All subcommands require the configured admin role, Administrator permission, or server ownership.

| Command | Description |
|---|---|
| `/botadmin setrole <role>` | Set the role that is allowed to manage bot settings (requires Administrator permission) |
| `/botadmin autodetect on\|off` | Enable or disable automatic deck-code detection for this server |
| `/botadmin allchannels on\|off` | `on` — monitor every text channel; `off` — only channels on the watch list |
| `/botadmin addchannel <channel>` | Add a channel to the deck-detection watch list |
| `/botadmin removechannel <channel>` | Remove a channel from the deck-detection watch list |
| `/botadmin status` | Show the current bot configuration (admin role, auto-detect state, monitored channels) |

---

## Quick Start

### 1. Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a **New Application** → **Bot** section → **Reset Token** → copy the token
3. Under **OAuth2 → URL Generator**: select `bot` + `applications.commands`
4. Add the required permissions: `Send Messages`, `Attach Files`, `Embed Links`
5. Open the generated URL to invite the bot to your server

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and fill in DISCORD_TOKEN
```

### 3. Run with Docker

```bash
# Production
docker compose up -d

# Development (with live reload and debug logs)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

### 4. Run Locally (without Docker)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
python -m bot.main
```

> **Note:** Without the cache container running, card images will be fetched directly from `art.hearthstonejson.com`.  
> Set `CACHE_BASE_URL=http://invalid` to force direct upstream fetches in local development.

---

## Project Structure

```
hs-snake/
├── bot/
│   ├── main.py               # Bot entry point
│   ├── config.py             # Settings from .env
│   ├── commands/             # Discord slash command cogs
│   │   ├── deck_commands.py  # /deck, /deckanalyze, /deckimage
│   │   ├── card_commands.py  # /card
│   │   ├── search_commands.py# /cardsearch
│   │   ├── rank_commands.py  # /rank, /rankset, /rankremove
│   │   ├── admin_commands.py # /botadmin group
│   │   └── auto_detect.py    # Passive deck-code detection
│   └── services/             # Business logic
│       ├── deck_decoder.py   # Hearthstone deck-code parsing
│       ├── hs_json_client.py # HearthstoneJSON API client
│       ├── image_generator.py# Pillow-based deck image rendering
│       ├── leaderboard_cache.py # Cached leaderboard data
│       ├── leaderboard_client.py# Blizzard leaderboard API client
│       ├── guild_settings.py # Per-guild configuration (SQLite)
│       ├── db.py             # SQLite connection helper
│       └── models.py         # Shared data models
├── docker/
│   ├── bot/Dockerfile        # Multi-stage Python image
│   └── cache/nginx.conf      # Nginx proxy cache for card images
├── assets/fonts/             # Optional: custom fonts for image rendering
├── docker-compose.yml
├── docker-compose.dev.yml
├── requirements.txt
├── .env.example
└── DESIGN.md
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ | — | Bot token from Discord Developer Portal |
| `DISCORD_GUILD_ID` | — | — | Restrict slash commands to one guild (dev) |
| `HSJSON_LOCALE` | — | `enUS` | Card data locale |
| `IMAGE_CARD_SIZE` | — | `256x` | Card render resolution (`256x` or `512x`) |
| `CACHE_BASE_URL` | — | `http://cache` | Internal Nginx cache URL |
| `COMMAND_PREFIX` | — | `!` | Legacy text command prefix |
| `LOG_LEVEL` | — | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FILE` | — | — | Optional path to write logs to a file |

---

## Architecture Overview

```
Discord User
     │  /deck AAECAZICBs...
     ▼
hs-snake-bot (Python + discord.py)
     │
     ├── DeckDecoder       →  hearthstone library
     ├── HSJsonClient      →  HearthstoneJSON API  ─────┐
     ├── ImageGenerator    →  Pillow                     │
     ├── LeaderboardClient →  Blizzard Leaderboard API   │
     └── GuildSettings     →  SQLite (per-server config) │
                                               hs-snake-cache (Nginx)
                                                    │ proxy + volume cache
                                               art.hearthstonejson.com
```

### Background Tasks

- **Quick refresh** (every 5 min) — updates top ~500 leaderboard entries per region/mode combination (EU, US, AP × Standard, Wild)
- **Full refresh** (every 30 min) — fetches all leaderboard pages for complete rank coverage

---

## Development

```bash
# Lint
pip install ruff
ruff check bot/

# Tests
pip install pytest pytest-asyncio
pytest
```

---

## License

MIT
