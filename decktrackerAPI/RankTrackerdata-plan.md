I want to extend bot rank commands to handle bronze, silver, gold, platina, diamond levels as well, based on the data what ranktracker plugin send to DB into rank_tracker_matches table.

data struckture here is:

```sql
CREATE TABLE IF NOT EXISTS rank_tracker_matches (
    game_id            UUID PRIMARY KEY,             -- idempotency key
    token_id           INTEGER NOT NULL REFERENCES rank_api_tokens(id),
    schema_version     INTEGER NOT NULL,
    start_time         TIMESTAMPTZ NOT NULL,
    end_time           TIMESTAMPTZ NOT NULL,
    game_mode          TEXT NOT NULL,
    format             TEXT NOT NULL,
    result             TEXT NOT NULL,
    was_conceded       BOOLEAN NOT NULL,
    player_battletag   TEXT NOT NULL,
    opponent_battletag TEXT NOT NULL,
    league_id          INTEGER NOT NULL,
    rank               INTEGER NOT NULL,
    star_level         INTEGER NOT NULL,
    stars              INTEGER NOT NULL,
    legend_rank        INTEGER NOT NULL,
    star_level_after   INTEGER NOT NULL,
    stars_after        INTEGER NOT NULL,
    legend_rank_after  INTEGER NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL DEFAULT now()
```

My idea is on ˛/rank command should present bronze-diamond rank data if the player has any.

To present the data use the vertical axis lower part (the full scale 1/5 or 1/6 part) for bronze-diamond range. Each level has 10 sub-part. Eg.: lowest bronze is Bronze10, the highest is bronze1. On each levet player should collect 3 star. this is the star level in DB. After diamond1 3* the player reach legend rank.

When a player did not reached legend, scale the vertical axis to show only Bronze-diamond region, but more precisely.


When the player reached the legend and have ranktracker data, the bot should use both leaderboard fetched (the curent source) and the new table as well to have the most up-to-date data. Leaderboard is more rarely re-freshed, but rank tracker has all matchdata.

I need similar solution for /rcc command.

Create a todo plan put it into ToDo.md.
