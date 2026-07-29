"""
Tests for bot/services/rank_scale.py — Bronze-Diamond <-> Legend rank math.
Run with: pytest tests/
"""
import pytest

from bot.services.rank_scale import (
    AxisMapper,
    RankPoint,
    classify,
    climb_score,
    league_label,
)


# ── league_label ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "star_level,expected",
    [
        (1, "Bronze 10"),
        (10, "Bronze 1"),
        (11, "Silver 10"),
        (20, "Silver 1"),
        (21, "Gold 10"),
        (30, "Gold 1"),
        (31, "Platinum 10"),
        (36, "Platinum 5"),
        (40, "Platinum 1"),
        (41, "Diamond 10"),
        (50, "Diamond 1"),
    ],
)
def test_league_label_boundaries(star_level, expected):
    assert league_label(star_level) == expected


def test_league_label_clamps_out_of_range():
    # Chart tick positions can interpolate slightly outside real data — must
    # never raise, just clamp to the nearest valid league/level.
    assert league_label(0) == league_label(1)
    assert league_label(-5) == league_label(1)
    assert league_label(51) == league_label(50)
    assert league_label(1000) == league_label(50)


# ── climb_score ───────────────────────────────────────────────────────────

def test_climb_score_range():
    assert climb_score(star_level=1, stars=0) == 0
    assert climb_score(star_level=50, stars=2) == 149  # max for stars_per_level=3


def test_climb_score_monotonic_across_every_level_up():
    # For every level-up, climb_score must strictly increase regardless of the
    # `stars` value on either side (including out-of-range/unclamped stars),
    # since the payload never confirms the real per-level star requirement.
    stars_variants = [0, 1, 2, 3, 5, 100, -1]
    for star_level in range(1, 50):
        worst_case_next_level = min(
            climb_score(star_level + 1, s) for s in stars_variants
        )
        best_case_this_level = max(
            climb_score(star_level, s) for s in stars_variants
        )
        assert worst_case_next_level > best_case_this_level, (
            f"level-up {star_level}->{star_level + 1} not monotonic: "
            f"{best_case_this_level} >= {worst_case_next_level}"
        )


def test_climb_score_stars_clamped_not_raising():
    # Stars beyond stars_per_level-1 clamp rather than overflow into the next
    # level's score range.
    assert climb_score(star_level=10, stars=99) == climb_score(star_level=10, stars=2)
    assert climb_score(star_level=10, stars=-5) == climb_score(star_level=10, stars=0)


# ── classify ──────────────────────────────────────────────────────────────

def test_classify_legend():
    point = classify(star_level=0, stars=0, legend_rank=42)
    assert point == RankPoint(kind="legend", ordinal=42)


def test_classify_subleg():
    point = classify(star_level=36, stars=1, legend_rank=0)
    assert point.kind == "subleg"
    # ordinal is "lower is better" — Platinum 5 with 1 star isn't the worst,
    # so it shouldn't be at the max possible ordinal (149).
    assert 0 <= point.ordinal < 149


def test_classify_subleg_ordinal_lower_is_better():
    worse = classify(star_level=1, stars=0, legend_rank=0)   # Bronze 10, 0 stars
    better = classify(star_level=50, stars=2, legend_rank=0)  # Diamond 1, near Legend
    assert better.ordinal < worse.ordinal


# ── AxisMapper ────────────────────────────────────────────────────────────

def test_axis_mapper_single_regime_legend_only_spans_full_range():
    points = [RankPoint("legend", 100), RankPoint("legend", 50), RankPoint("legend", 10)]
    mapper = AxisMapper(points)

    positions = {p.ordinal: mapper.position(p) for p in points}
    # Lower ordinal (better rank) -> lower position (top, after inversion).
    assert positions[10] < positions[50] < positions[100]
    assert all(0.0 <= v <= 1.0 for v in positions.values())
    # Single-regime data should use close to the full axis, not be confined
    # to a narrow band.
    assert max(positions.values()) - min(positions.values()) > 0.5


