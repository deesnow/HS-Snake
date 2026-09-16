"""
Bearer-token authentication for the plugin upload endpoint.

Tokens are stored hashed (SHA-256) in rank_api_tokens — the plaintext token
is never persisted, only handed to the user once at issuance time (by a
future bot command; see decktrackerAPI/RankTrackerAPI-design.md).
"""
import hashlib
from dataclasses import dataclass

from fastapi import Header, HTTPException

from decktrackerAPI.db import get_db


@dataclass
class TokenRecord:
    id: int
    discord_id: str


async def get_current_token(authorization: str | None = Header(default=None)) -> TokenRecord:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT id, discord_id FROM rank_api_tokens WHERE token_hash = $1 AND revoked_at IS NULL",
            token_hash,
        )
        if row is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked token")

        await conn.execute(
            "UPDATE rank_api_tokens SET last_used_at = now() WHERE id = $1", row["id"]
        )

    return TokenRecord(id=row["id"], discord_id=row["discord_id"])
