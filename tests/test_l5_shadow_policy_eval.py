"""The continuous shadow policy evaluator. SHADOW ONLY: nothing submits."""
import os
from decimal import Decimal as D

import pytest

from src import l5_shadow_policy_eval as E
from src import l5_two_sided_probe as TS

TICK = D("0.01"); MIN_QTY = D("0.01")
LIM = dict(tick=TICK, min_qty=MIN_QTY, depth_ticks=1,
           cash_envelope=D("2.00"), max_worst_case_loss=D("2.00"))


@pytest.fixture(autouse=True)
def _authorized(monkeypatch):
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "shadow-policy-eval-test")
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-*")


def snap(t, bid, ask, bids=(), asks=()):
    return {"t": t, "bid": bid, "ask": ask,
            "bids": [[p, q] for p, q in bids],
            "asks": [[p, q] for p, q in asks]}


def trade(t, px, qty):
    return {"t": t, "px": px, "qty": qty, "trade_time": "x%s" % t}


SLUG = "cpc-btc-updown-15m-2026-10-16-2030z"
T0 = E.window_start_epoch(SLUG) + 1.0


def base_ladder():
    # touch 0.61/0.62 -> our UP bid 0.60, our DOWN buy rests as ask at 0.63
    lv = snap(T0, "0.6100", "0.6200",
              bids=(("0.6100", "100"), ("0.6000", "10"), ("0.5900", "50")),
              asks=(("0.6200", "100"), ("0.6300", "10"), ("0.6400", "50")))
    return [lv, dict(lv, t=T0 + 30.0), dict(lv, t=T0 + 60.0)]


# ------------------------------------------------------------------ plumbing

def test_window_start_epoch_reads_the_slug_start():
    assert E.window_start_epoch(SLUG) == 1792182600.0
    assert E.window_start_epoch("not-a-slug") is None


def test_rung_depth_returns_none_for_a_price_that_is_not_a_rung():
    s = base_ladder()[0]
    assert E.rung_depth(s, "bids", D("0.6000")) == D("10")
    assert E.rung_depth(s, "bids", D("0.5500")) is None


def test_queue_fill_never_fills_on_an_unknown_queue():
    out = E.queue_fill([trade(T0 + 1, "0.60", "9999")], t_join=T0,
                       px=D("0.6000"), side="bid", queue_ahead=None)
    assert out["filled"] is False
    assert out["reason"] == "QUEUE_AHEAD_UNREADABLE"
    assert out["modelled"] is True


def test_queue_fill_ignores_prints_before_the_join():
    trs = [trade(T0 - 5, "0.60", "9999"), trade(T0 + 5, "0.60", "5")]
    out = E.queue_fill(trs, t_join=T0, px=D("0.6000"), side="bid",
                       queue_ahead=D("10"))
    assert out["filled"] is False and out["prints_counted"] == 1
    out2 = E.queue_fill(trs + [trade(T0 + 6, "0.59", "6")], t_join=T0,
                        px=D("0.6000"), side="bid", queue_ahead=D("10"))
    assert out2["filled"] is True and out2["t"] == T0 + 6


def test_queue_fill_side_direction():
    # an ASK at 0.63 fills on prints AT OR ABOVE 0.63, not below it
    low = E.queue_fill([trade(T0 + 1, "0.62", "99")], t_join=T0,
                       px=D("0.6300"), side="ask", queue_ahead=D("10"))
    assert low["filled"] is False
    hi = E.queue_fill([trade(T0 + 1, "0.63", "99")], t_join=T0,
                      px=D("0.6300"), side="ask", queue_ahead=D("10"))
    assert hi["filled"] is True


# ---------------------------------------------------------------- settlement

def test_settlement_net_matched_pair_is_outcome_independent():
    for yv in (D(0), D(1), None):
        out = E.settlement_net(qty_up=D(1), qty_dn=D(1), cash_up=D("0.60"),
                               cash_dn=D("0.37"), yes_value=yv)
        assert out["settled"] is True
        assert out["net_usd"] == "0.03"


def test_settlement_net_leaves_an_unsettled_residual_unresolved():
    out = E.settlement_net(qty_up=D(1), qty_dn=D(0), cash_up=D("0.60"),
                           cash_dn=D(0), yes_value=None)
    assert out["settled"] is False and out["pending_residual"] is True
    assert out["net_usd"] is None
    assert out["reason"] == "RESIDUAL_UNSETTLED"


def test_settlement_net_prices_a_residual_only_from_a_yes_value():
    won = E.settlement_net(qty_up=D(1), qty_dn=D(0), cash_up=D("0.60"),
                           cash_dn=D(0), yes_value=D(1))
    lost = E.settlement_net(qty_up=D(1), qty_dn=D(0), cash_up=D("0.60"),
                            cash_dn=D(0), yes_value=D(0))
    assert won["net_usd"] == "0.40" and lost["net_usd"] == "-0.60"


# --------------------------------------------------------------- eligibility

def test_eligibility_and_size_come_from_the_existing_builder():
    rec = E.evaluate_window(SLUG, base_ladder(), [], **LIM)
    assert rec["eligibility"]["ok"] is True
    plan = TS.build_orders(market=SLUG, bid="0.6100", ask="0.6200",
                           bid_depth="100", ask_depth="100",
                           book_age_s=D("0.5"), **LIM)
    assert rec["quote"]["qty"] == plan["qty_per_leg"]
    assert rec["quote"]["up_px"] == plan["legs"][0]["price"]


