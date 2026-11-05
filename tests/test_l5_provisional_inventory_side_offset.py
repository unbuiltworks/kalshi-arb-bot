"""Provisional matched/unmatched is a SIDE question, not a ROLE question.

THE PRODUCTION DISCREPANCY, 2026-11-05 window 2230z. One record carried two
answers to one question:

  pair_economics        pairs_completed 2, matched_qty 2, residual_qty UP 1
  provisional_inventory matched_estimate 1, unmatched_estimate 3

Five contracts filled. The truth is two pairs -- UP 0.4300 + DOWN 0.5600 and
UP 0.4500 + DOWN 0.5100 -- so four contracts matched and one UP unmatched.
`provisional_inventory` overstated unmatched THREEFOLD.

WHY. It read `min(role FIRST_LEG, role COMPLEMENT)` and
`abs(FIRST_LEG - COMPLEMENT)`. A matched pair is one UP against one DOWN;
which leg we happened to PLACE first is a fact about our sequencing. Role is
assigned economically -- "a leg is a COMPLEMENT when the OPPOSITE side
already holds filled inventory at the moment it is submitted" -- so across
several rounds in one window the complement role carries BOTH sides, and the
two counts never had to agree.

REPORTING ONLY, BEFORE AND AFTER. Nothing in src, scripts or analysis reads
`provisional_inventory`. It is what a human reads to judge unmatched
exposure before settlement, which is precisely why being wrong by 3x matters:
the settled economics for this campaign are matched +$0.1500 against residual
-$0.9900, so unmatched exposure is the number that decides whether the
strategy is working.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_window_accounting as WA


MARKET = "cpc-btc-updown-15m-2026-11-05-2230z"


def _sub(coid, side, own_px, qty="1"):
    return {"coid": coid, "side": side, "own_px": own_px, "qty": qty,
            "mode": "MAKER"}


def _life(coid, cum="1", state="ORDER_STATE_FILLED"):
    return {"coid": coid, "cum": cum, "state": state, "terminal": True,
            "commission": "0"}


#: THE REAL 2230z SEQUENCE. Submission ORDER is what assigns the roles, so it
#: is reproduced exactly: one UP opens, then every later leg finds the
#: opposite side already holding, which is why production recorded
#: first_leg_filled 1 and complement_filled 4.
SEQ = [
    ("o1", "UP", "0.4300"),
    ("o2", "DOWN", "0.5600"),
    ("o3", "UP", "0.4500"),
    ("o4", "DOWN", "0.5100"),
    ("o5", "UP", "0.5000"),      # the leg that ended the window unmatched
]


def _record(seq=SEQ, settlement=None):
    subs = [_sub(c, s, p) for c, s, p in seq]
    life = [_life(c) for c, _s, _p in seq]
    return WA.window_record(market=MARKET, submits=subs, lifecycle=life,
                            fills=(), cancels=(), settlement=settlement,
                            rebate_credits=(),
                            rebate_eligibility="UNVERIFIED")


def test_the_fixture_reproduces_productions_role_counts():
    """If the fixture's roles do not match production, it proves nothing."""
    pi = _record()["provisional_inventory"]
    assert pi["role_counts"] == {"first_leg": "1", "complement": "4"}


def test_five_contracts_are_four_matched_and_one_unmatched():
    rec = _record()
    pi = rec["provisional_inventory"]
    assert pi["filled_contracts"] == D(5)
    assert pi["matched_estimate"] == D(4), "two pairs is FOUR contracts"
    assert pi["matched_pairs_estimate"] == 2
    assert pi["unmatched_estimate"] == D(1)


def test_it_agrees_with_pair_economics_in_the_same_record():
    """The defect was two answers to one question. There is now one."""
    rec = _record()
    pi, pe = rec["provisional_inventory"], rec["pair_economics"]
    assert pi["matched_estimate"] == D(str(pe["matched_qty"])) * 2
    assert pi["unmatched_estimate"] == sum(
        (D(str(v)) for v in pe["residual_qty"].values()), D(0))
    assert pi["matched_pairs_estimate"] == pe["pairs_completed"]
    assert pi["basis"] == "SIDE_OFFSET_VIA_PAIR_ECONOMICS"


def test_the_old_role_arithmetic_would_have_been_wrong_here():
    """Pinned so the regression cannot come back quietly."""
    pi = _record()["provisional_inventory"]
    first = D(pi["role_counts"]["first_leg"])
    comp = D(pi["role_counts"]["complement"])
    assert min(first, comp) == D(1)          # the old matched_estimate
    assert abs(first - comp) == D(3)         # the old unmatched_estimate
    assert pi["matched_estimate"] != min(first, comp)
    assert pi["unmatched_estimate"] != abs(first - comp)


def test_role_counts_are_still_reported_beside_the_answer():
    """Role is real -- it is just not the offsetting question. Keeping it
    visible is what let this discrepancy be found at all."""
    pi = _record()["provisional_inventory"]
    assert set(pi["role_counts"]) == {"first_leg", "complement"}


def test_a_single_unmatched_first_leg_reads_as_one_unmatched():
    """2215z: one first leg filled, complement never filled, residual DOWN 1."""
    pi = _record(seq=[("o1", "DOWN", "0.5000")])["provisional_inventory"]
    assert pi["matched_estimate"] == D(0)
    assert pi["unmatched_estimate"] == D(1)
    assert pi["matched_pairs_estimate"] == 0


def test_a_clean_pair_reads_as_two_matched_and_none_unmatched():
    pi = _record(seq=[("o1", "UP", "0.4500"),
                      ("o2", "DOWN", "0.5100")])["provisional_inventory"]
    assert pi["matched_estimate"] == D(2)
    assert pi["unmatched_estimate"] == D(0)
    assert pi["matched_pairs_estimate"] == 1


def test_nothing_filled_reads_as_zero_not_as_unknown():
    pi = _record(seq=[])["provisional_inventory"]
    assert pi["filled_contracts"] == D(0)
    assert pi["matched_estimate"] == D(0)
    assert pi["unmatched_estimate"] == D(0)


def test_the_settled_block_still_overrides_the_provisional_one():
    """The venue remains the authority; this block only exists pre-settlement.
    """
    rec = _record(settlement={"matched_qty": "2", "matched_pnl": "0.05",
                              "residual_qty": "1", "residual_side": "UP",
                              "residual_pnl": "-0.50",
                              "settlement_pnl": "-0.45",
                              "resolution_source": "VENUE_SETTLEMENT_PX",
                              "state": "MARKET_STATE_EXPIRED"})
    assert "provisional_inventory" not in rec, (
        "a settled window reports the venue's lines, not an estimate")
    assert rec["settled"]["residual_qty"] == D(1)


def test_provisional_inventory_has_no_consumer_so_this_is_reporting_only():
    """Stated as a test so the claim is checkable rather than asserted."""
    import pathlib
    hits = []
    for p in list(pathlib.Path("src").rglob("*.py")) + \
            list(pathlib.Path("scripts").rglob("*.py")):
        if p.name == "l5_window_accounting.py":
            continue
        if "provisional_inventory" in p.read_text():
            hits.append(str(p))
    assert hits == [], hits
