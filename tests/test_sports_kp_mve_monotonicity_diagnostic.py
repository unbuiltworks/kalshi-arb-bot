from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src import sports_kp_mve_monotonicity_diagnostic as d
from src import sports_kp_mve_monotonicity_seed as seed

D = Decimal


def _sel(event, ticker, side, ask):
    return seed.Selected(
        event_ticker=event,
        market_ticker=ticker,
        side=side,
        ask=D(ask),
        depth=D("3"),
        fee=D("0.01"),
        close_time=datetime.now(timezone.utc) + timedelta(hours=2),
    )


def test_pair_stats_show_threshold_geometry():
    rows = [
        _sel("E1", "A", "yes", "0.60"),
        _sel("E2", "B", "no", "0.95"),
        _sel("E3", "C", "yes", "0.92"),
    ]
    out = d._pair_stats({"is_all_yes": False, "is_single_market_per_event": True}, rows)
    assert out["anchor_count"] == 1
    assert out["booster_counts"]["0.90"] == 2
    assert out["booster_counts"]["0.94"] == 1
    assert out["allowed_pair_counts"]["0.94"] == 1
    assert out["best_pair_by_threshold"]["0.94"]["anchor"]["ticker"] == "A"
    assert out["best_pair_by_threshold"]["0.94"]["booster"]["ticker"] == "B"
    assert D(out["best_pair_by_threshold"]["0.94"]["rough_product_penalty"]) == D("0.0300")


def test_all_yes_collection_excludes_no_sides():
    rows = [
        _sel("E1", "A", "yes", "0.60"),
        _sel("E2", "B", "no", "0.99"),
        _sel("E3", "C", "yes", "0.96"),
    ]
    out = d._pair_stats({"is_all_yes": True, "is_single_market_per_event": True}, rows)
    assert out["booster_counts"]["0.94"] == 1
    assert out["allowed_pair_counts"]["0.94"] == 1
    assert out["best_pair_by_threshold"]["0.94"]["booster"]["ticker"] == "C"


def test_same_event_pair_is_rejected_when_collection_requires_distinct_events():
    rows = [_sel("E1", "A", "yes", "0.60"), _sel("E1", "B", "yes", "0.99")]
    out = d._pair_stats({"is_all_yes": False, "is_single_market_per_event": True}, rows)
    assert out["allowed_pair_counts"]["0.94"] == 0
