"""When the residual accumulated, and what the public tape says about it.

THE TRAP THIS SUITE EXISTS TO AVOID. A counterfactual is the easiest thing in
this programme to get wrong, because nothing contradicts it. So the fill model
is not asserted -- it is CALIBRATED against 56 fills we know happened, from
the venue's own trade record, and the calibration is a test. If the model
cannot reproduce a fill that did happen it may not be used to assert one that
did not.

THE SECOND TRAP IS HINDSIGHT. `complement_opportunity` scans the whole
post-onset tape and is deliberately an upper bound; `simulate_completion` is
CAUSAL and the suite proves it by feeding it a tape whose only qualifying
print is in the past. A simulator that fills on that print is reading the
future and every number it produces is worthless.

THE THIRD IS COUNTING CYCLES AS ACTIONS. The controller keeps an order
resting; a mutation is a PRICE CHANGE. Counting cycles would invent hundreds
of actions against a 12-slot ledger and would make every policy look
unaffordable for a reason that is not real.
"""
import glob
import json
from decimal import Decimal as D

import pytest

from src.gemini_execution import residual_timeline as RT

COHORT2 = sorted(glob.glob("analysis/gemini/cohort_window_20260922T1[1-4]*.json"))


@pytest.fixture(scope="module")
def reps():
    out = [json.load(open(p)) for p in COHORT2]
    # scoped BY SYMBOL, never by file count: the glob grows whenever another
    # cohort runs and a test pinned to a count is pinning a moment.
    return [r for r in out
            if str(r.get("window", {}).get("symbol", "")).startswith(
                "GEMI-BTC15M26092212")
            or str(r.get("window", {}).get("symbol", "")).startswith(
                "GEMI-BTC15M26092213")
            or str(r.get("window", {}).get("symbol", "")).startswith(
                "GEMI-BTC15M26092214")]


#: a fixed base so a synthetic print's clock and a synthetic cycle's clock
#: are the SAME clock. Getting that wrong makes every opportunity scan empty
#: and the emptiness looks like a finding rather than a fixture bug.
T0 = 1790000000000


def _iso(ms):
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


def _rep(prints, decisions=None, trades=None):
    return {
        "decisions": decisions or [
            {"cycle": 1, "utc": _iso(T0), "tte_s": 100,
             "inventory_before_decision": {"yes_qty": "2", "no_qty": "0",
                                           "avg_yes_cost": "0.40"},
             "book_state": {"presented": {"readable": True,
                                          "yes": {"bid": "0.40", "ask": "0.50"},
                                          "no": {"bid": "0.50", "ask": "0.60"}},
                            "flow": {"readable": True, "prints": prints}}}],
        "after": {"trades": trades or [], "orders_seen": []},
        "window": {"symbol": "TEST", "inventory": {}},
    }


# -- the tape ---------------------------------------------------------------
def test_tape_dedupes_by_tid_and_orders_by_time():
    p = [{"tid": 2, "utc_ms": T0 + 200, "price": "0.5", "qty": "1",
          "side": "buy"},
         {"tid": 1, "utc_ms": T0 + 100, "price": "0.4", "qty": "1",
          "side": "sell"},
         {"tid": 2, "utc_ms": T0 + 200, "price": "0.5", "qty": "1",
          "side": "buy"}]
    t = RT.tape(_rep(p))
    assert t["n_prints"] == 2
    assert [x["tid"] for x in t["prints"]] == [1, 2]


def test_tape_drops_rows_it_cannot_read_rather_than_guessing():
    p = [{"tid": 1, "utc_ms": T0, "price": None, "qty": "1", "side": "sell"},
         {"tid": 2, "utc_ms": T0, "price": "0.4", "qty": "1", "side": "??"},
         {"tid": 3, "utc_ms": T0, "price": "0.4", "qty": "1", "side": "sell"}]
    assert RT.tape(_rep(p))["n_prints"] == 1


def test_tape_reports_its_own_truncation_risk():
    """A polled source that keeps returning a full page may be dropping."""
    dec = []
    for i in range(3):
        dec.append({"cycle": i, "utc": "2026-09-22T12:00:00+00:00", "tte_s": 1,
                    "inventory_before_decision": {},
                    "book_state": {"flow": {"readable": True, "prints": [
                        {"tid": 10 * i + j, "utc_ms": T0, "price": "0.4",
                         "qty": "1", "side": "sell"} for j in range(5)]}}})
    t = RT.tape({"decisions": dec})
    assert t["max_prints_per_read"] == 5
    assert t["reads_at_max"] == 3 and t["truncation_possible"] is True


