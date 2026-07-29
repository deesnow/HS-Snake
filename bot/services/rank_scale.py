"""
Bronze-Diamond <-> Legend rank math for /rankchart and /rcc.

Pure functions/classes only — no DB access, no matplotlib pyplot import (just
matplotlib.ticker for the FuncFormatter helper) — so this module is fully
unit-testable in isolation (see tests/test_rank_scale.py).

Callers are expected to have already filtered out legacy pre-rework data
(league_id < 5 in rank_tracker_matches, per payload-definition.md) before
calling classify() — this module only models the current Bronze->Diamond
star-league system.
"""
from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from matplotlib.ticker import FuncFormatter

_LEAGUES = ("Bronze", "Silver", "Gold", "Platinum", "Diamond")
_MIN_STAR_LEVEL = 1
_MAX_STAR_LEVEL = 50
_DEFAULT_STARS_PER_LEVEL = 3

# How much of the chart's vertical axis the Bronze-Diamond region occupies
# when a chart's data spans both Legend and sub-Legend points, per the
# original feature note ("use the vertical axis lower part, the full scale
# 1/5 or 1/6 part, for bronze-diamond"). Only used in the mixed-regime case —
# single-regime chart data (the common case) always gets the full [0,1] axis.
SUBLEGEND_AXIS_FRACTION = 1 / 6

# Same ±15% padding convention as the old rank_chart._rank_axis_limits.
_AXIS_PAD_FRACTION = 0.15


def _clamp_star_level(star_level: int) -> int:
    return max(_MIN_STAR_LEVEL, min(int(star_level), _MAX_STAR_LEVEL))


def league_label(star_level: int) -> str:
    """
    "Platinum 5"-style label for a StarLevel (1-50), per payload-definition.md's
    League/StarLevel table: StarLevel counts DOWN within each 10-level league
    (1 = bottom, 10 = top of that league). E.g. StarLevel 36 -> "Platinum 5".
    Out-of-range input is clamped rather than raising, since this also backs
    chart tick-label formatting, which must never crash on a slightly-off
    interpolated axis position.
    """
    level = _clamp_star_level(star_level)
    league_index = (level - 1) // 10
    within_level = 11 - (level - league_index * 10)
    return f"{_LEAGUES[league_index]} {within_level}"


def _max_climb_score(stars_per_level: int = _DEFAULT_STARS_PER_LEVEL) -> int:
    return _MAX_STAR_LEVEL * stars_per_level - 1


def climb_score(star_level: int, stars: int, stars_per_level: int = _DEFAULT_STARS_PER_LEVEL) -> int:
    """
    Ordinal "distance climbed toward Legend": 0 (Bronze 10, 0 stars) up to
    `stars_per_level*50 - 1` (Diamond 1, one star short of Legend). Higher is
    better/closer to Legend.

    `stars` is clamped to [0, stars_per_level-1] before scoring. The payload
    doesn't report how many stars are actually required to level up (real
    Hearthstone per-league requirements aren't in payload-definition.md, and
    may not uniformly be `stars_per_level`) — without clamping, an
    out-of-range `stars` value could make the score *decrease* across a
    level-up (e.g. star_level=1,stars=5 outscoring star_level=2,stars=0),
    which would render as a visible backwards dip in a progress chart.
    Clamping guarantees monotonicity across every level-up regardless of
    whether the assumed per-level requirement is exactly right.
    """
    level = _clamp_star_level(star_level)
    clamped_stars = max(0, min(int(stars), stars_per_level - 1))
    return (level - 1) * stars_per_level + clamped_stars


@dataclass(frozen=True)
class RankPoint:
    """
    A single classified rank observation. `ordinal` is always "lower is
    better", regardless of `kind` — mirrors the existing rank_chart
    convention (Legend rank 1 = best) so downstream open/close/low/high
    comparisons (e.g. /rcc's bullish/bearish candle coloring) keep working
    unchanged across both kinds.
    """
    kind: Literal["legend", "subleg"]
    ordinal: int


def classify(
    star_level: int,
    stars: int,
    legend_rank: int,
    stars_per_level: int = _DEFAULT_STARS_PER_LEVEL,
) -> RankPoint:
    """Legend (legend_rank > 0) vs sub-Legend (climb_score), as a RankPoint."""
    if legend_rank and legend_rank > 0:
        return RankPoint(kind="legend", ordinal=int(legend_rank))
    score = climb_score(star_level, stars, stars_per_level)
    # Invert climb_score (higher=better) to an ordinal (lower=better) so it's
    # directly comparable to legend_rank's convention without special-casing.
    ordinal = _max_climb_score(stars_per_level) - score
    return RankPoint(kind="subleg", ordinal=ordinal)


