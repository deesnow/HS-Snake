# Postgres scalability review: hundreds of guilds, thousands of users

## Context
The bot and rank-api share one Postgres 16 container (docker-compose.yml). It works today for a few guilds. The question is whether it holds up at hundreds of guilds and thousands of users.

**Verdict:** Postgres and one instance are fine at this scale. Thousands of users is a small workload for Postgres. The problems are in the **access patterns**: per-message queries, unbounded log growth, N+1 query loops in the refresh, nested pool connections and missing indexes. These will cause pool starvation and slow commands before Postgres itself is under any strain. The fixes below are ordered by impact.

## Findings, with fixes

### P1: will break or degrade first
1. **Every Discord message hits the DB.** `auto_detect.on_message` calls `gs.load()`, which runs 2 queries per message in every guild ([auto_detect.py:112](bot/commands/auto_detect.py#L112), [guild_settings.py:20](bot/services/guild_settings.py#L20)). Across hundreds of guilds this becomes the dominant query load.
   - Fix: add an in-process dict cache in `guild_settings.py` keyed by guild_id. Invalidate it in each `set_*` / `add_channel` / `remove_channel` call. Only the bot writes these rows, so no TTL is needed.
2. **Nested pool acquire and pool starvation.** `refresh_pages` holds a connection for the whole API fetch, which takes minutes. Inside it, `recalculate_season_score` acquires a **second** connection ([season_score.py:17](bot/services/season_score.py#L17)). The 6 combos run in parallel (`asyncio.gather`, [rank_commands.py:148](bot/commands/rank_commands.py#L148)), so they can take up to 12 of `max_size=15`. `/rank` with 3 regions fans out to about 9 concurrent acquires (`_build_section` → 2× `_fetch_entry` + 1 per section). A few simultaneous `/rank` calls exhaust the pool, and `acquire(timeout=10)` then raises errors.
   - Fix: pass `conn` into `recalculate_season_score(conn, ...)`. In `_section_default`/`_section_single`, reuse one connection per section instead of acquiring inside `_fetch_entry`. Better still, collapse the 8 per-section queries into 1–2 queries using `DISTINCT ON` / `WHERE mode = ANY(...)`. Raise the bot pool to about 20. Postgres's default of 100 connections leaves room for both services.
3. **`player_rank_log` grows without limit.** The table gets a row every time a registered player appears in a refresh: full refresh every ~3 min plus quick refresh every 5 min, for 2 modes, with no dedupe and no retention. With about 2,000 tracked legend players that is roughly 1.5M rows/day, or about 45M/month. `/glb` filters it by `(region, mode, season_id, date)`, but `idx_prl` leads with `battletag`, so that query becomes a sequential scan over the whole table ([guild_lb_commands.py:152](bot/commands/guild_lb_commands.py#L152)).
   - Fix: (a) insert only when rank or rating changed since that player's last observation. Keep a per-refresh in-memory dict of last (rank, rating), seeded once per run with `DISTINCT ON`. (b) Add index `(region, mode, season_id, observed_at DESC)`. (c) Add a retention job, e.g. delete rows older than 2–3 seasons, or partition by month later if needed.

### P2: N+1 loops in the refresh hot path ([leaderboard_cache.py:162-235](bot/services/leaderboard_cache.py#L162-L235))
For **each** registered player found on a page, the code runs INSERT log, UPSERT daily_best, `COUNT(*)` over the whole ldb for the combo, UPSERT daily_dps, then `recalculate_season_score`, which does 2 SELECTs and 1 UPSERT. That is about 7 round-trips per player per refresh, run serially. With thousands of players, refresh cycles slow down noticeably and hold their connection longer.
   - Fix: compute `legend_count` once per page, or once per run, instead of once per player. Collect matched players per page and write them with `executemany` for the three inserts. Run season-score recalculation once per run for the players whose daily_dps changed, not once per page hit. The recalculation can also be one SQL `UPSERT ... SELECT SUM(dps)/n` instead of fetching rows into Python.

### P3: indexes and query shape
- `player_season_score`: `/glb` filters by `(region, mode, season_id) ORDER BY season_score`, but the PK leads with battletag. Add index `(region, mode, season_id, season_score DESC)`.
- `user_battletags`: the refresh filters by `region`, and `/glb` joins on `LOWER(battletag), region`. Add index `(region, LOWER(battletag))`.
- `/glb` loads **every** scored player globally, then filters to guild members in Python ([guild_lb_commands.py:118-198](bot/commands/guild_lb_commands.py#L118-L198)). This is acceptable at thousands of users. Note it relies on the member cache / members intent, so it works but is O(all users) per call. Optional: pass `[m.id for m in guild.members]` as `discord_id = ANY($4)`.
- `resolve_current_season_id` runs `MAX(season_id)` plus EXISTS scans on every `/rank`, `/glb` and chart call. Cache the result in memory for about 60 s, or set it from the refresh loop.

### P4: operations and security (not scale-bound, but more important with more users)
- docker-compose.yml **publishes 5432 on the host** with a hardcoded password. Remove the `ports:` mapping (both services reach Postgres over the `internal` network) and move credentials to `.env`.
- No backups: add a nightly `pg_dump` container/cron job.
- Default tuning (`shared_buffers=128MB`): set `shared_buffers`, `work_mem` and `effective_cache_size` via a `command:` in compose once the data grows.
- Schema migrations run inside the bot on every startup (`_migrate`). This is fine for a single process. If the bot is ever split into multiple shard processes, the refresh loops and migrations must run in only one of them, or refreshes will be duplicated. A single process with `AutoShardedBot` is fine up to a few thousand guilds.

## Implementation status (2026-09-28)
Done:
- P1.1 guild settings cache: `guild_settings.py`. Writes invalidate the entry; a generation counter stops a racing load from caching stale data.
- P1.2 no nested acquire: `recalculate_season_score(conn, ...)`. `/rank` uses one connection per section, and the 4 stats queries per mode are now one (`_fetch_mode_stats`). Pool raised to 20.
- P1.3 (a)+(b) rank-log dedupe: a row is written only when rank or rating changes, on the first sighting of the UTC day, or at a 1 h heartbeat (`_RANK_LOG_HEARTBEAT`). Also added index `idx_prl_region_season_time`. The `/glb` per-day query now uses a timestamp range so the index applies.
- P2 batched refresh writes: `executemany` per page, one `legend_count` per page, and season scores recalculated once per run (in `finally`).
- P3 indexes `idx_pss_region_season_score` and `idx_ub_region_btag`. `resolve_current_season_id` has a 60 s cache, invalidated when a refresh starts.
- P4 port 5432 is published only in `docker-compose.dev.yml`. DB credentials come from `.env` (`${POSTGRES_*:-default}`).

Schema cleanup (2026-09-29), run once at startup and tracked in `schema_migrations`:
- Dropped `player_daily_best`: nothing read it, and it duplicated `player_daily_dps.best_rank`.
- New table `ldb_seasons`, one row per season, is now the season-month source. `ldb_refresh_log` keeps only 14 days (`prune_refresh_log`, run after each full refresh).
- `player_rank_log` compacted with zero risk: only rows inside same-day runs of identical readings, older than yesterday, were deleted. Per-day values and all 375 chart images were checked identical before and after. The unused `id` column was dropped.
- Retired the old one-off migrations (`ldb_snapshots`, discord_id→battletag, the column-type normalizer). `CREATE TABLE` now declares TIMESTAMPTZ directly.
- Local DB: 172 MB → 77 MB.

Not done yet:
- P1.3 (c) retention: this deletes history, so it needs a decision on how many seasons to keep, because charts accept old season numbers.
- Same-name clash: two leaderboard players who share a name both match one registered BattleTag, because the leaderboard has no `#tag`. There are 102 such same-timestamp pairs in `player_rank_log`.
- P3 `/glb` `discord_id = ANY(...)` filter (optional).
- P4 backups, Postgres tuning, and the multi-process shard note.

## Files to change
- `bot/services/guild_settings.py`: settings cache
- `bot/services/season_score.py`: accept `conn`; SQL-side computation
- `bot/services/leaderboard_cache.py`: batch writes, dedupe rank log, hoist `legend_count`
- `bot/commands/rank_commands.py`: one connection per section, fewer queries; pool fan-out
- `bot/services/season_id.py`: short TTL cache
- `bot/services/db.py`: new indexes (`CREATE INDEX IF NOT EXISTS`), pool size, retention helper
- `docker-compose.yml`: remove the 5432 port publish, move credentials to env, add a backup job

## Verification
- Run the bot against a dev DB (docker-compose.dev.yml). Confirm `/rank`, `/rankchart`, `/rcc`, `/glb` and auto-detect behave as before.
- Load check: seed `user_battletags` with about 5k synthetic battletags that match real leaderboard names, run one full refresh, and compare the cycle time and the `player_rank_log` insert count before and after.
- Run `EXPLAIN ANALYZE` on the `/glb` queries to confirm index scans on the new indexes.
- Concurrency: fire about 20 parallel `/rank` invocations (or call `_build_section` in a script) during a full refresh. There should be no `acquire` timeouts.
- `SELECT count(*) FROM pg_stat_activity` during a refresh should stay well under the pool limits.
