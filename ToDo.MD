# HS-Snake Bot — Feature ToDo

---

## Daily Point Score (DPS) & Season Score

**Goal:** Track each registered player's daily DPS and compute a season score using the new log-based formula, keyed by `discord_id`.

### Calculation

- **Daily Point Score (DPS):**
  $$DPS = \log_{10}(N) \times \left( \frac{N - R + 1}{N} \right) \times 100$$
  - $N$: Legend pool size (legend_count)
  - $R$: Player's best rank that day
  - $\log_{10}(N)$: Inflation factor for pool size
  - $100$: Scaling for readability

- **Season Score:**
  $$Season\ Score = \frac{\sum_{d=1}^{n} DPS_d}{n}$$
  - $n$: Number of days in the season so far
  - Average of daily DPS values

### New DB Tables

- **`player_daily_dps`** — `(discord_id, region, mode, season_id, date_utc, dps, best_rank, legend_count, updated_at)`
- **`player_season_score`** — `(discord_id, region, mode, season_id, season_score, days_counted, updated_at)`

### Tasks

- [x] **DPS-01** — Add `player_daily_dps` and `player_season_score` tables to `db.py` migrations
- [x] **DPS-02** — Add `season_score.py` service: functions to record daily DPS and recalculate season score
- [ ] **SR-01** — Recalculate and upsert season score for each player immediately after DPS calculation (in leaderboard refresh), using `season_score.py`
- [ ] **SR-02** — Handle season rollover: when `season_id` changes, do not carry over score; new season starts fresh
- [ ] **SR-03** — Handle missing days gracefully (player not yet in legend or outside top-N scan range)
- [ ] **SR-04** — Show season score, days counted, and best rank achieved alongside DPS in the `/rank` command (no separate `/seasonscore` command)
- [ ] **SR-05** — (Optional) `/seasonleaderboard [region] [mode]` — top-N season scores among all registered users in the server

### Open Questions

- **Timezone handling:** snapshot fires at a fixed UTC time. Players in different timezones may perceive this as slightly unfair at day boundaries — document the UTC cutoff clearly in the command output.
- **Top-N gap:** current leaderboard cache only guarantees ~top-500 for Standard/Wild. Players below this threshold are invisible — decide whether to expand the cache or mark the day as "unranked".
- **Multi-mode:** decide whether a combined cross-mode score is useful or if per-mode scores are sufficient.

---
