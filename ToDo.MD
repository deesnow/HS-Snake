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

### Phase 14 — Bot: season/date-range resolution independent of player data ✅
- [x] **T-44** — `season_id.resolve_season_month(conn, region, mode, season_id) ->
      date`: resolve a season's calendar month from `ldb_refresh_log`
      (append-only, keyed only by region+mode+season_id — not
      `ldb_current_entries`, which loses history every rollover); fall back to
      the current month when a season has zero refresh-log rows yet.
      Verified against a real throwaway Postgres running the bot's actual
      migration: resolves season 100 (seeded with a March 2026 refresh-log
      row) to `2026-03-01`, a different season (Feb 2026) to `2026-02-01`,
      and a season_id with no refresh-log rows at all falls back to the
      current month.
- [x] **T-45** — Reimplemented `rank_chart.resolve_days_in_month` as a thin
      wrapper over T-44 — signature/callers unchanged.
      Verified the specific case this fixes: seeded season 100 with
      `player_rank_log` data for one player but zero rows for another
      ("ghost#1234"), and confirmed `resolve_days_in_month` still correctly
      resolves to 31 days (March) even though it no longer reads any
      player-specific data at all — the old implementation would have
      returned `None`/broken for that player. Also confirmed the resolved day
      count is actually read per-season (31 for March, 28 for Feb 2026), not
      a hardcoded fallback.

### Phase 15 — Bot: Bronze→Diamond rank math (pure, unit-testable) ✅
- [x] **T-46** — `bot/services/rank_scale.py`: `league_label(star_level) -> str`
      (`league_index=(star_level-1)//10`, `level=11-(star_level-league_index*10)`,
      e.g. StarLevel 36 → "Platinum 5"). Clamps out-of-range input instead of
      raising (needed since it also backs chart tick-label formatting, which
      must never crash on an interpolated axis position).
- [x] **T-47** — `rank_scale.climb_score(star_level, stars, stars_per_level=3) ->
      int`: ordinal "distance climbed toward Legend" (0..149 for the default
      3 stars/level); clamps `stars` to `[0, stars_per_level-1]` before
      scoring so the score stays monotonic across a level-up regardless of
      whether "3" is exactly right for every league.
