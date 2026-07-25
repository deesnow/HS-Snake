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
| `/deck <code>` | Decode a deck code — simple card list with rarity icons, mana cost, format, and dust total. E.T.C. Band Manager / King of the Underbelly sideboard cards shown in a separate section. |
| `/deckanalyze <code>` | Detailed analysis: cards grouped by type (Minions/Spells/Weapons/Locations/Heroes), subtype/tribe column, mana curve bar chart, and E.T.C. Band Manager / King of the Underbelly sideboard sections. |
| `/deckimage <code>` | Render a visual deck image with card thumbnails, including E.T.C. / King of the Underbelly sideboard cards. |

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
| `/rankchart <mode> <region> [season] [timeframe] [rank_type]` | Line chart of rank progress — Season (default) plots one point per day (day 1..days-in-month, Last or Best rank per day), Today plots every raw intraday observation; legend-player-count overlaid on a secondary axis. `season` defaults to the current season, or accepts `previous` / an explicit season number |
| `/rcc <mode> <region> [season]` | Candlestick chart of daily rank — one candle per day the player has data (no placeholder for days without data): body spans opening/closing rank that day (green if rank improved, red if it worsened, gray for no change), wick spans that day's best/worst rank |

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
│                     │  PostgreSQL (asyncpg)   │                   │
│                     │  hs-snake-postgres      │                   │
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
A PostgreSQL container stores guild settings, user BattleTag registrations, and live leaderboard data.

---

## Technology Stack

