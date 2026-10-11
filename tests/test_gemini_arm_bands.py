"""The banded table and the six-step chain.

WHAT THESE PROTECT. The bands exist so the overlap interval (<=300) can be
checked for divergence: if the two arms behave differently where BOTH can
act, the "only timing changed" claim is false and the comparison is void.
The chain exists so "no complements" stops being a cause and becomes a
question with a located answer.
"""
from decimal import Decimal as D

from src.gemini_execution import arm_bands as AB
from src.gemini_execution import arm_compare as AC

ARM = "L5_ADAPTED_GEMINI_BTC15M_V2"


def _fill(leg, qty, price, kind, tte, first=False, queue=True):
    return {"leg": leg, "qty": qty, "price": price, "kind": kind,
            "tte_s": tte, "was_first_fill": first, "queue_known": queue}


def _cycle(n, tte, readable=True, buys=0, sells=0, cum=0, place=0, arm=None):
    c = {"cycle": n, "tte_s": tte,
         "habitat": {"book_readable": readable,
                     "flow": {"buy_prints": buys, "sell_prints": sells}},
         "hypothetical_rungs_rested_cumulative": cum,
         "decision": {"plan": {"place": [{"rung": {}}] * place}}}
    if arm is not None:
        c["correction_arms"] = {ARM: arm}
    return c


# ------------------------------------------------------------------ bands

def test_FILLS_LAND_IN_THE_BAND_OF_THEIR_OWN_TTE():
    w = {"symbol": "S", "hypothetical": {"rows": [
        _fill("YES", "0.01", "0.40", "OPEN", 800.0, first=True),
        _fill("NO", "0.01", "0.55", "COMPLETE", 450.0),
        _fill("YES", "0.01", "0.42", "OPEN", 200.0, first=False),
    ]}, "cycles_detail": []}
    r = AB.bands([w], AB.CONTROL)["bands"]
    assert r[">600"]["open_fills"] == 1 and r[">600"]["first_fills"] == 1
    assert r["300-600"]["complete_fills"] == 1
    assert r["<=300"]["open_fills"] == 1
    # the pair is attributed to the band of the COMPLETE, as in the L5 table
    assert r["300-600"]["matched_pairs"] == 1
    assert r[">600"]["matched_pairs"] == 0


def test_THE_PAIR_BASIS_IS_THE_PAIRS_NOT_ONE_LEGS():
    """0.40 held + 0.55 complement = 0.95, and it must be fee-positive."""
    w = {"symbol": "S", "hypothetical": {"rows": [
        _fill("YES", "0.01", "0.40", "OPEN", 800.0, first=True),
        _fill("NO", "0.01", "0.55", "COMPLETE", 700.0),
    ]}, "cycles_detail": []}
    b = AB.bands([w], AB.CONTROL)["bands"][">600"]
    assert b["actual_pair_basis_median"] == "0.95"
    assert b["fee_positive_pairs"] == 1
    assert b["matched_gross"] == "0.5000"      # 0.01 matched of 0.02 gross
    assert b["residual_generated"] == "0.01"
    assert b["residual_recycled"] == "0.01"


def test_AN_EXTEND_AVERAGES_INTO_THE_BASIS_RATHER_THAN_OPENING_A_SECOND_PAIR():
    w = {"symbol": "S", "hypothetical": {"rows": [
        _fill("YES", "0.01", "0.40", "OPEN", 800.0, first=True),
        _fill("YES", "0.01", "0.50", "EXTEND", 750.0),
        _fill("NO", "0.02", "0.49", "COMPLETE", 700.0),
    ]}, "cycles_detail": []}
    b = AB.bands([w], AB.CONTROL)["bands"][">600"]
    assert b["extend_fills"] == 1
    assert b["matched_pairs"] == 1
    # avg held 0.45 + 0.49 = 0.94
    assert b["actual_pair_basis_median"] == "0.94"


def test_TERMINAL_RESIDUAL_IS_REPORTED_NOT_NETTED_AWAY():
    w = {"symbol": "S", "hypothetical": {"rows": [
        _fill("YES", "0.01", "0.40", "OPEN", 800.0, first=True),
    ]}, "cycles_detail": []}
    r = AB.bands([w], AB.CONTROL)
    assert r["terminal_residual_by_window"][0]["terminal_residual"] == "0.01"
    assert r["bands"][">600"]["residual_generated"] == "0.01"
    assert r["bands"][">600"]["residual_recycled"] == "0"


