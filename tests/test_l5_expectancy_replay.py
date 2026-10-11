"""The decomposition must close to the cent, and the policies must be blind."""
import json
import datetime as dt
from decimal import Decimal as D

import pytest

from src.l5_adapted_v1 import OutcomeLeak
from src.l5_expectancy_replay import (Fill, InventoryWalk, Native, ResidualCap,
                                      ResidualAge, ComplementRepair,
                                      PairCostCap, RandomSuppress, replay,
                                      pooled_split, drawdown, UP, DOWN)

ORDERS = "analysis/pmus/evidence/probe07_orders.json"
TARGET = D("-0.771800")
BALANCE_DELTA = D("-0.77180")


def probe07_fills():
    doc = json.load(open(ORDERS))
    out = []
    for o in doc["orders"]:
        wire = D(o["avgPx"])
        q = D(str(o["cumQuantity"]))
        side, px = ((DOWN, D(1) - wire)
                    if o["intent"] == "ORDER_INTENT_BUY_SHORT" else (UP, wire))
        t = dt.datetime.fromisoformat(
            o["lastTransactTime"].replace("Z", "+00:00")).timestamp()
        f = Fill(t, side, px, q, rebate=-D(o["commission"]), ref=o["id"])
        f.insert_t = dt.datetime.fromisoformat(
            o["insertTime"].replace("Z", "+00:00")).timestamp()
        out.append(f)
    return sorted(out, key=lambda f: (f.t, f.insert_t))


# ------------------------------------------------- the headline arithmetic

def test_probe07_decomposition_reconciles_exactly():
    r = replay(probe07_fills(), Native(), winner=UP)
    assert r["matched_pnl"] + r["residual_pnl"] + r["maker_rebates"] == TARGET
    assert r["total"] == TARGET == BALANCE_DELTA


def test_probe07_parts_are_the_published_ones():
    r = replay(probe07_fills(), Native(), winner=UP)
    assert r["matched_qty"] == D("2.47")
    assert r["residual_qty"] == D("2.81")
    assert r["residual_side"] == DOWN
    assert r["matched_pnl"] == D("-0.073900")
    assert r["residual_pnl"] == D("-0.707900")
    assert r["maker_rebates"] == D("0.0100")


def test_the_loss_is_not_all_residual():
    """The directive's own condition: prove it, do not assume it."""
    r = replay(probe07_fills(), Native(), winner=UP)
    assert r["matched_pnl"] < 0, "the matched book lost money too"
    assert r["residual_pnl"] < r["matched_pnl"]


def test_matched_book_was_bought_above_par():
    r = replay(probe07_fills(), Native(), winner=UP)
    assert r["avg_pair_cost"] > D(1)


def test_pairing_convention_changes_the_split_but_not_the_total():
    fills = probe07_fills()
    fifo = replay(fills, Native(), winner=UP)
    pooled = pooled_split(fills, UP)
    assert abs(pooled["total"] - TARGET) < D("0.0000001")
    assert pooled["matched_pnl"] != fifo["matched_pnl"]


def test_independent_cash_identity():
    """redemption - spend + rebates, with no pairing convention at all."""
    r = replay(probe07_fills(), Native(), winner=UP)
    assert r["redemption"] - r["spend"] + r["maker_rebates"] == TARGET


def test_short_at_wire_equals_long_complement():
    """BUY_SHORT at wire p and buying DOWN at 1-p are the same cash."""
    wire, q = D("0.66"), D("2.25")
    venue = wire * q - D(1) * q          # credit now, owe 1 at settlement
    ours = -(D(1) - wire) * q            # cost of the DOWN share, worth 0
    assert venue == ours


# --------------------------------------------------------------- policies

def test_native_takes_everything():
    r = replay(probe07_fills(), Native(), winner=UP)
    assert r["actions_suppressed"] == 0 and r["actions_taken"] == 9


def test_complement_repair_allows_the_opening_leg():
    w = InventoryWalk()
    f = Fill(0, UP, D("0.7"), D(1))
    assert ComplementRepair().allows(w, f, w.blind_state(0, cand=f))[0]


def test_complement_repair_suppresses_same_side_while_imbalanced():
    w = InventoryWalk()
    w.accept(Fill(0, UP, D("0.7"), D(1)))
    same = Fill(1, UP, D("0.7"), D(1))
    ok, why = ComplementRepair().allows(w, same, w.blind_state(1, cand=same))
    assert not ok and why == "IMBALANCE_INCREASE_SUPPRESSED"