# -- the fill model ---------------------------------------------------------
@pytest.mark.parametrize("price,px,side,want", [
    ("0.40", "0.39", "sell", RT.THROUGH),
    ("0.40", "0.40", "sell", RT.AT_LEVEL),
    ("0.40", "0.41", "sell", None),
    ("0.40", "0.39", "buy", None),
])
def test_qualifies_yes_is_a_bid_hit_by_a_seller(price, px, side, want):
    assert RT.qualifies(RT.YES, D(price), {"price": D(px), "side": side}) == want


@pytest.mark.parametrize("price,px,side,want", [
    # a NO buy at 0.30 is an ask at 0.70; a buyer lifting above it is through
    ("0.30", "0.71", "buy", RT.THROUGH),
    ("0.30", "0.70", "buy", RT.AT_LEVEL),
    ("0.30", "0.69", "buy", None),
    ("0.30", "0.71", "sell", None),
])
def test_qualifies_no_is_mirrored_through_the_one_ladder(price, px, side, want):
    assert RT.qualifies(RT.NO, D(price), {"price": D(px), "side": side}) == want


def test_calibration_reproduces_every_confirmed_cohort2_fill(reps):
    """THE LICENCE FOR EVERY COUNTERFACTUAL IN THIS MODULE.

    Each confirmed maker fill must appear on the public tape as an aggressor
    print on the OPPOSITE side at the MIRRORED price. This is an invariant of
    the venue's ladder, not a property of one run.
    """
    cal = RT.calibrate(reps)
    assert cal["fills_checked"] >= 56
    assert cal["present_on_tape"] == cal["fills_checked"]
    assert cal["aggressor_side_as_modelled"] == cal["fills_checked"]
    assert cal["mirrored_price_as_modelled"] == cal["fills_checked"]
    assert cal["verdict"] == "CONFIRMED" and cal["misses"] == []


# -- the timeline -----------------------------------------------------------
def test_timeline_decomposes_each_cycle():
    s = RT.timeline(_rep([]))[0]
    assert (s["matched"], s["residual"], s["dominant"]) == (0, 2, RT.YES)


def test_onset_is_none_for_a_window_that_ended_matched():
    r = _rep([])
    r["decisions"][0]["inventory_before_decision"] = {"yes_qty": "2",
                                                      "no_qty": "2"}
    assert RT.terminal_residual_onset(RT.timeline(r))["onset"] is None


def _steps(pairs):
    return [{"cycle": i, "utc": None, "utc_ms": i, "tte_s": None,
             "yes_qty": y, "no_qty": n, "matched": min(y, n),
             "residual": abs(y - n),
             "dominant": None if y == n else (RT.YES if y > n else RT.NO),
             "avg_yes": None, "avg_no": None, "yes_bid": None, "no_bid": None}
            for i, (y, n) in enumerate(pairs)]


def test_onset_walks_back_only_to_the_last_balanced_cycle():
    s = _steps([(0, 0), (1, 0), (1, 1), (2, 1), (3, 1)])
    o = RT.terminal_residual_onset(s)
    assert o["onset"] == 3 and o["terminal_residual"] == 2
    assert o["cycles_exposed"] == 2


def test_onset_stops_at_a_change_of_dominant_leg():
    """A residual that switched sides is a different residual."""
    s = _steps([(0, 2), (1, 2), (2, 2), (3, 2), (4, 2)])
    assert RT.terminal_residual_onset(s)["onset"] == 3


# -- the opportunity scan ---------------------------------------------------
def test_our_own_fill_is_not_evidence_that_another_order_would_have_filled():
    p = [{"tid": 7, "utc_ms": T0 + 10, "price": "0.80", "qty": "1", "side": "buy"}]
    rep = _rep(p, trades=[{"tid": 7, "order_id": "1"}])
    opp = RT.complement_opportunity(rep)
    assert opp["own_prints_excluded"] == 1
    assert opp["verdict"] == "NO_QUALIFYING_PRINT"


def test_opportunity_prices_the_complement_off_the_mirrored_print():
    p = [{"tid": 7, "utc_ms": T0 + 10, "price": "0.80", "qty": "1", "side": "buy"}]
    opp = RT.complement_opportunity(_rep(p))
    assert opp["complement_leg"] == RT.NO
    # a NO buy at 0.20 is an ask at 0.80 -> at the level; 0.21 is through it
    assert opp["cheapest_complement_price"][RT.AT_LEVEL] == "0.2"
    assert opp["cheapest_complement_price"][RT.THROUGH] == "0.21"


