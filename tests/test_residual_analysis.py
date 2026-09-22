"""Matched economics vs directional residual, and the identity that binds them.

THE ONE PROPERTY THAT MATTERS: matched_pnl + residual_pnl - fees must re-add
to net, exactly, on every window. A decomposition that does not re-add to the
number it decomposes is not a decomposition -- it is a second opinion, and
this programme has already shipped one accounting line that looked reasonable
and measured a different quantity.

THE ARITHMETIC IS RATIONAL FOR A REASON. Splitting a leg's spend across its
matched and residual units needs that leg's average price, and an average is
a repeating decimal the moment quantity is not a power of ten. EP016's first
window averaged 1.15 over 3 YES; in Decimal that multiplies back to
1.1499...99 and the identity missed by 1e-28. Harmless in dollars, fatal to
the claim that it is exact.
"""
import glob
import json
from decimal import Decimal as D
from fractions import Fraction as F

import pytest

from src.gemini_execution import residual_analysis as RA


def _dec(**kw):
    kw.setdefault("resolution_side", "yes")
    return RA.decompose(**kw)


# ============ the identity

def test_the_decomposition_re_adds_to_net_exactly():
    r = _dec(yes_qty=3, no_qty=4, yes_spend="1.15", no_spend="1.37",
             resolution_side="yes")
    assert r["identity"]["holds"] is True
    assert r["identity"]["delta"] == "0"
    assert r["matched_pnl_usd"] == "0.8225"
    assert r["residual_pnl_usd"] == "-0.3425"
    assert r["net_usd"] == "0.48"


def test_the_repeating_average_that_broke_decimal_is_exact_in_rationals():
    """1.15 over 3 YES. The regression this module was rewritten for."""
    r = _dec(yes_qty=3, no_qty=4, yes_spend="1.15", no_spend="1.37",
             resolution_side="yes")
    assert r["avg_yes_cost"].startswith("0.3833")
    assert r["identity"]["holds"], "the average is repeating; the split is not"
    # and the same window via averages ALONE cannot be exact -- which is why
    # exact per-leg spend is preferred and the basis is reported
    assert r["cost_basis"] == "exact per-leg spend from the fills"
    viaavg = _dec(yes_qty=3, no_qty=4, avg_yes=r["avg_yes_cost"],
                  avg_no="0.3425", resolution_side="yes")
    assert viaavg["cost_basis"] == "reconstructed as average x quantity"


@pytest.mark.parametrize("y,n,side", [
    (4, 0, "yes"), (4, 0, "no"), (0, 4, "yes"), (0, 4, "no"),
    (2, 2, "yes"), (2, 2, "no"), (4, 2, "no"), (1, 3, "yes"), (0, 0, "yes"),
])
def test_the_identity_holds_on_every_shape_CAP4_permits(y, n, side):
    r = RA.decompose(yes_qty=y, no_qty=n, avg_yes="0.45", avg_no="0.50",
                     resolution_side=side)
    assert r["identity"]["holds"], r["identity"]


# ============ matched is locked, residual is the bet

def test_matched_pnl_does_not_depend_on_the_outcome():
    """The whole reason the split exists: a matched pair pays $1 either way."""
    a = RA.decompose(yes_qty=3, no_qty=3, avg_yes="0.45", avg_no="0.50",
                     resolution_side="yes")
    b = RA.decompose(yes_qty=3, no_qty=3, avg_yes="0.45", avg_no="0.50",
                     resolution_side="no")
    assert a["matched_pnl_usd"] == b["matched_pnl_usd"] == "0.15"
    assert a["residual_qty"] == b["residual_qty"] == 0
    assert a["matched_is_outcome_independent"] is True


