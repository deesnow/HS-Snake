# HS-Snake — Installation Guide

This guide covers three deployment paths:
- **[A] Docker from published image** — recommended, no source clone needed
- **[B] Local Python** — no Docker, direct run
- **[C] Build from source** — for developers

---

## Prerequisites

| Requirement | Minimum version | Notes |
|---|---|---|
| Docker Engine | 25+ | Required for paths A and C — installed in A.1 if missing |
| Docker Compose | v2 (plugin, bundled with Docker Engine) | Uses `docker compose` syntax |
| Python | 3.12+ | Required only for path B |
| Git | any | Required only for paths B and C |

---

## Step 1 — Register a Discord Bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and log in.
2. Click **New Application**, give it a name (e.g. `HS-Snake`), then click **Create**.
3. In the left sidebar select **Bot**.
   - Under **Privileged Gateway Intents** enable **Message Content Intent** — the bot needs this to read message text for auto-detect.
   - Set **Public Bot** to **Off** unless you want anyone to invite it.
4. Click **Reset Token**, copy the token and keep it safe — you will need it in Step 3.
5. In the left sidebar select **OAuth2 → URL Generator**.
   - Under **Scopes** check: `bot`, `applications.commands`
   - Under **Bot Permissions** check: `View Channels`, `Read Message History`, `Send Messages`, `Attach Files`, `Embed Links`
6. Copy the generated URL at the bottom and open it in your browser to invite the bot to your server.

> Full details are in [DISCORD_SETUP.md](DISCORD_SETUP.md).

---

## Step 2 — Configure Environment

Download the two required files (no source clone needed):

```bash
# PowerShell
Invoke-WebRequest https://raw.githubusercontent.com/deesnow/hs-snake/main/docker-compose.yml -OutFile docker-compose.yml
Invoke-WebRequest https://raw.githubusercontent.com/deesnow/hs-snake/main/docker/cache/nginx.conf -OutFile nginx.conf

# bash / macOS / Linux
curl -O https://raw.githubusercontent.com/deesnow/hs-snake/main/docker-compose.yml
curl --create-dirs -o docker/cache/nginx.conf \
     https://raw.githubusercontent.com/deesnow/hs-snake/main/docker/cache/nginx.conf
```

Create a `data/` directory for the bot's database and a `.env` file:

```bash
mkdir data
```

```env
# .env
DISCORD_TOKEN=your_discord_bot_token_here

# Optional — guild-scoped slash command sync (instant vs up-to-1-hour for global)
# DISCORD_GUILD_ID=123456789012345678

# Card image resolution: 256x (faster) or 512x (higher quality)
IMAGE_CARD_SIZE=256x

HSJSON_LOCALE=enUS
LOG_LEVEL=INFO
```

> `CACHE_BASE_URL` is set automatically by Docker Compose — do not change it.

---

## Option A — Docker from Published Image (Recommended)

No source clone required. Suitable for any Linux server or VPS with Docker installed.

### A.1 — Install Docker Engine (skip if already installed)

```bash
# Add Docker's apt repository and install
curl -fsSL https://get.docker.com | sudo sh

# Add your user to the docker group so you don't need sudo every time
sudo usermod -aG docker $USER

# Apply the group change to the current session
newgrp docker

# Verify
docker --version
docker compose version
```

> `get.docker.com` installs Docker Engine + the Compose plugin in one step. Supported on Ubuntu, Debian, Fedora, CentOS, and Raspberry Pi OS.

### A.2 — Create a working directory

```bash
mkdir hs-snake && cd hs-snake
```

### A.3 — Download the two required config files

```bash
# docker-compose.yml — defines the bot + nginx cache services
curl -O https://raw.githubusercontent.com/deesnow/hs-snake/main/docker-compose.yml

# nginx.conf — card image proxy cache configuration
mkdir -p docker/cache
curl -o docker/cache/nginx.conf \
     https://raw.githubusercontent.com/deesnow/hs-snake/main/docker/cache/nginx.conf
```

### A.4 — Create the `.env` file

```bash
nano .env
```

Paste and fill in your values:

```env
DISCORD_TOKEN=your_discord_bot_token_here

# Optional: guild-scoped slash command sync (instant vs up to 1 hour for global)
# DISCORD_GUILD_ID=123456789012345678

# Card image resolution: 256x (faster) or 512x (higher quality)
IMAGE_CARD_SIZE=256x

HSJSON_LOCALE=enUS
LOG_LEVEL=INFO
```

Save with `Ctrl+O` then `Ctrl+X`. The `CACHE_BASE_URL` variable is injected automatically by Docker Compose — do not add it.