def test_axis_mapper_single_regime_subleg_only_spans_full_range():
    points = [
        classify(star_level=1, stars=0, legend_rank=0),
        classify(star_level=25, stars=1, legend_rank=0),
        classify(star_level=50, stars=2, legend_rank=0),
    ]
    mapper = AxisMapper(points)
    positions = [mapper.position(p) for p in points]
    assert all(0.0 <= v <= 1.0 for v in positions)
    assert max(positions) - min(positions) > 0.5


def test_axis_mapper_mixed_regime_bands_dont_overlap():
    legend_points = [RankPoint("legend", r) for r in (5, 50, 500)]
    subleg_points = [
        classify(star_level=1, stars=0, legend_rank=0),
        classify(star_level=45, stars=2, legend_rank=0),
    ]
    mapper = AxisMapper(legend_points + subleg_points)

    legend_positions = [mapper.position(p) for p in legend_points]
    subleg_positions = [mapper.position(p) for p in subleg_points]

    # Legend (top band) must be entirely above sub-Legend (bottom band).
    assert max(legend_positions) < min(subleg_positions)
    # Sub-Legend band should be roughly the configured fraction of the axis.
    band_span = max(subleg_positions) - min(subleg_positions)
    assert band_span < 1 / 6 + 0.01


def test_axis_mapper_mixed_regime_respects_custom_fraction():
    legend_points = [RankPoint("legend", r) for r in (1, 1000)]
    subleg_points = [classify(star_level=1, stars=0, legend_rank=0),
                      classify(star_level=50, stars=2, legend_rank=0)]
    mapper = AxisMapper(legend_points + subleg_points, subleg_fraction=0.5)

    legend_positions = [mapper.position(p) for p in legend_points]
    subleg_positions = [mapper.position(p) for p in subleg_points]
    assert max(legend_positions) <= 0.5 + 1e-9
    assert min(subleg_positions) >= 0.5 - 1e-9


def test_axis_mapper_position_raises_for_absent_kind():
    mapper = AxisMapper([RankPoint("legend", 10)])
    with pytest.raises(ValueError):
        mapper.position(RankPoint("subleg", 5))


def test_axis_mapper_requires_at_least_one_point():
    with pytest.raises(ValueError):
        AxisMapper([])


def test_axis_mapper_describe_legend_only():
    points = [RankPoint("legend", 1), RankPoint("legend", 100)]
    mapper = AxisMapper(points)
    label = mapper.describe(mapper.position(RankPoint("legend", 1)))
    assert label.startswith("Legend #")


def test_axis_mapper_describe_subleg_only():
    points = [
        classify(star_level=1, stars=0, legend_rank=0),
        classify(star_level=50, stars=2, legend_rank=0),
    ]
    mapper = AxisMapper(points)
    top_point = classify(star_level=50, stars=2, legend_rank=0)
    label = mapper.describe(mapper.position(top_point))
    assert any(
        label.startswith(prefix)
        for prefix in ("Bronze", "Silver", "Gold", "Platinum", "Diamond")
    )


def test_axis_mapper_describe_mixed_regime_matches_band():
    legend_points = [RankPoint("legend", r) for r in (1, 100)]
    subleg_points = [
        classify(star_level=1, stars=0, legend_rank=0),
        classify(star_level=50, stars=2, legend_rank=0),
    ]
    mapper = AxisMapper(legend_points + subleg_points)

    legend_label = mapper.describe(mapper.position(legend_points[0]))
    assert legend_label.startswith("Legend #")

    subleg_label = mapper.describe(mapper.position(subleg_points[1]))
    assert any(
        subleg_label.startswith(prefix)
        for prefix in ("Bronze", "Silver", "Gold", "Platinum", "Diamond")
    )


def test_axis_mapper_formatter_is_callable_by_matplotlib():
    mapper = AxisMapper([RankPoint("legend", 1), RankPoint("legend", 50)])
    formatter = mapper.formatter()
    # matplotlib.ticker.FuncFormatter.__call__(value, pos=None)
    label = formatter(mapper.position(RankPoint("legend", 1)), 0)
    assert label.startswith("Legend #")
