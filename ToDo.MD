# HS-Snake Bot — Feature ToDo

---

## Season Ranking Score

**Goal:** Track each registered player's best daily legend rank throughout a season and compute a weighted season score that accounts for the size of the legend pool each day.

Calculation: https://gemini.google.com/share/2ec4311c05ac

### Background / Design

- Early in a season very few players reach legend, so a top rank is harder to hold — the legend pool size acts as a weight.
- Each day the player's **best rank** for that UTC calendar day is snapshotted.
- A **day score** is computed as a percentile (higher = better):
  ```
  day_score = (legend_count - best_rank + 1) / legend_count   →  0.0 … 1.0
  ```
- The **season score** is a cumulative weighted average updated each day:
  ```
  total_weighted_score += day_score × legend_count
  total_weight         += legend_count
  season_score          = total_weighted_score / total_weight
  ```
- Keyed per `(discord_id, region, mode, season_id)`.
- Season rollover detected via existing `season_id` from `ldb_current_entries`.

### New DB Tables

- **`rank_daily_snapshots`** — `(discord_id, region, mode, season_id, snapshot_date, best_rank, legend_count)`
- **`rank_season_scores`** — `(discord_id, region, mode, season_id, season_score, days_counted, total_weight, total_weighted_score, updated_at)`

### Tasks

- [ ] **SR-01** — Add `rank_daily_snapshots` and `rank_season_scores` tables to `db.py` migrations
- [ ] **SR-02** — Add `season_score.py` service: functions to record daily snapshot and recalculate season score
- [ ] **SR-03** — Add a daily background task (fires once at ~23:59 UTC) in `rank_commands.py` (or a new cog) that:
  - Reads all distinct `(discord_id, region, mode)` registrations from `user_battletags`
  - Looks up their current rank and total legend count from `ldb_current_entries`
  - Writes a snapshot row; skips players not found in top-N (or records as absent)
  - Recalculates and upserts the season score row
- [ ] **SR-04** — Handle season rollover: when `season_id` changes, do not carry over score; new season starts fresh
- [ ] **SR-05** — Handle missing days gracefully (player not yet in legend or outside top-N scan range)
- [ ] **SR-06** — Implement `/seasonscore [region] [mode]` command — shows the user's current season score, days counted, and best rank achieved
- [ ] **SR-07** — (Optional) `/seasonleaderboard [region] [mode]` — top-N season scores among all registered users in the server

### Open Questions

- **Timezone handling:** snapshot fires at a fixed UTC time. Players in different timezones may perceive this as slightly unfair at day boundaries — document the UTC cutoff clearly in the command output.
- **Top-N gap:** current leaderboard cache only guarantees ~top-500 for Standard/Wild. Players below this threshold are invisible — decide whether to expand the cache or mark the day as "unranked".
- **Multi-mode:** decide whether a combined cross-mode score is useful or if per-mode scores are sufficient.

---