- [x] **T-48** — `rank_scale.classify(star_level, stars, legend_rank) ->
      RankPoint(kind, ordinal)`: Legend (`legend_rank`) vs sub-Legend
      (`climb_score`, inverted to "lower is better" so it's directly
      comparable to `legend_rank`'s convention) based on `legend_rank > 0`.
- [x] **T-49** — `rank_scale.AxisMapper`: proportional dual-band position
      mapping — single-regime chart data occupies the full `[0,1]` range
      tightly cropped to observed min/max±pad; mixed-regime data splits into
      a Legend band (`1 - SUBLEGEND_AXIS_FRACTION`) and sub-Legend band
      (`SUBLEGEND_AXIS_FRACTION`, module constant, default `1/6`), each
      independently cropped; `.formatter()` returns a `matplotlib.ticker.
      FuncFormatter` (position → `"Legend #123"` / `"Platinum 5"`).
- [x] **T-50** — `tests/test_rank_scale.py` (28 tests, all passing): league/
      level boundary cases (StarLevel 1, 10, 11, 20, 21, 30, 31, 36, 40, 41,
      50) plus out-of-range clamping; `climb_score` monotonicity across every
      one of the 49 level-ups for a spread of `stars` values including
      unclamped/out-of-range ones; `classify`'s Legend/sub-Legend dispatch
      and ordinal inversion; `AxisMapper` single-regime (legend-only,
      sub-Legend-only) full-range positioning, mixed-regime non-overlapping
      bands (default and custom `subleg_fraction`), `describe()`/formatter
      round-trips, and error handling for an absent kind or zero points.
      Full suite (`pytest tests/`) still green — 31 passed, no regressions.

### Phase 16 — Bot: query `rank_tracker_matches` directly and merge ✅
- [x] **T-51** — `bot/services/rank_tracker_data.py`: `fetch_tracker_points(conn,
      battletag, region, mode, start, end) -> list[(timestamp, star_level, stars,
      legend_rank)]` — two rows per match (pre-match state at `start_time`,
      post-match state at `end_time`); filters `LOWER(player_battletag)=LOWER($1)`,
      `region=$2`, `game_mode='Ranked'`, `format ILIKE $3`, `league_id = 5`
      (excludes legacy pre-rework rows — not modeled by `rank_scale`), `end_time`
      within the requested range.
      Also added `rank_chart.fetch_rank_log_points(conn, battletag, region, mode,
      season_id)` — a season-wide raw (unaggregated) `player_rank_log` fetch,
      the seasonal counterpart to the existing `fetch_today_series`; needed
      because T-52's merge requires real observation timestamps, and every
      existing `player_rank_log` fetcher pre-aggregates to one point/day. Not
      a separately numbered task in the original plan, but required to make
      T-52 possible.
      Verified against a real Postgres running both services' migrations:
      seeded 7 `rank_tracker_matches` rows covering every exclusion case
      (wrong region, legacy `league_id`, wrong `format`, outside the date
      range, `NULL` region) plus 2 in-scope matches (including one that
      transitions sub-Legend → Legend mid-match) — only the 2 in-scope
      matches' 4 points came back; pre/post-match state correctly split into
      separate points; case-insensitive battletag matching and `format ILIKE`
      confirmed.
- [x] **T-52** — `rank_tracker_data.merge_with_rank_log(rank_log_points,
      tracker_points) -> list[(timestamp, RankPoint)]`: true chronological union
      of both sources sorted by timestamp — **never** excludes either source for
      a given day; `player_rank_log` observations (always Legend, classified as
      such) keep showing rank movement on days without a tracked match, while
      `rank_tracker_matches` points (classified via `rank_scale.classify`) add
      all-league, per-match granularity where available.
      Verified: 3 rank_log points + 4 tracker points merge into 7, fully
      chronologically sorted, with nothing dropped from either source.
- [x] **T-53** — `rank_tracker_data.aggregate_by_day(merged_points, axis_mapper)
      -> dict[day, OHLC]`: build the `AxisMapper` from the *entire* merged stream
      first (so single-/mixed-regime scaling reflects the whole season, not just
      one day), map every point to its `[0,1]` position, then compute per-UTC-day
      open (first position)/close (last position)/low/high (extremes) from
      positions — never raw `legend_rank`/`climb_score` values, which aren't
      directly comparable across Legend/sub-Legend kinds.
      Verified with both `tests/test_rank_tracker_data.py` (8 unit tests: UTC
      day-boundary splitting, open/close/low/high correctness, and the mixed
      sub-Legend→Legend day case) and against the real-DB scenario above — the
      match that crosses into Legend mid-day correctly produces a `close`
      position better than its `open`.

Full suite (`pytest tests/`) green — 39 passed, no regressions.

### Phase 17 — Bot: wire it into /rankchart and /rcc ✅
- [x] **T-54** — Updated `rank_chart.render_season_chart`/`render_today_chart` to
      accept merged, `rank_scale`-classified points (now `positions`/`mapper`
      params instead of raw `ranks`) and render via `AxisMapper.position()` (at
      the call site)/`AxisMapper.formatter()` instead of raw rank values /
      `_rank_axis_limits` (deleted — no longer used by anything).
      Also removed `fetch_season_series` and `fetch_season_candles` from
      `rank_chart.py` (superseded by `rank_tracker_data.fetch_rank_log_points`
      + `merge_with_rank_log` + `aggregate_by_day` — dead code otherwise).
- [x] **T-55** — Updated `rank_chart.render_candlestick_chart` the same way
      (`candles` entries and `mapper` param); replaced the old data-range-derived
      `epsilon` with a fixed `_CANDLE_MIN_BODY_HEIGHT = 0.01` constant in
      position-space, uniform regardless of source regime.
- [x] **T-56** — Wired `rank_commands.rankchart`/`rcc` (both the season and
      today branches of `/rankchart`, plus `/rcc`) to fetch both sources, call
      `merge_with_rank_log` and `aggregate_by_day`, and render via T-54/55, in
      place of the old direct `player_rank_log`-only path. Added
      `rank_chart.fetch_rank_log_points` (season-wide raw `player_rank_log`
      fetch — needed because the merge requires real timestamps, not
      pre-aggregated day values) and `season_id.resolve_season_month_range`
      (month_start/next_month_start/days_in_month as UTC datetimes, for the
      `rank_tracker_matches` date-range filter). The deploy-ordering guard is
      `_fetch_tracker_points_safe` in `rank_commands.py`, wrapping
      `rank_tracker_data.fetch_tracker_points` in a try/except for
      `asyncpg.exceptions.UndefinedColumnError`, logging a warning and
      degrading to `player_rank_log`-only (an empty tracker-points list) —
      used by all three call sites (today, season, `/rcc`).
      Verified against a real Postgres running both services' migrations: a
      season mixing sub-Legend matches (climbing Bronze→Diamond) with a
      Legend-scrape data point and a match that reaches Legend mid-month
      produces valid PNGs from all three renderers (`render_season_chart`,
      `render_candlestick_chart`, `render_today_chart` — checked via PNG
      magic-byte + size, since these are binary image outputs); the day the
      player reaches Legend renders as a bullish (green) candle, confirming
      the "lower position = better" convention survived the merge correctly.
      Separately verified the deploy-ordering guard against a DB with
      `rank_tracker_matches` present but *without* the `region` column (the
      exact race window): `_fetch_tracker_points_safe` returns `[]` and logs
      a warning instead of raising, so the command would degrade to
      Legend-only data rather than erroring out.
- [x] **T-57** — Updated `DESIGN.md`: command table, the `rank_chart` module
      doc section (now covers `rank_chart`/`rank_scale`/`rank_tracker_data`
      together), the `/rankchart`/`/rcc` detailed sections, the tech-stack
      table row, and a new "Phase 13-17" entry in the task-breakdown section
      pointing back to this file for the full history.

Full suite (`pytest tests/`) green — 39 passed, no regressions.

### Future
- [ ] Endpoints/commands to query `rank_tracker_matches` for purposes beyond
      charting.
- [ ] Standalone `/hdttoken revoke`.
