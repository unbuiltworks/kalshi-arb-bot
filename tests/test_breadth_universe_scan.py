"""Regressions for the P1 opportunity-funnel scan.

Every test here pins a defect the first run of the scanner actually shipped.
The scan ranks categories for breadth expansion, so a derivation bug in it
does not produce an obviously broken number -- it produces a plausible
ranking that points the research at the wrong venue.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "analysis/l5/breadth/scan_universe.py"


def _load():
    spec = importlib.util.spec_from_file_location("scan_universe", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scan_universe"] = mod
    spec.loader.exec_module(mod)
    return mod


S = _load()
NOW = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)


def _mkt(**kw):
    base = {"slug": "m", "accepting_orders": True, "order_book": True,
            "best_bid": 0.5, "best_ask": 0.51, "spread": 0.01,
            "liquidity": 1000.0, "volume24hr": 100.0, "volume_total": 100.0,
            "last_price": 0.5, "tick": 0.01, "min_size": 5.0,
            "rewards_max_spread": 0.03, "rewards_min_size": 50.0,
            "neg_risk": False, "category": "crypto", "event": "e",
            "horizon_min": 30.0, "horizon_state": "pre_end",
            "horizon_bucket": "<=1h"}
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# READ-ONLY BY CONSTRUCTION
# --------------------------------------------------------------------------

FORBIDDEN = ("place_order", "submit_order", "post_order", "cancel_order",
             "create_deployment", "redeploy", "set_variables", "restart_service",
             "buy", "sell", "arm", "execute_trade")


def test_scan_cannot_reach_the_order_or_deploy_path():
    """The docstring promises this file is read-only by construction. It said
    so before this test existed, which is exactly the kind of unbacked claim
    the test is here to make true."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not (names & set(FORBIDDEN)), f"order/deploy verb in scan: {names & set(FORBIDDEN)}"


def test_scan_only_talks_to_the_public_gamma_host():
    txt = SRC.read_text(encoding="utf-8")
    assert S.GAMMA == "https://gamma-api.polymarket.com"
    for bad in ("api.key", "PMUS_API", "private_key", "Authorization"):
        assert bad not in txt, f"credential-shaped token in read-only scan: {bad}"


# --------------------------------------------------------------------------
# TRAP (a): startDate is the LISTING date, not the trading-window start
# --------------------------------------------------------------------------

def test_listing_age_is_never_used_as_a_horizon():
    """`endDate - startDate` scored basketball at a 345-day median 'duration'
    and put 0.0 in every short bucket, because startDate is when the market
    was listed. The old derivation must not come back."""
    txt = SRC.read_text(encoding="utf-8")
    assert "duration_minutes" not in txt, "listing-age derivation resurrected"
    assert "startDate" not in txt.split('"""', 2)[2], \
        "startDate used outside the docstring that explains why it must not be"


def test_horizon_is_measured_from_now_not_from_listing():
    end = NOW + timedelta(minutes=7)
    m = {"startDate": "2025-01-01T00:00:00Z", "endDate": end.isoformat()}
    mins, state = S.horizon_minutes(m, NOW)
    assert state == "pre_end"
    assert mins == pytest.approx(7.0)          # not the 600-day listing age


# --------------------------------------------------------------------------
# TRAP (b): endDate is a SCHEDULED end; markets trade past it
# --------------------------------------------------------------------------

def test_market_open_past_its_scheduled_end_is_not_counted_as_short():
    """5,162 of 8,085 markets in the top-100 events were past endDate and
    still accepting orders -- an EPL match plays on past its 15:30Z endDate.
    Bucketing those as 'resolving now' would have inverted the ranking toward
    exactly the sports whose horizon we cannot see."""
    m = {"endDate": (NOW - timedelta(hours=3)).isoformat()}
    mins, state = S.horizon_minutes(m, NOW)
    assert state == "past_scheduled_end"
    assert mins is None, "a negative remaining time must not become a number"
    assert S.horizon_bucket(mins, state) == "past_scheduled_end"
    assert S.horizon_bucket(mins, state) not in {"<=5m", "<=15m", "<=1h"}


def test_past_end_markets_are_excluded_from_the_short_share_not_folded_in():
    rows = [_mkt(slug="a", horizon_min=10.0, horizon_state="pre_end"),
            _mkt(slug="b", horizon_min=None, horizon_state="past_scheduled_end"),
            _mkt(slug="c", horizon_min=None, horizon_state="past_scheduled_end")]
    agg = S.aggregate(rows)["crypto"]
    assert agg["horizon_known"] == 1
    assert agg["share_short_le_1h"] == 1.0, "share must be over KNOWN horizons only"
    assert agg["share_past_scheduled_end"] == round(2 / 3, 4)


def test_no_end_date_is_unknown_not_zero():
    assert S.horizon_minutes({}, NOW) == (None, "unknown")


# --------------------------------------------------------------------------
# TRAP (c): volume ordering hides the entire short-duration lane
# --------------------------------------------------------------------------

def test_scan_walks_both_orderings():
    """Volume-ordered alone, the scan saw ZERO live 5-minute markets: a market
    that lives five minutes has negligible 24h volume and never enters the top
    1,200 events."""
    orders = {o for o, _, _ in S.walks(NOW)}
    assert orders == {"volume24hr", "endDate"}, "single-ordering walk is not a safe sample"


def test_the_short_horizon_walk_is_floored_at_now():
    """Unfloored, endDate-ascending returns the OLDEST-ending markets -- dead
    5-minute markets from December still flagged closed=false, with a 0/1 book.
    81 of 91 updown markets in that walk were stale."""
    floored = [e for o, a, e in S.walks(NOW) if o == "endDate" and a == "true"]
    assert floored and "end_date_min" in floored[0], "short-horizon walk lost its floor"
    assert floored[0]["end_date_min"].startswith("2026-09-13T18:00:00")


