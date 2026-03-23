# HS-Snake Bot — Feature ToDo

---

## 1. Card Search with Interactive Filters

### Goal
Players can search the full Hearthstone card database using filters (mana cost, class, card type). Filters are presented as interactive Discord UI components — no need to memorise command syntax.

### Discord.py Capabilities & Constraints
- **`discord.ui.View`** — attaches interactive components to a message (buttons, dropdowns)
- **`discord.ui.Select`** — dropdown with up to 25 options per menu
- **`discord.ui.Button`** — clickable button
- **Max 5 action rows** per message; **max 5 components per row**
- Views time out (default 180 s); must handle `on_timeout` gracefully
- Interaction responses must arrive within **3 seconds** (use `defer()` for slow operations)

### Proposed Flow

```
/cardsearch
    └─► Bot sends a message with 3 Select dropdowns + a 🔍 Search button:
        ┌─────────────────────────────────────────────────────────┐
        │  🔎 Card Search                                          │
        │  ┌──────────────────┐  ┌──────────────────┐            │
        │  │ Mana Cost ▾      │  │ Class ▾           │            │
        │  └──────────────────┘  └──────────────────┘            │
        │  ┌──────────────────┐                                    │
        │  │ Card Type ▾      │  [ 🔍 Search ]  [ ✖ Cancel ]     │
        │  └──────────────────┘                                    │
        └─────────────────────────────────────────────────────────┘

    └─► Player sets filters, clicks Search
    └─► Bot edits the message with a paginated result list (10 cards/page)
        with ◀ Prev / ▶ Next buttons
```

### Filter Options
| Filter | Options |
|---|---|
| Mana Cost | Any, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10+ |
| Class | Any, Druid, Hunter, Mage, Paladin, Priest, Rogue, Shaman, Warlock, Warrior, Demon Hunter, Death Knight, Neutral |
| Card Type | Any, Minion, Spell, Weapon, Hero, Location |

### Result Display
- Each page shows up to 10 cards as text lines: `⚪ Wisp`
- Optional: clicking a card name button triggers `/card <name>` to show its image
- Results capped at 100 to avoid hitting Discord message size limits

### Implementation Notes
- Filter state stored inside the `View` instance (per interaction, ephemeral)
- Search runs against the in-memory `_name_index` / `_card_db` already loaded by `HSJsonClient`
- Use `ephemeral=True` so filter UI is only visible to the requesting user
- New file: `bot/commands/search_commands.py`, new cog `SearchCommands`

---

## 2. Player Legend Rank Query

### Goal
Players can query their current Hearthstone legend rank directly in Discord — no parameters needed once their BattleTag is registered. Optionally specify a game mode to narrow the output.

### Data Source — Blizzard Official Public API

```
GET https://hearthstone.blizzard.com/en-us/api/community/leaderboardsData
    ?region={region}
    &leaderboardId={mode}
    &page={page}
    [&seasonId={id}]   ← optional, omit for current season
```

**Parameters:**

| Parameter | Values |
|---|---|
| `region` | `US`, `EU`, `AP` |
| `leaderboardId` | `standard`, `wild`, `classic`, `battlegrounds`, `battlegroundsduo`, `arena`, `twist` |
| `page` | 1-based integer; response includes `pagination.totalPages` |

**Response shape (per page, 25 entries):**
```json
{
  "leaderboard": {
    "rows": [
      { "rank": 1, "accountid": "PlayerName#1234", "rating": null }
    ],
    "pagination": { "totalPages": 40 }
  },
  "season": { "seasonId": 134, "leaderboardId": "standard", "region": "EU" }
}
```

> Player names are returned as bare BattleTag `Name#NNNN` strings.  
> No auth token required — this endpoint is fully public.

### Caching Strategy
- Fetch and store all pages for a (region, mode, seasonId) combination as a single **snapshot** in SQLite.
- Cache TTL: **10–15 minutes** — refresh only if snapshot is older than TTL when queried.
- Schema additions to existing `bot/services/db.py`:
  ```sql
  CREATE TABLE ldb_snapshots (
      id          INTEGER PRIMARY KEY,
      region      TEXT NOT NULL,
      mode        TEXT NOT NULL,
      season_id   INTEGER NOT NULL,
      fetched_at  TEXT NOT NULL  -- ISO-8601 UTC
  );

  CREATE TABLE ldb_entries (
      snapshot_id INTEGER NOT NULL REFERENCES ldb_snapshots(id),
      rank        INTEGER NOT NULL,
      battletag   TEXT NOT NULL,   -- normalised to lower-case for lookup
      rating      INTEGER,
      PRIMARY KEY (snapshot_id, rank)
  );
  ```
- Normalise stored BattleTag to **lower-case** for case-insensitive lookup; display the original casing from the API in output.

### BattleTag Registration
Players register once per Discord user so `/rank` needs no arguments thereafter.

```sql
CREATE TABLE user_battletags (
    discord_id  TEXT PRIMARY KEY,
    battletag   TEXT NOT NULL,   -- original casing
    region      TEXT NOT NULL    -- default region: EU
);
```

- `/rankset battletag:<Name#1234> region:<EU|US|AP>` — registers / updates the caller's BattleTag
- Stored in existing SQLite DB; no guild scope (user-level, persists across servers)

### Commands

#### `/rank [mode]`
- `mode` is **optional** — choices: `standard`, `wild`, `bg` (maps to `battlegrounds`)
- If omitted: shows **Standard** and **Wild** ranks side by side
- If specified: shows only the requested mode
- Requires prior `/rankset`; if not registered → ephemeral prompt to run `/rankset`

**Default output (no mode):**
```
🏆 PlayerName#1234 — EU Legend  ·  Season 134

  Standard  │  Wild
  ─────────────────
    #842     │  #1205
```

**Mode-specific output:**
```
🏆 PlayerName#1234 — EU Legend  ·  Season 134
Battlegrounds  →  #317
```

#### `/rankset battletag:<Name#1234> region:<EU|US|AP>`
- Validates format with regex `^\w+#\d+$`
- Upserts into `user_battletags`
- Confirms with ephemeral reply

### Implementation Plan

| Step | File | Description |
|---|---|---|
| 1 | `bot/services/db.py` | Add `ldb_snapshots`, `ldb_entries`, `user_battletags` tables to schema |
| 2 | `bot/services/leaderboard_client.py` | Async HTTP client — fetch all pages for (region, mode), return flat list of `(rank, battletag, rating)` |
| 3 | `bot/services/leaderboard_cache.py` | Check snapshot freshness, fetch+store if stale, return entries from DB; expose `lookup(battletag, region, mode)` |
| 4 | `bot/commands/rank_commands.py` | `/rank [mode]` + `/rankset` slash commands; new cog `RankCommands` |
| 5 | `bot/main.py` | Load `bot.commands.rank_commands` extension |

### Edge Cases
- Player not on leaderboard (not legend this season) → "Not found in Top X legend this season."
- API pagination failure (network error mid-fetch) → log warning, return stale cache if available
- BattleTag name changes → user must re-run `/rankset`
- CN region uses a different endpoint (`webapi.blizzard.cn`) — out of scope for now

---

## Priority Order

| # | Feature | Complexity |
|---|---|---|
| 1 | Card Search UI | Medium |
| 2 | Player Rank Query | Medium |