### A.5 — Allow GHCR access (public image, no login needed)

The image is public on GitHub Container Registry — no `docker login` required. If you ever switch to a private image, log in with:

```bash
docker login ghcr.io -u YOUR_GITHUB_USERNAME
# paste a GitHub Personal Access Token with read:packages scope when prompted
```

### A.6 — Start the bot

```bash
docker compose up -d
```

Docker pulls `ghcr.io/deesnow/hs-snake:latest` and `nginx:1.27-alpine` on first run, then starts both containers.

**Verify both containers are running:**

```bash
docker compose ps
```

Expected output:

```
NAME              IMAGE                          STATUS
hs-snake-bot-1    ghcr.io/deesnow/hs-snake:latest  Up X seconds
hs-snake-cache-1  nginx:1.27-alpine              Up X seconds (healthy)
```

**Follow live logs:**

```bash
docker compose logs -f bot
```

You should see a line like:
```
HS-Snake v0.2.0 — logged in as HS-Snake#1234 (id=1234567890)
```

Press `Ctrl+C` to stop following logs — the bot keeps running in the background.

### A.7 — Day-to-day operations

| Task | Command |
|---|---|
| Stop the bot | `docker compose down` |
| Restart the bot | `docker compose restart bot` |
| Update to the latest image | `docker compose pull && docker compose up -d` |
| View recent logs | `docker compose logs --tail=100 bot` |

> Guild settings (SQLite) are persisted in the `bot-data` Docker volume and survive restarts and image updates.

---

## Option B — Run Locally (no Docker)

Card images are fetched directly from `art.hearthstonejson.com` — the Nginx cache is skipped.

Clone the repository first:

```bash
git clone https://github.com/deesnow/hs-snake.git
cd hs-snake
```

**Create and activate a virtual environment:**

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python -m venv .venv
source .venv/bin/activate
```

**Install dependencies:**

```bash
pip install -r requirements.txt
```

**Add to your `.env`** (disables the cache lookup):

```env
CACHE_BASE_URL=http://invalid
```

**Start the bot:**

```bash
python -m bot.main
```

---

## Option C — Build from Source (Developers)

Clone and build the Docker image locally instead of pulling it:

```bash
git clone https://github.com/deesnow/hs-snake.git
cd hs-snake
cp .env.example .env
# fill in DISCORD_TOKEN in .env

# Build and run (dev mode with live reload + DEBUG logs)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Source files are mounted from your local disk, so any `.py` change triggers an automatic container restart — no rebuild needed.

To build and run the production image locally:

```bash
docker compose build bot
docker compose up -d
```

---

## Step 3 — Verify

Once the bot is running you should see a log line similar to:

```
HS-Snake v0.2.0 — logged in as HS-Snake#1234 (id=1234567890)
```

The bot will also appear **Online** in your Discord server. Test it with:

```
/deck AAECAf0EBsQBvgPLA...
```

---

## Directory Layout Reference

```
hs-snake/
├── bot/                        # Python source
│   ├── main.py                 # Entry point
│   ├── config.py               # Settings loaded from .env
│   ├── commands/               # Slash command cogs
│   └── services/               # Decoder, API client, image generator, DB
├── assets/
│   ├── backs/                  # Class background images (0–14.png)
│   ├── labels/                 # Card-count label overlays (x2–x9.png)
│   └── fonts/                  # Belwe.ttf for deck image rendering
├── docker/
│   ├── bot/Dockerfile          # Multi-stage Python image
│   └── cache/nginx.conf        # Nginx proxy cache config
├── docker-compose.yml          # Production stack
├── docker-compose.dev.yml      # Development overrides
├── requirements.txt
├── .env.example                # Template for .env
├── DISCORD_SETUP.md            # Detailed Discord portal walkthrough
└── DESIGN.md                   # Architecture and task breakdown
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: Required environment variable 'DISCORD_TOKEN' is not set` | `.env` not created or token missing | Copy `.env.example` to `.env` and fill in the token |
| Bot is online but slash commands do not appear | Global sync delay | Set `DISCORD_GUILD_ID` to your server ID for instant guild-scoped sync |
| `deckimage` returns no image / error in logs | Card image fetch failed | Check internet access; try `IMAGE_CARD_SIZE=256x` |
| `cache` container unhealthy | Nginx still starting | Wait 10–15 s; run `docker compose ps` again |
| Permission denied writing `data/bot.db` | Volume mounted before user created | Rebuild the image: `docker compose build bot` |
| Message Content Intent error in logs | Intent not enabled in Developer Portal | Go to **Bot → Privileged Gateway Intents** and enable **Message Content Intent** |