def test_an_unauthorized_market_is_not_scored(monkeypatch):
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-eth-updown-15m-*")
    rec = E.evaluate_window(SLUG, base_ladder(), [], **LIM)
    assert rec["ok"] is False and rec["reason"] == "NOT_ELIGIBLE"
    assert rec["detail"] == "NOT_THE_AUTHORIZED_MARKET"


def test_risk_limits_are_respected_not_widened():
    rec = E.evaluate_window(SLUG, base_ladder(), [],
                            **dict(LIM, max_worst_case_loss=D("0.10")))
    qty = D(rec["quote"]["qty"])
    dear = max(D(rec["quote"]["up_px"]), D(rec["quote"]["dn_px"]))
    assert qty * dear + TS.worst_fee(qty, dear) <= D("0.10")


def test_no_ladder_evidence_is_a_refusal_not_an_empty_result():
    rec = E.evaluate_window(SLUG, [], [], **LIM)
    assert rec["ok"] is False and rec["reason"] == "NO_LADDER_EVIDENCE"


# ------------------------------------------------------------------ outcomes

def test_neither_leg_filling_is_reported_as_neither():
    rec = E.evaluate_window(SLUG, base_ladder(), [], **LIM)
    assert rec["outcome"] == "NEITHER"
    assert rec["selected"] == "HOLD"
    assert rec["decisions"] == []


def test_both_legs_filling_reports_the_one_sided_interval():
    trs = [trade(T0 + 5, "0.63", "20"),      # clears our DOWN ask (queue 10)
           trade(T0 + 40, "0.60", "20")]     # clears our UP bid (queue 10)
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    assert rec["outcome"] == "BOTH"
    assert rec["one_sided_s"] == pytest.approx(35.0, abs=0.01)
    assert rec["inventory_after_acquisition"]["held_side"] == "DOWN"
    assert rec["inventory_after_acquisition"]["deficient_side"] == "UP"


def test_one_sided_outcome_is_named_and_the_residual_left_unsettled():
    trs = [trade(T0 + 5, "0.63", "20")]      # only the DOWN leg clears
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    assert rec["outcome"] == "DOWN_ONLY"
    assert rec["one_sided_s"] is None
    assert rec["net"]["HOLD"]["net_usd"] is None
    assert rec["net"]["HOLD"]["pending_residual"] is True


# ------------------------------------------------------------- the three bars

def test_the_guard_blocks_the_cross_the_raw_decision_proposes():
    trs = [trade(T0 + 5, "0.63", "20"), trade(T0 + 40, "0.60", "20")]
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    rows = rec["decisions"]
    assert rows, "a one-sided interval must produce decision rows"
    assert any(r["cross_raw"]["ok"] for r in rows)
    assert not any(r["cross_guarded"]["ok"] for r in rows)
    assert rec["selected"] == "HOLD"
    assert rec["net"]["HOLD"]["net_usd"] == rec["net"]["CROSS"]["net_usd"]


def test_every_decision_row_records_the_evidence_it_was_made_on():
    trs = [trade(T0 + 5, "0.63", "20"), trade(T0 + 40, "0.60", "20")]
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    r = rec["decisions"][0]
    for k in ("t", "t_rel_s", "phase", "bid", "ask", "reprice",
              "cross_raw", "cross_guarded"):
        assert k in r
    for k in ("filled", "queue_ahead", "cum_qualifying", "prints_counted",
              "modelled"):
        assert k in rec["resting"]["up"]


def test_the_fee_model_is_the_shared_one_for_every_policy():
    trs = [trade(T0 + 5, "0.63", "20"), trade(T0 + 40, "0.60", "20")]
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    want = "maker=%s taker=%s" % (TS.MAKER_THETA, TS.TAKER_THETA)
    for p in E.POLICIES:
        assert rec["net"][p]["fee_model"] == want
    # and the module declares no fee table of its own
    src = open("src/l5_shadow_policy_eval.py").read()
    assert "0.0695" not in src and "0.0125" not in src


def test_nothing_in_a_record_claims_a_fill_or_a_submission():
    trs = [trade(T0 + 5, "0.63", "20"), trade(T0 + 40, "0.60", "20")]
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    assert rec["shadow_only"] is True
    assert rec["submitted"] is False
    assert rec["fills_are_modelled"] is True


# ------------------------------------------------------------------ summarize

def test_summarize_excludes_unsettled_residuals_instead_of_scoring_them_zero():
    both = E.evaluate_window(SLUG, base_ladder(),
                             [trade(T0 + 5, "0.63", "20"),
                              trade(T0 + 40, "0.60", "20")], **LIM)
    one = E.evaluate_window(SLUG, base_ladder(),
                            [trade(T0 + 5, "0.63", "20")], **LIM)
    s = E.summarize([both, one])
    assert s["windows"] == 2 and s["eligible"] == 2
    assert s["settled"] == 1 and s["pending_residual"] == 1
    assert s["outcomes"] == {"BOTH": 1, "DOWN_ONLY": 1}
    assert D(s["net_usd"]["HOLD"]) == D(both["net"]["HOLD"]["net_usd"])
    assert all(v == "0" or D(v) == 0 for v in s["vs_blind_usd"].values())
    assert "none of these figures is realized profit" in s["labels"]
