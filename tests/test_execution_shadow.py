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


# ---- reachability: WHY, not just whether ---------------------------------

def test_the_owners_example_measures_six_cents_of_gap():
    """First leg at 39c forces a complement at <=59c. If the opposing side
    sits at 65c, the order is 6c from being executable at all -- which is a
    different problem from an order the market declined to lift."""
    g = EX.gap_to_fill({"bid": 0.60, "ask": 0.65}, True, D("0.59"))
    assert g == D("0.06")
    assert float(g / EX.TICK) == 12.0        # 12 ticks at a half-cent tick


def test_gap_is_measured_in_the_fill_rules_frame_on_both_sides():
    # NO buy at 0.59 == YES sell at 0.41; the BID must come UP to 0.41
    assert EX.gap_to_fill({"bid": 0.35, "ask": 0.45}, False, D("0.59")) == D("0.06")
    assert EX.gap_to_fill({"bid": 0.41, "ask": 0.45}, False, D("0.59")) == D("0")


def test_reachability_records_initial_min_and_favorable_excursion():
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(30, 0.60, 0.65, 1.0, 0.62),     # gap 6c
                     (60, 0.58, 0.62, 1.0, 0.60),     # gap 3c
                     (90, 0.59, 0.64, 1.0, 0.61)])    # gap 5c
    r = EX.reachability(p, obs, now=T0 + 300)
    assert r["initial_gap_cents"] == pytest.approx(6.0)
    assert r["min_gap_cents"] == pytest.approx(3.0)
    assert r["max_favorable_excursion_cents"] == pytest.approx(3.0)
    assert r["reached"] is False
    assert r["time_to_touch_s"] is None


def test_reachability_reports_time_to_touch_when_the_market_arrives():
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(30, 0.60, 0.65, 1.0, 0.62),
                     (90, 0.54, 0.58, 1.0, 0.56)])    # ask 0.58 <= 0.59
    r = EX.reachability(p, obs, now=T0 + 300)
    assert r["reached"] is True
    assert r["time_to_touch_s"] == 90.0
    assert r["min_gap_cents"] <= 0


def test_first_leg_provenance_is_recorded_on_the_proposal():
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"),
                         first_leg={"price": "0.39", "t": 10.0, "side": "NO"})
    assert p["first_leg"]["price"] == "0.39"


# ---- the research horizon must never move the frozen verdict --------------

def test_research_never_changes_the_300s_verdict():
    """OWNER: keep the frozen R5F 300s result unchanged. A research field that
    can move the headline number is not a research field."""
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(30, 0.60, 0.65, 1.0, 0.62), (200, 0.60, 0.65, 1.0, 0.62)])
    resolved = EX.follow(p, obs, now=T0 + 400)
    assert resolved["status"] == EX.NO_FILL
    frozen = {k: resolved.get(k) for k in
              ("status", "pair_cost", "residual_after", "risk_returned",
               "latency_s", "fillable_qty", "traded_through")}

    # last_trade_px must be <= bid to infer a SELL; inside the spread the
    # direction resolves "unknown" and nothing fills.
    later = obs + _rows(T0, [(600, 0.55, 0.58, 5.0, 0.55)])   # fills at 600s
    after = EX.extend_research(resolved, later, now=T0 + 900)
    assert {k: after.get(k) for k in frozen} == frozen
    assert after["research"]["would_fill_later"] is True
    assert after["research"]["verdict"] == "SLOW_BUT_REACHABLE"


def test_research_distinguishes_unreachable_from_merely_slow():
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(30, 0.70, 0.75, 1.0, 0.72), (600, 0.70, 0.75, 1.0, 0.72)])
    resolved = EX.follow(p, obs, now=T0 + 400)
    after = EX.extend_research(resolved, obs, now=T0 + 900)
    assert after["research"]["verdict"] == "UNREACHABLE_SO_FAR"


def test_a_completed_proposal_is_not_researched_further():
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(0, 0.35, 0.60, 100.0, 0.40),
                     (60, 0.55, 0.60, 140.0, 0.55)])
    done = EX.follow(p, obs, now=T0 + 400)
    assert done["status"] == EX.VIRTUAL_MATCHED
    assert EX.extend_research(done, obs, now=T0 + 900) is done


