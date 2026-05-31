# Refactor: Replace `discord_id` with `battletag` in player tracking tables

## Why

`player_daily_best`, `player_daily_dps`, `player_rank_log`, and `player_season_score` currently
use `discord_id` as the player identifier. This is fragile because the data belongs to a
game account (battletag), not a Discord account. The correct key is `battletag`, and when a
Discord user runs a command the system can resolve their battletag from `user_battletags`.

---

## Tasks

### 1. Schema + Migration — `bot/services/db.py`

**1a. Update `CREATE TABLE IF NOT EXISTS` statements** for the 4 tables: replace `discord_id TEXT NOT NULL` with `battletag TEXT NOT NULL` (lowercase full tag, e.g. `player#1234`). Update PKs and the `idx_prl` index accordingly.

Tables to update:

- `player_rank_log` — replace column + drop/recreate index `idx_prl (battletag, region, mode, season_id, observed_at DESC)`
- `player_daily_best` — replace column + PK `(battletag, region, mode, season_id, date_utc)`
- `player_daily_dps` — replace column + PK `(battletag, region, mode, season_id, date_utc)`
- `player_season_score` — replace column + PK `(battletag, region, mode, season_id)`

**1b. Add a migration block** (inside `_migrate`, after existing blocks) that runs once when the old `discord_id` column is still present:

```sql
DO $$ BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'player_season_score' AND column_name = 'discord_id'
  ) THEN
    -- player_daily_best
    ALTER TABLE player_daily_best ADD COLUMN battletag TEXT;
    UPDATE player_daily_best pdb
      SET battletag = LOWER(ub.battletag)
      FROM user_battletags ub
      WHERE ub.discord_id = pdb.discord_id AND ub.region = pdb.region;
    DELETE FROM player_daily_best WHERE battletag IS NULL;
    ALTER TABLE player_daily_best DROP CONSTRAINT player_daily_best_pkey;
    ALTER TABLE player_daily_best DROP COLUMN discord_id;
    ALTER TABLE player_daily_best ADD PRIMARY KEY (battletag, region, mode, season_id, date_utc);

    -- player_daily_dps
    ALTER TABLE player_daily_dps ADD COLUMN battletag TEXT;
    UPDATE player_daily_dps pdd
      SET battletag = LOWER(ub.battletag)
      FROM user_battletags ub
      WHERE ub.discord_id = pdd.discord_id AND ub.region = pdd.region;
    DELETE FROM player_daily_dps WHERE battletag IS NULL;
    ALTER TABLE player_daily_dps DROP CONSTRAINT player_daily_dps_pkey;
    ALTER TABLE player_daily_dps DROP COLUMN discord_id;
    ALTER TABLE player_daily_dps ADD PRIMARY KEY (battletag, region, mode, season_id, date_utc);

    -- player_rank_log
    ALTER TABLE player_rank_log ADD COLUMN battletag TEXT;
    UPDATE player_rank_log prl
      SET battletag = LOWER(ub.battletag)
      FROM user_battletags ub
      WHERE ub.discord_id = prl.discord_id AND ub.region = prl.region;
    DELETE FROM player_rank_log WHERE battletag IS NULL;
    DROP INDEX IF EXISTS idx_prl;
    ALTER TABLE player_rank_log DROP COLUMN discord_id;
    CREATE INDEX idx_prl ON player_rank_log (battletag, region, mode, season_id, observed_at DESC);

    -- player_season_score
    ALTER TABLE player_season_score ADD COLUMN battletag TEXT;
    UPDATE player_season_score pss
      SET battletag = LOWER(ub.battletag)
      FROM user_battletags ub
      WHERE ub.discord_id = pss.discord_id AND ub.region = pss.region;
    DELETE FROM player_season_score WHERE battletag IS NULL;
    ALTER TABLE player_season_score DROP CONSTRAINT player_season_score_pkey;
    ALTER TABLE player_season_score DROP COLUMN discord_id;
    ALTER TABLE player_season_score ADD PRIMARY KEY (battletag, region, mode, season_id);
  END IF;
END $$;
```

