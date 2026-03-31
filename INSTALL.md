# HS-Snake — Installation Guide

This guide covers three deployment paths:

- **[A] Docker from published image** — recommended for production, no source clone needed
- **[B] Local Python** — no Docker, direct run
- **[C] Build from source** — for developers (Windows 11 + Docker Desktop + VS Code)

---

## Prerequisites

| Requirement | Minimum version | Notes |
|---|---|---|
| Docker Engine | 25+ | Required for paths A and C |
| Docker Compose | v2 (bundled with Docker Engine) | Uses `docker compose` syntax |
| PostgreSQL | 16+ | Required only for path B — paths A and C use the bundled container |
| Python | 3.12+ | Required only for path B |
| Git | any | Required only for paths B and C |

---

## Step 1 — Register a Discord Bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and log in.
2. Click **New Application**, give it a name (e.g. `HS-Snake`), then click **Create**.
3. In the left sidebar select **Bot**.
   - Under **Privileged Gateway Intents** enable **Message Content Intent** — required for auto-detect to read message text.
   - Set **Public Bot** to **Off** unless you want anyone to invite it.
4. Click **Reset Token**, copy the token and keep it safe — you will need it in Step 2.
5. In the left sidebar select **OAuth2 → URL Generator**.
   - Under **Scopes** check: `bot`, `applications.commands`
   - Under **Bot Permissions** check: `View Channels`, `Read Message History`, `Send Messages`, `Attach Files`, `Embed Links`
6. Copy the generated URL at the bottom and open it in your browser to invite the bot to your server.

---

## Step 2 — Configure Environment

Create a `.env` file in your working directory with the following contents:

```env
DISCORD_TOKEN=your_discord_bot_token_here

# Optional: guild-scoped slash command sync (instant vs up to 1 hour for global)
# DISCORD_GUILD_ID=123456789012345678

# Card image resolution: 256x (faster) or 512x (higher quality)
IMAGE_CARD_SIZE=256x

HSJSON_LOCALE=enUS
LOG_LEVEL=INFO
```

> `CACHE_BASE_URL` and all `POSTGRES_*` variables are injected automatically by Docker Compose — do not add them for Docker deployments.

---

## Option A — Docker from Published Image (Recommended)

No source clone required. Suitable for any Linux server or VPS.

### A.1 — Install Docker Engine

```bash
# Installs Docker Engine + Compose plugin (Ubuntu, Debian, Fedora, CentOS, Raspberry Pi OS)
curl -fsSL https://get.docker.com | sudo sh

# Add your user to the docker group
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version
```

### A.2 — Create a working directory

```bash
mkdir hs-snake && cd hs-snake
mkdir -p log data docker/cache pgdata
```

### A.3 — Download the required config files

```bash
# docker-compose.yml — defines bot + nginx cache + postgres services
curl -O https://raw.githubusercontent.com/deesnow/hs-snake/main/docker-compose.yml

# nginx.conf — card image proxy cache configuration
curl --create-dirs -o docker/cache/nginx.conf \
     https://raw.githubusercontent.com/deesnow/hs-snake/main/docker/cache/nginx.conf
```

### A.4 — Create the `.env` file

```bash
nano .env
```

Paste your values from Step 2. Save with `Ctrl+O` then `Ctrl+X`.

### A.5 — Start the bot

```bash
docker compose up -d
```

Docker pulls `ghcr.io/deesnow/hs-snake:latest`, `postgres:16-alpine`, and `nginx:1.27-alpine` on first run, starts all containers, and creates the database schema automatically.

**Verify all containers are running:**

```bash
docker compose ps
```

Expected output:

```
NAME                  IMAGE                             STATUS
hs-snake-bot-1        ghcr.io/deesnow/hs-snake:latest   Up X seconds
hs-snake-cache-1      nginx:1.27-alpine                 Up X seconds (healthy)
hs-snake-postgres-1   postgres:16-alpine                Up X seconds (healthy)
```

**Confirm the bot is online:**

```bash
docker compose logs --tail=20 bot
```

Look for:

```
HS-Snake v0.5.1 — logged in as HS-Snake#1234 (id=1234567890)
```

---

## Option B — Run Locally (no Docker)

Card images are fetched directly from `art.hearthstonejson.com` — the Nginx cache is skipped.

You need a running PostgreSQL 16+ instance. The quickest way even without Docker as main runtime:

```bash
docker run -d --name hs-snake-pg \
  -e POSTGRES_USER=hs-snake_user \
  -e POSTGRES_PASSWORD=hs-snake_password \
  -e POSTGRES_DB=hs-snake_db \
  -p 5432:5432 \
  postgres:16-alpine
```

**Clone and set up:**

```bash
git clone https://github.com/deesnow/hs-snake.git
cd hs-snake
```

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

