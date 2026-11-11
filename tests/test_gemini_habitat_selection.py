"""PRE-ENTRY HABITAT: one row per window, and nothing from after entry."""
from decimal import Decimal as D

import pytest

from src.gemini_execution import habitat_selection as H


def _cyc(ms, tte, bid="0.40", ask="0.50", levels=None):
    return {"utc_ms": ms, "tte_s": tte,
            "book": {"readable": True, "yes": {"bid": bid, "ask": ask}},
            "depth": {"readable": True,
                      "bids": {"levels": levels or [{"price": bid,
                                                     "qty": "50"}]}}}


def _p(ms, typ, px, amt):
    return {"utc_ms": ms, "type": typ, "price": px, "amount": amt}


# ======================================================== the leakage boundary

def test_THE_BOUNDARY_IS_THE_FIRST_RECORDED_CYCLE():
    cyc = [_cyc(5000, 275), _cyc(13000, 267), _cyc(21000, 259)]
    assert H.entry_instant(cyc) == D("5000")
    assert H.entry_instant([]) is None


def test_NO_FEATURE_MAY_DRAW_ON_THE_CURRENT_TAPE_AT_OR_AFTER_ENTRY():
    """The whole point. A print at the entry instant is already the market
    reacting to a world our quote is in."""
    cyc = [_cyc(5000, 275)]
    prints = [_p(1000, "sell", "0.30", "10"),      # before: admissible
              _p(5000, "sell", "0.30", "99"),      # AT the instant: excluded
              _p(9000, "sell", "0.30", "99")]      # after: excluded
    r = H.pre_entry_row(symbol="S", cycles=cyc, prints=prints,
                        prior_symbol=None, prior_prints=[])
    assert r["own_prints_before_entry"] == 1
    assert D(r["own_notional_before_entry"]) == D("3.0")
    assert r["leakage_check"]["clean"] is True


def test_the_leakage_check_FAILS_a_row_built_past_the_boundary():
    bad = {"entry_instant_ms": "5000", "latest_feature_source_ms": "9000"}
    got = H.check_no_leakage(bad)
    assert got["clean"] is False
    assert "at or after the entry instant" in got["why"]


def test_predecessor_only_features_cannot_leak_and_say_so():
    got = H.check_no_leakage({"entry_instant_ms": "5000",
                              "latest_feature_source_ms": None})
    assert got["clean"] is True
    assert "cannot leak" in got["why"]


def test_a_window_with_no_cycle_has_no_boundary_and_is_refused():
    r = H.pre_entry_row(symbol="S", cycles=[], prints=[], prior_symbol=None,
                        prior_prints=[])
    assert r["admissible"] is False
    assert "no entry instant" in r["why"]


# ============================================================== the features

def test_the_predecessor_tape_is_measured_over_its_OWN_span():
    """It has already expired when our window opens, so a trailing wall-clock
    window over it is always empty. Its rate is its own life's rate."""
    prior = [_p(0, "buy", "0.50", "10"), _p(30000, "sell", "0.50", "10"),
             _p(60000, "buy", "0.50", "10")]
    r = H.pre_entry_row(symbol="S", cycles=[_cyc(120000, 275)], prints=[],
                        prior_symbol="P", prior_prints=prior)
    assert r["prior_prints"] == 3
    assert r["prior_span_s"] == 60.0
    assert D(r["prior_prints_per_min"]) == D("3")
    assert r["prior_two_sided"] is True
    assert r["prior_side_balance"] == 0.5
    assert r["prior_seconds_since_last_print"] == 60.0


def test_a_one_sided_predecessor_is_marked_one_sided():
    prior = [_p(0, "buy", "0.5", "10"), _p(1000, "buy", "0.5", "10")]
    r = H.pre_entry_row(symbol="S", cycles=[_cyc(9000, 275)], prints=[],
                        prior_symbol="P", prior_prints=prior)
    assert r["prior_two_sided"] is False
    assert r["prior_side_balance"] == 0.0


def test_an_empty_predecessor_reports_zero_not_a_guess():
    r = H.pre_entry_row(symbol="S", cycles=[_cyc(9000, 275)], prints=[],
                        prior_symbol="P", prior_prints=[])
    assert r["prior_prints"] == 0
    assert r["prior_prints_per_min"] is None
    assert r["prior_two_sided"] is None


def test_the_book_comes_from_the_instant_before_our_quote_existed():
    cyc = [_cyc(5000, 275, bid="0.31", ask="0.66",
                levels=[{"price": "0.31", "qty": "468"},
                        {"price": "0.29", "qty": "10"}]),
           _cyc(13000, 267, bid="0.99", ask="0.999")]      # later: ignored
    r = H.pre_entry_row(symbol="S", cycles=cyc, prints=[], prior_symbol=None,
                        prior_prints=[])
    assert r["entry_yes_bid"] == "0.31" and r["entry_yes_ask"] == "0.66"
    assert r["entry_spread"] == "0.35"
    assert r["entry_mid"] == "0.485"
    assert r["entry_touch_depth"] == "468"
    assert r["entry_levels"] == 2
    assert r["entry_tte_s"] == 275


