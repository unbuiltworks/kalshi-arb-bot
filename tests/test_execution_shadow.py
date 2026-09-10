"""Arm B followed forward. The central rule: a posted price is not a fill.

OWNER: "Do not count `would_post` as a completion. Keep statuses explicit:
WOULD_POST -> WOULD_FILL -> VIRTUAL_MATCHED. Use the same conservative fill
evidence/calibration already validated against PMUS live fills. Never assume a
fill merely because the order was theoretically attractive."
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import pmus_execution_shadow as EX
from src import pmus_status_report as ST

PLAN = {"decision": "quote", "relation": "complete", "side": "YES",
        "target_price": "0.59", "q1_size": 1, "_side_yes": True,
        "max_pair_cost": "0.98"}


def _obs(t, bid, ask, cum, last):
    return {"t": ST._utc(t) if hasattr(ST, "_utc") else None,
            "_poll_t": t, "bid": bid, "ask": ask,
            "shares_traded_cum": cum, "last_trade_px": last,
            "state": "MARKET_STATE_OPEN"}


def _rows(t0, prints):
    """prints: list of (dt, bid, ask, cum, last)."""
    from datetime import datetime, timezone
    out = []
    for dt, bid, ask, cum, last in prints:
        r = _obs(t0 + dt, bid, ask, cum, last)
        r["t"] = datetime.fromtimestamp(t0 + dt, timezone.utc).isoformat()
        out.append(r)
    return out


T0 = 1_757_538_000.0


# ---- the statuses are never conflated -------------------------------------

def test_a_posted_price_starts_as_would_post_and_is_not_a_completion():
    p = EX.open_proposal("aec-nfl-bal-ind-2026-09-13", PLAN, T0, None, -2,
                         D("0.39"))
    assert p["status"] == EX.WOULD_POST
    assert p["pair_cost"] is None
    assert p["residual_after"] is None
    s = EX.summarize([p])
    assert s["counts"][EX.WOULD_POST] == 1
    assert s["p_completion_given_first_fill"] is None, (
        "a would_post must not enter a completion rate")


def test_an_attractive_quote_with_no_trades_does_not_fill():
    """Never assume a fill merely because the order was attractive."""
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40),
                     (60, 0.35, 0.60, 100.0, 0.40),
                     (120, 0.35, 0.60, 100.0, 0.40)])
    out = EX.follow(p, obs, now=T0 + 400)      # window closed
    assert out["status"] == EX.NO_FILL
    assert out["pair_cost"] is None


def test_pending_and_no_fill_are_different_facts():
    """'Not finished watching' must never read as 'the market never came'."""
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40), (30, 0.35, 0.60, 100.0, 0.40)])
    assert EX.follow(p, obs, now=T0 + 60)["status"] == EX.PENDING
    assert EX.follow(p, obs, now=T0 + 999)["status"] == EX.NO_FILL


def test_a_trade_through_the_price_fills_and_matches():
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40),
                     (60, 0.55, 0.60, 140.0, 0.55)])   # sell at 0.55 <= 0.59
    out = EX.follow(p, obs, now=T0 + 400)
    assert out["status"] == EX.VIRTUAL_MATCHED
    assert out["latency_s"] == 60.0
    assert out["pair_cost"] == "0.98"
    assert out["pair_at_or_below_ceiling"] is True
    assert out["residual_after"] == -1          # |residual| 2 -> 1
    assert out["risk_returned"] == 1.0
    assert out["risk_created"] == 0.0


def test_quantity_is_bounded_by_what_traded_through_not_by_what_rested():
    """We cannot be ahead of a queue we cannot see."""
    p = EX.open_proposal("s", {**PLAN, "q1_size": 10}, T0, None, -20, D("0.39"))
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40),
                     (60, 0.55, 0.60, 103.0, 0.55)])   # only 3 traded through
    out = EX.follow(p, obs, now=T0 + 400)
    assert out["traded_through"] == 3.0
    assert out["fillable_qty"] == 3.0


def test_touched_is_recorded_even_when_nothing_traded():
    """A market that reached the quote and did not trade is a different fact
    from one that never came near it."""
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    # ask descends to 0.55 <= our 0.59 buy, and NOTHING trades
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40),
                     (60, 0.50, 0.55, 100.0, 0.40)])
    out = EX.follow(p, obs, now=T0 + 400)
    assert out["status"] == EX.NO_FILL
    assert out["touched"] is True


def test_a_terminal_proposal_is_never_re_followed():
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40),
                     (60, 0.55, 0.60, 140.0, 0.55)])
    once = EX.follow(p, obs, now=T0 + 400)
    twice = EX.follow(once, obs, now=T0 + 900)
    assert twice == once


def test_module_defines_no_fill_rule_of_its_own():
    """The rule is the validated one, not a new one."""
    src = open("src/pmus_execution_shadow.py").read()
    assert "_fill_evidence" in src
    import ast
    tree = ast.parse(src)
    fns = {getattr(n, "name", None) for n in ast.walk(tree)}
    assert "_fill_evidence" not in fns, "must call the validated rule, not redefine it"


# ---- the metric -----------------------------------------------------------

def test_pending_proposals_are_excluded_from_the_rate():
    """Including them computes a rate against events that have not happened."""
    done = EX.open_proposal("a", PLAN, T0, None, -2, D("0.39"))
    done.update(status=EX.VIRTUAL_MATCHED, pair_at_or_below_ceiling=True,
                latency_s=60.0, risk_returned=1.0)
    pend = EX.open_proposal("b", PLAN, T0, None, -2, D("0.39"))
    pend["status"] = EX.PENDING
    s = EX.summarize([done, pend])
    assert s["counts"]["resolved"] == 1
    assert s["counts"]["pending"] == 1
    assert s["p_completion_given_first_fill"] == 1.0


def test_metric_combines_completion_risk_returned_and_exposure():
    done = EX.open_proposal("a", PLAN, T0, None, -2, D("0.39"))
    done.update(status=EX.VIRTUAL_MATCHED, pair_at_or_below_ceiling=True,
                latency_s=1800.0, risk_returned=1.0)
    s = EX.summarize([done])
    # 1.0 completion x 1.0 returned / 0.5h
    assert s["metric_completion_x_risk_returned_per_exposure_hour"] == 2.0


def test_summary_never_reports_realized_pnl():
    s = EX.summarize([])
    assert s["realized_pnl"] is None
    assert "never counted as a completion" in s["labels"].lower()


# ---- predictors are prospective ------------------------------------------

def test_state_is_captured_at_post_time_and_never_rewritten():
    row = {"flow_usd_per_min": 2150.0, "spread": 0.005, "mid_stdev": 0.0,
           "bid": 0.35, "ask": 0.40}
    p = EX.open_proposal("aec-nfl-bal-ind-2026-09-13", PLAN, T0, row, -2,
                         D("0.39"))
    before = json.dumps(p["state_at_post"], sort_keys=True, default=str)
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40),
                     (60, 0.55, 0.60, 140.0, 0.55)])
    out = EX.follow(p, obs, now=T0 + 400)
    assert out["status"] == EX.VIRTUAL_MATCHED
    assert json.dumps(out["state_at_post"], sort_keys=True,
                      default=str) == before


def test_state_snapshot_carries_the_named_predictors():
    row = {"flow_usd_per_min": 2150.0, "spread": 0.005, "mid_stdev": 0.001,
           "bid": 0.35, "ask": 0.40}
    p = EX.open_proposal("aec-nfl-bal-ind-2026-09-13", PLAN, T0, row, -2,
                         D("0.39"))
    s = p["state_at_post"]
    for k in ("flow_usd_per_min", "spread", "mid_stdev", "band_position",
              "sport", "market_type", "residual_before", "implied_pair_cost",
              "distance_to_touch"):
        assert k in s, k
    assert s["sport"] == "nfl"
    assert s["market_type"] == "moneyline"
    assert s["in_band"] is True          # mid 0.375 inside 0.25-0.75


def test_predictors_report_a_split_and_do_not_rank():
    src = open("src/pmus_execution_shadow.py").read()
    import ast
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "predictors")
    called = {c.func.id for c in ast.walk(fn)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert not any(w in " ".join(called) for w in ("rank", "score", "select"))


def test_a_thin_sample_is_flagged_rather_than_reported_as_a_finding():
    done = EX.open_proposal("a", PLAN, T0, None, -2, D("0.39"))
    done["status"] = EX.VIRTUAL_MATCHED
    out = EX.predictors([done])
    assert out["sufficient_sample"] is False


# ---- durability -----------------------------------------------------------

def test_tracker_survives_a_restart_with_open_proposals(tmp_path):
    """A complement posted at 22:52 is judged by trades that arrive minutes
    later. In-memory tracking resolves every open proposal to PENDING on
    restart and silently resets the denominator."""
    p = str(tmp_path / "ex.json")
    t = EX.ExecutionShadow(path=p, now=lambda: T0)
    t.record("s", PLAN, T0, None, -2, D("0.39"))
    assert t.save() is True

    t2 = EX.ExecutionShadow(path=p, now=lambda: T0)
    assert len(t2.proposals) == 1
    assert t2.open_count() == 1


def test_the_same_posted_price_is_one_proposal_however_often_replayed(tmp_path):
    t = EX.ExecutionShadow(path=str(tmp_path / "ex.json"), now=lambda: T0)
    assert t.record("s", PLAN, T0, None, -2, D("0.39")) is not None
    assert t.record("s", PLAN, T0, None, -2, D("0.39")) is None
    assert len(t.proposals) == 1


def test_advance_moves_proposals_to_terminal(tmp_path):
    t = EX.ExecutionShadow(path=str(tmp_path / "ex.json"),
                           now=lambda: T0 + 400)
    t.record("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40),
                     (60, 0.55, 0.60, 140.0, 0.55)])
    out = t.advance({"s": obs})
    assert out["advanced"] == 1
    assert t.by_status()[EX.VIRTUAL_MATCHED] == 1
    assert t.open_count() == 0


def test_no_order_entry_path():
    import ast
    tree = ast.parse(open("src/pmus_execution_shadow.py").read())
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for banned in ("submit", "place_order", "cancel_order"):
        assert banned not in calls


# ---- ranked-cohort comparison --------------------------------------------

def _matched(slug, tier, risk=1.0, lat=60.0, pair="0.98"):
    p = EX.open_proposal(slug, PLAN, T0, None, -2, D("0.39"))
    p.update(tier=tier, status=EX.VIRTUAL_MATCHED, latency_s=lat,
             risk_returned=risk, pair_cost=pair, pair_at_or_below_ceiling=True)
    return p


def _nofill(slug, tier):
    p = EX.open_proposal(slug, PLAN, T0, None, -2, D("0.39"))
    p.update(tier=tier, status=EX.NO_FILL)
    return p


def test_cohorts_are_compared_on_the_metric_not_on_proposal_counts():
    """A tier that posts many complements nobody lifts is WORSE than one that
    posts few and completes them."""
    busy = [_nofill(f"a{i}", "T1_top8") for i in range(9)] + \
           [_matched("a9", "T1_top8")]
    quiet = [_matched("b0", "T2_9_16"), _matched("b1", "T2_9_16")]
    out = EX.by_cohort(busy + quiet)["cohorts"]
    assert out["T1_top8"]["resolved"] == 10
    assert out["T2_9_16"]["resolved"] == 2
    assert out["T2_9_16"]["metric"] > out["T1_top8"]["metric"], (
        "the quieter tier completes everything it posts and must rank higher")


def test_each_cohort_flags_its_own_thin_sample():
    out = EX.by_cohort([_matched("a", "T1_top8")])["cohorts"]
    assert out["T1_top8"]["sufficient_sample"] is False


def test_cohorts_can_group_by_sport_or_market_type():
    p = _matched("aec-nfl-bal-ind-2026-09-13", "T1_top8")
    out = EX.by_cohort([p], key="sport")["cohorts"]
    assert "nfl" in out


# ---- one resting complement per market, not one per replay ---------------

def test_the_same_price_re_proposed_each_cycle_is_one_order(tmp_path):
    """MEASURED LIVE: asc-nfl-sf-lar-2026-09-10-pos-4pt5 was proposed three
    times at an identical 0.52, differing only in the replayed residual.
    Three proposals would treble the denominator with orders that never
    existed."""
    t = EX.ExecutionShadow(path=str(tmp_path / "ex.json"), now=lambda: T0)
    assert t.record("s", PLAN, T0, None, -1, D("0.39")) is not None
    assert t.record("s", PLAN, T0 + 50, None, -2, D("0.39")) is None
    assert t.record("s", PLAN, T0 + 100, None, -3, D("0.39")) is None
    assert len(t.proposals) == 1


def test_a_requote_supersedes_rather_than_adding_a_second_order(tmp_path):
    t = EX.ExecutionShadow(path=str(tmp_path / "ex.json"), now=lambda: T0)
    t.record("s", PLAN, T0, None, -2, D("0.39"))
    t.record("s", {**PLAN, "target_price": "0.61"}, T0 + 50, None, -2, D("0.39"))
    st = t.by_status()
    assert st[EX.SUPERSEDED] == 1
    assert st[EX.WOULD_POST] == 1
    assert t.open_count() == 1, "the live engine holds ONE outstanding order"


def test_a_superseded_order_is_excluded_from_the_completion_rate():
    """It neither filled nor failed to fill; counting it either way would be
    a verdict the evidence does not support."""
    sup = EX.open_proposal("a", PLAN, T0, None, -2, D("0.39"))
    sup["status"] = EX.SUPERSEDED
    won = EX.open_proposal("b", PLAN, T0, None, -2, D("0.39"))
    won.update(status=EX.VIRTUAL_MATCHED, pair_at_or_below_ceiling=True,
               latency_s=60.0, risk_returned=1.0)
    s = EX.summarize([sup, won])
    assert s["counts"]["resolved"] == 1
    assert s["counts"][EX.SUPERSEDED] == 1
    assert s["p_completion_given_first_fill"] == 1.0


def test_a_market_that_closed_its_order_can_open_a_new_one(tmp_path):
    t = EX.ExecutionShadow(path=str(tmp_path / "ex.json"), now=lambda: T0)
    t.record("s", PLAN, T0, None, -2, D("0.39"))
    for pid in list(t.proposals):
        t.proposals[pid]["status"] = EX.NO_FILL
    assert t.record("s", PLAN, T0 + 400, None, -2, D("0.39")) is not None
    assert len(t.proposals) == 2


# ---- touch and volume must agree with the fill rule -----------------------

def test_the_threshold_is_the_fill_rules_own():
    from src import pmus_sports_l5_decision as DEC2
    assert EX.fill_threshold(True, D("0.59")) == D("0.59")
    assert EX.fill_threshold(False, D("0.59")) == DEC2.ONE - D("0.59")


def test_a_yes_buy_is_touched_by_the_ask_coming_down_not_the_bid_going_up():
    """REGRESSION. The first draft read the BID and required it to RISE to
    px. A resting YES buy is reached by the ASK DESCENDING. It reported
    touched=True on 10 of 10 live proposals whose traded-through volume was
    0.0 -- and 'the market reached our price and declined to trade' is a
    completely different conclusion from 'the market never came near it'."""
    px = D("0.59")
    far = _rows(T0, [(0, 0.90, 0.95, 1.0, 0.92)])      # bid 0.90 >= 0.59
    assert EX._touched(far, True, px) is False, (
        "a high bid does not touch a resting YES buy")
    near = _rows(T0, [(0, 0.50, 0.55, 1.0, 0.52)])     # ask 0.55 <= 0.59
    assert EX._touched(near, True, px) is True


def test_a_no_buy_is_touched_by_the_bid_coming_up():
    px = D("0.59")                       # NO at 0.59 == YES sell at 0.41
    near = _rows(T0, [(0, 0.45, 0.50, 1.0, 0.47)])     # bid 0.45 >= 0.41
    assert EX._touched(near, False, px) is True
    far = _rows(T0, [(0, 0.20, 0.25, 1.0, 0.22)])      # bid 0.20 < 0.41
    assert EX._touched(far, False, px) is False


def test_a_fill_always_implies_a_touch_even_between_snapshots():
    """Quotes are snapshots and trades happen between them, so a print can
    come through the price with no snapshot ever showing the touch. A filled
    proposal must never report touched=False."""
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40),
                     (60, 0.55, 0.60, 140.0, 0.55)])   # ask 0.60 > 0.59
    assert EX._touched(obs, True, D("0.59")) is False   # no snapshot touch
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    out = EX.follow(p, obs, now=T0 + 400)
    assert out["status"] == EX.VIRTUAL_MATCHED
    assert out["touched"] is True


def test_no_side_volume_uses_one_minus_px():
    """Volume attribution had the matching error, using px instead of
    ONE - px on the NO side."""
    trades = [{"t": T0 + 10, "px": D("0.45"), "vol": 5.0,
               "dir_inferred": "buy"}]
    # NO at 0.59 -> threshold 0.41; a buy at 0.45 >= 0.41 counts
    assert EX._volume_through(trades, T0, T0 + 300, False, D("0.59")) == 5.0
    # the wrong rule (>= 0.59) would have counted nothing
    trades2 = [{"t": T0 + 10, "px": D("0.70"), "vol": 5.0,
                "dir_inferred": "buy"}]
    assert EX._volume_through(trades2, T0, T0 + 300, False, D("0.59")) == 5.0