def test_READABLE_CYCLES_AND_RUNGS_BAND_BY_THE_CYCLES_OWN_TTE():
    w = {"symbol": "S", "hypothetical": {"rows": []}, "cycles_detail": [
        _cycle(1, 880.0, readable=True, cum=2),
        _cycle(2, 700.0, readable=False, cum=5),
        _cycle(3, 250.0, readable=True, cum=9),
    ]}
    r = AB.bands([w], AB.CONTROL)["bands"]
    assert r[">600"]["readable_cycles"] == 1
    assert r[">600"]["unreadable_cycles"] == 1
    assert r[">600"]["rungs_rested"] == 5      # 2 then +3
    assert r["<=300"]["rungs_rested"] == 4     # +4
    assert r["<=300"]["readable_cycles"] == 1


def test_A_WINDOW_PREDATING_THE_ARM_IS_SKIPPED_NOT_SCORED_ZERO():
    w = {"symbol": "OLD", "hypothetical": {"rows": []}, "cycles_detail": []}
    assert AB.bands([w], ARM)["windows"] == 0
    assert AB.bands([w], AB.CONTROL)["windows"] == 1


# ------------------------------------------------------------------ chain

def test_THE_CHAIN_LOCATES_WHERE_RECYCLING_DIED():
    """An OPEN with flow that never came back breaks at opposite_flow."""
    w = {"symbol": "S",
         "hypothetical": {"rows": [
             _fill("YES", "0.01", "0.40", "OPEN", 800.0, first=True)]},
         "inventory": {"matched_qty": "0", "directional_residual": "0.01"},
         "cycles_detail": [
             _cycle(1, 800.0, cum=1, place=1),
             # holding YES, the complement is a NO rung reached by a BUY.
             # Only SELL flow arrived, so nothing could reach it.
             _cycle(2, 700.0, buys=0, sells=12, cum=2, place=1)]}
    r = AC.the_chain(w, AC.CONTROL)
    assert r["steps"]["OPEN_fill"]["happened"]
    assert r["steps"]["inventory_update"]["happened"]
    assert r["steps"]["subsequent_L5_action"]["happened"]
    assert r["steps"]["opposite_flow"]["happened"] is False
    assert r["broke_at"] == "opposite_flow"
    assert r["complete"] is False


def test_FLOW_ARRIVED_BUT_NEVER_REACHED_US_BREAKS_LATER_AND_SAYS_SO():
    """The distinction that matters: a queue problem, not a timing problem."""
    w = {"symbol": "S",
         "hypothetical": {"rows": [
             _fill("YES", "0.01", "0.40", "OPEN", 800.0, first=True)]},
         "inventory": {"matched_qty": "0", "directional_residual": "0.01"},
         "cycles_detail": [_cycle(1, 800.0, cum=1, place=1),
                           _cycle(2, 700.0, buys=30, cum=2, place=1)]}
    r = AC.the_chain(w, AC.CONTROL)
    assert r["steps"]["opposite_flow"]["happened"]
    assert r["steps"]["opposite_flow"][
        "aggressor_prints_that_could_reach_it"] == 30
    assert r["broke_at"] == "COMPLETE_fill"


def test_THE_WHOLE_CHAIN_HOLDING_IS_REPORTED_COMPLETE():
    w = {"symbol": "S",
         "hypothetical": {"rows": [
             _fill("YES", "0.01", "0.40", "OPEN", 800.0, first=True),
             _fill("NO", "0.01", "0.55", "COMPLETE", 700.0)]},
         "inventory": {"matched_qty": "0.01", "directional_residual": "0",
                       "effective_matched_pair_cost": "0.95"},
         "cycles_detail": [_cycle(1, 800.0, cum=1, place=1),
                           _cycle(2, 700.0, buys=30, cum=2, place=1)]}
    r = AC.the_chain(w, AC.CONTROL)
    assert r["broke_at"] is None and r["complete"] is True
    assert r["steps"]["matched_inventory"][
        "effective_matched_pair_cost"] == "0.95"


