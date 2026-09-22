# HS-Snake Promo Website — Plan

## Context

HS-Snake currently has no public-facing web presence — everything (deck decoding, card lookup, legend-rank tracking) is only discoverable by using the Discord bot directly or reading the GitHub README. The `www` branch and an empty `www/` folder already exist as a placeholder for this, but the folder is currently git-ignored (`.gitignore` lines 53-54: `#Static website for bot` / `www/`) and contains nothing.

The goal is a small static promotional website that documents every HS-Snake command with usage/description and inline screenshots, gives visitors the bot invite link, and is deployed the same way the existing `decktrackerAPI` (rank-api) service is: a Docker container on the repo's existing `docker-compose.yml` stack, exposed to the internet via a Cloudflare Tunnel.

Decisions already confirmed with the user:
1. **Reuse the existing Cloudflare Tunnel** (the one already exposing `rank-api` via `dee-service.cc`) — add a second Public Hostname route on that same tunnel pointing at the new `www` service, instead of running a second `cloudflared` container. Same tunnel, same token, two routes.
2. **nginx:alpine** serves the static files (matches the existing `cache` service's image).
3. **Plain hand-written HTML/CSS + minimal optional vanilla JS** — no build step, no Node toolchain, no static-site generator.
4. **Single scrolling page** — hero, then command reference grouped by category, then footer. No multi-page nav.

Screenshots are out of scope for the assistant to produce — the user will capture and drop these in themselves. This plan defines the exact filenames/paths expected so the HTML can reference them directly.

## File layout

```
www/
├── PLAN.md                        # this file
├── Dockerfile
├── docker/
│   └── nginx.conf
└── site/
    ├── index.html
    ├── css/style.css
    ├── js/main.js                 # optional polish: copy-to-clipboard invite link, in-page anchor nav
    └── img/
        ├── favicon.png            # placeholder — no existing source art, user to provide
        ├── logo.png               # optional hero logo — placeholder, user to provide
        └── screenshots/
            ├── deck-example.png
            ├── deckanalyze-example.png
            ├── deckimage-example.png
            ├── card-example.png
            ├── cardsearch-filters-example.png
            ├── cardsearch-results-example.png
            ├── rank-example.png
            ├── rankchart-example.png
            ├── rcc-example.png
            ├── glb-example.png
            └── botadmin-status-example.png
```

`www/site/` is the nginx document root (only what should be served lives here). `www/docker/nginx.conf` and `www/Dockerfile` sit alongside it, mirroring how `decktrackerAPI/Dockerfile` sits at that service's root.

No screenshot for `/hdttoken` (output is a secret-bearing DM, not screenshot-appropriate). No dedicated screenshots for `/rankset`, `/rankremove`, or `/botadmin` subcommands other than `status` (simple one-line confirmations, not visually distinctive) — can be added later following the same `<command>-example.png` pattern.

## `www/Dockerfile`

Single-stage, built from the **repo root** as context (so CI can build it the same way it builds `bot`, and `COPY www/site/` resolves):

```dockerfile
FROM nginx:1.27-alpine

COPY www/site/ /usr/share/nginx/html/
COPY www/docker/nginx.conf /etc/nginx/nginx.conf
```

No custom `USER`/`HEALTHCHECK` — matches the `cache` service, which also runs stock `nginx:1.27-alpine` with no Dockerfile-level hardening and relies on the compose-level healthcheck instead.

## `www/docker/nginx.conf`

Follows `docker/cache/nginx.conf`'s exact structure (stderr error log, `worker_processes 1`, `/health` endpoint returning `200 "ok"`), adding gzip and cache headers appropriate for a static site (the cache service doesn't need these since it's a proxy cache, not a file server):

```nginx
error_log /dev/stderr warn;

worker_processes 1;

events {
    worker_connections 256;
}

http {
    include       mime.types;
    default_type  application/octet-stream;

    access_log off;
    sendfile    on;
    tcp_nopush  on;

    gzip            on;
    gzip_vary       on;
    gzip_types      text/plain text/css application/javascript application/json image/svg+xml;
    gzip_min_length 256;

    server {
        listen 80;
        listen [::]:80;
        server_name www;

        root /usr/share/nginx/html;
        index index.html;

        location /health {
            return 200 "ok";
            add_header Content-Type text/plain;
        }

        location /img/ {
            expires 7d;
        }

        location ~* \.(css|js)$ {
            expires 1d;
        }

        location / {
            try_files $uri $uri/ =404;
        }
    }
}
```

## `docker-compose.yml` changes

Add one new `www` service (no new `cloudflared` container — the existing one gains a second route). Also widen the existing `cloudflared` service's `depends_on` so it waits on both origins being healthy before it starts routing either hostname:

```yaml
  # ── Static promo website ────────────────────────────────────────────
  www:
    image: ghcr.io/deesnow/hs-snake-www:${WWW_TAG:-latest}
    pull_policy: always
    restart: unless-stopped
    networks:
      - internal
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
```

`www` has no `env_file`/`environment` — it's a pure static file server with no app config or DB dependency, unlike `bot`/`rank-api`. It publishes no host port, same as `rank-api` (reachable only via `cloudflared` on the internal network, or a temporary port mapping for local testing).

Existing `cloudflared` block — add `www` to `depends_on`, nothing else changes (same image, same command, same single `TUNNEL_TOKEN`):

```diff
   cloudflared:
     image: cloudflare/cloudflared:latest
     command: tunnel --no-autoupdate run
     env_file: .env
     environment:
       TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN}
     depends_on:
       rank-api:
         condition: service_healthy
+      www:
+        condition: service_healthy
     restart: unless-stopped
     networks:
       - internal
```

No `.env.example` changes needed — reuses the existing `CLOUDFLARE_TUNNEL_TOKEN`, no second token.

Manual one-time setup (in the same Cloudflare Zero Trust tunnel already used for `rank-api`, on the `dee-service.cc` zone — no config file in-repo, this is dashboard-only):
1. Networks → Tunnels → open the existing tunnel (the one already routing to `rank-api`).
2. Add a **second** Public Hostname route: pick a subdomain (e.g. `www` or `hs-snake`) under `dee-service.cc` → Service `HTTP`, URL `www:80`. Leave the existing `rank-api` route untouched. Service name `www` has no underscore, so no hostname-URL rejection issue.
3. No `.env` change needed — the tunnel's existing token already authenticates both routes.
4. `docker compose up -d www` — the running `cloudflared` container picks up the new route automatically (Cloudflare tunnel routes are pulled from the dashboard config at runtime, no container restart required), though restarting it (`docker compose restart cloudflared`) is a safe way to confirm it re-reads the new route immediately.

## `.gitignore` fix

Remove these two lines (nothing under `www/` needs to stay untracked — no build step means no generated artifacts):

```diff
-
-#Static website for bot
-www/
```

## Page content (`www/site/index.html`)

Single scrolling page, semantic HTML5. Structure, reusing README wording verbatim where it already exists:

- **Hero**: "HS-Snake" + one-line pitch ("A Discord bot for Hearthstone: decode deck codes, visualise decks, search cards, look up legend ranks, and auto-detect deck codes posted anywhere in your server."), primary CTA button linking to the invite URL (`https://discord.com/oauth2/authorize?client_id=1484526968929255547&permissions=379968&integration_type=0&scope=bot+applications.commands`).
- **Deck Commands** — `/deck`, `/deckanalyze`, `/deckimage`, each with its screenshot.
- **Card Commands** — `/card`, `/cardsearch`, each with its screenshot.
- **Legend Rank Commands** — `/rankset`, `/rankremove`, `/rank`, `/rankchart`, `/rcc` (the latter two are undocumented in README but present in code — include them; screenshots for `/rank`, `/rankchart`, `/rcc`).
- **Guild Leaderboard** — `/glb` (undocumented in README, present in code), with screenshot.
- **HDT Token** — `/hdttoken` (undocumented in README, present in code), no screenshot (secret DM output).
- **Admin Commands** (`/botadmin ...`) — intro noting the permission requirement, all 7 subcommands listed, screenshot only for `status`.
- **Auto-Detect** — passive feature, not a slash command; styled as a distinct callout, description only (no screenshot).
- **Footer** — MIT license line, Blizzard disclaimer verbatim from README's Legal section ("This project is not affiliated with or endorsed by Blizzard Entertainment. Hearthstone and all related assets are property of Blizzard Entertainment. Card images are sourced from HearthstoneJSON."), GitHub repo link, repeat invite CTA.

Optional `main.js`: copy-to-clipboard for the invite link, and/or an in-page anchor jump list to the command sections — additive polish, not required for a working v1.

## Screenshot filenames the user will provide

| Filename | Captures |
|---|---|
| `www/site/img/screenshots/deck-example.png` | `/deck <code>` output |
| `www/site/img/screenshots/deckanalyze-example.png` | `/deckanalyze <code>` output |
| `www/site/img/screenshots/deckimage-example.png` | `/deckimage <code>` output |
| `www/site/img/screenshots/card-example.png` | `/card <name>` output |
| `www/site/img/screenshots/cardsearch-filters-example.png` | `/cardsearch` filter dropdowns (Mana Cost, Class, Card Type) |
| `www/site/img/screenshots/cardsearch-results-example.png` | `/cardsearch` paginated results list |
| `www/site/img/screenshots/rank-example.png` | `/rank [mode] [region]` output |
| `www/site/img/screenshots/rankchart-example.png` | `/rankchart ...` line chart |
| `www/site/img/screenshots/rcc-example.png` | `/rcc ...` candlestick chart |
| `www/site/img/screenshots/glb-example.png` | `/glb ...` leaderboard table |
| `www/site/img/screenshots/botadmin-status-example.png` | `/botadmin status` output |

Plus, not command screenshots but needed brand assets with no existing source material in the repo: `www/site/img/favicon.png` and (optional) `www/site/img/logo.png`.

## CI (`.github/workflows/docker-publish.yml`)

Add a third matrix entry alongside `bot` and `rank-api` — the workflow's steps already reference `matrix.image`/`matrix.context`/`matrix.file` generically, so no other change is needed:

```diff
       matrix:
         include:
           - name: bot
             image: ghcr.io/deesnow/hs-snake
             context: .
             file: docker/bot/Dockerfile
           - name: rank-api
             image: ghcr.io/deesnow/hs-snake-rank-api
             context: ./decktrackerAPI
             file: decktrackerAPI/Dockerfile
+          - name: www
+            image: ghcr.io/deesnow/hs-snake-www
+            context: .
+            file: www/Dockerfile
```

## Open items to confirm before/while implementing

- **GitHub repo URL** for the footer link — README doesn't state one explicitly; inferred as `https://github.com/deesnow/HS-Snake` from the GHCR namespace (`ghcr.io/deesnow/...`), needs confirmation.
- **Subdomain** to use for the new route on the existing `dee-service.cc` tunnel — dashboard-only decision, no repo impact.
- **Favicon/logo art** — none exists in the repo; ship v1 text-only or wait for assets.
- **Discord support server** — README only lists a contact username (`Deesnow#0840`), no server invite exists to link.

## Verification

1. `git status` after the `.gitignore` fix shows `www/Dockerfile`, `www/docker/nginx.conf`, `www/site/**` as trackable, nothing unexpected swept in.
2. `docker build -f www/Dockerfile -t hs-snake-www:dev .` from repo root — confirms `COPY www/site/` and `COPY www/docker/nginx.conf` resolve against the repo-root context.
3. `docker run --rm -p 8080:80 hs-snake-www:dev` then `curl -i http://localhost:8080/`, `curl -i http://localhost:8080/health` (expect `200 ok`), `curl -I http://localhost:8080/css/style.css` (expect `Cache-Control` header).
4. `docker compose build www && docker compose up -d www && docker compose ps www` — status should reach `healthy` after `start_period`.
5. `docker compose restart cloudflared` and check `docker compose logs cloudflared` — confirm it picks up the new route with no errors, and that the existing `rank-api` route keeps working (no regression to the already-live service).
6. After the second Public Hostname route is added manually in the Cloudflare dashboard: `curl -I https://<chosen-subdomain>.dee-service.cc` should return `200` with the page HTML; spot-check a screenshot path resolves once images are added; also re-check `https://<existing-rank-api-hostname>` still responds normally.
7. Push to `rc` (or open a PR) after the CI change to confirm three parallel matrix jobs run and `ghcr.io/deesnow/hs-snake-www` gets published alongside the other two images.
