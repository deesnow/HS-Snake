"""
Quick smoke-test for bot/services/db.py

Run from the project root:
    python -m bot.test_db

Requires env vars: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
"""
import asyncio

from dotenv import load_dotenv
load_dotenv()

from bot.services.db import init_db_pool, get_db

EXPECTED_TABLES = [
    "guild_settings",
    "monitored_channels",
    "user_battletags",
    "ldb_current_entries",
    "ldb_refresh_log",
    "player_rank_log",
    "player_daily_best",
    "player_daily_dps",
    "player_season_score",
]


async def test():
    print("Connecting to database...")
    await init_db_pool()

    async with get_db() as conn:
        version = await conn.fetchval("SELECT version()")
        print(f"Connected! PostgreSQL: {version}\n")

        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        existing = {r["tablename"] for r in rows}

        print("Tables found:")
        all_ok = True
        for table in EXPECTED_TABLES:
            status = "OK" if table in existing else "MISSING"
            if status == "MISSING":
                all_ok = False
            print(f"  [{status}] {table}")

        extra = existing - set(EXPECTED_TABLES)
        if extra:
            print(f"\nExtra tables (not in expected list): {sorted(extra)}")

        print()
        if all_ok:
            print("All expected tables present. DB layer is working correctly.")
        else:
            print("Some tables are missing — migration may have failed.")


if __name__ == "__main__":
    asyncio.run(test())
