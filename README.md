# HS-Snake — Hearthstone Discord Bot

A Discord bot for Hearthstone: decode deck codes, visualise decks, search cards, look up legend ranks, and auto-detect deck codes posted anywhere in your server.

See [INSTALL.md](INSTALL.md) for setup and deployment instructions.
See [DESIGN.md](DESIGN.md) for the full architecture and task breakdown.

---

## Features

### Deck Commands

| Command | Description |
|---|---|
| `/deck <code>` | Decode a deck code and display a card list with rarity icons, mana cost, format label, and total dust cost. Decks containing **E.T.C. Band Manager** show the 3 sideboard cards in a separate section. |
| `/deckanalyze <code>` | Detailed grouped analysis: cards split by type (Minions, Spells, Weapons, Locations, Heroes), subtype/tribe info, mana curve bar chart, and a dedicated **E.T.C. Band Manager sideboard** section when present. |
| `/deckimage <code>` | Render a visual image of the deck with card thumbnails. E.T.C. sideboard cards are included after the main deck cards. |

### Card Commands

| Command | Description |
|---|---|
| `/card <name>` | Look up a single card by name and display its full card image. |
| `/cardsearch [name]` | Interactive card search with dropdown filters for **Mana Cost**, **Class**, and **Card Type**; paginated results (10 per page, up to 100); click any result to view its card image. |

### Legend Rank Commands

| Command | Description |
|---|---|
| `/rankset <battletag> <region>` | Register a BattleTag for a region (EU / US / AP). Run once per region you play in. |
| `/rankremove <region>` | Remove your BattleTag registration for a region. |
| `/rank [mode] [region]` | Show your current legend rank across all registered regions. Optional filters for game mode (Standard, Wild, Classic, Battlegrounds, Battlegrounds Duo, Arena, Twist) and region. |

### Auto-Detection

The bot can passively monitor channels for Hearthstone deck codes:

- When a deck code is detected in a watched channel the bot replies with a deck image automatically.
- **@mention + deck code** always triggers a reply, regardless of channel settings.
- Fully configurable per server — see Admin Commands below.

### Admin Commands (`/botadmin`)

All subcommands require the configured admin role, Administrator permission, or server ownership.

| Command | Description |
|---|---|
| `/botadmin setrole <role>` | Set the role that is allowed to manage bot settings (requires Administrator permission). |
| `/botadmin autodetect on\|off` | Enable or disable automatic deck-code detection for this server. |
| `/botadmin allchannels on\|off` | `on` — monitor every text channel; `off` — only channels on the watch list. |
| `/botadmin addchannel <channel>` | Add a channel to the deck-detection watch list. |
| `/botadmin removechannel <channel>` | Remove a channel from the deck-detection watch list. |
| `/botadmin status` | Show the current bot configuration (admin role, auto-detect state, monitored channels). |

---

## Installation

See [INSTALL.md](INSTALL.md) for full setup instructions (Docker, local development, environment variables).

---

### Background Tasks

- **Quick refresh** (every 5 min) — updates top ~500 leaderboard entries per region/mode combination (EU, US, AP × Standard, Wild)
- **Full refresh** (every 30 min) — fetches all leaderboard pages for complete rank coverage

---

## Inspiration

This project was inspired by two open-source Hearthstone tools:

- [d0nkey.top](https://github.com/borisbabic/d0nkey.top) — Hearthstone statistics and deck tracking platform; reference for deck code parsing, sideboard handling, and leaderboard data.
- [HearthstoneDeckViewDS](https://github.com/hextract/HearthstoneDeckViewDS) — deck image rendering; reference for the visual card grid layout.

---

## Legal

This project is not affiliated with or endorsed by Blizzard Entertainment.
Hearthstone and all related assets are property of Blizzard Entertainment.
Card images are sourced from [HearthstoneJSON](https://hearthstonejson.com).

---

## License

MIT