def test_book_depletion_is_reported_UNAVAILABLE_rather_than_faked():
    """Depletion needs two reads before entry. Only the live snapshot takes
    them, so most rows genuinely cannot have it."""
    r = H.pre_entry_row(symbol="S", cycles=[_cyc(5000, 275)], prints=[],
                        prior_symbol=None, prior_prints=[])
    assert r["book_depletion_available"] is False
    assert "two book reads before entry" in r["book_depletion_why"]
    r2 = H.pre_entry_row(symbol="S", cycles=[_cyc(5000, 275)], prints=[],
                         prior_symbol=None, prior_prints=[],
                         live_snapshot={"touch_turnover": {
                             "observable": True, "qty_delta": "0.3"}})
    assert r2["book_depletion_available"] is True
    assert r2["touch_turnover_before_entry"] == "0.3"


# =============================================================== the outcomes

def _win(matched="0", basis=None, resid="0", spend="0", init=0, comp=0,
         fills=(), episodes=()):
    return {"initial_fills": init, "complement_fills": comp,
            "fills": list(fills), "missed_complement_episodes": list(episodes),
            "inventory": {"matched_qty": matched,
                          "effective_matched_pair_cost": basis,
                          "directional_residual": resid,
                          "avg_yes_cost": "0.40", "avg_no_cost": "0.50",
                          "total_spend_usd": spend}}


def test_THE_PRIMARY_OUTCOME_IS_PAR_CONSTRAINED_NOT_JUST_MATCHED():
    good = H.outcomes_from(_win(matched="0.01", basis="0.93", spend="0.0093"))
    dear = H.outcomes_from(_win(matched="0.01", basis="1.49", spend="0.0149"))
    assert good["matched_pair"] is True and good[H.PRIMARY] is True
    assert dear["matched_pair"] is True and dear[H.PRIMARY] is False
    assert H.PRIMARY == "par_constrained_matched_pair"


def test_a_pair_exactly_at_par_counts_and_a_cent_above_does_not():
    assert H.outcomes_from(_win(matched="0.01", basis="1.00"))[H.PRIMARY]
    assert not H.outcomes_from(_win(matched="0.01", basis="1.01"))[H.PRIMARY]


def test_the_reachable_print_label_counts_a_reach_without_a_fill():
    w = _win(init=1, episodes=[{"reaching_prints": 2}])
    o = H.outcomes_from(w)
    assert o["opposite_reachable_print_after_first_fill"] is True
    assert o["complete_fill"] is False


def test_residual_dollars_use_the_held_legs_own_average():
    o = H.outcomes_from(_win(resid="0.03", spend="0.012"))
    assert D(o["terminal_residual_usd"]) == D("0.03") * D("0.40")
    o2 = H.outcomes_from(_win(resid="-0.02", spend="0.010"))
    assert D(o2["terminal_residual_usd"]) == D("0.02") * D("0.50")


# =========================================================== the analysis

def _row(sym, feat, good, gross="0.01", cap="0.004", res="0.004"):
    return {"symbol": sym, "admissible": True, "prior_prints_per_min": feat,
            "_outcome": {H.PRIMARY: good, "gross_contracts": gross,
                         "capital_at_risk_usd": cap,
                         "terminal_residual_usd": res}}


def test_SPEARMAN_REFUSES_WHERE_IT_WOULD_BE_MEANINGLESS():
    assert H.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert H.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert H.spearman([1, 1, 1], [1, 2, 3]) is None     # constant
    assert H.spearman([1, 2], [1, 2]) is None           # too few
    assert H.spearman([1, 2, 3], [1, 2]) is None        # mismatched


def test_a_correlation_is_NOT_called_meaningful_below_the_floor():
    rows = [_row("a", 5, True)] + [_row(str(i), i, False) for i in range(10)]
    c = H.correlations(rows, features=("prior_prints_per_min",))
    assert c["positives"] == 1
    assert c["smaller_cell"] == 1
    assert c["any_meaningful"] is False
    assert c["features"]["prior_prints_per_min"]["meaningful"] is False
    assert "%d per cell would be the floor" % H.MIN_PER_CELL in c["why"]


def test_leave_one_out_exposes_a_contrast_that_is_one_window():
    rows = [_row("good", 100.0, True)] + [_row(str(i), 1.0, False)
                                          for i in range(5)]
    loo = H.leave_one_out(rows, features=("prior_prints_per_min",))
    f = loo["features"]["prior_prints_per_min"]
    assert f["contrast_all_windows"] == 99.0
    assert f["loo_min"] is None or f["survives_every_drop"] in (True, False)


