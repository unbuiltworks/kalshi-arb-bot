"""The causal seam, the target, and the arithmetic of the completion labels.

P9 rests on one property: a feature may only see prints up to the decision and
a label may only see prints after it. If that seam leaks, every AUC in the
study is meaningless, so it is asserted directly rather than trusted.
"""
from fractions import Fraction as F

from src.gemini_execution import completion_labels as CL

YES, NO = CL.YES, CL.NO


def _c(prints, **kw):
    base = {"symbol": "GEMI-X", "duration": "5m", "day": "2026-09-16",
            "hour": 12, "start": "2026-09-16T12:00:00.000Z",
            "expiry": "2026-09-16T12:05:00.000Z", "prints": prints}
    base.update(kw)
    return base


T0 = 1789560000


def sell(dt, p):                       # a sell print makes a YES buyable at p
    return [T0 + dt, "sell", str(p), "1"]


def buy(dt, p):                        # a buy print makes a NO buyable at 1-p
    return [T0 + dt, "buy", str(p), "1"]


# ------------------------------------------------------------- the seam
def test_a_feature_cannot_see_a_later_print():
    early = _c([sell(0, "0.40"), buy(10, "0.60"), buy(20, "0.55")])
    late_changed = _c([sell(0, "0.40"), buy(10, "0.60"), buy(20, "0.01")])
    a = CL.features_at(early, early["prints"], 0)
    b = CL.features_at(late_changed, late_changed["prints"], 0)
    assert a == b


def test_a_label_cannot_see_the_print_it_is_labelling_or_an_earlier_one():
    """Only prints AFTER the candidate count. A complement that already
    traded before the decision is not a complement we can still buy."""
    prints = [buy(0, "0.60"), sell(10, "0.40")]      # NO first, then the YES
    lab = CL.labels_after(prints, 1, need=NO, price=F(40, 100))
    assert lab["comp_any"] is False and lab["n_complement"] == 0


def test_the_label_never_reads_the_outcome():
    won = _c([sell(0, "0.40"), buy(10, "0.60")], resolution="yes")
    lost = _c([sell(0, "0.40"), buy(10, "0.60")], resolution="no")
    assert CL.label_opens(won) == CL.label_opens(lost)


def test_no_label_key_is_derived_from_settlement():
    rows = CL.label_opens(_c([sell(0, "0.40"), buy(10, "0.60")],
                             resolution="yes", settlement="1"))
    for k in rows[0]:
        assert "resolution" not in k and "settle" not in k


# ------------------------------------------------------------- the target
def test_the_complement_of_a_yes_is_a_no_at_one_minus_the_price():
    rows = CL.label_opens(_c([sell(0, "0.40"), buy(10, "0.70")]))
    r = rows[0]
    assert r["offered"] == YES and r["price"] == F(40, 100) and r["need"] == NO
    # a buy print at 0.70 makes a NO buyable at 0.30, so the pair costs 0.70
    assert r["y_best_complement"] == F(30, 100)
    assert r["y_best_pair"] == F(70, 100)
    assert r["y_pair_le_1.00"] and r["y_pair_le_0.95"]


def test_a_complement_too_dear_to_pair_is_counted_but_does_not_qualify():
    rows = CL.label_opens(_c([sell(0, "0.40"), buy(10, "0.35")]))
    r = rows[0]
    assert r["y_comp_any"] is True                 # a NO was buyable at 0.65
    assert r["y_best_pair"] == F(105, 100)
    assert r["y_pair_le_1.00"] is False


def test_chances_are_counted_so_a_capture_assumption_can_be_applied_later():
    rows = CL.label_opens(_c([sell(0, "0.40"), buy(10, "0.70"),
                              buy(20, "0.75"), buy(30, "0.65")]))
    assert rows[0]["y_n_qualifying_1.00"] == 3


def test_latency_is_measured_to_the_first_qualifying_complement():
    rows = CL.label_opens(_c([sell(0, "0.40"), buy(10, "0.35"),
                              buy(40, "0.70")]))
    r = rows[0]
    assert r["y_latency_any_s"] == 10.0            # first complement of any price
    assert r["y_latency_1.00_s"] == 40.0           # first one that would pair


# ------------------------------------------------------------- the clock
def test_times_are_seconds_not_milliseconds():
    rows = CL.label_opens(_c([sell(60, "0.40"), buy(120, "0.70")]))
    r = rows[0]
    assert r["tte_s"] == 240.0                     # expiry is 300s after start
    assert r["y_latency_any_s"] == 60.0


def test_two_sidedness_so_far_is_history_only():
    prints = [sell(0, "0.40"), sell(5, "0.41"), buy(9, "0.70"),
              sell(12, "0.42")]
    f = CL.features_at(_c(prints), prints, 3)
    assert f["n_same"] == 3 and f["n_opposite"] == 1
    assert f["since_opposite_s"] == 3.0 and f["ever_opposite"] is True


def test_a_market_with_no_opposite_print_yet_says_so():
    prints = [sell(0, "0.40"), sell(5, "0.41")]
    f = CL.features_at(_c(prints), prints, 1)
    assert f["ever_opposite"] is False and f["since_opposite_s"] is None


def test_the_module_is_offline():
    import inspect
    imports = {ln.strip() for ln in inspect.getsource(CL).splitlines()
               if ln.startswith(("import ", "from "))}
    assert imports == {
        "from __future__ import annotations",
        "import datetime as _dt",
        "from fractions import Fraction as F",
        "from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple",
    }, imports
    src = inspect.getsource(CL)
    for clock in (".now(", ".utcnow(", ".today(", "time.time("):
        assert clock not in src, clock