def test_union_dedupes_so_an_event_in_both_walks_is_counted_once(monkeypatch):
    ev = {"id": "1", "slug": "s", "tags": [{"label": "Crypto"}], "markets": []}
    monkeypatch.setattr(S, "_walk", lambda *a, **k: [ev, dict(ev)])
    assert len(S.fetch_events(pages=1)) == 1


# --------------------------------------------------------------------------
# LIVENESS: a one-sided book cannot host a pair
# --------------------------------------------------------------------------

def test_one_sided_markets_are_excluded_from_the_spread_median():
    """acceptingOrders && enableOrderBook is NOT sufficient: 3,579 of
    football's 8,486 such markets quote one side only. Including them moved
    the football median spread from 0.35 to 0.88."""
    rows = [_mkt(slug="two", spread=0.01),
            _mkt(slug="one", spread=0.90, best_ask=None)]
    agg = S.aggregate(rows)["crypto"]
    assert agg["tradeable_now"] == 1
    assert agg["listed_with_book"] == 2
    assert agg["median_spread"] == 0.01, "one-sided book poisoned the spread median"
    assert agg["share_one_sided"] == 0.5, "the one-sided count must survive as a metric"


def test_a_market_with_no_book_at_all_is_not_tradeable():
    agg = S.aggregate([_mkt(best_bid=0, best_ask=1, spread=1.0)])["crypto"]
    assert agg["tradeable_now"] == 0


# --------------------------------------------------------------------------
# SCORING: a measurement gap must not read as a bad score
# --------------------------------------------------------------------------

def test_unknown_horizon_drops_the_turnover_term_instead_of_scoring_it_zero():
    """A category that is all in-play sports has no observable horizon. A 0.0
    turnover term there is a measurement gap dressed up as 'slow'."""
    known = {"median_spread": 0.0, "median_liquidity_usd": 5000.0,
             "tradeable_now": 50, "horizon_known": 50, "share_short_le_1h": 1.0}
    gap = dict(known, horizon_known=0, share_short_le_1h=None)
    assert S.structural_score(known) == 1.0
    assert S.structural_score(gap) == 1.0, "renormalise, don't penalise the gap"


def test_thin_categories_are_not_ranked_at_all():
    assert S.structural_score({"median_spread": 0.0, "median_liquidity_usd": 9e9,
                               "tradeable_now": 4}) is None


def test_score_is_monotone_in_spread_tightness():
    a = {"median_spread": 0.00, "median_liquidity_usd": 1000.0, "tradeable_now": 50,
         "horizon_known": 50, "share_short_le_1h": 0.5}
    b = dict(a, median_spread=0.05)
    assert S.structural_score(a) > S.structural_score(b)


# --------------------------------------------------------------------------
# HONESTY: execution metrics are declared missing, never guessed
# --------------------------------------------------------------------------

def test_every_execution_metric_is_declared_unavailable():
    """The residual term decided both live losses and gamma cannot serve it.
    A category ranked as if it could is the error the owner warned against."""
    agg = S.aggregate([_mkt()])["crypto"]
    for k in ("first_leg_maker_fill_freq", "complement_maker_fill_freq",
              "time_to_complement_s", "effective_pair_cost",
              "share_completing_le_1_00", "share_completing_le_0_99",
              "adverse_move_after_first_fill", "residual_frequency",
              "residual_loss_severity", "two_sided_print_frequency",
              "trades_per_minute"):
        assert agg[k] == S.NEEDS_EXEC, f"{k} must not be guessed from gamma"


def test_structural_score_is_never_called_an_expectancy():
    txt = SRC.read_text(encoding="utf-8").lower()
    assert "expected realized economics" in txt
    assert "ranks where to look" in txt


# --------------------------------------------------------------------------
# SHORT-HORIZON BREAKOUT
# --------------------------------------------------------------------------

def test_breakout_only_admits_live_two_sided_pre_end_markets():
    rows = [_mkt(slug="good", horizon_min=5.0),
            _mkt(slug="one_sided", horizon_min=5.0, best_bid=None),
            _mkt(slug="past", horizon_min=None, horizon_state="past_scheduled_end"),
            _mkt(slug="too_long", horizon_min=500.0, horizon_bucket="<=6h"),
            _mkt(slug="closed_book", horizon_min=5.0, order_book=False)]
    out = S.short_horizon_breakout(rows)
    assert out["markets"] == 1
    assert out["categories"]["crypto"]["markets"] == 1


def test_breakout_collapses_the_epoch_suffix_so_a_series_is_one_row():
    rows = [_mkt(slug=f"btc-updown-5m-17893{i:05d}", horizon_min=3.0)
            for i in range(12)]
    top = S.short_horizon_breakout(rows)["categories"]["crypto"]["top_series"]
    assert top == {"btc-updown-5m-#": 12}


# --------------------------------------------------------------------------
# FORMATTING: three columns ran together into one 24-digit blob
# --------------------------------------------------------------------------

def test_numeric_cells_are_rounded_to_the_field_width(capsys):
    rows = [_mkt(spread=0.00156073, liquidity=53408496959.621006,
                 horizon_min=496959.621006)]
    doc_cats = S.aggregate(rows)
    for a in doc_cats.values():
        a["structural_score"] = S.structural_score(a)
    S._print_report({"categories": doc_cats,
                     "short_horizon": S.short_horizon_breakout(rows)})
    for line in capsys.readouterr().out.splitlines():
        assert "0.00156073.53408496959.621006" not in line
        assert len(line) < 200
