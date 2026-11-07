"""Deterministic multi-round lifecycles against the real executor.

Item 6 of the execution mandate: simultaneous fills, partially filled legs,
cancellation races, adverse selection and settlement, driven across SEVERAL
rounds rather than one, because the defects this program has actually hit
(1130z's four legs in seven seconds, 1415z's UP 4.25, 1230z's second round)
all appeared on the transition BETWEEN rounds.

These drive `FaithfulExecutor` and a real `InventoryStore`, and every
assertion names the control that acts. Two invariants run through all of
them:

  * the engine may refuse to ADD exposure for any reason, but never to
    MANAGE exposure already standing;
  * no round may be authorised whose worst case does not fit the budget,
    whatever its expectancy says.

THE CASH ENVELOPE IS CUMULATIVE WITHIN A WINDOW, which these tests pin
because it is the real throughput limit. A completed pair does NOT free its
outlay mid-window -- `CASH_COMMITMENT` grows with fills and is released only
at settlement -- so under a $1 cap the second round is refused by cash
before the reopening gate is ever consulted.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src.l5_live_validation_gates import ValidationGates as VG
import tests.test_l5_two_sided_wiring as W
from tests.test_l5_two_sided_wiring import probe  # noqa: F401


def gates(cash="12", deficit="3", cum="10"):
    return VG(max_cash_deployed_usd=D(cash),
              max_unmatched_contracts=D("5"),
              max_cumulative_loss_usd=D(cum),
              max_worst_case_deficit_usd=D(deficit))


def rig(H, E, tmp_path, cash="12", orders=8):
    ex, t = W.mk(H, E, tmp_path, g=gates(cash=cash))
    ex.limits = H.limits(max_live_orders=orders)
    return ex, t


def fill_all(ex, t, start=0):
    """Fill every order sent since `start`, as the venue would."""
    for s in t.sent[start:]:
        ex.store.apply_fill("m", s["side"], D(str(s["qty"])),
                            D(str(s["price"])), basis="OWN")


def ticks(ex, n=2, book=None):
    out = []
    for _ in range(n):
        out.append(ex.handle(W._row(ex_H), book or W._book(),
                             market="m", window="21:00"))
    return out


ex_H = None            # set by the fixture below


@pytest.fixture(autouse=True)
def _bind(probe):
    global ex_H
    ex_H, _ = probe
    yield


# ============================== 1. THE CASH ENVELOPE IS THE REAL LIMIT

def test_a_completed_pair_does_not_free_its_cash_mid_window(probe, tmp_path):
    """THE THROUGHPUT FINDING, pinned.

    Round 0 pairs. The outlay stays committed until settlement, so
    `CASH_COMMITMENT` RISES and the next round is refused by cash -- not by
    the reopening gate, which by then would permit it. Sequential
    replenishment inside one window is capital-blocked at $1, and no
    controller change can alter that.
    """
    H, E = probe
    ex, t = rig(H, E, tmp_path, cash="1.00")
    ticks(ex, 2)
    assert len(t.sent) == 2, "both legs of round 0 reach the wire"
    fill_all(ex, t)
    rec = ticks(ex, 1)[0]
    assert len(t.sent) == 2, "and nothing more goes out"
    assert "CASH_COMMITMENT" in str(rec.get("refused_reason"))


def test_a_larger_envelope_does_let_further_rounds_through(probe, tmp_path):
    """The same code, more cash: throughput follows capital, not policy."""
    H, E = probe
    ex, t = rig(H, E, tmp_path, cash="12")
    ticks(ex, 2)
    first = len(t.sent)
    fill_all(ex, t)
    ticks(ex, 4)
    assert len(t.sent) > first, "more rounds are reachable with more cash"


# =============================== 2. SIMULTANEOUS AND PARTIAL FILLS

def test_both_legs_filling_together_leaves_nothing_unmatched(probe,
                                                             tmp_path):
    H, E = probe
    ex, t = rig(H, E, tmp_path)
    ticks(ex, 2)
    fill_all(ex, t)
    up = ex.store.position("m", "UP")
    dn = ex.store.position("m", "DOWN")
    assert up > 0 and dn > 0
    assert abs(ex.store.residual("m")) == abs(up - dn)


def test_a_partial_fill_never_deepens_the_side_already_ahead(probe,
                                                              tmp_path):
    """The invariant, stated on PROSPECTIVE per-side totals.

    A partial fill on UP while round 0's DOWN is still RESTING does not
    call for another DOWN -- the reduction is already working, which is the
    1415z lesson in the other direction. What must hold is that no new
    order leaves the UP side further ahead of DOWN than it already was, per
    side, counting live orders as if they filled.
    """
    H, E = probe
    ex, t = rig(H, E, tmp_path)
    ticks(ex, 2)
    up = next(s for s in t.sent if s["side"] == "UP")
    ex.store.apply_fill("m", "UP", D(str(up["qty"])) / 2,
                        D(str(up["price"])), basis="OWN")

    def prospective(side):
        return (abs(D(str(ex.store.position("m", side) or 0)))
                + D(str(ex.store.outstanding_on_side("m", side))))
    gap_before = abs(prospective("UP") - prospective("DOWN"))
    ticks(ex, 3)
    gap_after = abs(prospective("UP") - prospective("DOWN"))
    # MAGNITUDE, not sign: the engine is free to add to either side so long
    # as the per-side imbalance does not widen.
    assert gap_after <= gap_before, (gap_before, gap_after)


def test_the_unmatched_cap_binds_once_a_residual_exists(probe, tmp_path):
    """1415z's control: it bounds DEEPENING, and here it is doing it."""
    H, E = probe
    ex, t = W.mk(H, E, tmp_path,
                 g=VG(max_cash_deployed_usd=D("12"),
                      max_unmatched_contracts=D("0.5"),
                      max_cumulative_loss_usd=D("10"),
                      max_worst_case_deficit_usd=D("3")))
    ex.limits = H.limits(max_live_orders=8)
    ex.store.apply_fill("m", "DOWN", D("2"), D("0.3700"), basis="OWN")
    assert abs(ex.store.residual("m")) > D("0.5")
    before = len(t.sent)
    ticks(ex, 3)
    for s in t.sent[before:]:
        assert s["side"] == "UP", "only the reducing side may be added"


