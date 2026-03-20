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
- Each page shows up to 10 cards as text lines: `⚪ Wisp — 0 mana Neutral Minion`
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
Players can query their current Hearthstone legend rank (and recent stats) directly in Discord.

### Data Source — d0nkey.top API
The open-source [d0nkey.top](https://github.com/borisbabic/d0nkey.top) project exposes a public REST API that aggregates Hearthstone legend ladder data (sourced from HSReplay, Firestone, and direct submissions).

**Relevant endpoints (base URL `https://www.d0nkey.top`):**

| Endpoint | Description |
|---|---|
| `GET /api/leaderboard?battletag=NAME&region=EU` | Fetch current legend rank for a BattleTag |
| `GET /api/player-profile?battletag=NAME` | Extended profile: rank history, decks played |
| `GET /api/grandmasters` | Current Grandmasters season standings |

> **Note:** Confirm exact endpoint signatures from the repo's router/controller files before implementation — the API is not officially documented; inspect `lib/d0nkey_top_web/controllers/` in the source.

### Proposed Command
```
/rank battletag:<BattleTag#12345> region:<EU|US|AP>
```

**Response example:**
```
🏆 PlayerName#1234 — EU Legend
Rank: #842
Season: March 2026
```

### Implementation Notes
- Use existing `HSJsonClient`-style async HTTP pattern via `httpx`
- `region` parameter maps to `EU`, `US`, `AP` (Asia-Pacific)
- BattleTag format validation before making the API call (regex: `\w+#\d+`)
- Cache results for ~5 minutes per BattleTag to avoid hammering the API
- Handle 404 (player not found / not legend) with a friendly message
- New file: `bot/services/donkey_client.py` + command added to a new cog or `deck_commands.py`

---

## Priority Order

| # | Feature | Complexity |
|---|---|---|
| 1 | Card Search UI | Medium |
| 2 | Player Rank Query | Low–Medium |