def test_a_balanced_window_has_no_directional_bet_at_all():
    r = RA.decompose(yes_qty=2, no_qty=2, avg_yes="0.48", avg_no="0.48",
                     resolution_side="no")
    assert r["residual_qty"] == 0 and r["residual_side"] is None
    assert r["residual_pnl_usd"] == "0"
    assert r["net_usd"] == r["matched_pnl_usd"] == "0.08"  # 2 x (1 - 0.96)


def test_a_one_sided_window_is_entirely_a_directional_bet():
    win = RA.decompose(yes_qty=4, no_qty=0, avg_yes="0.45",
                       resolution_side="yes")
    lose = RA.decompose(yes_qty=4, no_qty=0, avg_yes="0.45",
                        resolution_side="no")
    assert win["matched_pnl_usd"] == lose["matched_pnl_usd"] == "0"
    assert win["residual_pnl_usd"] == "2.2"      # 4 - 1.80
    assert lose["residual_pnl_usd"] == "-1.8"    # the whole stake
    assert win["residual_won"] is True and lose["residual_won"] is False


# ============ unresolved is not guessed

def test_an_unresolved_window_still_prices_its_matched_leg():
    r = RA.decompose(yes_qty=3, no_qty=2, avg_yes="0.40", avg_no="0.45",
                     resolution_side=None)
    assert r["resolved"] is False
    assert r["matched_pnl_usd"] == "0.3", "matched needs no outcome"
    assert r["residual_pnl_usd"] is None, "the bet's result is not known"
    assert r["net_usd"] is None
    assert r["identity"]["checkable"] is False


# ============ disagreement is reported, never absorbed

def test_a_spend_that_disagrees_with_the_controller_is_flagged():
    r = RA.decompose(yes_qty=2, no_qty=2, avg_yes="0.50", avg_no="0.50",
                     resolution_side="yes", spend_usd="9.99")
    assert r["spend_usd"] == "2", "our own split stays authoritative"
    assert r["reported_spend_usd"] == "9.99"
    assert r["spend_agrees"] is False


# ============ against the real EP016 artifacts

def _ep016():
    res = {'GEMI-BTC15M2609220230-UP': 'yes', 'GEMI-BTC15M2609220245-UP': 'yes',
           'GEMI-BTC15M2609220300-UP': 'no', 'GEMI-BTC15M2609220315-UP': 'no'}
    rows = []
    for f in sorted(glob.glob("analysis/gemini/cohort_window_*.json")):
        rep = json.loads(open(f).read())
        sym = (rep.get("window") or {}).get("symbol")
        # COHORT-2's windows share this glob; EP016 is the four it names.
        if sym not in res:
            continue
        rows.append(RA.from_window(
            rep, {"status": "settled", "resolutionSide": res[sym]}))
    return rows


def test_EP016_decomposes_exactly_and_re_adds_to_the_established_total():
    rows = _ep016()
    assert len(rows) == 4
    assert all(r["identity"]["holds"] for r in rows)
    assert all(r["spend_agrees"] for r in rows), (
        "our per-leg split must agree with the controller's own total spend")
    assert all(r["agrees_with_controller"] for r in rows), (
        "our matched_qty must agree with the controller's")
    b = RA.bucket(rows)
    assert D(b["totals"]["net_usd"]) == D("1.14")
    # THE FINDING, pinned: the positive total is MATCHED economics. The
    # directional residual lost money over the same four windows.
    assert D(b["totals"]["matched_pnl_usd"]) == D("1.2775")
    assert D(b["totals"]["residual_pnl_usd"]) == D("-0.1375")


def test_EP016_buckets_by_terminal_residual_size():
    b = RA.bucket(_ep016())
    assert set(b["buckets"]) == {"1", "2"}
    assert b["buckets"]["1"]["n"] == 2 and b["buckets"]["2"]["n"] == 2
    assert b["buckets"]["1"]["residual_win_rate"] == "0"
    assert b["buckets"]["2"]["residual_win_rate"] == "0.5"