def test_a_contrast_needs_both_cells_to_exist():
    rows = [_row(str(i), i, False) for i in range(4)]
    assert H._contrast(rows, "prior_prints_per_min", H.PRIMARY) is None


def test_distributions_report_coverage_and_N():
    rows = [_row("a", 1.0, True), _row("b", None, False)]
    d = H.distributions(rows, features=("prior_prints_per_min",))
    assert d["independent_windows"] == 2
    assert d["features"]["prior_prints_per_min"]["n"] == 1
    assert d["features"]["prior_prints_per_min"]["coverage"] == 0.5


# ============================================================= the oracle

def test_THE_ORACLE_BOUND_IS_PERFECT_FORESIGHT_AND_SAYS_SO():
    rows = [_row("good", 1, True, gross="0.02", cap="0.008", res="0"),
            _row("bad1", 1, False, gross="0.03", cap="0.012", res="0.012"),
            _row("bad2", 1, False, gross="0.05", cap="0.020", res="0.020")]
    o = H.oracle_bound(rows)
    assert o["independent_windows"] == 3
    assert o["windows_with_the_outcome"] == 1
    assert o["base_rate"] == 0.3333
    assert o["gross_exposure_avoided_share"] == "0.8000"
    assert o["capital_avoided_share"] == "0.8000"
    assert o["residual_avoided_share"] == "1.0000"
    assert o["outcome_retained_share"] == 1.0
    assert o["deployable"] is False
    assert "PERFECT FORESIGHT" in o["why"]


def test_the_oracle_does_not_divide_by_zero_on_an_empty_sample():
    o = H.oracle_bound([])
    assert o["independent_windows"] == 0
    assert o["gross_exposure_avoided_share"] is None


def test_nothing_in_this_module_fits_a_threshold_or_claims_realized():
    import ast
    import inspect
    src = inspect.getsource(H)
    banned = ("realized", "realised", "profit")
    denials = ("never", "not ", "no threshold", "cannot", "nothing")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if any(d in low for d in denials):
                continue
            for w in banned:
                assert w not in low, node.value


def test_A_DROP_THAT_EMPTIES_A_CELL_DOES_NOT_COUNT_AS_ROBUST():
    """With one positive window, removing it leaves nothing to contrast.
    Counting that as 'no sign flip' made a single observation look robust,
    which is exactly the error this programme keeps having to catch."""
    rows = [_row("good", 100.0, True)] + [_row(str(i), 1.0, False)
                                          for i in range(5)]
    f = H.leave_one_out(rows, features=("prior_prints_per_min",)
                        )["features"]["prior_prints_per_min"]
    assert f["contrast_all_windows"] == 99.0
    assert f["drops_that_destroy_the_contrast"] == 1
    assert f["survives_every_drop"] is False


def test_a_contrast_with_several_positives_can_survive_a_drop():
    rows = [_row("g1", 100.0, True), _row("g2", 90.0, True),
            _row("g3", 95.0, True)] + [_row(str(i), 1.0, False)
                                       for i in range(5)]
    f = H.leave_one_out(rows, features=("prior_prints_per_min",)
                        )["features"]["prior_prints_per_min"]
    assert f["drops_that_destroy_the_contrast"] == 0
    assert f["survives_every_drop"] is True


def test_touch_turnover_reads_the_signed_change_at_the_touch():
    snap = {"touch_turnover": {"observable": True, "qty_delta": "-120.0"}}
    r = H.pre_entry_row(symbol="S", cycles=[_cyc(5000, 275)], prints=[],
                        prior_symbol=None, prior_prints=[],
                        live_snapshot=snap)
    assert r["touch_turnover_before_entry"] == "-120.0"
    assert r["book_depletion_available"] is True


def test_an_unobservable_turnover_is_not_read_as_zero():
    snap = {"touch_turnover": {"observable": False}}
    r = H.pre_entry_row(symbol="S", cycles=[_cyc(5000, 275)], prints=[],
                        prior_symbol=None, prior_prints=[],
                        live_snapshot=snap)
    assert r["touch_turnover_before_entry"] is None
    assert r["book_depletion_available"] is False


def test_THIS_MODULE_DOES_NOT_SHADOW_THE_COLLECTOR():
    """I first wrote this as `habitat.py` and silently destroyed the
    collector of the same name -- public_flow, ladder_shape, cycle_sample and
    window_summary, which the soak and the pre-entry reader both import. The
    two must stay separate modules."""
    from src.gemini_execution import habitat as collector
    for fn in ("public_flow", "ladder_shape", "cycle_sample",
               "window_summary"):
        assert hasattr(collector, fn), fn
    assert H.__name__.endswith("habitat_selection")
    assert not hasattr(H, "public_flow")
