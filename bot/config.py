"""
Centralised settings loaded from environment variables / .env file.
"""
import os
import logging
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable '{name}' is not set.")
    return value


@dataclass
class Settings:
    discord_token: str = field(default_factory=lambda: _require("DISCORD_TOKEN"))
    discord_guild_id: Optional[int] = field(
        default_factory=lambda: int(v) if (v := os.getenv("DISCORD_GUILD_ID")) else None
    )
    hsjson_locale: str = field(default_factory=lambda: os.getenv("HSJSON_LOCALE", "enUS"))
    image_card_size: str = field(default_factory=lambda: os.getenv("IMAGE_CARD_SIZE", "256x"))
    cache_base_url: str = field(
        default_factory=lambda: os.getenv("CACHE_BASE_URL", "http://cache")
    )
    log_level: int = field(
        default_factory=lambda: getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    )


settings = Settings()
