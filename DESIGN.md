# HS-Snake — Hearthstone Discord Bot

## Overview

**HS-Snake** is a Discord bot that provides Hearthstone deck utilities directly inside Discord.  
Users paste a deck code and get back a clean text breakdown or a rendered deck image with full card artwork.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Technology Stack](#technology-stack)
4. [Data Sources](#data-sources)
5. [Project Structure](#project-structure)
6. [Service Design](#service-design)
7. [Bot Commands](#bot-commands)
8. [Image Generation Pipeline](#image-generation-pipeline)
9. [Caching Strategy](#caching-strategy)
10. [Docker & Deployment](#docker--deployment)
11. [Task Breakdown](#task-breakdown)
12. [Future Improvements](#future-improvements)

---

## Features

| Feature | Description |
|---|---|
| `/deck` | Decode a deck code and display a formatted card list |
| `/deckimage` | Decode a deck code and render a full deck image with card artwork |
| `/card` | Look up a single card by name |
| `/help` | Show available commands |

---

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                        Discord                                 │
│                     (User sends /command)                      │
└──────────────────────┬────────────────────────────────────────┘
                       │
                       ▼
┌───────────────────────────────────────────────────────────────┐
│                    hs-snake-bot (container)                    │
│                                                               │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐  │
│  │  Discord.py  │──▶│  Commands    │──▶│  Services        │  │
│  │  Slash Cmds  │   │  /deck       │   │  DeckDecoder     │  │
│  └──────────────┘   │  /deckimage  │   │  HSJsonClient    │  │
│                     │  /card       │   │  ImageGenerator  │  │
│                     └──────────────┘   └────────┬─────────┘  │
└──────────────────────────────────────────────────┼────────────┘
                                                   │
               ┌───────────────────────────────────┤
               │                                   │
               ▼                                   ▼
┌──────────────────────────┐       ┌───────────────────────────┐
│   hs-snake-cache         │       │   HearthstoneJSON API     │
│   (Nginx + local files)  │       │   api.hearthstonejson.com │
│                          │       │   art.hearthstonejson.com │
│   /cards/data/           │       │                           │
│   /cards/images/         │       └───────────────────────────┘
└──────────────────────────┘
```

The bot container handles all Discord interaction and image rendering.  
A lightweight Nginx container acts as a local HTTP cache for card metadata (JSON) and card artwork (PNG), eliminating repeated upstream requests after the first fetch.

---

## Technology Stack

| Layer | Technology | Reason |
|---|---|---|
| Bot framework | [discord.py 2.x](https://discordpy.readthedocs.io/) | Mature, async, slash command support |
| Deck decoding | [hearthstone](https://pypi.org/project/hearthstone/) | Official HS deck string parser |
| HTTP client | [httpx](https://www.python-httpx.org/) | Async-first, connection pooling |
| Image rendering | [Pillow](https://pillow.readthedocs.io/) | Card image composition |
| Card data | [HearthstoneJSON](https://hearthstonejson.com/) | Community-maintained card DB |
| Cache service | **Nginx** (Docker volume) | Simple, fast, zero-code static file cache |
| Orchestration | **Docker Compose** | Multi-container local and prod deployment |
| Config | **python-dotenv** | Environment variable management |

---

## Data Sources

### HearthstoneJSON

| Resource | URL |
|---|---|
| All cards (latest, enUS) | `https://api.hearthstonejson.com/v1/latest/enUS/cards.json` |
| Card thumbnail (256×) | `https://art.hearthstonejson.com/v1/tiles/{dbfId}.png` |
| Card full render (256×) | `https://art.hearthstonejson.com/v1/render/latest/enUS/256x/{dbfId}.png` |
| Card full render (512×) | `https://art.hearthstonejson.com/v1/render/latest/enUS/512x/{dbfId}.png` |

### Hearthstone Deck Code Format

Deck codes are base64-encoded binary structures:

```
[0x00]                   ← reserved byte
[varint version]         ← always 1
[varint format]          ← 1=Wild, 2=Standard, 3=Classic, 4=Twist
[varint count of n_heroes][varint heroId, ...]
[varint count of single-copy cards][varint dbfId, ...]
[varint count of double-copy cards][varint dbfId, ...]
[varint count of n-copy cards][varint n][varint dbfId, ...]
```

The `hearthstone` Python library handles this encoding/decoding transparently.

---

## Project Structure

```
hs-snake/
│
├── bot/                            # Bot application source
│   ├── __init__.py
│   ├── main.py                     # Entry point: bot init, cog loading
│   ├── config.py                   # Settings loaded from env vars
│   │
│   ├── commands/                   # Discord slash command cogs
│   │   ├── __init__.py
│   │   ├── deck_commands.py        # /deck, /deckimage
│   │   └── card_commands.py        # /card
│   │
│   ├── services/                   # Business logic (no Discord coupling)
│   │   ├── __init__.py
│   │   ├── deck_decoder.py         # Wrap hearthstone deckstrings library
│   │   ├── hs_json_client.py       # Fetch & cache card metadata
│   │   └── image_generator.py      # Compose deck image using Pillow
│   │
│   └── utils/
│       ├── __init__.py
│       └── image_utils.py          # Image helpers (resize, rounded corners, etc.)
│
├── docker/
│   ├── bot/
│   │   └── Dockerfile              # Python bot image
│   └── cache/
│       └── nginx.conf              # Nginx reverse-proxy / file cache config
│
├── assets/
│   └── fonts/
│       └── BelweGothic.ttf         # Font for card names on generated images
│
├── docker-compose.yml              # Orchestrates bot + cache containers
├── docker-compose.dev.yml          # Dev overrides (auto-reload, volume mounts)
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── DESIGN.md                       # ← this file
```

---

## Service Design

### `DeckDecoder`

```
Input:  deck code string (e.g. "AAECAZICBsP...")
Output: DeckInfo dataclass
  - format: str         ("Standard" | "Wild" | "Classic" | "Twist")
  - hero_class: str     (e.g. "Mage")
  - hero_card: CardInfo
  - cards: List[CardEntry]
    - card: CardInfo
    - count: int (1 or 2)
```

Uses the `hearthstone.deckstrings` module to decode dbfIds, then cross-references the HearthstoneJSON card database to enrich with name, cost, rarity, type.

---

### `HSJsonClient`

Responsibilities:
- Fetch all-cards JSON from HearthstoneJSON on startup (or from local cache)
- Build an in-memory lookup dict: `dbfId → CardInfo`
- Download individual card images on demand, storing them in the shared cache volume
- Served locally via Nginx; only fetches from upstream on a miss

```
Methods:
  async get_card(dbf_id: int) → CardInfo
  async get_card_image(dbf_id: int, size: str) → Path  # local file path
  async refresh_card_db() → None
```

---

### `ImageGenerator`

Builds a deck card image composed of rows of card renders.

```
Layout (default, ~1000 × N pixels):
┌────────────────────────────────────────┐
│  [Hero portrait]  CLASS  FORMAT        │  ← Header bar
├────────────────────────────────────────┤
│  [Card image][Card name    Cost]  ×2   │  ← One row per card
│  ...                                   │
│  ...                                   │
├────────────────────────────────────────┤
│  Total cards: 30     Dust: 4200        │  ← Footer bar
└────────────────────────────────────────┘

Methods:
  async generate_deck_image(deck: DeckInfo) → BytesIO
```

---

## Bot Commands

### `/deck [code]`

Responds with an embed containing:

```
🐍 HS-Snake Deck Viewer
══════════════════════════════
Class:   Warrior           Format: Standard
─────────────────── Minions (14) ──────────────────
[1] ⚪ Glaciaxe                           1 mana
[1] ⚪ Boom Wrench                         1 mana
[2] 🔵 Inventor Boom                       3 mana
...
─────────────────── Spells (10) ───────────────────
[2] 🟣 Shield Slam                         1 mana
...
─────────────────── Weapons (2) ───────────────────
...
══════════════════════════════
Total: 30 cards  |  Dust: 4,200  |  Rarity: ★★★★
```

Rarity color legend: ⚪ Common · 🔵 Rare · 🟣 Epic · 🟠 Legendary

---

### `/deckimage [code]`

- Responds with "Generating image..." ephemeral message
- Calls `ImageGenerator.generate_deck_image()`
- Edits the response with the rendered image attached as `deck.png`

---

### `/card [name]`

- Fuzzy-searches the card DB for a matching card name
- Returns an embed with card text, stats, and thumbnail

---

## Image Generation Pipeline

```
1. Decode deck code  →  List of (dbfId, count) pairs
2. For each card:
      a. Lookup CardInfo from card DB
      b. Fetch/retrieve card image from cache (Nginx) or upstream
      c. Resize to tile height (e.g. 64px)
3. Open Pillow canvas: width=800, height = HEADER + (N_cards × TILE_H) + FOOTER
4. draw header: hero portrait thumbnail + class name + format badge
5. For each card row:
      a. Paste card image tile
      b. draw card name (truncated if needed)
      c. draw mana cost bubble
      d. draw count badge (×2)
      e. color rarity strip on the left edge
6. draw footer: card count, dust cost
7. Return BytesIO (PNG)
```

---

## Caching Strategy

| Data | Location | TTL / Invalidation |
|---|---|---|
| All-cards JSON | Bot memory (dict) + Nginx volume | Refreshed on bot start; can force-refresh with env var |
| Card images (PNG) | Nginx volume (`/var/cache/nginx/cards/`) | Permanent (card art never changes for a given dbfId) |
| Rendered deck images | Not cached (fast to regenerate) | — |

The Nginx container mounts a named Docker volume (`card-cache`).  
On a cache miss, Nginx proxies the request to `art.hearthstonejson.com` and saves the response to the volume.  
The bot requests images through the Nginx cache endpoint (`http://cache/cards/...`) rather than upstream directly.

---

## Docker & Deployment

### Containers

| Container | Image | Role |
|---|---|---|
| `hs-snake-bot` | `python:3.12-slim` | Discord bot process |
| `hs-snake-cache` | `nginx:alpine` | Card image proxy/cache |

### docker-compose.yml (summary)

```yaml
services:
  bot:
    build: ./docker/bot
    env_file: .env
    depends_on: [cache]
    volumes:
      - ./bot:/app/bot        # dev: live reload
    restart: unless-stopped

  cache:
    image: nginx:alpine
    volumes:
      - ./docker/cache/nginx.conf:/etc/nginx/nginx.conf:ro
      - card-cache:/var/cache/nginx/cards
    restart: unless-stopped

volumes:
  card-cache:
```

### Environment Variables

| Variable | Description | Example |
|---|---|---|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal | `MTAyNDU2...` |
| `DISCORD_GUILD_ID` | (Optional) Limit slash commands to one guild during dev | `123456789` |
| `HSJSON_LOCALE` | Card data locale | `enUS` |
| `CACHE_BASE_URL` | Internal URL to the Nginx cache | `http://cache` |
| `IMAGE_CARD_SIZE` | Card render resolution to fetch | `256x` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

---

## Task Breakdown

### Phase 1 — Foundation

- [ ] **T-01** — Initialize git repo, project structure, `.gitignore`
- [ ] **T-02** — Write `config.py` with env-var loading via `python-dotenv`
- [ ] **T-03** — Create `main.py`: bot init, cog loading, graceful shutdown
- [ ] **T-04** — Register Discord application & bot token, document setup steps in `README.md`

### Phase 2 — Card Data Integration

- [ ] **T-05** — Implement `HSJsonClient.fetch_card_db()`: download & parse all-cards JSON
- [ ] **T-06** — Build in-memory `dbfId → CardInfo` lookup with `CardInfo` dataclass
- [ ] **T-07** — Write unit tests for card DB loading and lookup

### Phase 3 — Deck Decoding

- [ ] **T-08** — Implement `DeckDecoder.decode(code: str) → DeckInfo`
- [ ] **T-09** — Map decoded dbfIds to `CardInfo` objects using `HSJsonClient`
- [ ] **T-10** — Group cards by type (Minion / Spell / Weapon / Hero card / Location)
- [ ] **T-11** — Write unit tests for several known deck codes

### Phase 4 — `/deck` Command

- [ ] **T-12** — Implement `/deck` slash command in `deck_commands.py`
- [ ] **T-13** — Format Discord embed with grouped card list, mana cost, rarity icons
- [ ] **T-14** — Calculate and display dust cost and rarity breakdown

### Phase 5 — Image Generation

- [ ] **T-15** — Implement `HSJsonClient.get_card_image()`: download via cache, return local path
- [ ] **T-16** — Build Pillow image layout: header, card rows, footer
- [ ] **T-17** — Style elements: rarity color strip, mana bubbles, fonts, dark background
- [ ] **T-18** — Implement `/deckimage` slash command: generate & attach image

### Phase 6 — Cache Container

- [ ] **T-19** — Write `nginx.conf` for proxy caching of `art.hearthstonejson.com`
- [ ] **T-20** — Configure Docker volume for persistent card image cache
- [ ] **T-21** — Test cache hit/miss behavior and verify upstream fallback

### Phase 7 — Docker & CI

- [ ] **T-22** — Write `Dockerfile` for bot (multi-stage, slim final layer)
- [ ] **T-23** — Write `docker-compose.yml` (prod) and `docker-compose.dev.yml` (dev)
- [ ] **T-24** — Write `README.md` with quick-start, env setup, and running instructions
- [ ] **T-25** — Add basic health-check endpoint (or Discord heartbeat log) for monitoring

### Phase 8 — Polish & `/card` Command

- [ ] **T-26** — Implement fuzzy card name search (`/card`)
- [ ] **T-27** — Add error handling: invalid deck code, unknown dbfId, API down
- [ ] **T-28** — Add rate-limiting guard per user per command

---

## Future Improvements

| Idea | Notes |
|---|---|
| Deck comparison | `/deckdiff code1 code2` — show added/removed cards |
| HSReplay integration | Show winrate / meta tier for the pasted deck |
| Interactive embeds | Button to switch between text list and image views |
| Multi-language support | Locale-aware card names via HSJSON locale param |
| Standalone web UI | Export deck list as HTML/PDF |
| Auto-update card DB | Scheduled task to pull new set data on patch day |
| Redis cache | Replace in-memory dict with Redis for multi-instance scaling |