def _padded_bounds(values: Sequence[int], floor: int) -> tuple[int, int]:
    lo, hi = min(values), max(values)
    pad = max(1, round((hi - lo) * _AXIS_PAD_FRACTION))
    return max(floor, lo - pad), hi + pad


def _fraction(ordinal: int, lo: int, hi: int) -> float:
    if hi <= lo:
        return 0.0
    return (ordinal - lo) / (hi - lo)


def _describe_subleg_ordinal(ordinal: float, stars_per_level: int) -> str:
    max_climb = _max_climb_score(stars_per_level)
    climb = max_climb - ordinal
    climb = min(max(climb, 0.0), float(max_climb))
    star_level = int(climb // stars_per_level) + 1
    return league_label(star_level)


class AxisMapper:
    """
    Maps RankPoints to a single [0, 1] chart position (0 = best/top after
    ax.invert_yaxis(), 1 = worst/bottom) and back to human tick labels.

    Built once per chart render from every point that chart will plot:
    - Single-regime data (all-Legend or all-sub-Legend — today's common case)
      occupies the *entire* [0, 1] range, tightly cropped to the observed
      ordinal range ± padding (same spirit as the old `_rank_axis_limits`).
      This alone satisfies "when a player hasn't reached Legend, scale
      precisely to Bronze-Diamond."
    - Mixed-regime data (a season that both climbed Bronze->Diamond and
      reached Legend) splits the axis into a Legend band
      (`1 - SUBLEGEND_AXIS_FRACTION`, on top) and a sub-Legend band
      (`SUBLEGEND_AXIS_FRACTION`, on the bottom), each independently cropped
      to its own observed range. A flat additive offset between the two
      scales was considered and rejected — see ToDo.MD Phase 15/the approved
      plan for why (it squashes both regions flat on a mixed chart).
    """

    def __init__(
        self,
        points: Iterable[RankPoint],
        subleg_fraction: float = SUBLEGEND_AXIS_FRACTION,
        stars_per_level: int = _DEFAULT_STARS_PER_LEVEL,
    ) -> None:
        legend_ordinals = [p.ordinal for p in points if p.kind == "legend"]
        subleg_ordinals = [p.ordinal for p in points if p.kind == "subleg"]
        if not legend_ordinals and not subleg_ordinals:
            raise ValueError("AxisMapper needs at least one point")

        self._stars_per_level = stars_per_level
        self._has_legend = bool(legend_ordinals)
        self._has_subleg = bool(subleg_ordinals)
        mixed = self._has_legend and self._has_subleg

        if mixed:
            self._legend_band_height = 1.0 - subleg_fraction
            self._subleg_band_start = 1.0 - subleg_fraction
            self._subleg_band_height = subleg_fraction
        elif self._has_legend:
            self._legend_band_height = 1.0
            self._subleg_band_start = 1.0
            self._subleg_band_height = 0.0
        else:
            self._legend_band_height = 0.0
            self._subleg_band_start = 0.0
            self._subleg_band_height = 1.0

        if self._has_legend:
            self._legend_lo, self._legend_hi = _padded_bounds(legend_ordinals, floor=1)
        if self._has_subleg:
            self._subleg_lo, self._subleg_hi = _padded_bounds(subleg_ordinals, floor=0)

    def position(self, point: RankPoint) -> float:
        if point.kind == "legend":
            if not self._has_legend:
                raise ValueError("AxisMapper has no Legend points to position against")
            frac = _fraction(point.ordinal, self._legend_lo, self._legend_hi)
            return frac * self._legend_band_height
        if not self._has_subleg:
            raise ValueError("AxisMapper has no sub-Legend points to position against")
        frac = _fraction(point.ordinal, self._subleg_lo, self._subleg_hi)
        return self._subleg_band_start + frac * self._subleg_band_height

    def describe(self, position: float) -> str:
        use_subleg = self._has_subleg and (not self._has_legend or position >= self._subleg_band_start)
        if use_subleg:
            frac = 0.0 if self._subleg_band_height == 0 else (position - self._subleg_band_start) / self._subleg_band_height
            frac = min(1.0, max(0.0, frac))
            ordinal = self._subleg_lo + frac * (self._subleg_hi - self._subleg_lo)
            return _describe_subleg_ordinal(ordinal, self._stars_per_level)

        frac = 0.0 if self._legend_band_height == 0 else position / self._legend_band_height
        frac = min(1.0, max(0.0, frac))
        ordinal = self._legend_lo + frac * (self._legend_hi - self._legend_lo)
        return f"Legend #{round(ordinal)}"

    def formatter(self) -> FuncFormatter:
        return FuncFormatter(lambda position, _tick_index: self.describe(position))
