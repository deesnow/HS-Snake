# ToDo — Bronze→Diamond rank progression in /rankchart and /rcc

Extends `/rankchart` and `/rcc` beyond Legend-only data using `rank_tracker_matches`
(per-match uploads from the HDT RankTracker plugin, all leagues Bronze→Legend).
`/rank` stays text-only and unchanged — that idea was explicitly redirected into
`/rankchart`/`/rcc` instead. Full design rationale in the approved plan; see
`DESIGN.md` for the module map this plugs into.

### Phase 13 — decktrackerAPI: accept and store `region` ✅ (pending live-plugin check)
- [x] **T-41** — Add nullable `region TEXT` column to `rank_tracker_matches` via
      additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in
      `decktrackerAPI/decktrackerAPI/db.py` (pre-migration rows get NULL); add a
      supporting `(LOWER(player_battletag), end_time DESC)` index.
      Verified against a real throwaway Postgres container: applying the new
      migration on top of a pre-existing (no-`region`) schema adds the column
      without touching existing rows (they land `region = NULL`), creates the
      new index, and re-running the migration is idempotent.
- [x] **T-42** — Add `region: Optional[str] = None` (alias `"region"`) to
      `MatchUpload` in `decktrackerAPI/decktrackerAPI/models.py`; normalize
      (`.strip().upper()`) and log a warning (don't reject) on a value outside
      `{EU, US, AP, UNKNOWN, CHINA}` — the wider set matches the documented
      possible values in `payload-definition.md`'s "Region" section, not just
      the bot's three selectable regions; include `payload.region` in
      `main.py`'s `INSERT`.
      Verified end-to-end via `httpx.ASGITransport` against the real FastAPI
      app + Postgres: upload with `region` (normalizes `"eu"` → `"EU"`),
      upload without `region` (backward-compat, stores `NULL`), and a
      duplicate `gameId` re-POST (still idempotent, no duplicate row) all
      pass.
- [x] **T-43** — Updated `payload-definition.md` (already documented `region`
      in more detail than expected — includes `UNKNOWN`/`CHINA` cases, so the
      model's known-region set was widened to match) and
      `decktrackerAPI/RankTrackerAPI-design.md` (schema block + new Phase 5
      entry) for the new field/column.
      **Still open**: confirming real ingested `region` values from the
      user's updated HDT plugin actually match what's documented, against a
      live upload — needs the actual plugin running, tracked in
      `RankTrackerAPI-design.md`'s "Future" section.

### Phase 14 — Bot: season/date-range resolution independent of player data
- [ ] **T-44** — `season_id.resolve_season_month(conn, region, mode, season_id) ->
      date`: resolve a season's calendar month from `ldb_refresh_log`
      (append-only, keyed only by region+mode+season_id — not
      `ldb_current_entries`, which loses history every rollover); fall back to
      the current month when a season has zero refresh-log rows yet
- [ ] **T-45** — Reimplement `rank_chart.resolve_days_in_month` as a thin wrapper
      over T-44 — signature/callers unchanged, now works for players with zero
      Legend data in the requested season

### Phase 15 — Bot: Bronze→Diamond rank math (pure, unit-testable)
- [ ] **T-46** — `bot/services/rank_scale.py`: `league_label(star_level) -> str`
      (`league_index=(star_level-1)//10`, `level=11-(star_level-league_index*10)`,
      e.g. StarLevel 36 → "Platinum 5")
- [ ] **T-47** — `rank_scale.climb_score(star_level, stars, stars_per_level=3) ->
      int`: ordinal "distance to Legend"; clamp `stars` to
      `[0, stars_per_level-1]` before scoring so the score stays monotonic across
      a level-up regardless of whether "3" is exactly right for every league
- [ ] **T-48** — `rank_scale.classify(star_level, stars, legend_rank) ->
      RankPoint(kind, ordinal)`: Legend (`legend_rank`) vs sub-Legend
      (`climb_score`) based on `legend_rank > 0`
- [ ] **T-49** — `rank_scale.AxisMapper`: proportional dual-band position mapping
      — single-regime chart data occupies the full `[0,1]` range tightly cropped
      to observed min/max±pad; mixed-regime data splits into a Legend band
      (`1 - SUBLEGEND_AXIS_FRACTION`) and sub-Legend band
      (`SUBLEGEND_AXIS_FRACTION`, module constant, default `1/6`), each
      independently cropped; includes a `FuncFormatter` (position → `"Legend
      #123"` / `"Platinum 5"`)
- [ ] **T-50** — `tests/test_rank_scale.py`: league/level boundary cases
      (StarLevel 1, 10, 11, 40, 50), `climb_score` monotonicity across every
      level-up regardless of `stars`, `AxisMapper` single- and mixed-regime
      position ordering

### Phase 16 — Bot: query `rank_tracker_matches` directly and merge
- [ ] **T-51** — `bot/services/rank_tracker_data.py`: `fetch_tracker_points(conn,
      battletag, region, mode, start, end) -> list[(timestamp, star_level, stars,
      legend_rank)]` — two rows per match (pre-match state at `start_time`,
      post-match state at `end_time`); filters `LOWER(player_battletag)=LOWER($1)`,
      `region=$2`, `game_mode='Ranked'`, `format ILIKE $3`, `end_time` within the
      requested range
- [ ] **T-52** — `rank_tracker_data.merge_with_rank_log(rank_log_points,
      tracker_points) -> list[(timestamp, RankPoint)]`: true chronological union
      of both sources sorted by timestamp — **never** excludes either source for
      a given day; `player_rank_log` observations (always Legend, classified as
      such) keep showing rank movement on days without a tracked match, while
      `rank_tracker_matches` points (classified via `rank_scale.classify`) add
      all-league, per-match granularity where available
- [ ] **T-53** — `rank_tracker_data.aggregate_by_day(merged_points, axis_mapper)
      -> dict[day, OHLC]`: build the `AxisMapper` from the *entire* merged stream
      first (so single-/mixed-regime scaling reflects the whole season, not just
      one day), map every point to its `[0,1]` position, then compute per-UTC-day
      open (first position)/close (last position)/low/high (extremes) from
      positions — never raw `legend_rank`/`climb_score` values, which aren't
      directly comparable across Legend/sub-Legend kinds

### Phase 17 — Bot: wire it into /rankchart and /rcc
- [ ] **T-54** — Update `rank_chart.render_season_chart`/`render_today_chart` to
      accept merged, `rank_scale`-classified points and render via
      `AxisMapper.position()`/`AxisMapper.formatter()` instead of raw rank values
      / `_rank_axis_limits`
- [ ] **T-55** — Update `rank_chart.render_candlestick_chart` the same way;
      recompute `epsilon` in position-space (uniformly `[0,1]`-scaled regardless
      of source regime) so candle bodies stay proportionate on Legend,
      sub-Legend, or mixed (Legend-reached-that-day) candles
- [ ] **T-56** — Wire `rank_commands.rankchart`/`rcc` to fetch both sources, call
      T-52's merge and T-53's day-aggregation, and render via T-54/55, in place
      of the current direct `player_rank_log`-only path; catch
      `asyncpg.exceptions.UndefinedColumnError` around the `region`-filtered
      query and degrade to `player_rank_log`-only (logged warning) — guards the
      deploy-ordering race where `bot`/`rank-api` start in parallel
      (`docker-compose.yml` has no `depends_on` between them) and the bot could
      query `rank_tracker_matches.region` before decktrackerAPI's own migration
      creates it
- [ ] **T-57** — Update `DESIGN.md`'s `/rankchart`/`/rcc` sections and the
      `rank_chart` module doc for the merged data sources and dual-band axis

### Future
- [ ] Endpoints/commands to query `rank_tracker_matches` for purposes beyond
      charting.
- [ ] Standalone `/hdttoken revoke`.
