"""Mechanics of the offline backtest harness (synthetic books only)."""
from decimal import Decimal as D

from src import pmus_directional_backtest as B


def fr(t, bid, ask, ib=None, ia=None, bq=100, aq=100):
    return {"t": float(t), "yes_bid": D(bid), "yes_ask": D(ask),
            "bid_q": bq, "ask_q": aq, "state": "MARKET_STATE_OPEN",
            "ib": None if ib is None else D(ib),
            "ia": None if ia is None else D(ia), "ti": float(t)}


SIM = {"ladder_levels": 1, "latency_s": 3.0}


def mk(policy=None, fill="touch", outcome=1):
    s = dict(B.SIM_DEFAULTS)
    s.update(SIM, fill=fill)
    return B.Market("aec-x-a-b", D("0.01"), outcome,
                    policy or {"directional": False}, s)


def test_new_order_not_fillable_before_latency_and_touch_fills():
    m = mk()
    m.decide(fr(0, "0.48", "0.50", "0.49", "0.51"), flip=False)
    yes = m.orders[("YES", 0)]
    assert yes["px"] == D("0.47") and yes["active_t"] == 3.0
    m.fills_at(fr(1, "0.46", "0.47"))          # before active: no fill
    assert m.pos["YES"] == 0
    m.fills_at(fr(3, "0.46", "0.47"))          # ask touches 0.47
    assert m.pos["YES"] == D(2)


def test_through_needs_the_ask_strictly_below_the_bid():
    m = mk(fill="through")
    m.decide(fr(0, "0.48", "0.50", "0.49", "0.51"), flip=False)
    m.fills_at(fr(3, "0.46", "0.47"))
    assert m.pos["YES"] == 0
    m.fills_at(fr(6, "0.45", "0.46"))
    assert m.pos["YES"] == D(2)


def test_a_cancelled_order_stays_fillable_until_the_cancel_lands():
    m = mk()
    m.decide(fr(0, "0.48", "0.50", "0.49", "0.51"), flip=False)
    m.fills_at(fr(3, "0.48", "0.50"))
    m._replace({}, 3.0)                        # cancel at t=3, lands t=6
    m.fills_at(fr(4, "0.45", "0.46"))          # runs over the stale bid
    assert m.pos["YES"] == D(2)


def test_guard_withholds_an_opening_above_the_international_fair():
    m = mk()
    m.decide(fr(0, "0.48", "0.50", "0.40", "0.42"), flip=False)
    assert ("YES", 0) not in m.orders          # YES 0.47 > 0.41 - 0.01
    assert ("NO", 0) in m.orders


def test_size_cap_and_settlement_accounting():
    m = mk(outcome=0)
    m.decide(fr(0, "0.48", "0.50", "0.49", "0.51"), flip=False)
    m.fills_at(fr(3, "0.46", "0.47", aq=1))    # only 1 displayed
    assert m.pos["YES"] == D(1)
    m.pos.update(YES=D(3), NO=D(2))
    m.cost.update(YES=D("1.41"), NO=D("0.98"))
    r = m.result()
    assert D(r["matched_qty"]) == D(2)
    assert D(r["matched_pnl"]) == D(2) - D(2) * (D("0.47") + D("0.49"))
    assert r["residual_side"] == "YES" and D(r["residual_pnl"]) == -D("0.47")


def test_align_intl_detects_flip():
    same = [fr(i, "0.60", "0.62", "0.60", "0.62") for i in range(20)]
    flip = [fr(i, "0.60", "0.62", "0.38", "0.40") for i in range(20)]
    bad = [fr(i, "0.60", "0.62", "0.10", "0.12") for i in range(20)]
    assert B.align_intl(same) is True
    assert B.align_intl(flip) is False
    assert B.align_intl(bad) is None


def test_run_reports_components_separately_and_labels_theoretical():
    frames = {"aec-x-a-b": [fr(i * 3, "0.48", "0.50", "0.49", "0.51")
                            for i in range(10)]}
    meta = {"aec-x-a-b": {"tick": "0.01", "outcome_yes": 1, "flip": False}}
    for pol in ({"directional": False},
                {"directional": True, "cfg": {"judge_openings": False}}):
        r = B.run(frames, meta, pol, SIM)
        assert r["label"].startswith("THEORETICAL")
        for k in ("1_settled_trading_pnl_after_fees_usd",
                  "2_confirmed_maker_rebates_usd",
                  "3_confirmed_liquidity_rewards_usd",
                  "4_total_realized_net_usd", "5_modelled_incentives_usd",
                  "6_matched_pnl_usd", "6_residual_pnl_usd",
                  "7_peak_capital_usd"):
            assert k in r
