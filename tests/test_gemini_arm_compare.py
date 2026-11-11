"""The arm comparison counts RECYCLING, and cannot be satisfied by first fills."""
from src.gemini_execution import arm_compare as AC

ARM = "L5_ADAPTED_GEMINI_BTC15M_V2"


def _w(symbol, *, ctl, arm=None):
    """One window record: (first, complete, matched, basis) per arm."""
    def side(t):
        f, c, m, b = t
        return ({"first_fills": f, "complete_fills": c, "rungs_rested": 10,
                 "fills": f + c, "open_fills": f, "extend_fills": 0},
                {"matched_qty": m, "effective_matched_pair_cost": b,
                 "directional_residual": "0.01"})
    h, i = side(ctl)
    w = {"kind": "window", "symbol": symbol, "hypothetical": h,
         "inventory": i}
    if arm is not None:
        ah, ai = side(arm)
        w["correction_arms"] = {ARM: {"hypothetical": ah, "inventory": ai,
                                      "quote_between_tte_s": [0, 900],
                                      "corrects": "L5_ADAPTED_GEMINI_CRYPTO"}}
    return w


def test_A_FIRST_FILL_WITHOUT_A_COMPLETE_IS_NOT_RECYCLING():
    """The failure mode the metric exists to refuse: a band that opens and
    never completes manufactures residual and must not score."""
    ws = [_w("A", ctl=(1, 0, "0", None), arm=(3, 0, "0", None))]
    r = AC.compare(ws)["arms"]
    assert r[ARM]["first_fills"] == 3
    assert r[ARM]["recycled_windows"] == 0
    assert r[ARM]["residual_only_windows"] == 1
    assert AC.the_sequence(ws, ARM) == []


def test_THE_FULL_SEQUENCE_SCORES_AND_IS_SURFACED():
    ws = [_w("A", ctl=(1, 0, "0", None), arm=(1, 1, "0.01", "0.96"))]
    r = AC.compare(ws)["arms"]
    assert r[ARM]["recycled_windows"] == 1
    assert r[AC.CONTROL]["recycled_windows"] == 0
    assert r[ARM]["median_pair_basis"] == "0.96"
    assert r[ARM]["par_constrained_windows"] == 1
    hit = AC.the_sequence(ws, ARM)[0]
    assert hit["symbol"] == "A" and hit["matched_qty"] == "0.01"
    assert hit["quote_between_tte_s"] == [0, 900]


def test_ARMS_ARE_NEVER_SUMMED_AND_THE_CONTROL_KEEPS_ITS_OWN_TOTALS():
    ws = [_w("A", ctl=(1, 1, "0.01", "0.99"), arm=(2, 2, "0.02", "0.95"))]
    r = AC.compare(ws)["arms"]
    assert r[AC.CONTROL]["first_fills"] == 1
    assert r[ARM]["first_fills"] == 2
    assert r[AC.CONTROL]["matched_qty"] == "0.01"
    assert r[ARM]["matched_qty"] == "0.02"


def test_WINDOWS_PREDATING_THE_ARM_DO_NOT_COUNT_AGAINST_IT():
    """The lane has history from before the arm existed. Counting those as
    windows in which the arm failed to recycle would be inventing evidence."""
    ws = [_w("OLD", ctl=(1, 0, "0", None)),
          _w("NEW", ctl=(1, 0, "0", None), arm=(1, 1, "0.01", "0.94"))]
    r = AC.compare(ws)["arms"]
    assert r[AC.CONTROL]["windows"] == 2
    assert r[ARM]["windows"] == 1
    assert r[ARM]["recycle_rate_of_first_fill_windows"] == 1.0


def test_A_DEGENERATE_BASIS_IS_NOT_AVERAGED_IN():
    """The defect that produced a median pair basis of -12.7 once already."""
    ws = [_w("A", ctl=(1, 1, "0.01", "-12.7"), arm=(1, 1, "0.01", "0.95"))]
    r = AC.compare(ws)["arms"]
    assert r[AC.CONTROL]["median_pair_basis"] is None
    assert r[ARM]["median_pair_basis"] == "0.95"


def test_NOTHING_HERE_IS_CALLED_REALIZED():
    r = AC.compare([_w("A", ctl=(1, 1, "0.01", "0.95"))])
    joined = (r["labels"] + r["metric"]).lower()
    assert "hypothetical" in joined and "upper bound" in joined
    for forbidden in ("realized", "realised", "profit"):
        assert forbidden not in joined.replace("settled or realized", "")