# -- the counterfactual arithmetic ------------------------------------------
def test_counterfactual_is_the_identity_and_not_a_model():
    p = [{"tid": 7, "utc_ms": T0 + 10, "price": "0.80", "qty": "1", "side": "buy"}]
    rep = _rep(p)
    cf = RT.counterfactual(rep, resolution_side="no", avg_dominant="0.40")
    # dominant YES lost: each of 2 units pays 1 and cost 0.21 to complete
    assert cf["delta_net_usd"] == "1.58"
    assert cf["effective_pair_cost"] == "0.61"
    assert cf["pair_locked_profitable"] is True


def test_counterfactual_charges_the_completion_when_the_dominant_leg_won():
    p = [{"tid": 7, "utc_ms": T0 + 10, "price": "0.80", "qty": "1", "side": "buy"}]
    cf = RT.counterfactual(_rep(p), resolution_side="yes", avg_dominant="0.40")
    assert cf["dominant_won"] is True
    assert cf["delta_net_usd"] == "-0.42"        # -2 x 0.21


def test_counterfactual_refuses_to_guess_an_unread_resolution():
    p = [{"tid": 7, "utc_ms": T0 + 10, "price": "0.80", "qty": "1", "side": "buy"}]
    cf = RT.counterfactual(_rep(p), resolution_side=None, avg_dominant="0.40")
    assert cf["verdict"] == "UNRESOLVED" and cf["delta_net_usd"] is None


# -- the causal simulator ---------------------------------------------------
def _two_cycle(prints, ms=(0, 2000)):
    dec = []
    for i, t in enumerate(ms):
        dec.append({
            "cycle": i + 1,
            "utc": _iso(T0 + t),
            "tte_s": 100 - i,
            "inventory_before_decision": {"yes_qty": "1", "no_qty": "0",
                                          "avg_yes_cost": "0.30"},
            "book_state": {"presented": {"readable": True,
                                         "yes": {"bid": "0.28", "ask": "0.32"},
                                         "no": {"bid": "0.68", "ask": "0.72"}},
                           "flow": {"readable": True, "prints": prints}}})
    return {"decisions": dec, "after": {"trades": [], "orders_seen": []},
            "window": {"symbol": "TEST", "inventory": {}}}


def test_simulator_cannot_fill_on_a_print_that_predates_the_order():
    """READING THE FUTURE IS THE ONE FAILURE THAT INVALIDATES EVERYTHING."""
    early = [{"tid": 1, "utc_ms": T0 - 500, "price": "0.95", "qty": "5",
              "side": "buy"}]
    sim = RT.simulate_completion(_two_cycle(early), policy=RT.join_touch)
    assert sim["units_completed"] == 0


def test_simulator_fills_on_a_print_inside_the_resting_interval():
    late = [{"tid": 1, "utc_ms": T0 + 1500, "price": "0.95", "qty": "5",
             "side": "buy"}]
    sim = RT.simulate_completion(_two_cycle(late), policy=RT.join_touch)
    assert sim["units_completed"] == 1
    assert sim["completions"][0]["leg"] == RT.NO


def test_a_mutation_is_a_price_change_and_not_a_cycle():
    sim = RT.simulate_completion(_two_cycle([]), policy=RT.join_touch)
    assert sim["cycles_with_residual"] == 2 and sim["mutations"] == 1


def test_the_action_budget_freezes_the_working_order_rather_than_cancelling():
    """Spending the budget must not silently remove the resting order."""
    dec = _two_cycle([{"tid": 1, "utc_ms": T0 + 2500, "price": "0.95",
                       "qty": "5", "side": "buy"}])
    dec["decisions"][1]["book_state"]["presented"]["no"] = {"bid": "0.60",
                                                            "ask": "0.72"}
    sim = RT.simulate_completion(dec, policy=RT.join_touch, max_extra_actions=1)
    assert sim["mutations"] == 1 and sim["units_completed"] == 1


# -- the policies -----------------------------------------------------------
def test_join_touch_never_crosses():
    step = {"no_bid": D("0.68"), "no_ask": D("0.69"),
            "yes_bid": None, "yes_ask": None}
    assert RT.join_touch(comp=RT.NO, step=step, improve=0) == D("0.68")
    assert RT.join_touch(comp=RT.NO, step=step, improve=1) is None


def test_pair_target_declines_when_the_touch_cannot_reach_the_target():
    step = {"no_bid": D("0.68"), "no_ask": D("0.72")}
    # 0.96 - 0.40 = 0.56, and 0.56 <= 0.68, so it rests at 0.56
    assert RT.pair_target(comp=RT.NO, step=step, avg_dom=D("0.40")) == D("0.56")
    # 0.96 - 0.20 = 0.76 is BETTER than the touch: that would be crossing
    assert RT.pair_target(comp=RT.NO, step=step, avg_dom=D("0.20")) is None