| Layer | Technology | Reason |
|---|---|---|
| Bot framework | [discord.py 2.x](https://discordpy.readthedocs.io/) | Mature, async, slash command + UI components support |
| Deck decoding | [hearthstone](https://pypi.org/project/hearthstone/) | Official HS deck string parser (includes sideboard support) |
| HTTP client | [httpx](https://www.python-httpx.org/) | Async-first, connection pooling |
| Image rendering | [Pillow](https://pillow.readthedocs.io/) | Card image composition |
| Chart rendering | [Matplotlib](https://matplotlib.org/) | Rank-progress line charts (`/rankchart`) |
| Card data | [HearthstoneJSON](https://hearthstonejson.com/) | Community-maintained card DB |
| Leaderboard data | Blizzard public API | `hearthstone.blizzard.com/en-us/api/community/leaderboardsData` |
| Database | **PostgreSQL 16** via [asyncpg](https://magicstack.github.io/asyncpg/) | Guild settings, BattleTags, leaderboard cache |
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

Deck codes are base64-encoded binary structures using varint (LEB128) encoding:

```
[0x00]                    ← reserved byte
[varint version]          ← always 1
[varint format]           ← 1=Wild, 2=Standard, 3=Classic, 4=Twist
[varint count][heroId, ...]
[varint count][single-copy dbfId, ...]
[varint count][double-copy dbfId, ...]
[varint count][n][dbfId, ...]   ← multi-copy cards

Sideboard section (appended after main cards):
[0x00]                    ← no sideboards
  or
[0x01]                    ← sideboards present
[varint count][card_id, sideboard_owner, ...]     ← 1-copy sideboard entries
[varint count][card_id, sideboard_owner, ...]     ← 2-copy sideboard entries
[varint count][card_id, count, sideboard_owner, ...] ← multi-copy entries
```

The `sideboard_owner` identifies which card holds the sideboard (e.g. `90749` for E.T.C. Band Manager, `102983` for Zilliax 3000, `125998` for King of the Underbelly). The `hearthstone` Python library handles encoding/decoding transparently; `DeckDecoder` reads `raw_deck.sideboards` and filters by owner dbfId.

---

## Project Structure

```
hs-snake/
│
├── bot/                                # Bot application source
│   ├── __init__.py
│   ├── main.py                         # Entry point: bot init, cog loading, __version__
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
│   │   ├── deck_decoder.py             # Wrap hearthstone deckstrings + sideboard handling
│   │   ├── hs_json_client.py           # Fetch & cache card metadata + images
│   │   ├── image_generator.py          # Compose deck image using Pillow
│   │   ├── leaderboard_client.py       # Blizzard public leaderboard API client
│   │   ├── leaderboard_cache.py        # PostgreSQL upsert cache + refresh logic
│   │   ├── rank_chart.py               # player_rank_log queries + Matplotlib chart rendering
│   │   ├── guild_settings.py           # Per-guild config CRUD
│   │   └── db.py                       # asyncpg connection helper + migrations
│   │
│   └── utils/
│       └── __init__.py
│
├── docker/
│   ├── bot/
│   │   ├── Dockerfile                  # Multi-stage Python image (BOT_VERSION build arg)
│   │   └── entrypoint.sh
│   └── cache/
│       └── nginx.conf                  # Nginx reverse-proxy / file cache config
│
├── assets/
│   ├── fonts/                          # Optional custom fonts for image rendering
│   ├── backs/                          # Class background images
│   └── labels/                         # Card-count label overlays
│
├── data/
│   └── cards_cache.json                # Cached HearthstoneJSON card DB
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
├── INSTALL.md
├── README.md
└── DESIGN.md                           # ← this file
```

---

## Database Schema

Managed by `bot/services/db.py` via inline migrations applied on startup.

```sql
-- Per-guild bot configuration
CREATE TABLE guild_settings (
    guild_id      BIGINT PRIMARY KEY,
    admin_role_id BIGINT,
    auto_detect   BOOLEAN DEFAULT FALSE,
    all_channels  BOOLEAN DEFAULT FALSE
);

-- Per-guild monitored channel list (used when all_channels = false)
CREATE TABLE monitored_channels (
    guild_id   BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

-- User BattleTag registrations (one per discord_id × region)
CREATE TABLE user_battletags (
    discord_id  TEXT   NOT NULL,
    region      TEXT   NOT NULL,   -- EU | US | AP
    battletag   TEXT   NOT NULL,   -- original casing e.g. "Player#1234"
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
    updated_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (region, mode, rank)
);

CREATE INDEX idx_ldb_current_btag ON ldb_current_entries (region, mode, battletag);

-- Append-only rank observation log — one row per (registered) player sighting
-- during a background refresh; powers /rankchart's day-by-day and intraday series.
CREATE TABLE player_rank_log (
    id          SERIAL  PRIMARY KEY,
    battletag   TEXT    NOT NULL,       -- lower-cased
    region      TEXT    NOT NULL,       -- EU | US | AP
    mode        TEXT    NOT NULL,       -- standard | wild
    season_id   INTEGER NOT NULL,
    rank        INTEGER NOT NULL,
    rating      INTEGER,
    observed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_prl ON player_rank_log (battletag, region, mode, season_id, observed_at DESC);
```

---

## Service Design

### `DeckDecoder`

```
Input:  deck code string (e.g. "AAECAZICBsP...")
Output: DeckInfo dataclass
  - format_id: int
  - format_label: str           ("Standard" | "Wild" | "Classic" | "Twist")
  - hero_dbf_id: int
  - hero_class: str             (e.g. "Mage")
  - deck_name: str
  - cards: List[CardEntry]
      - card: CardInfo
      - count: int              (1 or 2)
  - etc_sideboard_cards: List[CardEntry]
      - card: CardInfo          (cards owned by E.T.C. Band Manager, dbfId 90749)
      - count: int
  - total_cards: int            (property, excludes sideboard)
```

Uses `hearthstone.deckstrings` to decode dbfIds, cross-references HearthstoneJSON to enrich with name, cost, rarity, type, race, and spell school. Reads `raw_deck.sideboards` and filters entries where `sideboard_owner == 90749` to populate `etc_sideboard_cards`.

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

Builds a deck image using class-specific background images and card thumbnails.

```
Layout (3000 × ~2344 px canvas, class background):
┌────────────────────────────────────────┐
│  [Card image] [Card image] [Card image]│  ← Grid of card renders
│  ...                                   │     (main deck + ETC sideboard)
├────────────────────────────────────────┤
│  Dust cost              hs-snake       │  ← Footer strip
└────────────────────────────────────────┘

Methods:
  async generate_deck_image(deck: DeckInfo) → BytesIO
```

E.T.C. sideboard cards are appended after the main deck cards in the grid.

---

### `LeaderboardClient`

Fetches pages from the Blizzard public leaderboard API.

- Shared token-bucket rate limiter (3 req/s) with adaptive backoff on 4xx/429
- Retry schedule: 30 s → 60 s → 120 s before giving up on a page
- Failed pages are skipped gracefully; previous DB rows remain
- Callbacks: `on_started(season_id)`, `on_page(page, rows)`, `on_page_error(page)`

```
async fetch_leaderboard(region, mode, *, on_started, on_page, on_page_error, max_page)
    → (List[LeaderboardEntry], season_id)
```

---

### `LeaderboardCache`

Wraps `LeaderboardClient` with PostgreSQL persistence.

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

### `rank_chart`

Queries `player_rank_log`/`player_daily_dps` and renders a PNG line chart for `/rankchart`. No Discord coupling.

```
async resolve_days_in_month(conn, region, mode, season_id) → int
    # derives the season's calendar month from its own data (MAX observed date);
    # works for current, previous, or any explicit season_id
async fetch_season_series(conn, battletag, region, mode, season_id, rank_type, days_in_month)
    → (days: List[int], ranks: List[int | None])       # 1..days_in_month, gaps = None
async fetch_season_legend_counts(conn, battletag, region, mode, season_id, days_in_month)
    → (days, legend_counts)
async fetch_today_series(conn, battletag, region, mode, season_id)
    → (times: List[datetime], ranks: List[int])         # every raw observation, no aggregation
async fetch_today_legend_count(conn, battletag, region, mode, season_id) → int | None
async fetch_season_candles(conn, battletag, region, mode, season_id)
    → List[{day, open, close, low, high}]   # one entry per day WITH data; no gap entries

render_season_chart(battletag, region, mode, season_id, rank_type, days, ranks, legend_counts) → BytesIO
render_today_chart(battletag, region, mode, season_id, times, ranks, legend_count) → BytesIO
render_candlestick_chart(battletag, region, mode, season_id, candles) → BytesIO
```

- Season series aggregates `player_rank_log` per day: **Best** = `MIN(rank)` that day, **Last** = most recent observation that day. Days with no data are left as gaps (Matplotlib skips `None`/`NaN` points rather than connecting across them).
- The command resolves `season_id` first via `bot.services.season_id.resolve_season_id_by_arg`/`parse_season_arg` (current / previous / explicit — same "previous = current − 1" convention `/glb` uses, factored out so `/rankchart` and `/rcc` share one implementation), then calls `resolve_days_in_month` so the x-axis always spans the correct month for whichever season was requested.
- Legend-player count is rendered as a **filled area** (`ax.fill_between`), not a line, on a secondary axis — visually reads as "how deep into the legend pool this rank sits" rather than a second trend line. `twinx()` draws that secondary axis above the first by default, so both renderers explicitly flip z-order (`ax1.set_zorder(ax2.get_zorder() + 1)`, `ax1.patch.set_visible(False)`) to keep the rank line on top.
- Colors and opacity are **developer-configurable module constants** at the top of `rank_chart.py` (not Discord command options): `RANK_LINE_COLOR`, `LEGEND_AREA_COLOR`, `AXIS_COLOR`, `BACKGROUND_COLOR`, and the candlestick `BULLISH_COLOR`/`BEARISH_COLOR`/`DOJI_COLOR` (hex strings), each paired with a `*_TRANSPARENCY` integer percentage (0–100). `_rgba()` converts a hex+percent pair into an RGBA color; `_style_axes()` applies `BACKGROUND_COLOR`/`AXIS_COLOR` to the figure, axes, ticks, and spines of all three renderers.
- Rank axis is inverted (rank 1 at the top) and scaled to `[min, max]` of the player's own data ± ~15% padding — this is what makes the same chart work for a top-50 Standard climber and a top-8000 Wild grinder. (`set_ylim` must be called *before* `invert_yaxis()` — the reverse order silently resets the axis to non-inverted.)
- Legend-player count (`player_daily_dps.legend_count`) is drawn semi-transparent on a secondary axis (`ax.twinx()`) so it can't distort the rank axis's dynamic scaling.
- `fetch_season_candles` groups raw `player_rank_log` rows by day (`itertools.groupby`, already ordered by day/`observed_at` in SQL): `open`/`close` are the first/last rank observed that day, `low`/`high` are that day's best/worst rank. Days with zero observations simply have no entry — `render_candlestick_chart` plots candles at consecutive integer x-positions labeled with their real day-of-month, so the x-axis never shows empty days. A candle is colored bullish (green) when the closing rank is numerically lower (better) than the opening rank, bearish (red) when higher (worse), or a neutral doji gray when unchanged — note this is inverted from standard OHLC price convention, since for rank a smaller number is the "improvement".

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

Responds with a plain-text card list. If E.T.C. Band Manager is in the deck, a separate sideboard section is appended:

```
# **Warrior**
**Cost:** 3,200 💠
**Format:** Standard
────────────────────────────────────────
⚪ 2x (1) Boom Wrench
🔵 2x (2) Shield Slam
🟡 1x (5) Grommash Hellscream
...
────────────────────────────────────────
**E.T.C. Band Manager:**
⚪ (2) Backstab
🟣 (4) Brawl
🟡 (7) Ragnaros the Firelord

**Deck Code:**
AAECAZICBsP...
```

Rarity icons: ⚪ Free/Common · 🔵 Rare · 🟣 Epic · 🟡 Legendary

---

### `/deckanalyze <code>`

Responds with a structured Discord embed:

- Cards grouped into sections: **Minions**, **Spells**, **Weapons**, **Locations**, **Heroes**
- Each section rendered as a monospace code-block table with columns: Rarity, Cost, Count, Name, Subtype/Tribe
- **E.T.C. Band Manager sideboard** section when present
- **Mana Curve** section rendered as an ASCII vertical bar chart (0–7+)
- Header: `ClassName — Format · N cards · Dust cost`

---

### `/deckimage <code>`

- Defers the interaction (shows loading indicator)
- Decodes deck code and generates a PNG using `ImageGenerator`
- E.T.C. sideboard cards rendered after the main deck grid
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

### `/rankchart <mode> <region> [season] [timeframe] [rank_type]`

Renders a rank-progress line chart from `player_rank_log` via the `rank_chart` service and attaches it as a PNG.

- `mode` (required): Standard or Wild — the only two modes tracked in `player_rank_log`
- `region` (required): EU / US / AP — one line per chart, so no "all regions" fan-out like `/rank`
- `season` (default **current**): free-text — `current`, `previous` (current season's id − 1), or an explicit season number (e.g. `150`); invalid non-numeric values are rejected with an ephemeral error before the chart is generated
- `timeframe` (default **Season**): Season → x-axis is season day 1..days-in-month (the month is derived from that season's own data via `rank_chart.resolve_days_in_month`, not assumed to be the current month — same approach `/glb` uses for its previous-season view); Today → x-axis is time-of-day
- `rank_type` (default **Last**): for Season only — Last (most recent rank observed each day) or Best (lowest rank observed each day); ignored for Today, which always plots every raw observation
- Legend-player count is overlaid semi-transparently on a secondary axis
- Errors mirror `/rank`: unregistered region → "register with `/rankset` first"; no data yet → friendly message instead of a blank/broken image (this is also what happens if you combine a non-current `season` with `timeframe=Today`, since past seasons have no data dated "today")

---

### `/rcc <mode> <region> [season]`

Renders a daily rank candlestick chart from `player_rank_log` via `rank_chart.fetch_season_candles`/`render_candlestick_chart` and attaches it as a PNG.

- `mode`/`region` (required), `season` (default **current**): same semantics as `/rankchart` (shared via `bot.services.season_id.parse_season_arg`/`resolve_season_id_by_arg`)
- One candle per day the player has data — days with zero observations are simply absent, so the x-axis never shows an empty gap; each candle is still labeled with its real day-of-month
- Body spans that day's opening (first observed) and closing (last observed) rank; wick spans that day's best (lowest) and worst (highest) rank
- Candle color: green if the closing rank improved on the opening rank, red if it worsened, gray for no change (a deliberate inversion of standard OHLC convention, since a lower rank number is the improvement)
- Same dark-theme color/background config and inverted dynamic rank axis as `/rankchart`
- Errors mirror `/rankchart`: unregistered region / no leaderboard data yet / no rank data this season

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
1. Decode deck code → List of (dbfId, count) pairs + ETC sideboard pairs
2. Combine: sorted main deck entries + sorted ETC sideboard entries
3. For each card:
      a. Lookup CardInfo from card DB
      b. Fetch/retrieve card image via Nginx cache or upstream
      c. Crop and resize to tile dimensions
4. Open Pillow canvas using class-specific background image (3000 × 2344 px)
5. For each card tile:
      a. Paste count label (×2, ×3, etc.) behind card
      b. Paste card image
6. Overwrite background branding strip with clean background row
7. Draw dust cost text with stroke
8. Return BytesIO (PNG)
```

---

## Caching Strategy

| Data | Location | TTL / Invalidation |
|---|---|---|
| All-cards JSON | `data/cards_cache.json` + in-memory dict | Written on first fetch; reloaded on bot start |
| Card images (PNG) | Nginx volume (`/var/cache/nginx/`) | Permanent (card art never changes for a given dbfId) |
| Rendered deck images | Not cached — generated on each request | Fast to regenerate (< 1 s) |
| Leaderboard entries | PostgreSQL `ldb_current_entries` | Upserted every 5 min (top 500) and 30 min (full) |
| BattleTag registrations | PostgreSQL `user_battletags` | Persistent until user runs `/rankremove` |
| Guild settings | PostgreSQL `guild_settings` + `monitored_channels` | Persistent; updated via `/botadmin` commands |

The Nginx container mounts a bind-mounted volume (`./docker/cache/cards`).
On a cache miss, Nginx proxies the request to `art.hearthstonejson.com` and stores the response on disk.
The bot requests images through the Nginx endpoint (`http://cache/...`) rather than upstream directly.

---

## Docker & Deployment

### Containers

| Container | Image | Role |
|---|---|---|
| `hs-snake-bot` | `ghcr.io/deesnow/hs-snake:<tag>` | Discord bot process |
| `hs-snake-cache` | `nginx:1.27-alpine` | Card image proxy/cache |
| `hs-snake-postgres` | `postgres:16-alpine` | Persistent database |

### Versioning

The Docker image is built and tagged by the CI pipeline (`.github/workflows/docker-publish.yml`):

| Git event | Image tag(s) |
|---|---|
| Push to `main` | `:latest` |
| Push to `rc` branch | `:rc` |
| Git tag `v0.5.1` | `:v0.5.1`, `:0.5`, `:latest` |

The git tag version is injected as a Docker build arg (`BOT_VERSION`) and baked into the image as an env var. The bot reads it via `os.getenv("BOT_VERSION", "dev")` and displays it in the Discord presence and startup log.

### docker-compose.yml (summary)

```yaml
services:
  bot:
    image: ghcr.io/deesnow/hs-snake:${BOT_TAG:-latest}
    env_file: .env
    depends_on:
      cache: { condition: service_healthy }
      postgres: { condition: service_healthy }
    volumes:
      - ./data:/app/data
      - ./log:/app/logs
    restart: unless-stopped

  cache:
    image: nginx:1.27-alpine
    volumes:
      - ./docker/cache/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./docker/cache/cards:/var/cache/nginx/cards
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: hs-snake_user
      POSTGRES_PASSWORD: hs-snake_password
      POSTGRES_DB: hs-snake_db
    volumes:
      - ./pgdata:/var/lib/postgresql/data
    restart: unless-stopped
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
| `BOT_VERSION` | Injected by CI from git tag; shown in Discord presence | `dev` |
| `POSTGRES_HOST` | PostgreSQL host (set automatically by Docker Compose) | `postgres` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `POSTGRES_USER` | PostgreSQL user | — |
| `POSTGRES_PASSWORD` | PostgreSQL password | — |
| `POSTGRES_DB` | PostgreSQL database name | — |

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
- [x] **T-11** — Write unit tests for known deck codes including E.T.C. sideboard

### Phase 4 — Deck Commands ✅

- [x] **T-12** — Implement `/deck` — simple card list with rarity icons, dust cost, format, E.T.C. sideboard section
- [x] **T-13** — Implement `/deckanalyze` — grouped embed with monospace tables, mana curve, E.T.C. sideboard field
- [x] **T-14** — Implement `/deckimage` — render and attach PNG via `ImageGenerator`

### Phase 5 — Image Generation ✅

- [x] **T-15** — Implement `HSJsonClient.get_card_image_bytes()`: fetch via Nginx cache
- [x] **T-16** — Build Pillow image layout using class background images and card grid
- [x] **T-17** — Style elements: count labels, dust cost footer, branding strip

### Phase 6 — Card Search ✅

- [x] **T-18** — Implement `/card <name>` — single card image lookup
- [x] **T-19** — Implement `/cardsearch` — interactive filter UI with paginated results and inline image viewer

### Phase 7 — Legend Rank Tracking ✅

- [x] **T-20** — Implement `LeaderboardClient` with rate limiting and retry logic
- [x] **T-21** — Implement `LeaderboardCache` with PostgreSQL live-upsert table
- [x] **T-22** — Implement `/rankset`, `/rankremove`, `/rank` commands
- [x] **T-23** — Add background refresh tasks (5 min top-500, 30 min full)

### Phase 8 — Per-Guild Config & Auto-Detection ✅

- [x] **T-24** — Design PostgreSQL schema; implement `db.py` with auto-migration
- [x] **T-25** — Implement `GuildSettings` CRUD service
- [x] **T-26** — Implement `/botadmin` command group (setrole, autodetect, channels, status)
- [x] **T-27** — Implement `AutoDetectCog` with regex pipeline and @mention path

### Phase 9 — Cache Container & Docker ✅

- [x] **T-28** — Write `nginx.conf` for proxy caching of `art.hearthstonejson.com`
- [x] **T-29** — Write `Dockerfile` (multi-stage, slim final layer, `BOT_VERSION` build arg)
- [x] **T-30** — Write `docker-compose.yml` (prod) and `docker-compose.dev.yml` (dev)
- [x] **T-31** — Configure CI pipeline for automated image builds and semver tagging

### Phase 10 — Polish & Error Handling ✅

- [x] **T-32** — Global error handling for invalid deck codes, unknown cards, API outages
- [x] **T-33** — Structured logging with configurable level and optional file output

### Phase 11 — E.T.C. Band Manager Sideboard Support ✅

- [x] **T-34** — Read `raw_deck.sideboards` in `DeckDecoder`; populate `DeckInfo.etc_sideboard_cards`
- [x] **T-35** — Show E.T.C. sideboard in `/deck`, `/deckanalyze`, and `/deckimage`
- [x] **T-36** — Unit test for deck code containing E.T.C. sideboard cards
- [x] **T-40** — Extend sideboard handling to King of the Underbelly (dbfId `125998`), which uses the same 3-card sideboard mechanism as E.T.C.; populates `DeckInfo.king_sideboard_cards` and renders in `/deck`, `/deckanalyze`, and `/deckimage`

### Phase 12 — Rank Progress Chart ✅

- [x] **T-37** — Implement `rank_chart.py`: season (best/last per day) and today (raw) series queries against `player_rank_log`, plus legend-count overlay from `player_daily_dps`
- [x] **T-38** — Render PNG line charts with Matplotlib: inverted/dynamically-scaled rank axis, gap handling for missing days, secondary-axis legend-count overlay
- [x] **T-39** — Implement `/rankchart` command (mode/region/timeframe/rank_type params)

---

## Future Improvements

| Idea | Notes |
|---|---|
| Zilliax 3000 sideboard | Card `102983` uses the same sideboard mechanism — expose its module selection similarly to E.T.C. |
| Deck comparison | `/deckdiff code1 code2` — show added/removed cards between two versions |
| HSReplay integration | Show winrate / meta tier for a pasted deck |
| Leaderboard top-N | `/leaderboard [region] [mode]` — list top players in a server |
| Auto-update card DB | Scheduled task to pull new set data automatically on patch day |
| Multi-language support | Locale-aware card names via HSJSON locale param (currently `enUS` only) |
| Slash-command rate limiting | Per-user cooldown to prevent abuse of image generation commands |
| Redis leaderboard cache | Replace PostgreSQL leaderboard table with Redis for lower-latency lookups |
| Standalone web UI | Export deck list as HTML / PDF from a companion web service |