def test_THE_CENSUS_COUNTS_EACH_WINDOW_ONCE_AT_ITS_FIRST_BREAK():
    nothing = {"symbol": "A", "hypothetical": {"rows": []},
               "inventory": {"matched_qty": "0"}, "cycles_detail": []}
    stalled = {"symbol": "B",
               "hypothetical": {"rows": [
                   _fill("YES", "0.01", "0.40", "OPEN", 800.0, first=True)]},
               "inventory": {"matched_qty": "0"},
               "cycles_detail": [_cycle(1, 800.0, cum=1, place=1),
                                 _cycle(2, 700.0, buys=9, cum=2)]}
    c = AC.chain_census([nothing, stalled], AC.CONTROL)
    assert c["windows"] == 2
    assert c["broke_at"]["OPEN_fill"] == 1
    assert c["broke_at"]["COMPLETE_fill"] == 1
    assert c["broke_at"]["COMPLETE"] == 0
    assert sum(c["broke_at"].values()) == 2


def test_A_BARE_TAPE_CROSSING_IS_NEVER_A_FILL_HERE():
    """Every row these read comes from HypotheticalBook.consume, which walks
    prints against RESTING rungs. Flow alone moves nothing."""
    w = {"symbol": "S", "hypothetical": {"rows": []},
         "inventory": {"matched_qty": "0"},
         "cycles_detail": [_cycle(1, 800.0, buys=500, sells=500, cum=0)]}
    assert AB.bands([w], AB.CONTROL)["bands"][">600"]["fills"] == 0
    assert AC.the_chain(w, AC.CONTROL)["broke_at"] == "OPEN_fill"


# ------------------------------------------- the live per-window log output

def test_THE_LIVE_BANDED_PRINT_USES_THE_COMPACT_CYCLE_VIEW(capsys):
    """_print_bands must produce the banded line from a window record as
    one_window hands it over -- otherwise the log shows zeros for the very
    columns the report is for."""
    from src.gemini_execution import shadow_soak as SS
    w = {"symbol": "GEMI-BTC15M2609192130-UP",
         "hypothetical": {"rows": [
             _fill("NO", "0.01", "0.58", "OPEN", 810.0, first=True),
             _fill("YES", "0.01", "0.39", "COMPLETE", 640.0)]},
         "inventory": {"matched_qty": "0.01"},
         "cycles_detail": [_cycle(1, 810.0, cum=1), _cycle(2, 640.0, cum=4)]}
    SS._print_bands(w)
    out = capsys.readouterr().out
    assert "ARM_BAND" in out and "GEMI-BTC15M2609192130-UP" in out
    assert "tte=>600" in out
    assert "readC=2" in out and "rested=4" in out
    assert "first=1" in out and "COMP=1" in out
    assert "basis=0.97" in out          # 0.58 + 0.39


def test_A_REPORTING_ERROR_NEVER_ENDS_THE_COLLECTOR(capsys):
    from src.gemini_execution import shadow_soak as SS
    SS._print_bands({"symbol": "S", "hypothetical": "not-a-dict",
                     "cycles_detail": []})
    assert "ARM_BAND_ERROR" in capsys.readouterr().out


def test_THE_COMPACT_VIEW_IS_DROPPED_SO_EVIDENCE_DOES_NOT_DOUBLE():
    """It is attached after _persist and popped after printing; a lane that
    runs for ever must not accumulate every cycle on every window record."""
    import inspect
    src = inspect.getsource(__import__(
        "src.gemini_execution.shadow_soak", fromlist=["x"]).cohort_loop)
    assert 'w.pop("cycles_detail", None)' in src
    ow = inspect.getsource(__import__(
        "src.gemini_execution.shadow_soak", fromlist=["x"]).one_window)
    assert ow.index("runner._persist(w)") < ow.index('w["cycles_detail"]')


def test_THE_BAND_CLASSIFIER_USES_THE_PROBES_OWN_FILL_KINDS():
    """arm_bands compares fill kinds against string literals. If the probe
    ever renames them, every OPEN/EXTEND/COMPLETE column silently reads zero
    while the fill counts stay right -- a failure that looks like a finding.
    """
    import inspect
    from src.gemini_execution import continuous_probe as CP
    src = inspect.getsource(AB._walk_window)
    for const in (CP.OPEN, CP.EXTEND, CP.COMPLETE):
        assert '"%s"' % const in src, const
