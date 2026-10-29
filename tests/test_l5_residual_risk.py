"""OWNER 5566810119 #1/#2: L5's residual-risk envelope in dollars and time,
and what a dollar unmatched cap would have done to R3L. Measurement only."""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_r4_counterfactual as cf
from src import l5_residual_risk as rr


def _row(wid, seq, ts, s2s, up_q, up_s, dn_q, dn_s, resid, side, outcome="Up",
         dollars="1", dur=15):
    return {"window_id": wid, "market_slug": "m-" + wid, "asset": "BTC",
            "duration_minutes": str(dur), "trade_seq": str(seq),
            "timestamp": str(ts), "seconds_to_settlement": str(s2s),
            "cum_up_qty": str(up_q), "cum_up_spend": str(up_s),
            "cum_down_qty": str(dn_q), "cum_down_spend": str(dn_s),
            "residual_qty_signed": str(resid), "residual_side": side,
            "settlement_outcome": outcome, "dollars": dollars,
            "maker_taker": "maker"}


def test_residual_notional_is_unmatched_shares_at_their_own_cost():
    # 10 Up @0.40 and 4 Down: 6 unmatched Up at 0.40 = $2.40 at risk
    r = _row("w", 1, 1000, 500, 10, 4.0, 4, 2.0, 6, "Up")
    got = rr.residual_at(r)
    assert got["qty"] == 6 and got["side"] == "Up"
    assert got["avg_price"] == 0.4 and got["notional"] == 2.4
    # flat book carries no settlement risk at all
    flat = rr.residual_at(_row("w", 2, 1010, 490, 10, 4.0, 10, 5.0, 0, ""))
    assert flat["notional"] == 0.0


def test_window_path_reports_peak_final_duration_and_the_settlement_draw():
    rows = [_row("w", 1, 1000, 800, 10, 4.0, 0, 0, 10, "Up", dollars="4"),
            _row("w", 2, 1100, 700, 20, 8.0, 0, 0, 20, "Up", dollars="4"),
            _row("w", 3, 1200, 600, 20, 8.0, 15, 6.0, 5, "Up", dollars="6")]
    w = rr.window_residual(rows)
    assert w["peak_residual_usd"] == 8.0 and w["final_residual_usd"] == 2.0
    assert w["peak_at_seconds_to_settlement"] == 700
    # exposure opened on the first fill and ran to settlement at 1200+600
    assert w["settlement_ts"] == 1800 and w["residual_open_s"] == 800
    assert w["market_life_s"] == 900
    assert w["residual_open_share_of_life"] == round(800 / 900, 4)
    assert w["first_open_seconds_to_settlement"] == 800
    assert w["complemented_by_settlement"] is False
    assert w["peak_reduced_share"] == 0.75            # 8.00 -> 2.00
    # settlement went Up and the residual was Up: it paid $1 a share
    assert w["residual_won"] is True
    assert w["residual_pnl_usd"] == pytest.approx(5 * (1 - 0.4))
    # two creations: the open and the extend, each adding $4
    assert [c["added_usd"] for c in w["creations"]] == [4.0, 4.0]
    assert [c["seconds_to_settlement"] for c in w["creations"]] == [800, 700]


def test_a_losing_residual_costs_exactly_its_notional():
    rows = [_row("w", 1, 1000, 300, 19.27, 6.2855, 0, 0, 19.27, "Up",
                 outcome="Down", dollars="6.2855")]
    w = rr.window_residual(rows)
    assert w["final_residual_usd"] == pytest.approx(6.2855, abs=1e-4)
    assert w["residual_won"] is False
    assert w["residual_pnl_usd"] == pytest.approx(-6.2855, abs=1e-4)
    assert w["complemented_by_settlement"] is False


def test_envelope_separates_dollars_time_and_outcome():
    win = [_row("a", 1, 0, 600, 10, 4.0, 0, 0, 10, "Up", outcome="Up")]
    lose = [_row("b", 1, 0, 600, 10, 4.0, 0, 0, 10, "Up", outcome="Down")]
    flat = [_row("c", 1, 0, 600, 10, 4.0, 10, 5.0, 0, "", outcome="Up")]
    e = rr.envelope([rr.window_residual(x) for x in (win, lose, flat)])
    assert e["windows"] == 3 and e["windows_taking_unmatched_exposure"] == 2
    assert e["windows_holding_residual_to_settlement"] == 2
    assert e["outcome"]["residual_won"] == 1 and e["outcome"]["residual_lost"] == 1
    assert e["outcome"]["win_rate"] == 0.5
    assert e["outcome"]["total_residual_pnl_usd"] == pytest.approx(6 - 4, abs=1e-6)