def test_cost_aware_pays_up_only_for_a_cheap_residual():
    """L5's history, not COHORT-2, is what sets this threshold."""
    step = {"no_bid": D("0.68"), "no_ask": D("0.72")}
    assert RT.cost_aware(comp=RT.NO, step=step,
                         avg_dom=D("0.30")) == D("0.68")     # cheap -> join
    assert RT.cost_aware(comp=RT.NO, step=step,
                         avg_dom=D("0.60")) == D("0.36")     # dear -> hold out


def test_settle_simulation_matches_the_counterfactual_identity():
    sim = {"completions": [{"price": "0.21", "qty": 1},
                           {"price": "0.25", "qty": 1}],
           "units_completed": 2}
    assert RT.settle_simulation(sim, resolution_side="no",
                                dominant=RT.YES)["delta_net_usd"] == "1.54"
    assert RT.settle_simulation(sim, resolution_side="yes",
                                dominant=RT.YES)["delta_net_usd"] == "-0.46"


# -- the real windows -------------------------------------------------------
def test_every_cohort2_window_ended_with_a_terminal_residual(reps):
    """The finding, stated as the invariant it is: not one of the eleven
    windows finished matched. `residual` is not an edge case of this policy,
    it is its normal terminal state."""
    assert len(reps) >= 11
    for rep in reps:
        o = RT.terminal_residual_onset(RT.timeline(rep))
        assert o["onset"] is not None, rep["window"]["symbol"]
        assert o["terminal_residual"] >= 1


def test_the_ladder_identity_holds_on_every_readable_cycle(reps):
    """NO bid = 1 - YES ask, which is what makes the mirrored fill model
    legitimate. The venue reports the deviation itself."""
    checked = 0
    for rep in reps:
        for dec in rep["decisions"]:
            pres = (dec.get("book_state") or {}).get("presented") or {}
            ident = pres.get("complement_identity")
            if not pres.get("readable") or not ident:
                continue
            checked += 1
            for v in ident.values():
                assert D(str(v)) == 0, (rep["window"]["symbol"], ident)
    assert checked > 100


def test_the_spring_completion_rule_never_fires_on_cohort2(reps):
    """THE DEFECT THIS STUDY FOUND. `pair <= 0.96` completes nothing across
    all eleven windows: by the time a residual exists the complement costs
    more than the target allows, so the rule declines exactly when completion
    is worth most."""
    units = 0
    for rep in reps:
        units += RT.simulate_completion(
            rep, policy=RT.pair_target, target=D("0.96"))["units_completed"]
    assert units == 0


def test_cohort2_forfeited_slots_while_its_completion_order_sat_stale(reps):
    """THE CONTROL-PLANE DEFECT, pinned to the artifacts that recorded it.

    Not a counterfactual and not a market fact: the controller recomputed a
    complement price every cycle, planned a mutation on a small minority of
    them, and ended every market with unspent action slots while a residual
    it had decided to complete sat open. These files are a historical record
    and will not change, so this is an assertion about what happened, scoped
    by symbol rather than by file count.
    """
    cycles = planned = 0
    stale_obs = []
    for rep in reps:
        decisions = rep["decisions"]
        cycles += len(decisions)
        planned += sum(1 for d in decisions if d["plan"]["mutations"] > 0)
        assert decisions[-1]["budget_after_decision"]["remaining"] > 0, (
            rep["window"]["symbol"])           # slots left unspent, every one
        steps = RT.timeline(rep)
        o = RT.terminal_residual_onset(steps)
        comp = RT.NO if o["dominant"] == RT.YES else RT.YES
        for s in steps[o["onset"]:]:
            bid = s["no_bid"] if comp == RT.NO else s["yes_bid"]
            for w in s["working"]:
                if w["leg"] == comp and bid is not None and w["price"] is not None:
                    stale_obs.append((bid - w["price"]) / D("0.01"))
    assert planned / cycles < D("0.15"), "planner re-priced more than recorded"
    assert len(stale_obs) > 300
    stale_obs.sort()
    median = stale_obs[len(stale_obs) // 2]
    assert median >= 20, f"complement was {median} ticks behind, not 30"


def test_the_module_is_offline_and_read_only():
    """It opens no socket and reads no account. A counterfactual module that
    could touch the venue is a live process wearing an analysis name."""
    import ast
    src = ast.parse(open("src/gemini_execution/residual_timeline.py").read())
    mods = set()
    for n in ast.walk(src):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert mods <= {"decimal", "fractions", "typing", "__future__",
                    "datetime"}, mods


def test_it_is_not_a_bundle_member():
    """The authorized bundle is the money-moving surface. An analysis module
    joining it would move `bundle_sha` and invalidate an authorization for a
    file that cannot place an order."""
    from src.gemini_execution import bundle
    assert "residual_timeline.py" not in set(bundle.MEMBERS)
