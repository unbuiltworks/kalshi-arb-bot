"""The settlement-observability defect, against the venue's own resolved shape.

WHAT WENT WRONG. The Gemini UI showed GEMI-BTC05M2609181730-UP as "Resolved --
Outcome has occurred and payouts have been distributed", and our probe kept
reporting `authoritative_outcome` absent and `settlement_cashflow` absent.

Nothing ever assigned `Settlement.settlement_value` or `settlement_source`, so
the proof chain's `authoritative_outcome` link evaluated `bool(None and None)`
-- FALSE for every symbol, always. It was not a schema problem: the contract
object is PUBLIC and had carried `status`, `resolutionSide`, `resolvedAt` and
`settlementValue` since 17:30:07.369Z. The probe simply never asked.

Two further defects were latent behind it, and both would have produced a
WRONG NUMBER rather than a missing one:

  `settlementValue` is the UNDERLYING INDEX PRINT, not a payout. Measured:
  "80727.9451111111" against a strike of "80694.84". Wiring it in as a
  settlement value would have valued a $0.01 position at $807.

  `Leg` carried no outcome, so a YES leg and a NO leg were indistinguishable
  and the whole position was multiplied by one settlement value. For
  CONTINUOUS-4's YES 0.01 + NO 0.01 that pays out BOTH units: +0.0094, a
  phantom profit with the sign inverted, where the truth is -0.0006.

FIXTURES ARE THE CAPTURED RESPONSES in
analysis/gemini/venue_resolution_shape_20260918.json.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from src import gemini_settlement as S

SHAPE = json.load(open(os.path.join(
    "analysis", "gemini", "venue_resolution_shape_20260918.json"),
    encoding="utf-8"))
CONTRACT = SHAPE["contract_object"]
RESOLUTION = SHAPE["resolution_for"]
SYMBOL = SHAPE["symbol"]

#: the CONTINUOUS-4 fills, as /v1/mytrades reported them
def c4_legs():
    return [S.Leg(side="BUY", qty="0.01", price="0.92", fee="0",
                  source="/v1/mytrades", outcome="yes"),
            S.Leg(side="BUY", qty="0.01", price="0.14", fee="0",
                  source="/v1/mytrades", outcome="no")]


def c4():
    return S.Settlement(SYMBOL, legs=c4_legs())


# =================================================== THE CAPTURED VENUE SHAPE

def test_the_captured_contract_really_is_resolved():
    """If this ever fails the fixture was edited, not the venue."""
    assert CONTRACT["status"] == "settled"
    assert CONTRACT["marketState"] == "closed"
    assert CONTRACT["resolutionSide"] == "yes"
    assert CONTRACT["resolvedAt"] == "2026-09-18T17:30:07.369Z"
    assert CONTRACT["settlementValue"] == "80727.9451111111"
    assert CONTRACT["strike"]["value"] == "80694.84"


def test_resolution_for_reports_settled_on_that_shape():
    assert RESOLUTION["verdict"] == "SETTLED"
    assert RESOLUTION["resolutionSide"] == "yes"
    assert RESOLUTION["is_terminal"] is True
    assert RESOLUTION["strike_check"]["agrees"] is True, (
        "the published side must be independently implied by the strike")


def test_the_venue_resolved_seconds_after_expiry_not_hours():
    """Our reads at 17:31 and 17:55 were both AFTER this. The data was there."""
    from datetime import datetime
    exp = datetime.fromisoformat(
        CONTRACT["expiryDate"].replace("Z", "+00:00"))
    res = datetime.fromisoformat(
        CONTRACT["resolvedAt"].replace("Z", "+00:00"))
    assert 0 < (res - exp).total_seconds() < 60


# ========================================== THE LINK THAT COULD NEVER BE TRUE

def test_without_a_resolution_the_outcome_link_is_unsatisfiable():
    """THE ROOT CAUSE, stated as a test. This is what shipped."""
    st = c4()
    assert st.settlement_value is None and st.settlement_source is None
    assert st.satisfied()[S.SETTLEMENT_VALUE] is False


def test_applying_the_captured_resolution_satisfies_it():
    st = c4()
    out = S.apply_resolution(st, RESOLUTION)
    assert out["applied"] is True
    assert st.resolution_side == "yes"
    assert st.resolved_at == "2026-09-18T17:30:07.369Z"
    assert st.settlement_value == "1"
    assert st.settlement_source
    assert st.satisfied()[S.SETTLEMENT_VALUE] is True


def test_an_unsettled_reading_attaches_nothing():
    """Absence of evidence is not evidence. An endpoint that flaps must never
    be recorded as having UN-settled a contract."""
    st = c4()
    out = S.apply_resolution(st, {"verdict": "PENDING",
                                  "resolutionSide": None})
    assert out["applied"] is False
    assert st.settlement_value is None
    assert st.satisfied()[S.SETTLEMENT_VALUE] is False


def test_a_resolution_naming_no_side_is_refused():
    st = c4()
    for side in (None, "", "maybe", "YES-ish"):
        assert S.apply_resolution(
            st, {"verdict": "SETTLED", "resolutionSide": side}
        )["applied"] is False
    assert st.settlement_value is None


# ================================ THE INDEX PRICE IS NOT A PAYOUT (defect two)

def test_the_index_print_is_kept_but_never_used_as_a_payout():
    st = c4()
    S.apply_resolution(st, RESOLUTION)
    assert st.underlying_settlement_price == "80727.9451111111"
    assert st.settlement_value == "1", (
        "the payout is 1/0 from resolutionSide, NOT the index print")
    # the mistake this guards against, in numbers
    naive = st.position() * D(st.underlying_settlement_price)
    assert naive > D("1000"), naive
    assert abs(st.reconstructed()) < D("0.01"), (
        "a $0.01 position must not reconstruct to hundreds of dollars")


def test_a_no_sided_resolution_flips_the_yes_payout():
    st = c4()
    S.apply_resolution(st, dict(RESOLUTION, resolutionSide="no"))
    assert st.settlement_value == "0"
    assert st.payout() == D("0.01"), "the NO leg is the one that redeems"


# ========================= PER-LEG PAYOUT, NOT WHOLE-POSITION (defect three)

def test_each_leg_redeems_by_its_own_side():
    st = c4()
    S.apply_resolution(st, RESOLUTION)
    yes_leg, no_leg = st.legs
    assert yes_leg.payout_per_unit("yes") == D("1")
    assert no_leg.payout_per_unit("yes") == D("0")


def test_the_two_sided_position_pays_out_once_not_twice():
    """THE CONTINUOUS-4 NUMBER. Cost 0.0106, payout 0.0100, P&L -0.0006."""
    st = c4()
    S.apply_resolution(st, RESOLUTION)
    assert st.fills_cash() == D("-0.0106")
    assert st.payout() == D("0.0100")
    assert st.reconstructed() == D("-0.0006")


def test_the_old_whole_position_arithmetic_was_wrong_by_sign():
    """What the previous code would have said, kept as a standing warning."""
    st = c4()
    S.apply_resolution(st, RESOLUTION)
    old = st.fills_cash() + st.position() * D(st.settlement_value) - st.fees()
    assert old == D("0.0094"), old
    assert old > 0 and st.reconstructed() < 0, (
        "the defect inverted the sign of the result")


def test_a_leg_without_an_outcome_stops_the_reconstruction(tmp_path):
    """UNREADABLE, not assumed. A side we cannot name cannot be valued."""
    legs = c4_legs()
    legs[1].outcome = None
    st = S.Settlement(SYMBOL, legs=legs)
    S.apply_resolution(st, RESOLUTION)
    assert st.payout() is None
    assert st.reconstructed() is None


def test_a_one_sided_position_still_reconstructs():
    """The converse: the fix must not break the simple case it used to get
    right."""
    st = S.Settlement(SYMBOL, legs=[c4_legs()[0]])
    S.apply_resolution(st, RESOLUTION)
    assert st.fills_cash() == D("-0.0092")
    assert st.payout() == D("0.01")
    assert st.reconstructed() == D("0.0008")


# =================================================== SETTLED IS NOT REALIZED

def test_an_outcome_alone_does_not_make_anything_realized():
    """The whole point of the ladder. A resolution is the OUTCOME leg; cash is
    a separate fact and the UI's rounded $0.01 is not it."""
    st = c4()
    S.apply_resolution(st, RESOLUTION)
    assert st.satisfied()[S.SETTLEMENT_VALUE] is True
    assert st.satisfied()[S.CASH_CONFIRMED] is False
    with pytest.raises(S.NotSettled):
        st.realized()


def test_reconstructed_is_labelled_as_arithmetic_not_cash():
    st = c4()
    S.apply_resolution(st, RESOLUTION)
    rep = st.report()
    # compare as Decimal: D("0.01") == D("0.0100") but their strings differ,
    # and the value is what matters
    assert D(rep["reconstructed_cash"]) == D("-0.0006")
    assert D(rep["payout"]) == D("0.0100")
    assert rep["resolution_side"] == "yes"
    assert rep["resolved_at"] == "2026-09-18T17:30:07.369Z"
    assert rep["underlying_settlement_price"] == "80727.9451111111"
    assert rep["gate"] == "BLOCKED"
    assert S.CASH_CONFIRMED in st.missing()
    assert any("reconstructed_cash is arithmetic" in s
               for s in rep["labels"]), rep["labels"]
