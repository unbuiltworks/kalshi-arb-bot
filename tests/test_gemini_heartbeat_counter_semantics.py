"""The heartbeat's counters run on three different clocks. Pin which is which.

THE CONFUSION THIS EXISTS TO END. A live heartbeat read

    "first_fills": 0, "COMPLETE_fills": 0, "matched_pairs": 1

which invites the reading "a pair matched without a first fill" -- i.e. a
counter bug or carry-in inventory. It is neither. `first_fills` is a
CURRENT-WINDOW gauge reset by every rollover; `matched_pairs` is a CUMULATIVE
count of WINDOWS that ended matched. They are not a ratio and never were.
"""
from src.gemini_execution import shadow_soak as SS


def _win(symbol="S", first=0, complete=0, matched="0", residual="0"):
    return {"symbol": symbol, "state": "COMPLETED", "finished_utc": "t",
            "hypothetical": {"first_fills": first, "complete_fills": complete,
                             "rungs_rested": 0},
            "inventory": {"matched_qty": matched,
                          "directional_residual": residual},
            "correction_arms": None}


class _Hyp:
    def __init__(self, first=0, complete=0):
        self.live, self.placed, self.requoted = [], 0, 0
        self._f, self._c = first, complete

    def report(self):
        return {"first_fills": self._f, "complete_fills": self._c,
                "rungs_rested": 0}


class _Inv:
    def report(self):
        return {"directional_residual": "0", "matched_qty": "0"}


def test_the_reported_pairing_is_explained_not_a_bug():
    """Reproduce the exact live shape and assert what it means."""
    hb = SS.CohortHeartbeat("BTC5m")
    # window 1 matches
    hb.window_done(_win("W1", first=1, complete=1, matched="0.01"))
    # window 2 begins: the fresh HypotheticalBook reports zero so far
    hb.cycle(symbol="W2", cycle=1, tte_s=200, inv=_Inv(), hyp=_Hyp(0, 0),
             book_readable=True, book_why=None)
    d = _emit(hb)
    assert d["first_fills"] == 0          # the live shape
    assert d["matched_pairs"] == 1        # ... reproduced exactly
    # and now it is unambiguous
    assert d["first_fills_current_window"] == 0
    assert d["first_fills_cumulative"] == 1
    assert d["windows_ending_matched"] == 1


def _emit(hb):
    """The heartbeat payload, captured from the line `emit` prints."""
    import contextlib
    import io
    import json
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hb.emit()
    line = buf.getvalue().strip().splitlines()[-1]
    return json.loads(line.split("L5_COHORT_HB ", 1)[1])


def test_current_window_gauges_reset_on_rollover():
    hb = SS.CohortHeartbeat("BTC5m")
    hb.cycle(symbol="W1", cycle=1, tte_s=200, inv=_Inv(), hyp=_Hyp(2, 1),
             book_readable=True, book_why=None)
    assert _emit(hb)["first_fills"] == 2
    hb.window_done(_win("W1", first=2, complete=1, matched="0.01"))
    # next window's fresh book reports nothing yet
    hb.cycle(symbol="W2", cycle=1, tte_s=200, inv=_Inv(), hyp=_Hyp(0, 0),
             book_readable=True, book_why=None)
    d = _emit(hb)
    assert d["first_fills"] == 0                 # gauge reset
    assert d["first_fills_cumulative"] == 2      # total preserved


def test_matched_pairs_counts_windows_not_pairs():
    """A window holding several matched pairs increments by ONE. The name is
    misleading, so the semantics are stated rather than inferred."""
    hb = SS.CohortHeartbeat("BTC15m")
    for i in range(3):
        hb.window_done(_win("W%d" % i, first=5, complete=5, matched="0.05"))
    d = _emit(hb)
    assert d["matched_pairs"] == 3
    assert d["windows_ending_matched"] == 3
    assert "NOT a number of pairs" in d["counter_semantics"]["matched_pairs"]


def test_an_unmatched_window_does_not_increment():
    hb = SS.CohortHeartbeat("BTC5m")
    hb.window_done(_win("W1", first=1, complete=0, matched="0"))
    d = _emit(hb)
    assert d["matched_pairs"] == 0
    assert d["windows_ending_matched"] == 0
    assert d["first_fills_cumulative"] == 1     # a first fill with no match


def test_the_semantics_travel_on_every_heartbeat():
    hb = SS.CohortHeartbeat("BTC5m")
    cs = _emit(hb)["counter_semantics"]
    assert "CURRENT WINDOW ONLY" in cs["first_fills"]
    assert "CUMULATIVE" in cs["matched_pairs"]
    assert "DIFFERENT CLOCKS" in cs["why_this_matters"]


def test_the_original_fields_are_unchanged_for_cross_service_comparison():
    """The control service runs an older build. If these three fields changed
    meaning, its rows and ours would stop being comparable."""
    hb = SS.CohortHeartbeat("BTC5m")
    hb.window_done(_win("W1", first=1, complete=1, matched="0.01"))
    d = _emit(hb)
    for k in ("first_fills", "COMPLETE_fills", "matched_pairs", "residual"):
        assert k in d