@pytest.mark.skipif(not os.path.exists(rr.CANONICAL_PATH),
                    reason="canonical history not present")
def test_the_envelope_on_the_real_history_is_what_r4_must_be_built_from():
    ws = rr.windows(rr.load_rows())
    assert len(ws) == 2216
    e = rr.envelope(ws)
    # L5 took unmatched exposure in EVERY window and almost never closed it
    assert e["windows_taking_unmatched_exposure"] == 2216
    assert e["share_complemented_before_settlement"] < 0.02
    # the residual is a coin flip, and across the sample it LOST money:
    # pairs and incentives are the income, never the residual
    assert 0.45 < e["outcome"]["win_rate"] < 0.55
    assert e["outcome"]["total_residual_pnl_usd"] < 0
    # at material size L5 held unmatched exposure to a SMALL share of the
    # capital it had deployed in that market -- the transferable ratio
    big = rr.by(ws, rr._size_band)["spend>=$500"]
    assert big["dollars"]["peak_share_of_window_spend"]["p50"] < 0.20
    assert big["dollars"]["window_spend_usd"]["p50"] > 500


def test_r4_replay_reproduces_r3l_exactly_before_any_cap_is_applied():
    """The counterfactual is only worth reading if the uncapped replay
    reproduces what the venue actually did."""
    base = cf.campaign(None)
    per = base["per_market"]
    a = per["asc-cfb-lou-miss-2026-09-06-pos-6pt5"]
    b = per["tsc-cfb-lou-miss-2026-09-06-total-41pt5"]
    c = per["asc-cfb-wisc-nd-2026-09-06-pos-10pt5"]
    assert a["matched_qty"] == "16.54" and a["matched_pair_cost"].startswith("0.9345")
    assert a["matched_pnl_usd"] == "1.0825" and a["final_unmatched_usd"] == "0.0000"
    assert b["matched_qty"] == "16.29" and b["matched_pair_cost"].startswith("0.7917")
    assert b["matched_pnl_usd"] == "3.3928"
    assert c["matched_qty"] == "0" and c["residual_pnl_usd"] == "-6.2855"
    assert base["campaign_trading_pnl_usd"] == "-1.8102"
    assert base["turnover_usd"] == "34.6402"          # venue's own filled notional
    assert base["orders_refused"] == 0


def test_a_dollar_cap_bounds_the_loser_AND_shrinks_the_winners():
    """The honest reading: the cap scales both sides down. It does not turn
    this campaign positive, and it must not be sold as if it did."""
    capped = cf.campaign(D("2.50"))
    per = capped["per_market"]
    loser = per["asc-cfb-wisc-nd-2026-09-06-pos-10pt5"]
    assert loser["fills_refused"] == 3
    assert loser["residual_pnl_usd"] == "-1.9055"          # was -6.2855
    # ... and both winners give up most of their gain
    won = sum(D(per[s]["matched_pnl_usd"]) for s in per if "wisc" not in s)
    assert won < D("1.50")                                  # was 4.4753
    assert D(capped["campaign_trading_pnl_usd"]) < 0        # still a loss
    # every refusal was an order that would have ADDED exposure past the cap
    for m in per.values():
        assert all(D(r["would_reach_usd"]) > D("2.50") for r in m["refused"])


def test_the_cap_never_refuses_an_order_that_reduces_exposure():
    """The trap to avoid: a cap that blocks the very complement that would
    close the position. An order is judged by the exposure it LEAVES, so a
    reducing order is always taken however large the standing residual."""
    fills = [("t1", "YES", "0.40", "10"),      # +$4.00 unmatched
             ("t2", "YES", "0.40", "10"),      # would reach $8.00 -> refused
             ("t3", "NO",  "0.40", "10")]      # reduces to $0 -> always taken
    out = cf.replay(fills, D("5.00"))
    assert out["fills_taken"] == 2 and out["fills_refused"] == 1
    assert out["refused"][0]["t"] == "t2"
    assert out["final_unmatched_usd"] == "0.0000"
    assert out["matched_qty"] == "10"
    # and with no cap at all the same tape simply runs
    assert cf.replay(fills, None)["fills_refused"] == 0


def test_the_cap_is_monotone_and_never_refuses_a_first_leg():
    caps = ["7.50", "5.00", "3.25", "2.50"]
    peaks = [D(cf.campaign(D(c))["worst_market_peak_unmatched_usd"]) for c in caps]
    assert peaks == sorted(peaks, reverse=True)
    for c in caps:
        for m in cf.campaign(D(c))["per_market"].values():
            assert m["fills_taken"] >= 1          # the opening leg always lands