def test_the_bucket_keys_are_exactly_what_CAP4_permits():
    rows = [RA.decompose(yes_qty=k, no_qty=0, avg_yes="0.5",
                         resolution_side="no") for k in range(5)]
    rows.append(RA.decompose(yes_qty=7, no_qty=0, avg_yes="0.5",
                             resolution_side="no"))
    b = RA.bucket(rows)
    assert set(b["buckets"]) == {"0", "1", "2", "3", "4", "over_cap"}
    assert b["buckets"]["over_cap"]["n"] == 1, (
        "a residual past the cap must be visible, not silently bucketed at 4")


# ============ exact per-leg spend comes from the venue's own fills

def test_leg_spend_is_summed_from_the_fills_not_from_an_average():
    evs = [{"X": "FILLED", "O": "YES", "L": "0.45", "Z": "1"},
           {"X": "FILLED", "O": "YES", "L": "0.37", "Z": "1"},
           {"X": "FILLED", "O": "NO", "L": "0.40", "Z": "1"},
           {"X": "NEW", "O": "NO", "L": "0.99", "Z": "1"}]
    got = RA.leg_spend(evs)
    assert got["readable"] and got["n_fills"] == 3
    assert got["yes_spend"] == F("0.82") and got["no_spend"] == F("0.40")
    assert got["n_yes_fills"] == 2 and got["n_no_fills"] == 1


def test_no_fills_means_unreadable_and_falls_back_rather_than_claiming_zero():
    got = RA.leg_spend([])
    assert got["readable"] is False
    assert got["yes_spend"] == F(0) and got["no_spend"] == F(0)


# ============ nothing here is realized, and nothing here can mutate

def test_nothing_is_labelled_realized():
    r = RA.decompose(yes_qty=2, no_qty=1, avg_yes="0.4", avg_no="0.4",
                     resolution_side="yes")
    assert "REALIZED" in r["labels"] and "IMPLIED" in r["labels"]
    assert "realized" not in {k.lower() for k in r}


