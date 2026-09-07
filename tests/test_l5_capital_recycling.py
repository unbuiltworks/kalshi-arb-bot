"""OWNER 5570456323: capital recycling from FIFO lots, deterministic."""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_capital_recycling as cr


def _p(t, side, px, qty):
    return {"t": t, "side": side, "px": D(px), "qty": D(qty), "usd": D(px) * D(qty), "tts": 0}


def test_market_recycling_splits_release_between_complement_and_settlement():
    # YES 10@0.40 at t=0, NO 5@0.55 at t=600 (half completed), settle at t=3600 NO
    ps = [_p(0, "YES", "0.40", "10"), _p(600, "NO", "0.55", "5")]
    lots = cr.lots_with_match_times(ps, "NO", D("4"))
    assert lots[0]["first_match_t"] == 600 and lots[1]["first_match_t"] == 600
    m = cr.market_recycling(lots, 3600.0)
    assert m["first_fill_to_first_complement_s"] == 600
    assert m["deployed_usd"] == 6.75 and m["released_at_complement_usd"] == 4.75     # 2.00 of YES + 2.75 of NO
    assert round(m["released_at_settlement_usd"], 4) == 2.0
    # capital-hours: 2.00 x 600/3600 + 2.75 x 0 + 2.00 x 1 h = 2.3333
    assert round(m["capital_hours"], 4) == 2.3333
    assert round(m["hours_per_deployed_dollar"], 4) == round(2.3333 / 6.75, 4)


def test_never_completed_market_releases_everything_at_settlement():
    ps = [_p(0, "YES", "0.37", "5")]
    lots = cr.lots_with_match_times(ps, "NO", D("2.5"))
    m = cr.market_recycling(lots, 7200.0)
    assert m["first_fill_to_first_complement_s"] is None and m["released_at_complement_share"] == 0.0
    assert m["hours_per_deployed_dollar"] == 2.0


def test_our_at_size_markets_reconcile_with_the_scoreboard_turnover():
    ours = cr.our_recycling()
    assert ours["overall"]["markets"] == 5
    assert abs(ours["overall"]["deployed_usd"] - 53.26) < 0.05
    wisc = ours["per_market"]["asc-cfb-wisc-nd-2026-09-06-pos-10pt5"]
    assert wisc["released_at_complement_share"] == 0.0 and wisc["hours_per_deployed_dollar"] > 1.0


def test_l5_history_headline():
    out = cr.l5_recycling()
    o = out["overall"]
    assert o["windows"] == 2216 and 0.85 < o["released_at_complement_share"] < 0.92
    assert o["first_fill_to_first_complement_s"]["p50"] < 120
    assert o["hours_per_deployed_dollar"]["p50"] < 0.05
