# PostgreSQL Migration Plan for HS-Snake Bot

## Overview
This document outlines the steps required to migrate the bot's database from SQLite to PostgreSQL, including data migration, code refactoring, and Docker integration.

---

## Migration Tasks

### 1. Set Up PostgreSQL in Docker
- [x] Add a PostgreSQL service to `docker-compose.yml` and `docker-compose.dev.yml`.
- [x] Configure environment variables for DB connection (user, password, db name, host, port).

### 2. Data Migration
- [x] Export existing SQLite data to a format suitable for PostgreSQL (CSV or SQL dump).
- [x] Create PostgreSQL schema matching the current SQLite schema.
- [x] Import data into PostgreSQL tables.

### 3. Code Refactoring
- [x] Replace `aiosqlite` with an async PostgreSQL driver (e.g., `asyncpg` or `databases[postgresql]`).
- [ ] Update all database access logic to use PostgreSQL connections and SQL dialect.
- [ ] Update connection settings to use environment variables for PostgreSQL.

#### Python files to refactor:
- [ ] `bot/services/db.py` (main DB layer)
- [ ] `bot/services/guild_settings.py` (guild settings CRUD)
- [ ] `bot/services/leaderboard_cache.py` (leaderboard cache and writes)
- [ ] `bot/services/leaderboard_client.py` (if any DB access)
- [ ] `bot/services/season_score.py` (season score calculations)
- [ ] Any other file using `get_db()` or direct DB access

### 4. Testing & Validation
- [ ] Test all bot features for correct DB operation.
- [ ] Validate data integrity after migration.

### 5. Documentation & Cleanup
- [ ] Update `README.md` and `How-To-Install.MD` with new setup instructions.
- [ ] Remove SQLite-specific code and files if no longer needed.

---

## Notes
- PostgreSQL allows concurrent writes and is more robust for multi-user/multi-task bots.
- Use Docker volumes to persist PostgreSQL data.
- Ensure all environment variables are set securely for production.

---

## References
- [asyncpg documentation](https://magicstack.github.io/asyncpg/current/)
- [PostgreSQL Docker image](https://hub.docker.com/_/postgres)
- [SQLAlchemy async docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