def test_research_stops_at_the_extended_horizon():
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    obs = _rows(T0, [(30, 0.70, 0.75, 1.0, 0.72)])
    resolved = EX.follow(p, obs, now=T0 + 400)
    far = EX.extend_research(resolved, obs, now=T0 + EX.EXTENDED_FOLLOW_S + 10)
    assert "research" not in far


def test_no_rate_or_cohort_reads_the_research_field():
    src = open("src/pmus_execution_shadow.py").read()
    import ast
    tree = ast.parse(src)
    for name in ("summarize", "by_cohort", "predictors"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        consts = {n.value for n in ast.walk(fn) if isinstance(n, ast.Constant)}
        assert "research" not in consts, f"{name} reads the research field"


# ---- comparison against our own real completes ---------------------------

def test_historical_benchmark_is_read_not_reconstructed():
    h = EX.historical_benchmark()
    assert h["filled"]["median"] == 1
    assert h["unfilled"]["median"] == 2
    assert h["unfilled"]["p75"] == 7
    assert "authenticated" in h["unavailable"]


def test_comparison_refuses_a_verdict_on_a_thin_sample():
    def _p(ticks):
        p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
        p.update(status=EX.NO_FILL,
                 reachability={"observations": 3, "min_gap_ticks": ticks,
                               "reached": ticks <= 0})
        return p
    out = EX.compare_to_history([_p(12.0) for _ in range(5)])
    assert "too small" in out["verdict"]


def test_a_far_live_distribution_reads_as_a_reachability_bottleneck():
    def _p(ticks):
        p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
        p.update(status=EX.NO_FILL,
                 reachability={"observations": 3, "min_gap_ticks": ticks,
                               "reached": False})
        return p
    out = EX.compare_to_history([_p(12.0) for _ in range(40)])
    assert "reachability bottleneck" in out["verdict"]
    assert out["prospective"]["median"] == 12.0


def test_a_close_live_distribution_clears_reachability():
    def _p(ticks):
        p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
        p.update(status=EX.VIRTUAL_MATCHED,
                 reachability={"observations": 3, "min_gap_ticks": ticks,
                               "reached": True})
        return p
    out = EX.compare_to_history([_p(0.0) for _ in range(40)])
    assert "not the constraint" in out["verdict"]


def test_missing_historical_file_does_not_raise():
    out = EX.historical_benchmark("/nope/missing.json")
    assert out["filled"] is None and "error" in out


def test_record_accepts_and_stores_first_leg_provenance(tmp_path):
    """REGRESSION. open_proposal gained a first_leg parameter and record()
    did not, so the loop's call raised TypeError on EVERY arm-B proposal --
    which would have crash-looped the deployed collector. No unit test
    exercised record() with the argument the caller actually passes; the live
    run found it in seconds. Callers and callees must be tested together at
    the seam, not only on either side of it."""
    t = EX.ExecutionShadow(path=str(tmp_path / "ex.json"), now=lambda: T0)
    p = t.record("s", PLAN, T0, None, -2, D("0.39"), tier="T1_top8",
                 first_leg={"price": "0.39", "t": 10.0, "side": "NO"})
    assert p is not None
    assert p["first_leg"]["price"] == "0.39"
    assert p["tier"] == "T1_top8"


def test_the_loop_calls_record_with_exactly_the_signature_it_has():
    """Bind the caller's keywords against the callee's signature so a
    parameter added to one and not the other fails here, not in production."""
    import inspect
    sig = inspect.signature(EX.ExecutionShadow.record)
    sig.bind(None, "slug", PLAN, T0, ranked_row=None, residual_before=-2,
             surplus_avg=D("0.39"), tier="T1_top8", first_leg={})


def test_sufficient_sample_requires_a_floor_on_the_SMALLER_class():
    """LIVE at 00:50Z: 50 resolved, 1 matched, 49 no-fill passed the old bar
    and reported a flow 'separation' of 3,573 usd/min -- a difference computed
    from a single matched observation. A total is not a sample."""
    def _p(status):
        p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
        p["status"] = status
        return p
    lopsided = [_p(EX.VIRTUAL_MATCHED)] + [_p(EX.NO_FILL) for _ in range(49)]
    out = EX.predictors(lopsided)
    assert out["resolved"] == 50
    assert out["sufficient_sample"] is False
    assert out["matched_n"] == 1 and out["min_per_class"] == EX.MIN_CLASS

    balanced = ([_p(EX.VIRTUAL_MATCHED) for _ in range(15)]
                + [_p(EX.NO_FILL) for _ in range(20)])
    assert EX.predictors(balanced)["sufficient_sample"] is True


def test_the_historical_benchmark_file_ships_in_the_image():
    """It returned FileNotFoundError in production while passing locally: the
    Dockerfile copied two analysis files and not this one."""
    docker = open("deploy/pmus_shadow_breadth/Dockerfile").read()
    assert "complete_no_fill_forensics.json" in docker
    assert EX.HISTORICAL_FORENSICS.endswith("complete_no_fill_forensics.json")


# ---- the report cannot contradict itself ---------------------------------

def _measured(status, reached=False, ticks=4.0):
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    p.update(status=status,
             reachability={"observations": 4, "reached": reached,
                           "min_gap_ticks": ticks, "initial_gap_cents": 2.0,
                           "min_gap_cents": 2.0, "time_to_touch_s": None,
                           "max_favorable_excursion_cents": 0.0})
    return p


def _unmeasured(status):
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    p["status"] = status                      # no reachability record
    return p


def test_the_exact_contradiction_that_shipped_cannot_be_emitted():
    """DEPLOYED at 00:45Z: reached_our_price 0, filled 1,
    reached_but_unfilled -1 -- one report implying a complement both filled
    and was never reached. The filled proposal predated the measurement, so
    `filled` counted every resolved proposal while `reached` counted only
    measured ones."""
    props = [_unmeasured(EX.VIRTUAL_MATCHED)] + \
            [_measured(EX.NO_FILL) for _ in range(15)]
    out = EX.reachability_report(props)
    m, u = out[EX.MEASURED], out[EX.UNMEASURED]
    assert m["filled"] == 0 and m["reached_our_price"] == 0
    assert u["filled"] == 1 and u["resolved"] == 1
    assert m["reached_but_unfilled"] == 0
    assert out["coherence"]["ok"] is True


def test_a_fill_inside_the_measured_cohort_counts_as_reached():
    out = EX.reachability_report([_measured(EX.VIRTUAL_MATCHED, reached=False)])
    m = out[EX.MEASURED]
    assert m["reached_our_price"] == 1 and m["never_reached"] == 0
    assert m["filled"] == 1 and m["reached_but_unfilled"] == 0
    assert out["coherence"]["ok"] is True


def test_coherence_flags_a_violation_rather_than_printing_it_silently():
    """Checked at EMISSION, because the contradiction that shipped was in a
    deployed row and not in any test."""
    bad = _coh_probe = EX._coherence(
        {EX.MEASURED: {"reached_but_unfilled": -1}}, 5, 0, 5, 1)
    assert bad["ok"] is False
    assert any("implies reach" in v for v in bad["violations"])
    assert any("negative" in v for v in bad["violations"])


def test_measured_and_unmeasured_partition_every_resolved_proposal():
    props = [_measured(EX.NO_FILL), _unmeasured(EX.NO_FILL),
             _measured(EX.VIRTUAL_MATCHED, reached=True),
             _unmeasured(EX.VIRTUAL_MATCHED)]
    out = EX.reachability_report(props)
    assert (out[EX.MEASURED]["resolved"] + out[EX.UNMEASURED]["resolved"]
            == out["resolved_total"] == 4)


def test_the_unmeasured_cohort_feeds_no_reachability_figure():
    many = [_unmeasured(EX.VIRTUAL_MATCHED) for _ in range(40)]
    out = EX.reachability_report(many)
    m = out[EX.MEASURED]
    assert m["resolved"] == 0
    assert m["bottleneck"] is None, "no bottleneck may be named from no measurement"
    assert m["median_min_gap_cents"] is None
    assert out["coherence"]["ok"] is True


def test_bottleneck_is_named_only_from_measured_proposals():
    out = EX.reachability_report([_measured(EX.NO_FILL) for _ in range(12)])
    assert "REACHABILITY" in out[EX.MEASURED]["bottleneck"]
    out2 = EX.reachability_report(
        [_measured(EX.VIRTUAL_MATCHED, reached=True) for _ in range(3)]
        + [_measured(EX.NO_FILL, reached=True) for _ in range(9)])
    assert "QUEUE" in out2[EX.MEASURED]["bottleneck"]


def test_every_proposal_is_stamped_with_its_evidence_window():
    p = EX.open_proposal("s", PLAN, T0, None, -2, D("0.39"))
    assert p["schema"] == EX.SCHEMA


# ---- completion-time distribution ----------------------------------------

def test_completion_time_keeps_the_frozen_and_research_horizons_apart():
    p = _measured(EX.VIRTUAL_MATCHED, reached=True)
    p["latency_s"] = 260.4
    q = _measured(EX.NO_FILL)
    q["research"] = {"would_fill_later": True, "later_fill_latency_s": 900.0,
                     "verdict": "SLOW_BUT_REACHABLE"}
    out = EX.completion_time_report([p, q])
    f, r = out["frozen_300s"], out["research_1800s"]
    assert f["filled"] == 1 and f["median_s"] == 260.4
    assert f["fraction_filled_by"]["le_300s"] == 0.5
    assert "le_600s" not in f["fraction_filled_by"], "frozen must not exceed 300s"
    assert r["additional_fills_after_frozen_verdict"] == 1
    assert r["fraction_filled_by"]["le_1800s"] == 1.0


def test_a_fill_near_the_horizon_is_flagged_by_the_final_fifth_share():
    p = _measured(EX.VIRTUAL_MATCHED, reached=True)
    p["latency_s"] = 260.4                       # > 0.8 * 300
    assert EX.completion_time_report([p])[
        "frozen_300s"]["share_of_fills_in_final_fifth"] == 1.0


def test_historical_time_buckets_are_declared_unavailable_not_fabricated():
    h = EX.completion_time_report([])["historical"]
    assert h["available"] is False
    assert h["median_rest_s_filled"] == 7.71
    assert "not comparable" in h["why_unavailable"]
    assert "median_s" not in h and "p90_s" not in h


# ---- reachability buckets -------------------------------------------------

def test_buckets_report_how_close_the_market_came_per_first_leg_state():
    close = [_measured(EX.NO_FILL, ticks=1.0) for _ in range(3)]
    far = [_measured(EX.NO_FILL, ticks=20.0) for _ in range(2)]
    g = EX.reachability_buckets(close + far)["groups"]["all"]
    assert g["n"] == 5
    assert g["within"]["le_1_ticks"] == 0.6
    assert g["within"]["le_8_ticks"] == 0.6
    assert g["median_min_gap_ticks"] == 1.0


def test_buckets_group_by_a_state_field_and_flag_thin_groups():
    p = EX.open_proposal("aec-nfl-bal-ind-2026-09-13", PLAN, T0, None, -2,
                         D("0.39"))
    p.update(status=EX.NO_FILL,
             reachability={"observations": 4, "reached": False,
                           "min_gap_ticks": 2.0, "initial_gap_cents": 1.0,
                           "min_gap_cents": 1.0, "time_to_touch_s": None,
                           "max_favorable_excursion_cents": 0.0})
    out = EX.reachability_buckets([p], key="sport")["groups"]
    assert "nfl" in out
    assert out["nfl"]["sufficient_sample"] is False, "one row is not a group"


def test_buckets_exclude_unmeasured_proposals():
    out = EX.reachability_buckets([_unmeasured(EX.VIRTUAL_MATCHED)])
    assert out["groups"] == {}, "an unmeasured proposal has no gap to bucket"
