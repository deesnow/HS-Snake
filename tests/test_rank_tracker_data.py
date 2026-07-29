"""
Tests for bot/services/rank_tracker_data.py's pure merge/aggregate logic
(fetch_tracker_points needs a real DB — covered separately, not here).
Run with: pytest tests/
"""
from datetime import date, datetime, timezone

from bot.services.rank_scale import AxisMapper, RankPoint, classify
from bot.services.rank_tracker_data import aggregate_by_day, merge_with_rank_log


def _dt(hour, minute=0, day=1, month=6, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ── merge_with_rank_log ──────────────────────────────────────────────────

def test_merge_interleaves_chronologically():
    rank_log_points = [(_dt(9), 100), (_dt(15), 90)]
    tracker_points = [
        (_dt(10), 36, 1, 0),   # 10:00, sub-Legend
        (_dt(12), 36, 2, 0),   # 12:00, sub-Legend
    ]
    merged = merge_with_rank_log(rank_log_points, tracker_points)
    timestamps = [ts for ts, _ in merged]
    assert timestamps == sorted(timestamps)
    assert timestamps == [_dt(9), _dt(10), _dt(12), _dt(15)]


def test_merge_never_drops_either_source():
    # A day with tracker matches must NOT exclude that day's rank_log points —
    # true chronological union, not day-level source selection.
    rank_log_points = [(_dt(1), 100), (_dt(23), 80)]
    tracker_points = [(_dt(12), 40, 2, 0)]
    merged = merge_with_rank_log(rank_log_points, tracker_points)
    assert len(merged) == len(rank_log_points) + len(tracker_points)
    kinds = [p.kind for _, p in merged]
    assert kinds.count("legend") == 2
    assert kinds.count("subleg") == 1


def test_merge_classifies_tracker_points_correctly():
    tracker_points = [(_dt(10), 36, 1, 0), (_dt(11), 0, 0, 5)]  # second is Legend
    merged = merge_with_rank_log([], tracker_points)
    assert merged[0][1].kind == "subleg"
    assert merged[1][1] == RankPoint(kind="legend", ordinal=5)


def test_merge_with_empty_sources():
    assert merge_with_rank_log([], []) == []
    assert merge_with_rank_log([(_dt(1), 5)], []) == [(_dt(1), RankPoint("legend", 5))]


# ── aggregate_by_day ─────────────────────────────────────────────────────

def test_aggregate_by_day_open_close_low_high():
    # Single day, all Legend: ranks 100 -> 80 -> 90 over the day.
    points = [
        (_dt(1), RankPoint("legend", 100)),
        (_dt(12), RankPoint("legend", 80)),
        (_dt(23), RankPoint("legend", 90)),
    ]
    mapper = AxisMapper([p for _, p in points])
    result = aggregate_by_day(points, mapper)

    day = _dt(1).date()
    assert set(result.keys()) == {day}
    ohlc = result[day]

    # open = first point's position, close = last point's position.
    assert ohlc.open == mapper.position(RankPoint("legend", 100))
    assert ohlc.close == mapper.position(RankPoint("legend", 90))
    # low/high = extremes among that day's positions (rank 80 is best -> lowest position).
    assert ohlc.low == mapper.position(RankPoint("legend", 80))
    assert ohlc.high == mapper.position(RankPoint("legend", 100))


def test_aggregate_by_day_splits_on_utc_boundary():
    points = [
        (datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc), RankPoint("legend", 50)),
        (datetime(2026, 6, 2, 0, 1, tzinfo=timezone.utc), RankPoint("legend", 40)),
    ]
    mapper = AxisMapper([p for _, p in points])
    result = aggregate_by_day(points, mapper)
    assert set(result.keys()) == {date(2026, 6, 1), date(2026, 6, 2)}


def test_aggregate_by_day_mixed_regime_uses_position_not_raw_ordinal():
    # A day where the player was sub-Legend in the morning, then reached
    # Legend by evening — raw ordinals (climb-derived vs legend_rank) aren't
    # comparable, but positions are: Legend should end up with a lower
    # (better/topper) position than sub-Legend regardless of raw magnitude.
    subleg_point = classify(star_level=50, stars=2, legend_rank=0)  # near-Legend
    legend_point = classify(star_level=0, stars=0, legend_rank=500)  # just reached Legend
    points = [
        (_dt(8), subleg_point),
        (_dt(20), legend_point),
    ]
    mapper = AxisMapper([p for _, p in points])
    result = aggregate_by_day(points, mapper)
    ohlc = result[_dt(8).date()]

    assert ohlc.open == mapper.position(subleg_point)
    assert ohlc.close == mapper.position(legend_point)
    # Legend (top band) must be better (lower position) than sub-Legend (bottom band).
    assert mapper.position(legend_point) < mapper.position(subleg_point)
    assert ohlc.low == mapper.position(legend_point)
    assert ohlc.high == mapper.position(subleg_point)


def test_aggregate_by_day_empty_input():
    assert aggregate_by_day([], AxisMapper([RankPoint("legend", 1)])) == {}
