"""
Centralised settings loaded from environment variables / .env file.
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    postgres_host: str = field(default_factory=lambda: os.getenv("POSTGRES_HOST", "localhost"))
    postgres_port: int = field(default_factory=lambda: int(os.getenv("POSTGRES_PORT", 5432)))
    postgres_user: str = field(default_factory=lambda: os.getenv("POSTGRES_USER"))
    postgres_password: str = field(default_factory=lambda: os.getenv("POSTGRES_PASSWORD"))
    postgres_db: str = field(default_factory=lambda: os.getenv("POSTGRES_DB"))


settings = Settings()