def test_complement_repair_always_allows_the_repairing_side():
    w = InventoryWalk()
    w.accept(Fill(0, UP, D("0.7"), D(5)))
    rep = Fill(1, DOWN, D("0.3"), D(5))
    assert ComplementRepair().allows(w, rep, w.blind_state(1, cand=rep))[0]


def test_residual_cap_below_one_clip_is_degenerate():
    """A cap under a single clip can never open a position. Documented."""
    w = InventoryWalk()
    f = Fill(0, UP, D("0.7"), D(1))
    assert not ResidualCap(D("0.5")).allows(w, f, w.blind_state(0, cand=f))[0]
    assert ResidualCap(D("1")).allows(w, f, w.blind_state(0, cand=f))[0]


def test_residual_cap_never_blocks_a_repair():
    w = InventoryWalk()
    w.accept(Fill(0, UP, D("0.7"), D(10)))
    rep = Fill(1, DOWN, D("0.3"), D(10))
    assert ResidualCap(D("1")).allows(w, rep, w.blind_state(1, cand=rep))[0]


def test_residual_age_only_bites_once_the_residual_is_old():
    w = InventoryWalk()
    w.accept(Fill(0, UP, D("0.7"), D(1)))
    p = ResidualAge(60)
    early = Fill(30, UP, D("0.7"), D(1))
    late = Fill(120, UP, D("0.7"), D(1))
    assert p.allows(w, early, w.blind_state(30, cand=early))[0]
    assert not p.allows(w, late, w.blind_state(120, cand=late))[0]


def test_pair_cost_cap_never_refuses_an_opening_leg():
    w = InventoryWalk()
    f = Fill(0, UP, D("0.99"), D(1))
    assert PairCostCap(D("0.96")).allows(w, f, w.blind_state(0, cand=f))[0]


def test_pair_cost_cap_refuses_an_expensive_completion():
    w = InventoryWalk()
    w.accept(Fill(0, UP, D("0.71"), D(1)))
    bad = Fill(1, DOWN, D("0.33"), D(1))          # pair costs 1.04
    ok, why = PairCostCap(D("0.96")).allows(w, bad, w.blind_state(1, cand=bad))
    assert not ok and why == "PAIR_COST_ABOVE_CAP"


def test_random_control_is_state_blind():
    """The control must not consult inventory, or it is not a control."""
    a, b = InventoryWalk(), InventoryWalk()
    b.accept(Fill(0, UP, D("0.7"), D(50)))
    f = Fill(1, UP, D("0.7"), D(1))
    x = RandomSuppress(0.5, 7).allows(a, f, a.blind_state(1, cand=f))[0]
    y = RandomSuppress(0.5, 7).allows(b, f, b.blind_state(1, cand=f))[0]
    assert x == y


# ------------------------------------------------------------- blindness

def test_decision_state_carries_no_settlement_information():
    w = InventoryWalk()
    w.accept(Fill(0, UP, D("0.7"), D(1)))
    st = w.blind_state(1.0, window_end=900.0)     # raises if it leaks
    assert "settlementPx" not in st and "winner" not in st


def test_a_leaking_state_is_refused():
    w = InventoryWalk()
    with pytest.raises(OutcomeLeak):
        from src.l5_adapted_v1 import assert_blind
        assert_blind({"residual": "1", "settlementPx": "1.0"})


def test_settle_is_scoring_not_deciding():
    """Two winners, identical decisions: the policy path cannot differ."""
    fills = probe07_fills()
    up = replay(fills, ComplementRepair(), winner=UP)
    dn = replay(fills, ComplementRepair(), winner=DOWN)
    assert up["actions_taken"] == dn["actions_taken"]
    assert up["matched_qty"] == dn["matched_qty"]
    assert up["residual_pnl"] != dn["residual_pnl"]


# ----------------------------------------------------------- bookkeeping

def test_suppressed_actions_do_not_reach_inventory():
    fills = probe07_fills()
    r = replay(fills, ComplementRepair(), winner=UP)
    assert r["fills"] == r["actions_taken"]
    assert r["actions_taken"] + r["actions_suppressed"] == len(fills)


def test_drawdown_of_a_rising_series_is_zero():
    assert drawdown([D(1), D(2), D(3)]) == 0
    assert drawdown([D(5), D(-3), D(-1), D(4)]) == D(4)


def test_fifo_matches_against_the_oldest_lot_first():
    w = InventoryWalk()
    w.accept(Fill(0, UP, D("0.60"), D(1)))
    w.accept(Fill(1, UP, D("0.80"), D(1)))
    w.accept(Fill(2, DOWN, D("0.30"), D(1)))
    assert w.matched_locked_pnl == D(1) - D("0.90")      # 0.60 lot, not 0.80
