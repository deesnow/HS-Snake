# 🐍 HS-Snake — Hearthstone Discord Bot

A Discord bot that decodes Hearthstone deck codes into a clean card list or a rendered deck image with full card artwork.

See [DESIGN.md](DESIGN.md) for the full architecture and task breakdown.

---

## Features

| Command | Description |
|---|---|
| `/deck <code>` | Decode a deck code and display a formatted card list |
| `/deckimage <code>` | Render a visual deck image with card thumbnails |
| `/card <name>` | Look up a single card by name |

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
│   └── services/             # Business logic (decoder, API client, image gen)
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
| `LOG_LEVEL` | — | `INFO` | Logging verbosity |

---

## Architecture Overview

```
Discord User
     │  /deck AAECAZICBs...
     ▼
hs-snake-bot (Python + discord.py)
     │
     ├── DeckDecoder  →  hearthstone library
     ├── HSJsonClient →  HearthstoneJSON API  ─────┐
     └── ImageGenerator → Pillow                    │
                                               hs-snake-cache (Nginx)
                                                    │ proxy + volume cache
                                               art.hearthstonejson.com
```

---

## Development

```bash
# Lint
pip install ruff
ruff check bot/

# Tests (once added under tests/)
pip install pytest pytest-asyncio
pytest
```

---

## License

MIT
