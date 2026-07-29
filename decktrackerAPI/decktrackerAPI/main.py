"""
RankTrackerAPI — ingests per-match rank uploads from the RankTrackerPlugin
(Hearthstone Deck Tracker plugin). See RankTrackerAPI-design.md and the
repo's payload-definition.md for the full contract.
"""
import json
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from decktrackerAPI.auth import TokenRecord, get_current_token
from decktrackerAPI.db import close_db_pool, get_db, init_db_pool
from decktrackerAPI.models import MatchUpload, UploadResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db_pool()
    yield
    await close_db_pool()


app = FastAPI(title="RankTrackerAPI", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def malformed_body_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # payload-definition.md: any malformed/mismatched body is a 400, not FastAPI's default 422.
    # include_input=False: pydantic echoes back the raw offending value (which can be raw
    # bytes for a JSON-decode failure) and that isn't always JSON-serializable itself.
    return JSONResponse(
        status_code=400,
        content={"status": "error", "detail": exc.errors(include_url=False, include_input=False)},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/API", response_model=UploadResponse)
async def upload_match(request: Request, token: TokenRecord = Depends(get_current_token)):
    # Auth (Depends above) is resolved before this body runs, so an unauthenticated or
    # revoked-token request is rejected with 401 before we ever parse/validate its body.
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Malformed JSON body"})

    try:
        payload = MatchUpload.model_validate(body)
    except ValidationError as exc:
        logger.warning("Rejected malformed /API payload: %s", exc)
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "detail": exc.errors(include_url=False, include_input=False),
            },
        )

    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO rank_tracker_matches (
                game_id, token_id, schema_version, start_time, end_time, game_mode, format,
                result, was_conceded, region, player_battletag, opponent_battletag,
                league_id, rank, star_level, stars, legend_rank,
                star_level_after, stars_after, legend_rank_after
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12,
                $13, $14, $15, $16, $17,
                $18, $19, $20
            )
            ON CONFLICT (game_id) DO NOTHING
            """,
            payload.game_id,
            token.id,
            payload.schema_version,
            payload.start_time,
            payload.end_time,
            payload.game_mode,
            payload.format,
            payload.result,
            payload.was_conceded,
            payload.region,
            payload.player_battletag,
            payload.opponent_battletag,
            payload.rank.league_id,
            payload.rank.rank,
            payload.rank.star_level,
            payload.rank.stars,
            payload.rank.legend_rank,
            payload.rank_after.star_level_after,
            payload.rank_after.stars_after,
            payload.rank_after.legend_rank_after,
        )

    # Repeat POST of the same gameId is a safe no-op — never an error.
    return UploadResponse(status="ok", gameId=str(payload.game_id))
