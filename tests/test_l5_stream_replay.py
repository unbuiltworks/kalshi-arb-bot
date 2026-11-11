"""L5's venue-confirmed fill stream through the production controller.

The synthetic cases need no data. The last group reads the committed stream
and result files and nothing else.
"""
import ast
import json
import os

import pytest

from analysis.l5 import l5_stream_replay as SR
from src import l5_portfolio_controller as PC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAL = PC.Params(**PC.BALANCE).checked()


def window(winner, *fills):
    return {"winner": winner, "f": [list(f) for f in fills]}


def test_as_traded_is_settlement_less_what_was_paid():
    w = window("Up", (10, "Up", 0.40, 30), (20, "Down", 0.55, 30),
               (30, "Up", 0.20, 10))
    r = SR.play(w, SR.as_traded)
    assert r["contracts"] == 70 and r["matched"] == 30
    assert r["net"] == pytest.approx(40 - (12 + 16.5 + 2))
    assert r["residual"] == 10 and r["residual_won"] is True
    assert r["pair_cost"] == pytest.approx(14 / 40 + 0.55)


def test_the_book_puts_his_price_at_that_sides_best_bid():
    assert SR.book_for(PC.UP, 0.41) == {"bid": 0.41, "ask": 0.42}
    b = SR.book_for(PC.DOWN, 0.41)
    assert round(1 - b["ask"], 2) == 0.41 and b["bid"] < b["ask"]


def test_balance_takes_the_first_fill_and_then_only_the_other_side():
    w = window("Down", (10, "Up", 0.50, 30), (20, "Up", 0.45, 30),
               (30, "Up", 0.40, 30), (40, "Down", 0.62, 30),
               (50, "Up", 0.36, 30))
    r = SR.play(w, SR.controller(BAL))
    # UP, then two more UP offers refused, DOWN taken, then UP again
    assert r["fills"] == 3 and r["peak_residual"] == 30
    assert r["matched"] == 30


def test_balance_refuses_new_exposure_under_thirty_cents_but_not_a_hedge():
    cheap_add = window("Up", (10, "Up", 0.20, 30))
    assert SR.play(cheap_add, SR.controller(BAL))["fills"] == 0
    cheap_hedge = window("Up", (10, "Down", 0.80, 30), (20, "Up", 0.12, 30))
    r = SR.play(cheap_hedge, SR.controller(BAL))
    assert r["fills"] == 2 and r["residual"] == 0


def test_balance_hedges_late_and_adds_only_early():
    late_add = window("Up", (500, "Up", 0.50, 30))
    assert SR.play(late_add, SR.controller(BAL))["fills"] == 0
    late_hedge = window("Up", (100, "Up", 0.50, 30), (800, "Down", 0.60, 30))
    assert SR.play(late_hedge, SR.controller(BAL))["fills"] == 2
    too_late = window("Up", (100, "Up", 0.50, 30), (860, "Down", 0.60, 30))
    assert SR.play(too_late, SR.controller(BAL))["fills"] == 1


def test_a_hedge_is_taken_whatever_the_pair_costs():
    w = window("Down", (10, "Up", 0.60, 30), (60, "Down", 0.70, 30))
    r = SR.play(w, SR.controller(BAL))
    assert r["fills"] == 2 and r["pair_cost"] == pytest.approx(1.30)
    capped = SR.play(w, SR.controller(PC.with_overrides(BAL,
                                                        pair_ceiling=1.0)))
    assert capped["fills"] == 1


def test_the_current_profile_takes_nothing_on_a_book_belief():
    w = window("Up", (10, "Up", 0.50, 30), (20, "Down", 0.48, 30))
    r = SR.play(w, SR.controller(PC.Params(**PC.PRODUCTION).checked()))
    assert r["fills"] == 0 and r["net"] == 0


def test_the_money_is_matched_plus_residual():
    w = window("Down", (10, "Up", 0.50, 30), (20, "Up", 0.40, 30),
               (30, "Down", 0.62, 30))
    r = SR.play(w, SR.as_traded)
    s = SR.summarise([r])
    assert s["matched_net_total"] + s["residual_net_total"] == \
        pytest.approx(s["net_total"])


def test_the_module_reads_one_file_and_trades_nothing():
    tree = ast.parse(open(SR.__file__).read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= {"__future__", "json", "os", "random", "statistics",
                    "typing", "src"}
    for base, _d, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if f.endswith(".py"):
                assert "l5_stream_replay" not in open(
                    os.path.join(base, f), errors="ignore").read(), f


# ------------------------------------------------- the committed stream
def test_the_committed_stream_is_what_the_venue_confirmed():
    wins = SR.load()
    assert len(wins) == 118
    assert sum(len(w["f"]) for w in wins) == 6931
    assert wins == sorted(wins, key=lambda w: w["ws"])
    spent = sum(px * z for w in wins for _t, _o, px, z in w["f"])
    redeemed = sum(w["redeemed_usdc"] for w in wins)
    assert spent == pytest.approx(56027.65, abs=1.0)
    assert redeemed == pytest.approx(56855.36, abs=0.01)
    # the window in the owner's screenshot, 19:30-19:45Z on 2026-11-10,
    # settled after this file was cut and is not in it
    assert all(w["ws"] < 1794339000 for w in wins)


def test_the_committed_result_and_what_it_claims():
    """If a rerun changes this, change the test by hand, with the
    evidence."""
    with open(os.path.join(ROOT, "analysis", "l5",
                           "l5_stream_replay.json")) as fh:
        r = json.load(fh)
    assert "assumes we could have had his fills" in r["evidence_class"]
    rows = r["rows"]
    l5, bal = rows["L5 as traded"]["ALL"], \
        rows["controller, BALANCE profile"]["ALL"]
    # as traded: positive on average, not distinguishable from zero
    assert l5["net_per_contract"] > 0 > l5["net_per_contract_ci95"][0]
    # balanced: lower bound above zero over all 118 windows; positive in
    # each chronological half, but NOT separately distinguishable from
    # zero in either -- 70 and 48 windows are too few
    assert bal["net_per_contract_ci95"][0] > 0
    assert bal["net_per_window_ci95"][0] > 0
    for h in ("IS", "OOS"):
        s = rows["controller, BALANCE profile"][h]
        assert s["net_per_contract"] > 0, h
        assert s["net_per_contract_ci95"][0] < 0, h
    # the residual loss and the worst window are a fraction of his
    assert abs(bal["residual_net_total"]) < 0.1 * abs(
        l5["residual_net_total"])
    assert bal["worst_window"] > 0.3 * l5["worst_window"]
    assert bal["peak_residual_max"] <= 40          # one of his clips
    # and it earns LESS per window, on about a third of the volume
    assert bal["net_per_window"] < l5["net_per_window"]
    assert bal["contracts_per_window"] < 0.4 * l5["contracts_per_window"]
    # a pair ceiling on hedges does not help
    for c in ("1.00", "0.96"):
        capd = rows["BALANCE with a %s pair ceiling on hedges" % c]["ALL"]
        assert capd["net_per_window"] < bal["net_per_window"]
    # the profile in production today would have taken none of his fills
    assert rows["controller, MODEL_EDGE profile (current)"]["ALL"][
        "contracts_per_window"] == 0
