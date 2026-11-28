"""P0.3: the behavioral ledger must track the book it is supposed to describe.

OWNER 2026-09-13, from the live R5FT run. `record_behavioral_shares` credited
L5's whole p50 target for the state|relation ONCE PER FILL, regardless of how
many contracts actually filled. Two failures follow, and both were observed:

  FALSE FLAT. cov-bha filled 2.00, 2.27, 1.81 and 1.44 contracts; each was
  credited +/-10 shares, the four netted to EXACTLY 0.0, and the state key read
  `flat` while the executable book was 1.02 contracts long. `flat` is not a
  neighbouring state -- p25-45c|tte>10m|flat is 100% open (n=83) and imb1-10 is
  0% open (n=366) -- so thirty OPENs followed that the frozen model, keyed
  faithfully, assigns probability ZERO.

  SIGN INVERSION. lal-lev-fcb read book +0.28 contracts LONG against a
  behavioral -2.76 shares SHORT, with no exact cancellation involved.
  _relation() chooses extend vs complete from that sign.

Measured over 138 live decisions: sign wrong 33.3%, false-flat 24.6%, bucket
wrong 25.4%, any 34.1%.

THE FIX IS THE ADAPTER, NEVER THE FROZEN MODEL. Probabilities, price and TTE
buckets, stochastic selection, the OPEN/EXTEND/COMPLETE/HOLD definitions, the
pair ceiling, maker-only execution and every dollar/residual/loss/concurrency
gate are untouched; only how much of L5's own p50 we credit per fill changes,
plus two invariants enforced where the residual is READ.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_spring_l5_session as base
from src.polymarket_l5.action_model import state_key


def _inv(fills):
    """fills: (side_up, px, qty, p50). Records executable then behavioral,
    in the order every live call site does."""
    inv = base.Inventory()
    for side_up, px, qty, p50 in fills:
        inv.record(side_up, D(str(px)), D(str(qty)), "x")
        inv.record_behavioral_shares(side_up, float(p50))
    return inv


def _bucket(inv):
    r = inv.behavioral_residual_shares()
    return state_key(0.30, 1e9, r if r is not None else 0.0).split("|")[2]


# ------------------------------------------------ A/B/C: the fill sets scale

def test_the_fill_quantity_determines_the_behavioral_change():
    """A. One contract of a two-contract unit moves the ledger half a p50."""
    inv = _inv([(True, "0.50", "2.00", 10.0)])
    assert inv.behavioral_residual_shares() == pytest.approx(10.0)
    inv2 = _inv([(True, "0.50", "1.00", 10.0)])
    # unit is set by the FIRST fill, so a lone 1.00 fill IS one unit
    assert inv2.behavioral_residual_shares() == pytest.approx(10.0)
    # but a second, half-sized fill credits half
    inv2.record(True, D("0.50"), D("0.50"), "x")
    inv2.record_behavioral_shares(True, 10.0)
    assert inv2.behavioral_residual_shares() == pytest.approx(15.0)


def test_double_size_fill_credits_double():
    inv = _inv([(True, "0.50", "2.00", 10.0), (True, "0.50", "4.00", 10.0)])
    assert inv.behavioral_residual_shares() == pytest.approx(30.0)


def test_partial_fills_scale_proportionally():
    """C. Four quarter-unit partials equal one whole-unit fill."""
    whole = _inv([(True, "0.50", "4.00", 12.0), (True, "0.50", "4.00", 12.0)])
    parts = _inv([(True, "0.50", "4.00", 12.0)] +
                 [(True, "0.50", "1.00", 12.0)] * 4)
    assert parts.behavioral_residual_shares() == pytest.approx(
        whole.behavioral_residual_shares())


def test_equal_sized_fills_still_net_to_zero_when_the_book_does():
    inv = _inv([(True, "0.45", "2.00", 10.0), (False, "0.50", "2.00", 10.0)])
    assert inv.residual == 0
    assert inv.behavioral_residual_shares() == 0.0


def test_unequal_sized_fills_no_longer_net_to_a_false_zero():
    """The heart of it: equal COUNTS of fills, unequal CONTRACTS."""
    inv = _inv([(True, "0.47", "2.00", 10.0), (True, "0.44", "2.27", 10.0),
                (False, "0.30", "1.81", 10.0), (False, "0.30", "1.44", 10.0)])
    assert inv.residual == D("1.02")
    r = inv.behavioral_residual_shares()
    assert r is not None and r > 1.0, r
    assert _bucket(inv) == "imb1-10"


def test_multiple_same_side_extends_accumulate_by_contracts():
    inv = _inv([(True, "0.45", "2.22", 10.0), (True, "0.45", "2.22", 10.0),
                (True, "0.59", "1.69", 10.0), (True, "0.39", "2.45", 10.0)])
    assert inv.residual == D("8.58")
    assert inv.behavioral_residual_shares() == pytest.approx(
        8.58 * (10.0 / 2.22), rel=1e-9)


def test_a_partial_complete_reduces_but_does_not_flip():
    inv = _inv([(True, "0.45", "4.00", 10.0), (False, "0.50", "1.00", 10.0)])
    assert inv.residual == D("3.00")
    r = inv.behavioral_residual_shares()
    assert r > 0 and _bucket(inv) != "flat"


# ------------------------------------------------------ D/E/F: the invariants

def test_sign_always_matches_the_executable_book():
    """D. The pathological lal-lev-fcb shape: bigger p50s on the short side
    drag the raw accumulator negative while the book is long."""
    inv = _inv([(True, "0.39", "2.45", 8.0), (True, "0.39", "2.45", 8.0),
                (False, "0.57", "2.45", 22.0), (True, "0.39", "2.45", 7.0),
                (False, "0.57", "2.45", 22.0), (False, "0.53", "2.17", 22.0)])
    assert inv.residual > 0, inv.residual
    r = inv.behavioral_residual_shares()
    assert r > 0, f"behavioral {r} inverted against a long book {inv.residual}"


def test_sign_matches_for_a_short_book_too():
    inv = _inv([(False, "0.45", "1.88", 10.0), (True, "0.55", "0.50", 40.0)])
    assert inv.residual < 0
    assert inv.behavioral_residual_shares() < 0


def test_zero_only_when_the_book_is_actually_balanced():
    """E. Exact balance -> exactly zero; near balance -> never zero."""
    exact = _inv([(True, "0.45", "3.00", 10.0), (False, "0.50", "3.00", 14.0)])
    assert exact.residual == 0 and exact.behavioral_residual_shares() == 0.0
    near = _inv([(True, "0.45", "3.00", 10.0), (False, "0.50", "2.99", 14.0)])
    assert near.residual != 0
    assert near.behavioral_residual_shares() != 0.0


def test_a_material_residual_can_never_round_into_flat():
    """F. Even when the raw accumulator lands inside the flat bucket, a real
    position is reported at its faithful scale."""
    inv = _inv([(True, "0.47", "2.00", 10.0), (True, "0.44", "2.27", 10.0),
                (False, "0.30", "1.81", 10.0), (False, "0.30", "1.44", 10.0)])
    assert abs(inv.residual) > base.BEHAVIORAL_FLAT_QTY_TOL
    assert abs(inv.behavioral_residual_shares()) >= base.BEHAVIORAL_FLAT_SHARES


def test_the_tolerances_are_named_not_inlined():
    assert base.BEHAVIORAL_FLAT_QTY_TOL == D("0.0001")
    assert base.BEHAVIORAL_FLAT_SHARES == 1.0


# ------------------------------------------------------- H: the live tapes

def test_live_cov_bha_reproduction():
    """The exact executed tape. Residual 1.02 contracts MUST NOT be flat."""
    inv = _inv([(True, "0.47", "2.00", 10.0), (True, "0.44", "2.27", 10.0),
                (False, "0.30", "1.81", 10.0), (False, "0.30", "1.44", 10.0)])
    assert inv.residual == D("1.02")
    assert inv.behavioral_residual_shares() == pytest.approx(5.10, abs=1e-6)
    assert _bucket(inv) == "imb1-10", "the 100%-OPEN bucket must not be reached"


def test_live_cov_bha_full_tape_ends_balanced():
    """...and the same tape run to its real end (fully matched) IS flat."""
    inv = _inv([(True, "0.47", "2.00", 10.0), (True, "0.44", "2.27", 10.0),
                (False, "0.30", "1.81", 10.0), (False, "0.30", "1.44", 10.0),
                (True, "0.71", "1.40", 10.0), (False, "0.29", "1.42", 10.0),
                (False, "0.01", "1.00", 10.0)])
    assert inv.residual == 0
    assert inv.behavioral_residual_shares() == 0.0
    assert _bucket(inv) == "flat", "a genuinely matched book is genuinely flat"


def test_live_lal_lev_fcb_reproduction_does_not_invert():
    inv = _inv([(True, "0.39", "2.45", 10.0), (True, "0.39", "2.45", 10.0),
                (False, "0.57", "2.45", 22.52), (True, "0.39", "2.45", 10.0),
                (False, "0.57", "2.45", 12.52), (False, "0.53", "2.17", 7.24)])
    assert inv.residual == D("0.28")
    r = inv.behavioral_residual_shares()
    assert r > 0, f"book is +0.28 long, behavioral read {r}"


# -------------------------------------------------- sequences and restarts

def test_open_extend_complete_extend_complete():
    inv = base.Inventory()
    seq = [(True, "0.45", "2.00", "open"), (True, "0.45", "2.00", "extend"),
           (False, "0.52", "1.50", "complete"), (True, "0.46", "1.00", "extend"),
           (False, "0.52", "2.50", "complete")]
    signs = []
    for side_up, px, qty, rel in seq:
        inv.record(side_up, D(px), D(qty), rel)
        inv.record_behavioral_shares(side_up, 10.0)
        r = inv.behavioral_residual_shares()
        signs.append((inv.residual, r))
    for resid, r in signs:
        if resid == 0:
            assert r == 0.0
        elif r is not None:
            assert (r > 0) == (resid > 0), (resid, r)


def test_restart_reconstruction_is_identical():
    """Replaying the same fills from a fresh Inventory reproduces the state
    exactly -- no hidden per-process accumulation."""
    fills = [(True, "0.47", "2.00", 10.0), (True, "0.44", "2.27", 10.0),
             (False, "0.30", "1.81", 10.0), (False, "0.30", "1.44", 10.0)]
    a, b = _inv(fills), _inv(fills)
    assert a.behavioral_residual_shares() == b.behavioral_residual_shares()
    assert a.residual == b.residual
    assert a.shares_per_contract() == b.shares_per_contract()


def test_a_matched_pair_still_closes_the_behavioral_sequence():
    """The 2026-09-07 R4 fix stays: a balanced book resets the ledger so the
    next open starts fresh. MEASURED SUPPORT: at exactly zero residual L5
    starts a fresh buy in 209/209 canonical observations."""
    inv = _inv([(True, "0.45", "2.00", 10.0), (False, "0.50", "2.00", 10.0)])
    assert inv.up_shares_behavioral == 0.0 and inv.dn_shares_behavioral == 0.0
    assert inv.shares_credited == 0.0 and inv.contracts_credited == 0
    inv.record(True, D("0.45"), D("2.00"), "open")
    inv.record_behavioral_shares(True, 10.0)
    assert inv.behavioral_residual_shares() == pytest.approx(10.0)


def test_legacy_callers_without_a_fill_size_still_work():
    """The shadow lanes and replay fixtures credit the behavioral side
    directly, with no executable fill to scale by. Without a unit or a fill
    size the recorder degrades to the pre-P0.3 whole-p50 credit rather than
    crediting nothing -- and the READ-side invariants still apply."""
    inv = base.Inventory()
    inv.up = D("1")                 # an unbalanced book, recorded elsewhere
    inv.record_behavioral_shares(True, 10.0, "q5-15")
    assert inv.unit is None and inv.last_fill_qty is None
    assert inv.up_shares_behavioral == 10.0
    assert inv.last_size_behavioral == "q5-15"


# --------------------------------------------------------- G: nothing moved

def test_the_frozen_model_and_every_gate_are_untouched():
    from src.polymarket_l5.action_model import (IMBALANCE_BUCKETS, PRICE_BANDS,
                                                SIZE_BUCKETS, TTE_BUCKETS)
    assert IMBALANCE_BUCKETS == ((1.0, "flat"), (10.0, "imb1-10"),
                                 (50.0, "imb10-50"), (1e12, "imb>50"))
    assert PRICE_BANDS[2] == (0.45, "p25-45c") and PRICE_BANDS[3] == (0.55, "p45-55c")
    assert TTE_BUCKETS[-1] == (1e12, "tte>10m")
    assert SIZE_BUCKETS[0] == (5.0, "q<5")
    assert (base.SPRING_BAND_LO, base.SPRING_BAND_HI) == (D("0.25"), D("0.75"))
    assert base.completion_pair_ceiling() == D("1.00")
    assert base.COMPLETE_REJECT_STREAK_MAX == 3


def test_the_adapter_change_is_confined_to_the_adapter():
    """The fix must not have reached into pricing or the gates."""
    import ast
    src = open(base.__file__).read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef)
              and n.name == "record_behavioral_shares")
    body = ast.unparse(fn)
    for forbidden in ("complement", "pair_cost", "ceiling", "daily_loss",
                      "post_only", "submit", "cancel"):
        assert forbidden not in body, forbidden
