"""A fill's economics have to be computed correctly at the fill, because that
is the only moment the complement's price is still the one we could have paid.
"""
import os
import sys
from decimal import Decimal as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.gemini_execution import continuous_probe as CP        # noqa: E402
from src.gemini_execution import fill_quality as FQ            # noqa: E402

BOOK = {"yes": {"bid": "0.90", "ask": "0.96"},
        "no": {"bid": "0.04", "ask": "0.10"}}


def _inv(*fills):
    inv = CP.Inventory()
    for leg, q, p in fills:
        inv.apply_fill(leg, q, p, source="TEST")
    return inv


def test_the_live3_fill_is_now_fully_described():
    """LIVE-3 logged 'FILL NO 1 @ 0.02' and nothing about completability."""
    r = FQ.quality(_inv(("NO", 1, "0.02")), BOOK, tte_s=700.0,
                   next_action="COMPLETE")
    assert r["needed_leg"] == "YES"
    assert r["executable_complement_cost"] == "0.96"
    assert r["projected_pair_cost_if_completed_now"] == "0.98"
    assert r["pair_locked_below_1"] is True
    assert r["residual_dollars_at_risk"] == "0.02"
    assert r["matched_qty"] == "0"


def test_complement_cost_is_the_ask_never_the_mid_or_bid():
    """A mid would flatter every pair by half a spread."""
    inv = _inv(("NO", 1, "0.02"))
    assert FQ.executable_complement_cost(BOOK, "YES") == D("0.96")
    assert FQ.executable_complement_cost(BOOK, "NO") == D("0.10")
    r = FQ.quality(inv, BOOK)
    assert r["executable_complement_cost"] != "0.93"      # the mid
    assert r["executable_complement_cost"] != "0.90"      # the bid


def test_a_pair_that_cannot_be_completed_below_one_says_so():
    r = FQ.quality(_inv(("NO", 1, "0.30")), BOOK)
    assert r["projected_pair_cost_if_completed_now"] == "1.26"
    assert r["pair_locked_below_1"] is False


def test_residual_risk_is_basis_not_face_value():
    """One NO at 0.02 risks two cents, not a dollar."""
    assert FQ.residual_dollars_at_risk(_inv(("NO", 1, "0.02"))) == D("0.02")
    assert FQ.residual_dollars_at_risk(_inv(("YES", 2, "0.40"))) == D("0.80")


def test_flat_inventory_has_no_needed_leg_and_no_risk():
    inv = _inv(("YES", 1, "0.40"), ("NO", 1, "0.55"))
    assert FQ.needed_leg(inv) is None
    assert FQ.residual_dollars_at_risk(inv) == D("0")
    r = FQ.quality(inv, BOOK)
    assert r["executable_complement_cost"] is None
    assert r["effective_matched_pair_cost"] == "0.95"
    assert r["matched_qty"] == "1"


def test_no_rebate_field_anywhere_in_the_record():
    """A trade is acceptable pre-rebate or it is not acceptable."""
    r = FQ.quality(_inv(("NO", 1, "0.02")), BOOK)
    blob = " ".join(str(k) + str(v) for k, v in r.items()).lower()
    assert "rebate" not in blob.replace("pre-rebate", "").replace(
        "no rebate is netted", "")


def test_lifecycle_stages_are_ordered_and_realized_is_absent():
    assert FQ.LIFECYCLE == ("SUBMITTED", "FILLED", "SETTLED",
                            "CASH_RECONCILED")
    assert "REALIZED" not in FQ.LIFECYCLE
    try:
        FQ.quality(_inv(("NO", 1, "0.02")), BOOK, stage="REALIZED")
    except ValueError:
        return
    raise AssertionError("REALIZED was accepted as a stage")


def test_fee_is_unreconciled_until_the_venue_says_otherwise():
    r = FQ.quality(_inv(("NO", 1, "0.02")), BOOK)
    assert r["fee_paid_this_fill"] is None
    assert r["fee_source"] == "NOT YET RECONCILED"
    r2 = FQ.quality(_inv(("NO", 1, "0.02")), BOOK, fee_paid="0.0000")
    assert r2["fee_paid_this_fill"] == "0.0000"
    assert "mytrades" in r2["fee_source"]


# ---- the CAP counterfactual ----------------------------------------------

def test_cap_record_is_written_whether_or_not_it_binds():
    """The denominator has to be visible or the zero means nothing again."""
    r = FQ.cap_counterfactual(_inv(("NO", 1, "0.02")), "NO", "0.02",
                              action_size=1)
    assert r["would_envelope_change_it"] is False
    assert r["desired_action"] == "EXTEND"
    assert r["headroom_units"] == "3"


def test_cap_record_names_the_bound_when_it_would_bind():
    inv = _inv(*[("NO", 1, "0.02")] * 4)
    r = FQ.cap_counterfactual(inv, "NO", "0.02", action_size=1)
    assert r["would_envelope_change_it"] is True
    assert r["which_bound"] == "MAX_IMBALANCE"
    assert r["desired_action"] == "EXTEND"
    assert r["headroom_units"] == "0"


def test_cap_record_changes_no_decision():
    """It is evidence. It must not be reachable as a gate."""
    import inspect
    src = inspect.getsource(FQ.cap_counterfactual)
    for verb in ("raise", "return None", "veto", "skip"):
        assert verb not in src.split('"""')[2], verb


def test_the_record_distinguishes_internal_bounds_from_authorized_dollars():
    """At q=1 the controller's own spend bound reads 15; the authorized
    ceiling is $4.00 and binds far tighter."""
    r = FQ.cap_counterfactual(_inv(("NO", 1, "0.02")), "NO", "0.02",
                              action_size=1)
    assert r["max_total_spend"] == "15"
    assert "live_envelope" in r["bounds_are"]
    assert "$4.00" in r["bounds_are"]


def test_module_places_no_order_and_reads_no_credential():
    import inspect
    src = inspect.getsource(FQ)
    for bad in ("place_order", "order.place", "urlopen", "api_key",
                "GEMINI_CANARY", "arming"):
        assert bad not in src, bad


# ---- the settlement-aware result, beside the stop ------------------------

def test_the_ep016_cohort_reconciles_to_its_recorded_plus_1_14():
    r = FQ.settlement_economics(buy_spend="6.86", settlement_payout="8.00",
                                fees_actual="0", rebates_paid="0",
                                cash_out_stop_measure="10.63")
    assert r["net_pre_rebate_usd"] == "1.14"
    assert r["cash_out_stop_measure_usd"] == "10.63"


def test_rebate_never_enters_the_selection_basis():
    r = FQ.settlement_economics(buy_spend="1.00", settlement_payout="0.90",
                                rebates_paid="0.50")
    assert r["net_pre_rebate_usd"] == "-0.10"      # the configuration loses
    assert r["net_with_rebate_usd"] == "0.40"      # the rebate would mask it
    assert r["selection_basis"] == "net_pre_rebate_usd"


def test_fees_reduce_the_result_and_are_not_assumed_zero():
    a = FQ.settlement_economics(buy_spend="1.00", settlement_payout="1.20")
    b = FQ.settlement_economics(buy_spend="1.00", settlement_payout="1.20",
                                fees_actual="0.07")
    assert a["net_pre_rebate_usd"] == "0.20"
    assert b["net_pre_rebate_usd"] == "0.13"


def test_sell_proceeds_are_carried_even_though_this_venue_shows_none():
    r = FQ.settlement_economics(buy_spend="1.00", settlement_payout="0.00",
                                sell_proceeds="0.95")
    assert r["net_pre_rebate_usd"] == "-0.05"