---

### 2. Leaderboard cache — `bot/services/leaderboard_cache.py`

Change the `registered` dict (line ~104) to map `battletag_name_only → battletag_lower_full` instead of `→ discord_id`:

```python
registered: dict[str, str] = {
    row["battletag"].lower().split("#")[0]: row["battletag"].lower()
    for row in await conn.fetch(
        "SELECT battletag FROM user_battletags WHERE region = $1",
        region.upper(),
    )
}
```

Then in `on_page`, replace all `discord_id` lookups/writes with `battletag`:

- `discord_id = registered.get(entry.battletag)` → `battletag = registered.get(entry.battletag)`
- All `INSERT INTO player_rank_log (discord_id, ...)` → `(battletag, ...)`
- All `INSERT INTO player_daily_best (discord_id, ...)` → `(battletag, ...)`
- All `INSERT INTO player_daily_dps (discord_id, ...)` → `(battletag, ...)`
- `recalculate_season_score(discord_id, ...)` → `recalculate_season_score(battletag, ...)`

---

### 3. Season score service — `bot/services/season_score.py`

Rename parameter `discord_id → battletag` throughout `recalculate_season_score` and `recalculate_all_season_scores`. Update all SQL queries to use `battletag` column instead of `discord_id`.

---

### 4. Rank commands — `bot/commands/rank_commands.py`

The `/rank` command already fetches all registered battletags for the user from `user_battletags`. Use `LOWER(row["battletag"])` as the key for all subsequent queries against `player_season_score`, `player_daily_dps`, and `player_rank_log` instead of passing `discord_id`.

Affected query sites (approximate lines): 240–246, 270–335, 396–405.

Pattern change for each: replace the `discord_id = $1` filter with `battletag = $1` and pass `row["battletag"].lower()` as the value.

---

### 5. Guild leaderboard commands — `bot/commands/guild_lb_commands.py`

**5a. Season score query (line ~118–128):** Update JOIN condition:

```sql
-- old:
JOIN user_battletags ub ON ub.discord_id = pss.discord_id AND ub.region = pss.region
-- new:
JOIN user_battletags ub ON LOWER(ub.battletag) = pss.battletag AND ub.region = pss.region
```

Add `ub.discord_id` to the SELECT so the guild member filter (`guild.get_member(int(...))`) still works.

**5b. Rank log query (line ~148–166):** Change `discord_id` filter/column to `battletag`. Since `player_rank_log` no longer carries `discord_id`, the join with the season score results (which now has `discord_id` from step 5a via `user_battletags`) is used for member-filtering only; rank data is fetched by `battletag`.

---

## Verification

1. Start the bot against a dev DB.
2. Run `/rankset` for a test account → confirm `user_battletags` row exists.
3. Trigger a leaderboard refresh → confirm rows appear in `player_daily_dps`, `player_rank_log`, `player_daily_best`, `player_season_score` with `battletag` column instead of `discord_id`.
4. Run `/rank` → confirm correct scores and ranks are displayed.
5. Run `/glb` → confirm leaderboard filters to guild members and shows correct battletags/scores.
6. Run bot against a DB that still has old `discord_id` columns → confirm migration runs cleanly and data is preserved for users with active battletag registrations.

---

## Simplify `/rankset`: register for all regions at once

`/rankset` now accepts only a BattleTag and registers the user for **EU, US, and AP** in one command. No region parameter is needed.

- Update `/rankremove` to also drop all 3 regions with a single command (no region arg), or keep per-region removal if users commonly play in a subset of regions.
- Consider updating the DB schema so `user_battletags` stores one row per `discord_id` (instead of one per `discord_id + region`) if the battletag is the same across all regions for a user. Evaluate trade-offs first.