**Add to your `.env`:**

```env
CACHE_BASE_URL=http://invalid

POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=hs-snake_user
POSTGRES_PASSWORD=hs-snake_password
POSTGRES_DB=hs-snake_db
```

**Start the bot:**

```bash
python -m bot.main
```

---

## Option C — Build from Source (Developers — Windows 11 + Docker Desktop + VS Code)

### Prerequisites

| Tool | Notes |
|---|---|
| Docker Desktop | Enable the **WSL 2 backend** in Docker Desktop → Settings → General |
| VS Code | With the Docker extension (optional but handy) |
| Git | Comes with Git for Windows or VS Code's built-in Git |

### C.1 — One-time setup

Open a PowerShell or VS Code terminal in the repo root:

```powershell
# Copy env template and fill in your DISCORD_TOKEN
Copy-Item .env.example .env
notepad .env

# Create directories required by Docker bind-mounts
New-Item -ItemType Directory -Force -Path log, data, pgdata, docker\cache
```

### C.2 — Switch to the branch you want to test

```powershell
git branch -a
git checkout feature/my-branch

# Or check out a remote branch for the first time
git checkout -b feature/my-branch origin/feature/my-branch
```

You can also switch branches via the branch indicator in the VS Code bottom-left status bar.

### C.3 — Build and run

```powershell
# Dev mode: live-reload + DEBUG logs (recommended during active development)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Production image build (to test exactly what would ship)
docker compose build bot
docker compose up -d
```

> `--build` is required after every branch switch — it rebuilds the image from the checked-out source.
> Omit `--build` only when you changed `.py` files that are live-mounted; the watchdog reloader restarts the bot automatically.

### C.4 — Monitoring in Docker Desktop

- **Containers tab** → expand the `hs-snake` stack → click the `bot` container to view live logs
- Click **▶ / ■** to start or stop individual containers without touching the terminal

### C.5 — Switching branches while the stack is running

```powershell
# 1. Stop the running stack (keeps volumes/data intact)
docker compose down

# 2. Switch branch
git checkout other-branch

# 3. Rebuild and restart
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

> Logs are written to `./log/bot.log` on the host — visible in VS Code's Explorer panel while the bot runs.

---

## Maintenance & Updates

### Update to the latest stable image

```bash
docker compose pull bot && docker compose up -d bot
```

> Only the `bot` container is recreated. `cache` and `postgres` keep running. Data in `./pgdata/` is preserved.

### Run the RC (pre-release) image

```bash
BOT_TAG=rc docker compose up -d
# or set BOT_TAG=rc in .env permanently
```

### Switch back to stable

Remove `BOT_TAG` from `.env`, then:

```bash
docker compose pull bot && docker compose up -d bot
```

### Verify the running version

```bash
docker compose logs --tail=20 bot | grep "logged in"
```

### Day-to-day operations

| Task | Command |
|---|---|
| Stop all services | `docker compose down` |
| Restart the bot only | `docker compose restart bot` |
| View live logs | `docker compose logs -f bot` |
| View recent logs | `docker compose logs --tail=100 bot` |
| Check container health | `docker compose ps` |

> `docker compose down` keeps `pgdata/` intact. Only `docker compose down -v` removes volumes.

---

## Directory Layout

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
├── log/                        # Bot log files (bind-mounted)
├── data/                       # Misc bot data (bind-mounted)
├── pgdata/                     # PostgreSQL data files (persists DB)
├── docker-compose.yml          # Production stack
├── docker-compose.dev.yml      # Development overrides
├── requirements.txt
├── .env.example                # Template for .env
└── DESIGN.md                   # Architecture and task breakdown
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: Required environment variable 'DISCORD_TOKEN' is not set` | `.env` missing or token blank | Copy `.env.example` to `.env` and fill in the token |
| Slash commands do not appear after invite | Global sync delay (up to 1 hour) | Set `DISCORD_GUILD_ID` to your server ID for instant guild-scoped sync |
| `/deckimage` returns an error | Card image fetch failed | Check internet access; try `IMAGE_CARD_SIZE=256x` |
| `cache` container unhealthy | Nginx still starting | Wait 10–15 s then run `docker compose ps` again |
| `postgres` container unhealthy | `pgdata/` missing or wrong ownership | Run `mkdir -p pgdata` before `docker compose up`; check `docker compose logs postgres` |
| Bot fails to connect to database | `postgres` not healthy yet | Bot waits for the healthcheck — check `docker compose ps` and `docker compose logs postgres` |
| Permission denied writing to `log/` | `log/` directory missing | Run `mkdir -p log` before `docker compose up` |
| Message Content Intent error | Intent not enabled | Go to **Bot → Privileged Gateway Intents** and enable **Message Content Intent** |
