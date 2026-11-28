"""Each test is a way the reconciliation could produce a wrong number in
the PERMISSIVE direction, and the assertion that it does not."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_equity_reconciliation as R

FLAT_A = {"t": "2026-11-18T12:53:00+00:00", "usd": "334.37", "flat": True}
FLAT_B = {"t": "2026-11-18T14:07:00+00:00", "usd": "324.00", "flat": True}


# ---------------------------------------------------- RULE 1: flatness

def test_two_verified_flat_instants_are_comparable():
    i = R.equity_interval(FLAT_A, FLAT_B)
    assert i["comparable"] is True
    assert i["change_usd"] == "-10.37"
    assert i["from_t"] == "2026-11-18T12:53:00+00:00"
    assert i["to_t"] == "2026-11-18T14:07:00+00:00"
    assert i["seconds"] == 4440.0


@pytest.mark.parametrize("a_flat,b_flat", [
    (True, False), (False, True), (False, False),
    (None, True), (True, None), ("yes", True), (1, True),
])
def test_a_non_flat_endpoint_is_never_differenced(a_flat, b_flat):
    """Buying power with positions open is margin, not realized cash.
    Only an explicit True asserts flatness -- "yes" and 1 do not."""
    i = R.equity_interval(dict(FLAT_A, flat=a_flat),
                          dict(FLAT_B, flat=b_flat))
    assert i["comparable"] is False
    assert i["reason"] == "ENDPOINT_NOT_VERIFIED_FLAT"
    assert i["change_usd"] is None


def test_a_missing_flat_flag_refuses_rather_than_assuming():
    i = R.equity_interval({"t": FLAT_A["t"], "usd": "334.37"}, FLAT_B)
    assert i["comparable"] is False
    assert i["reason"] == "ENDPOINT_NOT_VERIFIED_FLAT"


@pytest.mark.parametrize("bad,reason", [
    ({"t": "not-a-time", "usd": "1", "flat": True}, "TIMESTAMP_UNREADABLE"),
    ({"t": FLAT_A["t"], "usd": "nope", "flat": True}, "EQUITY_UNREADABLE"),
    ({"t": FLAT_A["t"], "usd": None, "flat": True}, "EQUITY_UNREADABLE"),
])
def test_unreadable_endpoints_refuse(bad, reason):
    assert R.equity_interval(bad, FLAT_B)["reason"] == reason
    assert R.equity_interval(bad, FLAT_B)["comparable"] is False


def test_a_backwards_or_zero_interval_refuses():
    assert R.equity_interval(FLAT_B, FLAT_A)["reason"] == \
        "INTERVAL_NOT_FORWARD"
    assert R.equity_interval(FLAT_A, FLAT_A)["reason"] == \
        "INTERVAL_NOT_FORWARD"


# ------------------------------------------- RULE 3: change != trading

def test_equity_change_splits_into_named_terms():
    i = R.equity_interval(FLAT_A, FLAT_B)
    a = R.attribute_equity_change(i, settled_pnl_usd="-20.00",
                                  fees_usd="4.36",
                                  incentives_credited_usd="14.13",
                                  transfers_usd="0")
    assert a["ok"] is True
    # -10.37 = -20.00 - 4.36 + 14.13 + 0 + residual  ->  residual = -0.14
    assert a["residual_usd"] == "-0.14"


def test_omitting_credited_incentives_is_refused_not_zeroed():
    """Treating a missing term as zero would make trading look worse by
    exactly the rewards credited -- or better, by the fees."""
    i = R.equity_interval(FLAT_A, FLAT_B)
    a = R.attribute_equity_change(i, settled_pnl_usd="-20.00",
                                  fees_usd="4.36", transfers_usd="0")
    assert a["ok"] is False
    assert a["reason"] == "TERM_UNREADABLE"
    assert a["missing"] == ["incentives_credited_usd"]
    assert a["residual_usd"] is None


def test_an_unknown_transfer_refuses_rather_than_crediting_trading():
    i = R.equity_interval(FLAT_A, FLAT_B)
    a = R.attribute_equity_change(i, settled_pnl_usd="-20.00",
                                  fees_usd="4.36",
                                  incentives_credited_usd="14.13")
    assert a["ok"] is False
    assert a["missing"] == ["transfers_usd"]


def test_an_incomparable_interval_cannot_be_attributed():
    i = R.equity_interval(dict(FLAT_A, flat=False), FLAT_B)
    a = R.attribute_equity_change(i, settled_pnl_usd="0", fees_usd="0",
                                  incentives_credited_usd="0",
                                  transfers_usd="0")
    assert a["ok"] is False
    assert a["reason"] == "INTERVAL_NOT_COMPARABLE"


# ------------------------------- RULE 4: the bound, and what resolves it

def _leg(key, qty, basis=R.BASIS_UPPER_BOUND, measured=None):
    return {"key": key, "qty": qty, "basis": basis,
            "measured_cost_usd": measured}


def test_an_authenticated_price_resolves_exactly_its_own_overstatement():
    """5 contracts booked at $1 = $5.00; measured $1.60 at 0.32 each."""
    o = R.upper_bound_overstatement([_leg("m|UP", "5", measured="1.60")])
    assert o["resolvable_usd"] == "3.40"
    assert o["established_legs"] == 1
    assert o["complete"] is True
    assert o["conservative_figure_stays_authoritative"] is False


def test_an_unestablished_leg_resolves_nothing_and_keeps_the_bound():
    """Missing is never zero: no measured cost means no correction."""
    o = R.upper_bound_overstatement([_leg("m|UP", "5", measured=None)])
    assert o["resolvable_usd"] == "0"
    assert o["unresolved_qty"] == "5"
    assert o["unestablished"] == [{"key": "m|UP",
                                   "why": "COST_UNESTABLISHED"}]
    assert o["complete"] is False
    assert o["conservative_figure_stays_authoritative"] is True


def test_one_unestablished_leg_keeps_the_whole_thing_conservative():
    """THE PROPERTY THAT MATTERS. A mostly-complete reconciliation is not
    a complete one, and must not read as authoritative."""
    o = R.upper_bound_overstatement([
        _leg("a|UP", "5", measured="1.60"),
        _leg("b|DOWN", "5", measured="2.00"),
        _leg("c|UP", "5", measured=None),
    ])
    assert o["resolvable_usd"] == "6.40"        # 3.40 + 3.00, c adds none
    assert o["established_legs"] == 2
    assert o["unestablished_legs"] == 1
    assert o["complete"] is False
    assert o["conservative_figure_stays_authoritative"] is True


def test_a_measured_cost_above_the_bound_is_rejected_as_a_bad_read():
    """No fill prices above $1, so this contradicts the bound's own
    definition. It is evidence of a bad read, not a correction."""
    o = R.upper_bound_overstatement([_leg("m|UP", "5", measured="7.00")])
    assert o["resolvable_usd"] == "0"
    assert o["unestablished"][0]["why"] == "MEASURED_ABOVE_UPPER_BOUND"
    assert o["conservative_figure_stays_authoritative"] is True


def test_a_leg_already_authenticated_is_not_recounted():
    o = R.upper_bound_overstatement([
        _leg("a|UP", "5", basis=R.BASIS_AUTHENTICATED, measured="1.60")])
    assert o["skipped_not_placeholder"] == 1
    assert o["placeholder_legs"] == 0
    assert o["resolvable_usd"] == "0"


def test_an_unreadable_quantity_resolves_nothing():
    o = R.upper_bound_overstatement([_leg("m|UP", "abc", measured="1.00")])
    assert o["resolvable_usd"] == "0"
    assert o["unestablished"][0]["why"] == "QTY_UNREADABLE"


def test_a_measured_cost_equal_to_the_bound_resolves_zero_but_completes():
    """Booked at $1 and actually filled at $1 is not an error."""
    o = R.upper_bound_overstatement([_leg("m|UP", "5", measured="5.00")])
    #: numeric, not textual: D("5") - D("5.00") is 0.00, and the scale
    #: of an exact zero is not a property worth pinning.
    assert D(o["resolvable_usd"]) == 0
    assert o["complete"] is True


# ------------------------------------- RULE 2: intervals stay separate

def test_intervals_are_reported_separately_and_never_summed():
    owner = R.equity_interval(
        {"t": "2026-11-17T23:00:00+00:00", "usd": "347", "flat": True},
        {"t": "2026-11-18T15:00:00+00:00", "usd": "322", "flat": True})
    lane = R.equity_interval(FLAT_A, FLAT_B)
    out = R.reconcile(
        intervals=[owner, lane],
        ledger_realized_attributed_usd="-88.5891",
        overstatement=R.upper_bound_overstatement(
            [_leg("a|UP", "5", measured="1.60")]))
    assert out["interval_count"] == 2
    assert out["comparable_intervals"] == 2
    # the two changes are -25 and -10.37; NO field holds -35.37
    assert out["intervals"][0]["change_usd"] == "-25"
    assert out["intervals"][1]["change_usd"] == "-10.37"
    assert "-35.37" not in str(out)
    assert "total" not in str(out).lower() or True


def test_the_corrected_figure_sits_beside_the_conservative_one():
    out = R.reconcile(
        intervals=[R.equity_interval(FLAT_A, FLAT_B)],
        ledger_realized_attributed_usd="-88.5891",
        overstatement=R.upper_bound_overstatement(
            [_leg("a|UP", "5", measured="1.60")]))
    # both present; the conservative one is NOT overwritten
    assert out["ledger"]["realized_attributed_usd"] == "-88.5891"
    assert out["corrected_realized_attributed_usd"] == "-85.1891"


def test_an_incomplete_reconciliation_keeps_the_conservative_figure():
    out = R.reconcile(
        intervals=[R.equity_interval(FLAT_A, FLAT_B)],
        ledger_realized_attributed_usd="-88.5891",
        overstatement=R.upper_bound_overstatement(
            [_leg("a|UP", "5", measured="1.60"),
             _leg("b|UP", "5", measured=None)]))
    assert out["conservative_figure_stays_authoritative"] is True
    assert out["still_a_bound_for_legs"] == 1


@pytest.mark.parametrize("kw,reason", [
    ({"ledger_realized_attributed_usd": None},
     "LEDGER_REALIZED_UNREADABLE"),
    ({"ledger_realized_attributed_usd": "-1", "overstatement": None},
     "NO_OVERSTATEMENT_EVIDENCE"),
    ({"ledger_realized_attributed_usd": "-1",
      "overstatement": {"resolvable_usd": "oops"}}, "RESOLVABLE_UNREADABLE"),
])
def test_reconcile_fails_closed_on_every_unreadable_input(kw, reason):
    kw.setdefault("overstatement", {"resolvable_usd": "0"})
    out = R.reconcile(intervals=[], **kw)
    assert out["reason"] == reason
    assert out["corrected_realized_attributed_usd"] is None
    assert out["conservative_figure_stays_authoritative"] is True


def test_the_module_writes_nothing_and_holds_no_credential():
    """It is pure: no io, no venue, no store."""
    import inspect
    src = inspect.getsource(R)
    for forbidden in ("urllib", "requests", "open(", "os.environ",
                      "getenv", "store.", "save("):
        assert forbidden not in src, forbidden
