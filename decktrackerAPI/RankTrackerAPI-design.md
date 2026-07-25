# RankTrackerAPI — Design

## Overview

RankTrackerAPI is a standalone HTTP service that receives per-match rank
uploads from the Hearthstone Deck Tracker "RankTrackerPlugin" and stores them
in Postgres. It is intentionally decoupled from the `bot/` Discord service —
different runtime (async HTTP server vs. gateway client), different
dependencies, and it owns/migrates its own tables in the shared Postgres
database.

The service is exposed publicly through a Cloudflare Tunnel (`cloudflared`),
since the host it runs on has no public IP.

See the repo root's `payload-definition.md` for the exact upstream payload
contract this service implements against (request/response shape, field
list, idempotency requirement). That doc is local/git-ignored — this design
doc is the tracked, canonical reference going forward.

## Architecture

```
RankTrackerPlugin (HDT)
   │  POST https://<tunnel-hostname>/API
   │  Authorization: Bearer <token>
   ▼
cloudflared (Cloudflare Tunnel, token mode)
   │  http://rank-api:8000  (internal Docker network only)
   ▼
rank-api (FastAPI + uvicorn)
   │  asyncpg
   ▼
postgres (shared with the bot; RankTrackerAPI owns its own tables)
```

All three new/involved containers (`rank-api`, `cloudflared`, and the
existing `postgres`) share the `internal` Docker bridge network defined in
`docker-compose.yml`. `rank-api` does not publish a host port — only
`cloudflared` reaches it, and `cloudflared` itself needs no inbound port
(outbound-only connection to Cloudflare's edge).

The Compose service is named `rank-api` (hyphen, not underscore)
specifically because it doubles as the origin hostname in the Cloudflare
Tunnel's public hostname route — Cloudflare's dashboard rejects underscores
in that field (`http://rank_api:8000` fails validation as "Invalid service
URL format") even though Docker's own internal DNS resolves underscored
service names just fine.

## Technology Stack

- **FastAPI** + **uvicorn** — async HTTP framework; chosen over Flask so
  request bodies can be validated with Pydantic (needed for the documented
  400-on-malformed-body behavior) and so DB access stays async/non-blocking
  like the rest of the stack.
- **asyncpg** — same raw-SQL, no-ORM approach as `bot/services/db.py`.
- **Postgres 16** — the same instance/database the bot uses
  (`hs-snake_db`), via the `internal` network hostname `postgres`.
- **cloudflared** (token-based / remote-managed tunnel) — the tunnel and its
  public-hostname ingress rule are created once in the Cloudflare Zero Trust
  dashboard; the container just runs with a `TUNNEL_TOKEN`, no config file
  or credentials JSON to manage in the repo.

## Database Schema

Owned and migrated by `decktrackerAPI/db.py`'s `_migrate()`, run once at
process startup (`CREATE TABLE IF NOT EXISTS`, additive-only — same
convention as the bot's migration function, just independent of it).

```sql
CREATE TABLE IF NOT EXISTS rank_api_tokens (
    id           SERIAL PRIMARY KEY,
    discord_id   TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,   -- sha256 hex digest; plaintext token is never stored
    label        TEXT,                   -- optional human note set at issuance (e.g. device name)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);

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
);

CREATE INDEX IF NOT EXISTS idx_rank_tracker_matches_battletag
    ON rank_tracker_matches (player_battletag, end_time DESC);
```

`rank_api_tokens.token_hash` stores only a SHA-256 digest — the plaintext
bearer token is generated, handed to the user once, and never persisted.
`rank_tracker_matches.game_id` is the primary key, enforcing the
idempotency requirement directly at the DB level (`ON CONFLICT (game_id) DO
NOTHING`).

## Endpoints

### `POST /API`

- `Authorization: Bearer <token>` required. Token is SHA-256-hashed and
  looked up in `rank_api_tokens` (must exist and not be revoked); on match,
  `last_used_at` is updated. No match → `401`.
- Body validated against `decktrackerAPI/models.py:MatchUpload`, matching
  `payload-definition.md` field-for-field (including its inconsistent casing
  — camelCase top-level, PascalCase inside `rank`/`rankAfter`).
- On success: `INSERT ... ON CONFLICT (game_id) DO NOTHING`, then always
  respond `200 {"status": "ok", "gameId": "<gameId>"}` — a repeat POST of an
  already-seen `gameId` is a no-op, not an error.
- On a body that fails validation (malformed JSON, missing/mistyped
  fields): `400`. FastAPI's default `422` is overridden by a
  `RequestValidationError` exception handler in `main.py`.

### `GET /health`

Plain `{"status": "ok"}`, `200`. Used by the Docker healthcheck and as the
`cloudflared`/compose `depends_on` readiness gate.

## Auth / Token Lifecycle

Token issuance and revocation are **not** implemented by this service —
they're planned as a future bot command (e.g. `/ranktoken generate`,
`/ranktoken revoke`) that would insert/update rows in `rank_api_tokens`
directly (or via a shared helper), keyed by the requesting Discord user's
`discord_id`. Until that command exists, tokens are provisioned manually:

```bash
# token = a securely-generated random string handed to the user out-of-band.
# Hash it client-side (no pgcrypto extension needed) and insert the hex digest:
TOKEN_HASH=$(python -c "import hashlib; print(hashlib.sha256(b'<token>').hexdigest())")
docker exec hs-snake-postgres-1 psql -U hs-snake_user -d hs-snake_db -c \
  "INSERT INTO rank_api_tokens (discord_id, token_hash, label) VALUES ('<discord_id>', '$TOKEN_HASH', 'manual');"
```

## Docker & Deployment

### Containers

- **`rank-api`** — built from `decktrackerAPI/Dockerfile` (build context is
  the `decktrackerAPI/` folder itself — fully self-contained: code,
  `requirements.txt`, `Dockerfile`, and this design doc all live under one
  top-level folder rather than being spread across the repo root/`docker/`
  the way the bot's build is). Published as
  `ghcr.io/deesnow/hs-snake-rank-api`.
- **`cloudflared`** — official `cloudflare/cloudflared` image, runs
  `tunnel --no-autoupdate run`, authenticated purely via a `TUNNEL_TOKEN`
  environment variable (sourced from `.env`, same pattern as the bot's
  secrets) — no config file or credentials JSON in the repo.

### Cloudflare Tunnel setup (one-time, manual)

Not automatable from this repo — done once via the Cloudflare Zero Trust
dashboard (**Networks → Tunnels**):

1. Create a tunnel named e.g. `rank-tracker-api` (choose the
   "Docker"/token-based connector option).
2. Add a **Public Hostname** route on the tunnel pointing
   `<chosen-hostname>` → `http://rank-api:8000` (HTTP, not HTTPS — that's
   plain traffic on the internal Docker network; Cloudflare terminates TLS
   at its edge). Use the hyphenated service name exactly — Cloudflare's
   dashboard rejects underscores in the service URL's host, so
   `rank_api` fails validation while `rank-api` works.
3. Copy the tunnel token shown in the dashboard (or via
   `cloudflared tunnel token rank-tracker-api` if created via CLI) into this
   host's `.env` as `CLOUDFLARE_TUNNEL_TOKEN=<token>`.
4. `docker compose up -d cloudflared` — the container picks up
   `CLOUDFLARE_TUNNEL_TOKEN` via `docker-compose.yml`'s `TUNNEL_TOKEN` env
   var and connects outbound to Cloudflare's edge; no inbound port needed.

### Environment Variables

Same Postgres variables the bot uses, pointed at the same instance, plus the
Cloudflare Tunnel token:

| Variable | Default | Notes |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | `postgres` inside Docker Compose |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_USER` | — required | `hs-snake_user` |
| `POSTGRES_PASSWORD` | — required | `hs-snake_password` |
| `POSTGRES_DB` | — required | `hs-snake_db` |
| `CLOUDFLARE_TUNNEL_TOKEN` | — required (for `cloudflared`) | From the Cloudflare Zero Trust dashboard; set in `.env`, never committed |

## Task Breakdown

### Phase 1 — Service scaffold ✅
- [x] `decktrackerAPI/` package: `config.py`, `db.py`, `auth.py`,
      `models.py`, `main.py`.
- [x] `POST /API` + `GET /health` endpoints.
- [x] Own DB migration (`rank_api_tokens`, `rank_tracker_matches`).

### Phase 2 — Containerization ✅
- [x] `decktrackerAPI/Dockerfile`.
- [x] `rank-api` + `cloudflared` services in `docker-compose.yml`
      (token-based tunnel — `CLOUDFLARE_TUNNEL_TOKEN` from `.env`).
- [x] `rank-api` dev override in `docker-compose.dev.yml`.
- [ ] Cloudflare Tunnel provisioned (manual, one-time — dashboard tunnel +
      public hostname route + `CLOUDFLARE_TUNNEL_TOKEN` in `.env`).

### Phase 3 — CI ✅
- [x] `docker-publish.yml` matrix-builds and publishes
      `ghcr.io/deesnow/hs-snake-rank-api` alongside the bot image.

### Future — Token lifecycle (bot-side, out of scope for this phase)
- [ ] `/ranktoken generate` / `/ranktoken revoke` bot commands.
- [ ] Endpoints/commands to query `rank_tracker_matches` back out
      (dashboards, `/rank` integration, etc.).
