# HS-Snake — Hearthstone Discord Bot

## Overview

**HS-Snake** is a Discord bot that provides Hearthstone utilities directly inside Discord.  
Users can decode deck codes, analyse deck composition, render deck images, search cards with interactive filters, look up live legend leaderboard ranks, and configure per-server auto-detection of deck codes.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Technology Stack](#technology-stack)
4. [Data Sources](#data-sources)
5. [Project Structure](#project-structure)
6. [Database Schema](#database-schema)
7. [Service Design](#service-design)
8. [Bot Commands](#bot-commands)
9. [Image Generation Pipeline](#image-generation-pipeline)
10. [Caching Strategy](#caching-strategy)
11. [Docker & Deployment](#docker--deployment)
12. [Task Breakdown](#task-breakdown)
13. [Future Improvements](#future-improvements)

---

## Features

### Deck Commands

| Command | Description |
|---|---|
| `/deck <code>` | Decode a deck code — simple card list with rarity icons, mana cost, format, and dust total |
| `/deckanalyze <code>` | Detailed analysis: cards grouped by type (Minions/Spells/Weapons/Locations/Heroes), subtype/tribe column, mana curve bar chart |
| `/deckimage <code>` | Render a visual deck image with card thumbnails |

### Card Commands

| Command | Description |
|---|---|
| `/card <name>` | Display the card art for a single card looked up by name |
| `/cardsearch [name]` | Interactive search with Mana Cost / Class / Card Type dropdowns; paginated results (10/page, up to 100); inline image viewer per result |

### Legend Rank Commands

| Command | Description |
|---|---|
| `/rankset <battletag> <region>` | Register a BattleTag for a region (EU / US / AP) |
| `/rankremove <region>` | Remove a BattleTag registration for a region |
| `/rank [mode] [region]` | Look up legend rank from the cached leaderboard — all registered regions by default; optional mode (Standard, Wild, Classic, Battlegrounds, Battlegrounds Duo, Arena, Twist) and region filter |

### Auto-Detection

| Trigger | Behaviour |
|---|---|
| Passive (watched channels) | When a valid deck code appears in a monitored channel, the bot replies with a deck image automatically |
| @mention + deck code | Always replies with a deck image, regardless of channel/server settings |

### Admin Commands (`/botadmin`)

| Command | Description |
|---|---|
| `/botadmin setrole <role>` | Set the role allowed to manage bot settings (requires Administrator) |
| `/botadmin autodetect on\|off` | Enable or disable auto-detection for this server |
| `/botadmin allchannels on\|off` | Monitor all channels vs. the explicit watch list |
| `/botadmin addchannel <channel>` | Add a channel to the deck-detection watch list |
| `/botadmin removechannel <channel>` | Remove a channel from the watch list |
| `/botadmin status` | Show current server configuration (admin role, auto-detect state, watched channels) |

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                           Discord                                  │
│              (User sends /command or pastes deck code)             │
└──────────────────────────┬────────────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────────────┐
│                    hs-snake-bot (container)                        │
│                                                                   │
│  ┌──────────────┐   ┌────────────────────────┐                   │
│  │  discord.py  │──▶│  Commands (Cogs)        │                   │
│  │  Slash Cmds  │   │  deck_commands.py       │                   │
│  │  on_message  │   │  card_commands.py       │                   │
│  └──────────────┘   │  search_commands.py     │                   │
│                     │  rank_commands.py       │                   │
│                     │  admin_commands.py      │                   │
│                     │  auto_detect.py         │                   │
│                     └──────────┬─────────────┘                   │
│                                │                                  │
│                     ┌──────────▼─────────────┐                   │
│                     │  Services               │                   │
│                     │  DeckDecoder            │                   │
│                     │  HSJsonClient ──────────┼──────────────────►│
│                     │  ImageGenerator         │                   │
│                     │  LeaderboardClient ─────┼──────────────────►│
│                     │  LeaderboardCache       │                   │
│                     │  GuildSettings          │                   │
│                     └──────────┬─────────────┘                   │
│                                │                                  │
│                     ┌──────────▼─────────────┐                   │
│                     │  SQLite (aiosqlite)     │                   │
│                     │  data/bot.db            │                   │
│                     └─────────────────────────┘                  │
└──────────────────────────────┬────────────────────────────────────┘
                               │
            ┌──────────────────┴───────────────────┐
            ▼                                       ▼
┌───────────────────────┐          ┌───────────────────────────────┐
│   hs-snake-cache      │          │   External APIs               │
│   (Nginx container)   │          │                               │
│   proxy + vol cache   │◄─────────│   art.hearthstonejson.com     │
│                       │          │   api.hearthstonejson.com     │
└───────────────────────┘          │   hearthstone.blizzard.com    │
                                   │   (leaderboard — public)      │
                                   └───────────────────────────────┘
```

The bot container handles all Discord interaction, deck decoding, image rendering, leaderboard lookups, and per-guild configuration.  
A lightweight Nginx container acts as a local HTTP cache for card artwork, eliminating repeated upstream requests after the first fetch.  
A single SQLite database (`data/bot.db`) stores guild settings, user BattleTag registrations, and live leaderboard data.

---

## Technology Stack

| Layer | Technology | Reason |
|---|---|---|
| Bot framework | [discord.py 2.x](https://discordpy.readthedocs.io/) | Mature, async, slash command + UI components support |
| Deck decoding | [hearthstone](https://pypi.org/project/hearthstone/) | Official HS deck string parser |
| HTTP client | [httpx](https://www.python-httpx.org/) | Async-first, connection pooling |
| Image rendering | [Pillow](https://pillow.readthedocs.io/) | Card image composition |
| Card data | [HearthstoneJSON](https://hearthstonejson.com/) | Community-maintained card DB |
| Leaderboard data | Blizzard public API | `hearthstone.blizzard.com/en-us/api/community/leaderboardsData` |
| Database | **SQLite** via [aiosqlite](https://aiosqlite.omnilib.dev/) | Guild settings, BattleTags, leaderboard cache |
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

### Blizzard Leaderboard API (public, no auth)

```
https://hearthstone.blizzard.com/en-us/api/community/leaderboardsData
    ?region={EU|US|AP}
    &leaderboardId={standard|wild|classic|battlegrounds|battlegroundsduo|arena|twist}
    &page={n}
```

Returns 25 entries per page. Response includes `seasonId` and `totalPages`.

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
├── bot/                                # Bot application source
│   ├── __init__.py
│   ├── main.py                         # Entry point: bot init, cog loading
│   ├── config.py                       # Settings loaded from env vars
│   │
│   ├── commands/                       # Discord slash command cogs
│   │   ├── __init__.py
│   │   ├── deck_commands.py            # /deck, /deckanalyze, /deckimage
│   │   ├── card_commands.py            # /card
│   │   ├── search_commands.py          # /cardsearch (interactive UI)
│   │   ├── rank_commands.py            # /rank, /rankset, /rankremove + bg refresh
│   │   ├── admin_commands.py           # /botadmin group
│   │   └── auto_detect.py             # Passive on_message deck-code detection
│   │
│   ├── services/                       # Business logic (no Discord coupling)
│   │   ├── __init__.py
│   │   ├── models.py                   # CardInfo, CardEntry, DeckInfo dataclasses
│   │   ├── deck_decoder.py             # Wrap hearthstone deckstrings library
│   │   ├── hs_json_client.py           # Fetch & cache card metadata + images
│   │   ├── image_generator.py          # Compose deck image using Pillow
│   │   ├── leaderboard_client.py       # Blizzard public leaderboard API client
│   │   ├── leaderboard_cache.py        # SQLite upsert cache + refresh logic
│   │   ├── guild_settings.py           # Per-guild config CRUD
│   │   └── db.py                       # aiosqlite connection helper + migrations
│   │
│   └── utils/
│       └── __init__.py
│
├── docker/
│   ├── bot/
│   │   ├── Dockerfile                  # Multi-stage Python image
│   │   └── entrypoint.sh
│   └── cache/
│       └── nginx.conf                  # Nginx reverse-proxy / file cache config
│
├── assets/
│   ├── fonts/                          # Optional custom fonts for image rendering
│   ├── backs/                          # Card back images
│   └── labels/                         # Label/badge assets
│
├── data/
│   ├── cards_cache.json                # Cached HearthstoneJSON card DB
│   └── bot.db                          # SQLite database (created at runtime)
│
├── tests/
│   ├── __init__.py
│   └── test_deck_decoder.py
│
├── docker-compose.yml
├── docker-compose.dev.yml
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── DESIGN.md                           # ← this file
```

---

## Database Schema

Managed by `bot/services/db.py` via inline migrations on every connection open.

```sql
-- Per-guild bot configuration
CREATE TABLE guild_settings (
    guild_id      INTEGER PRIMARY KEY,
    admin_role_id INTEGER,            -- role that may run /botadmin commands
    auto_detect   INTEGER DEFAULT 0,  -- 1 = enabled
    all_channels  INTEGER DEFAULT 0   -- 1 = monitor every channel
);

-- Per-guild monitored channel list (used when all_channels = 0)
CREATE TABLE monitored_channels (
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

-- User BattleTag registrations (one per discord_id × region)
CREATE TABLE user_battletags (
    discord_id  TEXT NOT NULL,
    region      TEXT NOT NULL,   -- EU | US | AP
    battletag   TEXT NOT NULL,   -- original casing e.g. "Player#1234"
    PRIMARY KEY (discord_id, region)
);

-- Live leaderboard cache — always reflects latest API data
-- One row per (region, mode, rank); upserted on each background refresh
CREATE TABLE ldb_current_entries (
    region         TEXT    NOT NULL,
    mode           TEXT    NOT NULL,
    season_id      INTEGER NOT NULL,
    rank           INTEGER NOT NULL,
    battletag      TEXT    NOT NULL,   -- lower-cased for lookup
    battletag_orig TEXT    NOT NULL,   -- original casing for display
    rating         INTEGER,
    updated_at     TEXT    NOT NULL,   -- ISO-8601 UTC
    PRIMARY KEY (region, mode, rank)
);

CREATE INDEX idx_ldb_current_btag ON ldb_current_entries (region, mode, battletag);
```

---

## Service Design

### `DeckDecoder`

```
Input:  deck code string (e.g. "AAECAZICBsP...")
Output: DeckInfo dataclass
  - format_id: int
  - format_label: str      ("Standard" | "Wild" | "Classic" | "Twist")
  - hero_dbf_id: int
  - hero_class: str        (e.g. "Mage")
  - deck_name: str
  - cards: List[CardEntry]
    - card: CardInfo
    - count: int           (1 or 2)
  - total_cards: int       (property)
```

Uses the `hearthstone.deckstrings` module to decode dbfIds, then cross-references the HearthstoneJSON card database to enrich with name, cost, rarity, type, race, and spell school.

---

### `HSJsonClient`

Responsibilities:
- Load all-cards JSON from `api.hearthstonejson.com` on startup; write to `data/cards_cache.json` for fast subsequent loads
- Build in-memory lookup dicts: `dbfId → CardInfo` and `name → CardInfo`
- Fuzzy card name search (`find_card_by_name`)
- Download individual card images on demand; fetched via the Nginx cache container on hits, upstream on misses

```
Methods:
  async load_cards() → None
  async get_card(dbf_id: int) → CardInfo | None
  async find_card_by_name(name: str) → CardInfo | None
  async get_card_image_bytes(card_id: str, dbf_id: int) → bytes
  async search_cards(name, cost, card_class, card_type) → List[CardInfo]
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
├────────────────────────────────────────┤
│  Total cards: 30     Dust: 4200        │  ← Footer bar
└────────────────────────────────────────┘

Methods:
  async generate_deck_image(deck: DeckInfo) → BytesIO
```

---

### `LeaderboardClient`

Fetches pages from the Blizzard public leaderboard API.

- Shared token-bucket rate limiter (3 req/s) with adaptive backoff on 4xx/429
- Retry schedule: 30 s → 60 s → 120 s before giving up on a page
- Failed pages are skipped gracefully via `on_page_error` callback; previous DB rows remain
- Callbacks: `on_started(season_id)`, `on_page(page, rows)`, `on_page_error(page)`

```
async fetch_leaderboard(region, mode, *, on_started, on_page, on_page_error, max_page)
    → (List[LeaderboardEntry], season_id)
```

---

### `LeaderboardCache`

Wraps `LeaderboardClient` with SQLite persistence using the live upsert table.

- **`get_snapshot(region, mode)`** — reads from DB only; never calls the API
- **`refresh_pages(region, mode, max_page)`** — called by background tasks; upserts pages as they arrive; detects season rollover and wipes stale rows
- **`lookup(battletag, region, mode)`** — convenience wrapper over `get_snapshot`

Background refresh schedule (driven by `RankCommands` background tasks):

| Task | Interval | Scope | Purpose |
|---|---|---|---|
| `_quick_refresh` | 5 min | Top 20 pages (~500 players) | Near-realtime top-rank data |
| `_full_refresh` | 30 min | All pages | Full leaderboard coverage |

Both tasks run for all 6 warm combos: EU/US/AP × Standard/Wild.

---

### `GuildSettings`

Thin async CRUD layer over the `guild_settings` and `monitored_channels` tables.

```python
@dataclass
class GuildSettings:
    guild_id: int
    admin_role_id: Optional[int]
    auto_detect: bool
    all_channels: bool
    monitored_channels: list[int]

async load(guild_id) → GuildSettings
async set_admin_role(guild_id, role_id)
async set_auto_detect(guild_id, enabled)
async set_all_channels(guild_id, enabled)
async add_channel(guild_id, channel_id)
async remove_channel(guild_id, channel_id)
```

---

## Bot Commands

### `/deck <code>`

Responds with a plain-text card list:

```
# **Warrior**
**Cost:** 3,200 💠
**Format:** Standard
────────────────────────────────────────
⚪ 2x (1) Glaciaxe
⚪ 2x (1) Boom Wrench
🔵 2x (2) Shield Slam
🟣 2x (4) Brawl
🟡 1x (5) Grommash Hellscream
...

**Deck Code:**
AAECAZICBsP...
```

Rarity icons: ⚪ Free/Common · 🔵 Rare · 🟣 Epic · 🟡 Legendary

---

### `/deckanalyze <code>`

Responds with a structured Discord embed:

- Cards grouped into sections: **Minions**, **Spells**, **Weapons**, **Locations**, **Heroes**
- Each section rendered as a monospace code-block table with columns: Rarity, Cost, Count, Name, Subtype/Tribe
- **Mana Curve** section rendered as an ASCII vertical bar chart (0–7+)
- Header: `ClassName — Format · N cards · Dust cost`

---

### `/deckimage <code>`

- Defers the interaction (shows loading indicator)
- Decodes deck code and generates a PNG using `ImageGenerator`
- Sends the image as an attachment with a plain-text caption

---

### `/card <name>`

- Looks up the card by name in the in-memory card DB
- Downloads the card art via the Nginx cache
- Sends the image as a Discord file attachment

---

### `/cardsearch [name]`

Interactive ephemeral UI built with `discord.ui.View`:

1. **Filter view**: three `Select` dropdowns (Mana Cost 0–10+, Class, Type) + optional name prefix; **🔍 Search** button
2. **Results view**: paginated embed (10 cards/page, up to 100 results); **◀ Prev / ▶ Next** navigation; card-image dropdown to view any result inline; **🔙 New Search** to go back

---

### `/rankset <battletag> <region>`

Registers (or updates) a BattleTag for EU / US / AP.  
Stored in `user_battletags`. Validates `Name#1234` format.

---

### `/rankremove <region>`

Removes the BattleTag registration for the specified region.

---

### `/rank [mode] [region]`

Looks up the user's rank in `ldb_current_entries`.

- With no arguments: shows Standard and Wild ranks for all registered regions in a compact monospace table per region
- With `mode`: shows that single mode for all regions (or filtered region)
- BattleTag matching strips `#NNNN` suffix (Blizzard API returns names only)
- If the DB has no data yet, returns a friendly "loading" message

---

### `/botadmin` group

All subcommands check `_is_admin()` (server owner → Administrator perm → configured admin role).  
All responses are ephemeral.

---

### Auto-detect (`on_message`)

Detection pipeline:
1. **Regex scan** — finds `AAE[A-Za-z0-9+/]{20,}={0,2}` tokens in message text
2. **Base64 validation** — token must decode without errors
3. **Deck parse** — `DeckDecoder.decode()` must succeed
4. **Reply** — same image format as `/deckimage`, mentions-safe reply

@mention path always runs regardless of guild settings.  
Passive path respects `auto_detect` flag and channel scope (`all_channels` or `monitored_channels`).

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
| All-cards JSON | `data/cards_cache.json` + in-memory dict | Written on first fetch; reloaded on bot start |
| Card images (PNG) | Nginx volume (`/var/cache/nginx/`) | Permanent (card art never changes for a given dbfId) |
| Rendered deck images | Not cached — generated on each request | Fast to regenerate (< 1 s) |
| Leaderboard entries | SQLite `ldb_current_entries` | Upserted every 5 min (top 500) and 30 min (full) |
| BattleTag registrations | SQLite `user_battletags` | Persistent until user runs `/rankremove` |
| Guild settings | SQLite `guild_settings` + `monitored_channels` | Persistent; updated via `/botadmin` commands |

The Nginx container mounts a named Docker volume (`card-cache`).  
On a cache miss, Nginx proxies the request to `art.hearthstonejson.com` and stores the response on the volume.  
The bot requests images through the Nginx endpoint (`http://cache/...`) rather than upstream directly.

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
      - ./data:/app/data       # SQLite DB + cards_cache.json
    restart: unless-stopped

  cache:
    image: nginx:alpine
    volumes:
      - ./docker/cache/nginx.conf:/etc/nginx/nginx.conf:ro
      - card-cache:/var/cache/nginx
    restart: unless-stopped

volumes:
  card-cache:
```

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal | *(required)* |
| `DISCORD_GUILD_ID` | Limit slash commands to one guild during dev | — |
| `HSJSON_LOCALE` | Card data locale | `enUS` |
| `CACHE_BASE_URL` | Internal URL to the Nginx cache | `http://cache` |
| `IMAGE_CARD_SIZE` | Card render resolution to fetch | `256x` |
| `COMMAND_PREFIX` | Legacy text command prefix | `!` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `LOG_FILE` | Optional path to write logs to a file | — |
| `DB_PATH` | SQLite database path | `data/bot.db` |

---

## Task Breakdown

### Phase 1 — Foundation ✅

- [x] **T-01** — Initialize git repo, project structure, `.gitignore`
- [x] **T-02** — Write `config.py` with env-var loading via `python-dotenv`
- [x] **T-03** — Create `main.py`: bot init, cog loading, graceful shutdown
- [x] **T-04** — Register Discord application & bot token, document setup steps in `README.md`

### Phase 2 — Card Data Integration ✅

- [x] **T-05** — Implement `HSJsonClient`: download & parse all-cards JSON; cache to disk
- [x] **T-06** — Build in-memory `dbfId → CardInfo` and `name → CardInfo` lookups
- [x] **T-07** — Write unit tests for card DB loading and lookup

### Phase 3 — Deck Decoding ✅

- [x] **T-08** — Implement `DeckDecoder.decode(code: str) → DeckInfo`
- [x] **T-09** — Map decoded dbfIds to `CardInfo` objects using `HSJsonClient`
- [x] **T-10** — Group cards by type (Minion / Spell / Weapon / Hero / Location)
- [x] **T-11** — Write unit tests for several known deck codes

### Phase 4 — Deck Commands ✅

- [x] **T-12** — Implement `/deck` — simple card list with rarity icons, dust cost, format
- [x] **T-13** — Implement `/deckanalyze` — grouped embed with monospace tables and mana curve
- [x] **T-14** — Implement `/deckimage` — render and attach PNG via `ImageGenerator`

### Phase 5 — Image Generation ✅

- [x] **T-15** — Implement `HSJsonClient.get_card_image_bytes()`: fetch via Nginx cache
- [x] **T-16** — Build Pillow image layout: header, card rows, footer
- [x] **T-17** — Style elements: rarity strips, mana bubbles, fonts, dark background

### Phase 6 — Card Search ✅

- [x] **T-18** — Implement `/card <name>` — single card image lookup
- [x] **T-19** — Implement `/cardsearch` — interactive filter UI with paginated results and inline image viewer

### Phase 7 — Legend Rank Tracking ✅

- [x] **T-20** — Implement `LeaderboardClient` with rate limiting and retry logic
- [x] **T-21** — Implement `LeaderboardCache` with SQLite live-upsert table
- [x] **T-22** — Implement `/rankset`, `/rankremove`, `/rank` commands
- [x] **T-23** — Add background refresh tasks (5 min top-500, 30 min full)

### Phase 8 — Per-Guild Config & Auto-Detection ✅

- [x] **T-24** — Design SQLite schema; implement `db.py` with auto-migration
- [x] **T-25** — Implement `GuildSettings` CRUD service
- [x] **T-26** — Implement `/botadmin` command group (setrole, autodetect, channels, status)
- [x] **T-27** — Implement `AutoDetectCog` with regex pipeline and @mention path

### Phase 9 — Cache Container & Docker ✅

- [x] **T-28** — Write `nginx.conf` for proxy caching of `art.hearthstonejson.com`
- [x] **T-29** — Write `Dockerfile` (multi-stage, slim final layer)
- [x] **T-30** — Write `docker-compose.yml` (prod) and `docker-compose.dev.yml` (dev)

### Phase 10 — Polish & Error Handling ✅

- [x] **T-31** — Global error handling for invalid deck codes, unknown cards, API outages
- [x] **T-32** — Structured logging with configurable level and optional file output

---

## Future Improvements

| Idea | Notes |
|---|---|
| Deck comparison | `/deckdiff code1 code2` — show added/removed cards between two versions |
| HSReplay integration | Show winrate / meta tier for a pasted deck |
| Per-user rank history | Track rank over time and show a sparkline graph |
| Leaderboard top-N | `/leaderboard [region] [mode]` — list top players in a server |
| Auto-update card DB | Scheduled task to pull new set data automatically on patch day |
| Multi-language support | Locale-aware card names via HSJSON locale param (currently `enUS` only) |
| Slash-command rate limiting | Per-user cooldown to prevent abuse of image generation commands |
| Redis leaderboard cache | Replace SQLite with Redis for multi-instance / horizontal scaling |
| `/deckimport` to clipboard | Browser extension or bookmarklet companion to copy deck codes |
| Standalone web UI | Export deck list as HTML / PDF from a companion web service |
