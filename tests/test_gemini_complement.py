"""COMPLEMENT REACHABILITY: can the SECOND half of the L5 cycle happen here?

The arms may change a COMPLETE price or how long a quote rests. They may not
change what the frozen policy decides, and nothing here may look like a
realized number.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import complement as CM


# ===================================== the side convention, both legs, pinned

def test_EXTEND_AND_COMPLETE_ARE_REACHED_BY_OPPOSITE_AGGRESSORS():
    """The whole study rests on this. Holding YES, an EXTEND (more YES) is
    reached by someone SELLING YES; the COMPLETE (buy NO) is reached by
    someone BUYING YES, because selling NO to us IS buying YES."""
    for held in ("YES", "NO"):
        assert (CM.reaching_aggressor(held)
                != CM.reaching_aggressor(CM.complement_leg(held)))
    assert CM.complement_leg("YES") == "NO"
    assert CM.complement_leg("no") == "YES"
    assert CM.reaching_aggressor("YES") == "sell"
    assert CM.reaching_aggressor("NO") == "buy"


def test_a_YES_rung_is_reached_only_by_a_seller_at_or_below_it():
    p = {"type": "sell", "price": "0.40", "amount": "5"}
    assert CM.reaches("YES", "0.40", p) is True
    assert CM.reaches("YES", "0.41", p) is True
    assert CM.reaches("YES", "0.39", p) is False
    assert CM.reaches("YES", "0.40", dict(p, type="buy")) is False


def test_a_NO_rung_is_reached_only_by_a_buyer_at_or_above_its_mirror():
    """CONTINUOUS-5's real fill: a passive NO buy at 0.46 appears on the
    YES-priced tape as an aggressor BUY at 0.54."""
    p = {"type": "buy", "price": "0.54", "amount": "3"}
    assert CM.reaches("NO", "0.46", p) is True     # needs >= 0.54: reached
    assert CM.reaches("NO", "0.47", p) is True     # needs >= 0.53: reached
    assert CM.reaches("NO", "0.45", p) is False    # needs >= 0.55: not reached
    assert CM.reaches("NO", "0.40", dict(p, price="0.53")) is False
    assert CM.reaches("NO", "0.46", dict(p, type="sell")) is False


def test_an_unreadable_print_reaches_nothing():
    assert CM.reaches("YES", "0.40", {"type": "sell", "price": None}) is False
    assert CM.reaches("YES", None, {"type": "sell", "price": "0.1"}) is False
    assert CM.reaches("YES", "0.40", {}) is False


# ====================================================== flow after our fill

def _t(ms, typ, px, amt):
    return {"utc_ms": ms, "type": typ, "price": px, "amount": amt}


def test_flow_is_split_by_which_of_OUR_rungs_it_could_reach():
    prints = [_t(100, "sell", "0.40", "10"), _t(200, "sell", "0.40", "10"),
              _t(300, "buy", "0.60", "5")]
    f = CM.flow_direction_after(prints, from_ms=0, to_ms=1000, held_leg="YES")
    assert f["extend_is_reached_by"] == "sell"
    assert f["complement_is_reached_by"] == "buy"
    assert f["prints_toward_extend"] == 2
    assert f["prints_toward_complement"] == 1
    assert f["one_sided_toward_extend"] is False
    assert f["no_flow_at_all"] is False


def test_one_sided_means_the_complement_side_never_traded():
    prints = [_t(100, "sell", "0.40", "10"), _t(200, "sell", "0.30", "10")]
    f = CM.flow_direction_after(prints, from_ms=0, to_ms=1000, held_leg="YES")
    assert f["one_sided_toward_extend"] is True
    assert f["prints_toward_complement"] == 0


def test_flow_before_our_fill_is_not_ours_to_count():
    prints = [_t(50, "sell", "0.40", "10"), _t(500, "sell", "0.40", "10")]
    f = CM.flow_direction_after(prints, from_ms=100, to_ms=1000,
                                held_leg="YES")
    assert f["prints_toward_extend"] == 1


def test_no_flow_at_all_is_distinguished_from_one_sided_flow():
    f = CM.flow_direction_after([], from_ms=0, to_ms=10, held_leg="YES")
    assert f["no_flow_at_all"] is True
    assert f["one_sided_toward_extend"] is None
    assert f["extend_share_of_prints"] is None


def test_share_is_by_notional_not_only_by_count():
    """One 1000-lot on the complement side is not outvoted by three 1-lots."""
    prints = [_t(1, "sell", "0.50", "1"), _t(2, "sell", "0.50", "1"),
              _t(3, "sell", "0.50", "1"), _t(4, "buy", "0.50", "1000")]
    f = CM.flow_direction_after(prints, from_ms=0, to_ms=99, held_leg="YES")
    assert f["extend_share_of_prints"] == 0.75
    assert D(f["extend_share_of_notional"]) < D("0.01")


# ============================================ coverage: were we even there?

def test_COMPLEMENT_FLOW_AFTER_OUR_LAST_CYCLE_IS_NOT_ABSENT_FLOW():
    """The finding this module exists to make measurable: flow we were not
    there for is a coverage failure, not a one-sidedness failure."""
    prints = [_t(100, "buy", "0.60", "10"), _t(900, "buy", "0.60", "90")]
    c = CM.complement_flow_coverage(prints, from_ms=0, last_cycle_ms=500,
                                    to_ms=1000, held_leg="YES")
    assert c["prints_inside_our_cycles"] == 1
    assert c["prints_after_our_last_cycle"] == 1
    assert D(c["notional_inside_our_cycles"]) == D("6.0")
    assert D(c["notional_after_our_last_cycle"]) == D("54.0")
    assert D(c["share_arriving_after_we_stopped"]) == D("0.9")


def test_coverage_counts_only_the_complement_direction():
    prints = [_t(900, "sell", "0.40", "100")]          # toward EXTEND
    c = CM.complement_flow_coverage(prints, from_ms=0, last_cycle_ms=500,
                                    to_ms=1000, held_leg="YES")
    assert c["prints_after_our_last_cycle"] == 0
    assert c["share_arriving_after_we_stopped"] is None


def test_coverage_stops_at_expiry():
    prints = [_t(5000, "buy", "0.60", "10")]
    c = CM.complement_flow_coverage(prints, from_ms=0, last_cycle_ms=500,
                                    to_ms=1000, held_leg="YES")
    assert c["prints_after_our_last_cycle"] == 0


def test_pooled_coverage_ignores_traces_that_did_not_compute():
    traces = [{"computable": False},
              {"computable": True, "symbol": "S", "seed": {"leg": "YES"},
               "complement_flow_coverage": {
                   "prints_inside_our_cycles": 1,
                   "prints_after_our_last_cycle": 2,
                   "notional_inside_our_cycles": "10",
                   "notional_after_our_last_cycle": "90",
                   "share_arriving_after_we_stopped": "0.9"}}]
    p = CM.pooled_flow_coverage(traces)
    assert p["seeds"] == 1
    assert D(p["share_of_complement_notional_arriving_after_we_stopped"]) == D("0.9")


# ================================================ improvement stays a maker

def test_ONE_TICK_BETTER_IS_REFUSED_WHEN_IT_WOULD_CROSS():
    """Crossing is not authorized. The arm must decline rather than take."""
    book = {"yes": {"bid": "0.40", "ask": "0.41"}}
    r = CM.maker_only_improvement(book, "YES", "0.40")
    assert r["exists"] is False
    assert r["maker_only"] is False
    assert "crossing is not authorized" in r["why"]


def test_one_tick_better_is_allowed_when_it_stays_inside_the_spread():
    book = {"yes": {"bid": "0.40", "ask": "0.50"}}
    r = CM.maker_only_improvement(book, "YES", "0.40")
    assert r == {"exists": True, "maker_only": True, "price": "0.41"}


def test_improvement_is_refused_when_maker_only_cannot_be_PROVEN():
    """No ask means we cannot show the improved price rests. Refuse."""
    r = CM.maker_only_improvement({"yes": {"bid": "0.40"}}, "YES", "0.40")
    assert r["exists"] is False
    assert "cannot be proven" in r["why"]


def test_improvement_reads_the_leg_it_is_told_to():
    book = {"yes": {"bid": "0.40", "ask": "0.41"},
            "no": {"bid": "0.30", "ask": "0.90"}}
    assert CM.maker_only_improvement(book, "NO", "0.30")["price"] == "0.31"
    assert CM.maker_only_improvement(book, "YES", "0.40")["exists"] is False


# ============================================================ queue ahead

def test_size_ahead_counts_every_rung_at_or_better_than_ours():
    depth = {"readable": True, "bids": {"levels": [
        {"price": "0.45", "qty": "10"}, {"price": "0.40", "qty": "7"},
        {"price": "0.35", "qty": "99"}]}}
    assert CM._size_ahead(depth, "YES", "0.40") == D("17")


def test_size_ahead_is_unknown_for_a_NO_rung_and_for_an_unreadable_ladder():
    depth = {"readable": True, "bids": {"levels": [
        {"price": "0.45", "qty": "10"}]}}
    assert CM._size_ahead(depth, "NO", "0.40") is None
    assert CM._size_ahead({"readable": False}, "YES", "0.40") is None
    assert CM._size_ahead(None, "YES", "0.40") is None


# ========================================== the materiality bar, and its math

def test_the_exact_test_calls_one_extra_seed_out_of_eighteen_a_coin_flip():
    """1-of-18 against 0-of-18 is p = 0.50. It is not a finding."""
    assert CM._fisher_one_sided_greater(1, 17, 0, 18) == pytest.approx(0.5)


def test_the_exact_test_separates_a_real_difference():
    assert CM._fisher_one_sided_greater(10, 8, 0, 18) < 0.01


def test_a_single_matched_pair_does_NOT_materially_change_leakage():
    by_arm = {
        CM.NATIVE: {"initial_fills": 18, "became_matched_inventory": 0,
                    "total_residual_units": "0.25", "median_pair_basis": None},
        CM.IMPROVE_1: {"initial_fills": 18, "became_matched_inventory": 1,
                       "total_residual_units": "0.24",
                       "median_pair_basis": "0.84"}}
    v = CM.leakage_verdict(by_arm)
    assert v["verdict"] == "BROAD_LEAKAGE_UNCHANGED_IN_EVERY_ARM"
    assert v["any_arm_forms_matched_inventory"] is True
    assert v["matched_inventory_formed_but_not_materially"] is True
    assert v["arms_that_materially_change_leakage"] == []


def test_MATCHED_INVENTORY_ABOVE_PAR_IS_A_MATCHED_LOSS_NOT_A_FIX():
    """An arm that only forms pairs by overpaying has paid for the leak."""
    by_arm = {
        CM.NATIVE: {"initial_fills": 18, "became_matched_inventory": 0,
                    "total_residual_units": "0.25", "median_pair_basis": None},
        "ARM": {"initial_fills": 18, "became_matched_inventory": 12,
                "total_residual_units": "0.20", "median_pair_basis": "1.02"}}
    v = CM.leakage_verdict(by_arm)
    row = [r for r in v["arms"] if r["arm"] == "ARM"][0]
    assert row["gate_separable_from_native"] is True
    assert row["gate_pair_basis_at_or_below_par"] is False
    assert row["materially_changes_leakage"] is False
    assert v["verdict"] == "BROAD_LEAKAGE_UNCHANGED_IN_EVERY_ARM"


def test_an_arm_that_buys_pairs_with_MORE_residual_is_not_a_fix_either():
    by_arm = {
        CM.NATIVE: {"initial_fills": 18, "became_matched_inventory": 0,
                    "total_residual_units": "0.10", "median_pair_basis": None},
        "ARM": {"initial_fills": 18, "became_matched_inventory": 12,
                "total_residual_units": "0.99", "median_pair_basis": "0.90"}}
    row = [r for r in CM.leakage_verdict(by_arm)["arms"]
           if r["arm"] == "ARM"][0]
    assert row["gate_residual_not_worse_than_native"] is False
    assert row["materially_changes_leakage"] is False


def test_an_arm_that_clears_all_three_gates_IS_material():
    by_arm = {
        CM.NATIVE: {"initial_fills": 18, "became_matched_inventory": 0,
                    "total_residual_units": "0.25", "median_pair_basis": None},
        "ARM": {"initial_fills": 18, "became_matched_inventory": 12,
                "total_residual_units": "0.20", "median_pair_basis": "0.94"}}
    v = CM.leakage_verdict(by_arm)
    assert v["verdict"] == "SOME_ARM_MATERIALLY_CHANGES_LEAKAGE"
    assert v["arms_that_materially_change_leakage"] == ["ARM"]


def test_NATIVE_can_never_be_material_against_itself():
    by_arm = {CM.NATIVE: {"initial_fills": 18, "became_matched_inventory": 9,
                          "total_residual_units": "0.20",
                          "median_pair_basis": "0.90"}}
    row = CM.leakage_verdict(by_arm)["arms"][0]
    assert row["gate_separable_from_native"] is False
    assert row["materially_changes_leakage"] is False


# ================================================ the arms and the summary

def test_the_arms_are_the_ones_the_owner_asked_to_compare():
    assert CM.ARMS == ("NATIVE", "IMPROVE_1", "IMPROVE_1_REST_15",
                       "IMPROVE_1_REST_30", "IMPROVE_1_REST_45",
                       "IMPROVE_1_REST_60",
                       "NATIVE_OPEN_EXTEND_COMPETITIVE_COMPLETE")
    assert sorted(CM.REST_SECONDS.values()) == [15, 30, 45, 60]


def test_THE_PRIMARY_METRIC_IS_MATCHED_INVENTORY_NOT_FILL_COUNT():
    traces = [
        {"computable": True, "final": {"matched": "0.00", "residual": "-0.02",
                                       "effective_pair_cost": None,
                                       "capital_at_risk_usd": "0.01"},
         "complement_quotes": 4, "complement_quotes_with_a_reaching_print": 0,
         "complement_theoretical_fills": 0, "extend_theoretical_fills": 3},
        {"computable": True, "final": {"matched": "0.01", "residual": "0.00",
                                       "effective_pair_cost": "0.84",
                                       "capital_at_risk_usd": "0.0084"},
         "complement_quotes": 6, "complement_quotes_with_a_reaching_print": 1,
         "complement_theoretical_fills": 1, "extend_theoretical_fills": 1,
         "seconds_to_first_reachable_complement": 33.2,
         "seconds_to_complement_fill": 33.2}]
    s = CM.arm_summary(traces)
    assert s["initial_fills"] == 2
    assert s["became_matched_inventory"] == 1
    assert s["matched_share"] == 0.5
    assert s["median_pair_basis"] == "0.84"
    assert s["complement_reach_rate"] == 0.1
    assert s["extend_to_complement_fill_ratio"] == "4.00"
    assert s["median_seconds_to_complement_fill"] == 33.2


def test_zero_complement_fills_is_reported_as_INFINITE_not_as_a_division():
    traces = [{"computable": True,
               "final": {"matched": "0", "residual": "0.07",
                         "effective_pair_cost": None,
                         "capital_at_risk_usd": "0.03"},
               "complement_quotes": 9,
               "complement_quotes_with_a_reaching_print": 0,
               "complement_theoretical_fills": 0,
               "extend_theoretical_fills": 7}]
    s = CM.arm_summary(traces)
    assert s["extend_to_complement_fill_ratio"].startswith("INFINITE")
    assert s["became_matched_inventory"] == 0
    assert s["median_pair_basis"] is None


# ======================================================= the forward replay

def _cycle(ms, tte, ybid, yask, nbid, nask, utc="u"):
    return {"utc_ms": ms, "utc": utc, "tte_s": tte,
            "book": {"readable": True, "identity_consistent": True,
                     "tick": "0.01", "t_utc": ms / 1000.0,
                     "yes": {"bid": ybid, "ask": yask},
                     "no": {"bid": nbid, "ask": nask}},
            "depth": {"readable": False}}


@pytest.fixture()
def _terms(monkeypatch):
    from src.gemini_execution import metadata as MD
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})


def test_an_unreadable_seed_refuses_rather_than_guessing(_terms):
    t = CM.trace(symbol="S", arm=CM.NATIVE, seed_leg="YES", seed_qty=None,
                 seed_price="0.4", seed_ms=0, cycles=[], prints=[],
                 resolution={}, expiry_ms=1000)
    assert t["computable"] is False
    assert "SHADOW/THEORETICAL" in t["labels"]


def test_THE_ARM_NEVER_CHANGES_WHAT_THE_FROZEN_POLICY_DECIDES(_terms):
    """Every arm must take the same actions in the same order. Only the
    COMPLETE price and the rest may differ."""
    cycles = [_cycle(1000 * i, 200 - 8 * i, "0.40", "0.50", "0.45", "0.55")
              for i in range(1, 8)]
    shapes = {}
    for arm in CM.ARMS:
        t = CM.trace(symbol="S", arm=arm, seed_leg="YES", seed_qty="0.01",
                     seed_price="0.40", seed_ms=0, cycles=cycles, prints=[],
                     resolution={}, expiry_ms=10 ** 9)
        shapes[arm] = [[(a["kind"], a["leg"]) for a in s["actions"]]
                       for s in t["steps"]]
    assert len({str(v) for v in shapes.values()}) == 1


def test_cycles_before_the_seed_fill_are_not_replayed(_terms):
    cycles = [_cycle(-5000, 260, "0.40", "0.50", "0.45", "0.55"),
              _cycle(5000, 240, "0.40", "0.50", "0.45", "0.55")]
    t = CM.trace(symbol="S", arm=CM.NATIVE, seed_leg="YES", seed_qty="0.01",
                 seed_price="0.40", seed_ms=0, cycles=cycles, prints=[],
                 resolution={}, expiry_ms=10 ** 9)
    assert t["cycles_replayed"] == 1


def test_NO_CYCLES_AFTER_THE_FILL_MEANS_NO_COMPLETE_WAS_EVER_POSSIBLE(_terms):
    """Seven of eighteen real seeds land here. It is not a null result --
    it is the coverage failure, and it must stay visible."""
    t = CM.trace(symbol="S", arm=CM.NATIVE, seed_leg="YES", seed_qty="0.01",
                 seed_price="0.40", seed_ms=0, cycles=[], prints=[],
                 resolution={}, expiry_ms=10 ** 9)
    assert t["computable"] is True
    assert t["cycles_replayed"] == 0
    assert t["complement_quotes"] == 0
    assert t["seconds_to_first_reachable_complement"] is None
    assert D(t["final"]["residual"]) == D("0.01")


def test_a_residual_mark_no_cycle_reached_says_so(_terms):
    cycles = [_cycle(1000, 240, "0.40", "0.50", "0.45", "0.55")]
    t = CM.trace(symbol="S", arm=CM.NATIVE, seed_leg="YES", seed_qty="0.01",
                 seed_price="0.40", seed_ms=0, cycles=cycles, prints=[],
                 resolution={}, expiry_ms=10 ** 9)
    for k in ("tte_60", "tte_30", "tte_0"):
        assert "no cycle reached this mark" in t["residual_at"][k]["note"]


def test_every_theoretical_fill_carries_a_SHADOW_provenance(_terms):
    cycles = [_cycle(1000 * i, 200 - 8 * i, "0.40", "0.50", "0.45", "0.55")
              for i in range(1, 6)]
    prints = [{"utc_ms": 1500, "type": "sell", "price": "0.20",
               "amount": "500"}]
    t = CM.trace(symbol="S", arm=CM.NATIVE, seed_leg="YES", seed_qty="0.01",
                 seed_price="0.40", seed_ms=0, cycles=cycles, prints=prints,
                 resolution={}, expiry_ms=10 ** 9)
    assert t["computable"] is True
    assert "SHADOW/THEORETICAL" in t["labels"]
    assert "coin flip" in t["settlement_caveat"]


def test_settlement_is_reported_but_never_used_to_justify(_terms):
    t = CM.trace(symbol="S", arm=CM.NATIVE, seed_leg="YES", seed_qty="0.01",
                 seed_price="0.40", seed_ms=0, cycles=[], prints=[],
                 resolution={"outcome": "yes"}, expiry_ms=10 ** 9)
    assert "settlement" in t
    assert "NOT used to justify" in t["settlement_caveat"]


def test_nothing_in_this_module_is_labelled_realized():
    import ast
    import inspect
    src = inspect.getsource(CM)
    banned = ("realized", "realised", "profit", "pnl")
    # a DISCLAIMER may name the word; a CLAIM may not.
    denials = ("never", "not ", "nothing was", "no order existed", "cannot")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if any(d in low for d in denials):
                continue
            for w in banned:
                assert w not in low, node.value