def test_the_module_is_offline_and_read_only():
    import ast
    src = ast.parse(open("src/gemini_execution/residual_analysis.py").read())
    imported = {a.name.split(".")[0]
                for n in ast.walk(src) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {(n.module or "").split(".")[0]
                 for n in ast.walk(src) if isinstance(n, ast.ImportFrom)}
    assert imported <= {"decimal", "fractions", "typing", "__future__"}, imported


# ============ the identity must be checked on VALUES, not on their display

def test_a_repeating_pair_cost_still_re_adds_exactly():
    """COHORT-2's y3/n2 window. Its pair cost is 0.81833... and the first
    implementation called check_identity on the dict it had just FORMATTED,
    so the value was quantized to six places and then re-parsed as if it were
    the value. The check was measuring the rounding it had itself introduced,
    and reported the split as broken when the split was fine."""
    r = RA.decompose(yes_qty=3, no_qty=2, yes_spend="0.49", no_spend="1.31",
                     resolution_side="no")
    assert r["matched_pair_unit_cost"].startswith("0.81833"), (
        "COHORT-2's GEMI-BTC15M2609221330-UP: 0.49 over 3 YES repeats")
    assert r["identity"]["holds"] is True
    assert r["identity"]["exact"] is True
    assert r["identity"]["delta"] == "0"


def test_the_string_entry_point_says_it_is_not_exact():
    """`check_identity` is still public and still useful, but it reads
    display strings, so it must not claim the exactness decompose() has."""
    r = RA.decompose(yes_qty=3, no_qty=2, yes_spend="0.49", no_spend="1.31",
                     resolution_side="no")
    viastr = RA.check_identity(r)
    assert viastr["exact"] is False
    assert "DISPLAY strings" in viastr["note"]


def test_every_COHORT2_window_re_adds_exactly():
    """All eleven, against their published resolutions."""
    res = {"GEMI-BTC15M2609221200-UP": "no", "GEMI-BTC15M2609221215-UP": "no",
           "GEMI-BTC15M2609221230-UP": "yes", "GEMI-BTC15M2609221245-UP": "no",
           "GEMI-BTC15M2609221300-UP": "no", "GEMI-BTC15M2609221315-UP": "yes",
           "GEMI-BTC15M2609221330-UP": "no", "GEMI-BTC15M2609221345-UP": "yes",
           "GEMI-BTC15M2609221400-UP": "yes", "GEMI-BTC15M2609221415-UP": "yes",
           "GEMI-BTC15M2609221430-UP": "no"}
    rows = []
    for f in sorted(glob.glob("analysis/gemini/cohort_window_*.json")):
        rep = json.loads(open(f).read())
        sym = (rep.get("window") or {}).get("symbol")
        if sym not in res:
            continue
        rows.append(RA.from_window(
            rep, {"status": "settled", "resolutionSide": res[sym]}))
    if not rows:
        pytest.skip("COHORT-2 window artifacts are not on this branch")
    assert len(rows) == 11
    assert all(r["identity"]["holds"] and r["identity"]["exact"] for r in rows)


# ============ the recorded event list is a capped SAMPLE, not a fill ledger

def test_an_incomplete_fill_record_is_detected_and_not_silently_trusted():
    """COHORT-2 recorded 54 fills against an inventory counting 56.
    `_drain_events` takes at most _MAX_EVENTS_PER_CYCLE frames per cycle, so a
    busy cycle drops orderUpdate rows from the artifact while the controller's
    own inventory still counts them. Summing the recorded rows understated
    spend by $0.34 -- and silently narrowed the set of fills whose maker flag
    anyone could check."""
    rep = {"window": {"symbol": "S", "inventory": {
        "yes_qty": "1", "no_qty": "4", "avg_yes_cost": "0.50",
        "avg_no_cost": "0.29", "total_spend_usd": "1.66", "n_fills": 5},
        "order_events": [
            {"X": "FILLED", "O": "YES", "L": "0.50", "Z": "1", "m": True},
            {"X": "FILLED", "O": "NO", "L": "0.43", "Z": "1", "m": True},
            {"X": "FILLED", "O": "NO", "L": "0.31", "Z": "1", "m": True},
            {"X": "FILLED", "O": "NO", "L": "0.24", "Z": "1", "m": True}]}}
    r = RA.from_window(rep, {"status": "settled", "resolutionSide": "yes"})
    fr = r["fill_record"]
    assert fr["complete"] is False
    assert fr["controller_n_fills"] == 5 and fr["recorded_n_fills"] == 4
    assert fr["unverifiable_fills"] == 1
    assert "CAPPED sample" in fr["why"] and "never maker" in fr["why"]
    # the cost basis falls back, so spend reconciles to the controller's total
    assert r["cost_basis"] == "reconstructed as average x quantity"
    assert r["spend_usd"] == "1.66" and r["spend_agrees"] is True


def test_a_complete_fill_record_still_uses_the_exact_fill_sum():
    rep = {"window": {"symbol": "S", "inventory": {
        "yes_qty": "2", "no_qty": "1", "avg_yes_cost": "0.45",
        "avg_no_cost": "0.40", "total_spend_usd": "1.30", "n_fills": 3},
        "order_events": [
            {"X": "FILLED", "O": "YES", "L": "0.50", "Z": "1", "m": True},
            {"X": "FILLED", "O": "YES", "L": "0.40", "Z": "1", "m": True},
            {"X": "FILLED", "O": "NO", "L": "0.40", "Z": "1", "m": True}]}}
    r = RA.from_window(rep, {"status": "settled", "resolutionSide": "no"})
    assert r["fill_record"]["complete"] is True
    assert r["cost_basis"] == "exact per-leg spend from the fills"
    assert r["spend_usd"] == "1.3" and r["spend_agrees"] is True
    assert r["identity"]["holds"] and r["identity"]["exact"]
