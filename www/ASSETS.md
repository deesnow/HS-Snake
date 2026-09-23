# Assets needed for the promo site

`index.html` already references these paths — drop matching files in place and they'll show up on the site with no code changes.

## Brand

| Path | Notes |
|---|---|
| `www/site/img/favicon.png` | Browser tab icon. Not yet added. |
| `www/site/img/Jeeves.png` | Bot's avatar/logo. Added — used in the top bar and as the Open Graph share image. |

## Screenshots — one per command/feature

Capture each by running the command in Discord and cropping to the reply/embed.

| Path | Capture |
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

No screenshot needed for `/hdttoken` (secret-bearing DM), for `/botadmin` subcommands other than `status` (simple one-line confirmations), or for Auto-Detect (description only, no image).

See [PLAN.md](PLAN.md) for the full implementation plan.
