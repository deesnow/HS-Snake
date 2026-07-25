"""
Pydantic request/response models matching payload-definition.md field-for-field.

Note the payload's inconsistent casing (an artifact of the C# client's
serializer): top-level fields are camelCase, but the nested `rank`/`rankAfter`
objects use PascalCase field names.
"""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RankInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    league_id: Annotated[int, Field(alias="LeagueId")]
    rank: Annotated[int, Field(alias="Rank")]
    star_level: Annotated[int, Field(alias="StarLevel")]
    stars: Annotated[int, Field(alias="Stars")]
    legend_rank: Annotated[int, Field(alias="LegendRank")]


class RankAfterInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    star_level_after: Annotated[int, Field(alias="StarLevelAfter")]
    stars_after: Annotated[int, Field(alias="StarsAfter")]
    legend_rank_after: Annotated[int, Field(alias="LegendRankAfter")]


class MatchUpload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_version: Annotated[int, Field(alias="schemaVersion")]
    game_id: Annotated[UUID, Field(alias="gameId")]
    start_time: Annotated[datetime, Field(alias="startTime")]
    end_time: Annotated[datetime, Field(alias="endTime")]
    game_mode: Annotated[str, Field(alias="gameMode")]
    format: str
    result: str
    was_conceded: Annotated[bool, Field(alias="wasConceded")]
    player_battletag: Annotated[str, Field(alias="playerBattleTag")]
    opponent_battletag: Annotated[str, Field(alias="opponentBattleTag")]
    rank: RankInfo
    rank_after: Annotated[RankAfterInfo, Field(alias="rankAfter")]


class UploadResponse(BaseModel):
    status: str
    gameId: str
