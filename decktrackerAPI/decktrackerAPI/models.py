"""
Pydantic request/response models matching payload-definition.md field-for-field.

Note the payload's inconsistent casing (an artifact of the C# client's
serializer): top-level fields are camelCase, but the nested `rank`/`rankAfter`
objects use PascalCase field names.
"""
import logging
from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# EU/US/AP match the bot's region choices (bot/commands/rank_commands.py
# _REGION_CHOICES). UNKNOWN and CHINA are legitimate per payload-definition.md
# ("Region" section): UNKNOWN means the plugin's region lookup hadn't completed
# yet when the match started, CHINA is a separate client not expected in
# practice but documented as possible. A value outside this set is still
# accepted and stored as-is (just logged) rather than rejected — an unexpected
# string shouldn't drop an otherwise-valid match.
_KNOWN_REGIONS = {"EU", "US", "AP", "UNKNOWN", "CHINA"}


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
    # Optional: added to the payload after schemaVersion 1 shipped, so older plugin
    # builds may still omit it. Older uploads land with region = NULL in the DB.
    region: Optional[str] = None
    player_battletag: Annotated[str, Field(alias="playerBattleTag")]
    opponent_battletag: Annotated[str, Field(alias="opponentBattleTag")]
    rank: RankInfo
    rank_after: Annotated[RankAfterInfo, Field(alias="rankAfter")]

    @field_validator("region")
    @classmethod
    def _normalize_region(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in _KNOWN_REGIONS:
            logger.warning("Unrecognized region %r in /API payload — storing as-is", value)
        return normalized


class UploadResponse(BaseModel):
    status: str
    gameId: str