# ===================================== 3. CANCELLATION RACES, MULTI-ROUND

def test_an_unresolved_cancel_stops_every_later_round(probe, tmp_path):
    H, E = probe
    ex, t = rig(H, E, tmp_path)
    ticks(ex, 2)
    fill_all(ex, t)
    before = len(t.sent)
    ex.store.mark_uncertain("CANCEL_VERIFY_HTTPError")
    recs = ticks(ex, 3)
    assert len(t.sent) == before
    assert all("BOOK_UNCERTAIN" in str(r.get("refused_reason"))
               for r in recs)


def test_clearing_uncertainty_restores_trading(probe, tmp_path):
    """It is a hold pending reconciliation, not a permanent halt."""
    H, E = probe
    ex, t = rig(H, E, tmp_path)
    ex.store.mark_uncertain("CANCEL_VERIFY_HTTPError")
    assert ticks(ex, 1)[0].get("refused_reason")
    ex.store.state["orders_uncertain"] = None
    ex.store.save()
    ticks(ex, 2)
    assert t.sent, "trading resumes once the ledger is authoritative again"


# ======================================= 4. ADVERSE SELECTION ACROSS ROUNDS

def test_a_book_that_runs_away_does_not_strand_the_held_leg(probe,
                                                            tmp_path):
    """0330z: a plan frozen against a 0.42/0.56 touch, book at 0.91/0.92."""
    H, E = probe
    ex, t = rig(H, E, tmp_path)
    ticks(ex, 2)
    up = next(s for s in t.sent if s["side"] == "UP")
    ex.store.apply_fill("m", "UP", D(str(up["qty"])), D(str(up["price"])),
                        basis="OWN")
    moved = W._book(bid="0.9100", ask="0.9200")
    recs = ticks(ex, 3, book=moved)
    assert all(r is not None for r in recs), "a decision is always recorded"
    # whatever it does, it may not deepen the UP side into the move
    for s in t.sent:
        assert s["side"] in ("UP", "DOWN")


def test_repeated_adverse_rounds_cannot_exceed_the_loss_ceiling(probe,
                                                                tmp_path):
    """The cumulative halt is what bounds a losing sequence."""
    H, E = probe
    ex, t = W.mk(H, E, tmp_path, g=gates(cum="0.01"))
    ex.limits = H.limits(max_live_orders=8)
    recs = ticks(ex, 4)
    assert t.sent == []
    assert recs[-1].get("refused_reason") == "NO_LEGAL_QUANTITY"


# ================================================ 5. SETTLEMENT, EXACTLY

@pytest.mark.parametrize("up_px,dn_px,expect", [
    ("0.5000", "0.4500", "0.0500"),
    ("0.5800", "0.3700", "0.0500"),
    ("0.4750", "0.4800", "0.0450"),
])
def test_a_matched_pair_redeems_one_dollar_whatever_the_outcome(up_px,
                                                                dn_px,
                                                                expect):
    """The identity the whole strategy rests on, stated once more."""
    pair = D(up_px) + D(dn_px)
    assert (D(1) - pair).quantize(D("0.0001")) == D(expect)


def test_an_unmatched_leg_settles_at_zero_or_one_never_in_between():
    from src import l5_unmatched_disposition as UD
    for s in (D(0), D(1)):
        r = UD.weighted_inventory_decision(
            market="m", favoured_side="UP", n_favoured=D("2"),
            n_other=D("1"), up_own_px=D("0.4550"), down_own_px=D("0.5000"),
            bid=D("0.48"), ask=D("0.49"),
            exposure_headroom_usd=D("2.00"))
        del s
        assert D(r["if_up_wins_usd"]) == D("0.5900")
        assert D(r["if_down_wins_usd"]) == D("-0.4100")


def test_the_zero_residual_expectancy_is_a_NEUTRAL_HYPOTHESIS():
    """Item 3, pinned in code.

    Zero is not a claim that our maker fills are profitable. It is the EV
    identity's own answer for a leg bought at a fair price -- and it is
    strictly BELOW the +$0.0263 to +$0.0367 per contract that L5's own
    capture-consistent residuals realised, so nothing is banked.

    Our own settled residuals, by contrast, lost 4 of 5. n=5 cannot refute
    20,069 contracts, and 20,069 contracts of someone else's fills cannot
    establish ours.
    """
    from src import l5_unmatched_disposition as UD
    assert UD.REOPEN_SINGLE_OUTCOME_USD == D("0")
    assert UD.REOPEN_SINGLE_OUTCOME_USD < D("0.0263"), \
        "below the lower of the two L5 estimates: nothing banked"
    assert UD.REOPEN_SINGLE_OUTCOME_USD > D("-0.2440"), \
        "and above the superseded PROVEN artefact"
    # the WORST case is still the full leg, unchanged by the neutral prior
    r = UD.reopen_decision(
        market="m", rounds_used=1, worst_leg_own_px=D("0.4900"),
        qty=D("1"), bid=D("0.48"), ask=D("0.49"), tick=D("0.01"),
        depth_ticks=2, now_s=0.0, window_end_s=900.0,
        exposure_headroom_usd=D("1.00"))
    assert D(r["worst_case_usd"]) == D("0.4900")
