"""The classifier must never turn our own behaviour into a market fact."""
from decimal import Decimal as D

import pytest

from src import l5_complete_conversion as cc

LAT = 3.19          # median observed fill latency of the live corpus


def order(**kw):
    base = {"order_id": "X", "px": "0.50", "qty": "2.0", "filled_qty": "0",
            "rest_s": "10.0", "l5_touch_px": "0.50", "end_reason": "deadline",
            "rest_book": {"min_opp_ask": "0.60", "max_touch": "0.50",
                          "polls": 8},
            "recorder_book": {"yes_bid": "0.50", "yes_ask": "0.60"}}
    base.update(kw)
    return base


def feat(**kw):
    return cc.attempt_features(order(**kw), D("0.01"))


# ---------- tick

def test_a_declared_tick_always_wins():
    assert cc.infer_tick(["0.005"], ["0.50"]) == (D("0.005"),
                                                  "declared_by_venue")


def test_a_half_cent_price_proves_the_fine_grid():
    t, why = cc.infer_tick([], ["0.50", "0.535"])
    assert (t, why) == (D("0.005"), "proved_by_a_half_cent_price")


def test_seeing_only_whole_cents_is_an_assumption_and_says_so():
    t, why = cc.infer_tick([], ["0.50", "0.61"])
    assert t == D("0.01")
    assert "assumed" in why


def test_an_inconsistent_declaration_refuses_to_guess():
    assert cc.infer_tick(["0.01", "0.005"], ["0.50"])[0] is None


def test_distance_is_none_not_zero_when_the_tick_is_unknown():
    assert cc.ticks_between("0.55", "0.50", None) is None


# ---------- the mode that must not be over-claimed

def test_a_row_priced_behind_its_own_touch_is_not_the_market_moving_past_us():
    """We posted below the touch on purpose; max_touch > px is the state we
    created, not a move the market made."""
    f = feat(px="0.40", l5_touch_px="0.50", end_reason="repriced:touch_moved",
             rest_s="2.0", rest_book={"min_opp_ask": "0.60",
                                      "max_touch": "0.50", "polls": 1})
    v = cc.classify(f, LAT)
    assert v["mode"] == cc.M3_CANCELLED_EARLY
    assert cc.M6_MOVED_THROUGH not in v["co_occurring"]
    assert cc.PRICED_BEHIND_THE_TOUCH_AT_POST in v["co_occurring"]


def test_the_touch_rising_after_we_posted_at_it_IS_the_market_moving():
    f = feat(px="0.50", l5_touch_px="0.50",
             rest_book={"min_opp_ask": "0.60", "max_touch": "0.55",
                        "polls": 6})
    assert cc.classify(f, LAT)["mode"] == cc.M6_MOVED_THROUGH


# ---------- our own cancel may never become evidence about the book

def test_a_cancel_inside_our_own_fill_latency_is_labelled_as_our_cancel():
    f = feat(end_reason="repriced:touch_moved:0.55", rest_s="2.0",
             rest_book={"min_opp_ask": "0.90", "max_touch": "0.50",
                        "polls": 1})
    assert cc.classify(f, LAT)["mode"] == cc.M3_CANCELLED_EARLY


def test_never_reached_needs_both_time_and_more_than_one_poll():
    """One poll is a snapshot. It can say what was true at an instant and
    can never say what was true over a window."""
    one = feat(rest_s="30.0", rest_book={"min_opp_ask": "0.90",
                                         "max_touch": "0.50", "polls": 1},
               end_reason="deadline")
    assert cc.classify(one, LAT)["mode"] != cc.M1_NEVER_REACHED
    many = feat(rest_s="30.0", rest_book={"min_opp_ask": "0.90",
                                          "max_touch": "0.50", "polls": 9},
                end_reason="deadline")
    assert cc.classify(many, LAT)["mode"] == cc.M1_NEVER_REACHED


# ---------- queue is never inferred

def test_reaching_our_price_without_a_fill_is_reported_without_claiming_queue():
    f = feat(rest_book={"min_opp_ask": "0.50", "max_touch": "0.50",
                        "polls": 5})
    v = cc.classify(f, LAT)
    assert v["mode"] == cc.M2_REACHED_NO_FILL
    assert "UNKNOWN" in v["why"]
    assert "QUEUE" in v["why"].upper()


def test_the_mode_name_itself_carries_the_queue_caveat():
    assert "QUEUE_UNKNOWN" in cc.M2_REACHED_NO_FILL


def test_l5_process_marks_everything_public_fills_cannot_establish():
    out = cc.l5_switch_process([
        {"t": 0, "outcome": "Up", "price": "0.5", "quantity": "10"},
        {"t": 4, "outcome": "Down", "price": "0.6", "quantity": "12"}])
    assert out["seconds_to_opposite_side_fill"] == [4.0]
    for k in ("queue_position", "cancel_and_reprice_behaviour",
              "resting_quotes"):
        assert out[k].startswith("UNKNOWN")


# ---------- size

def test_depth_alone_never_supports_the_size_mode_for_a_maker_order():
    """Far-side resting depth bounds a TAKER execution. A maker order is
    filled by arriving flow, which depth does not measure."""
    f = feat(obs_executable_depth="0.5", obs_executable_depth_status="OBSERVED",
             qty="99")
    assert cc.classify(f, LAT)["mode"] != cc.M4_SIZE_EXCEEDS_FLOW


def test_a_partial_fill_is_what_evidences_the_size_mode():
    f = feat(qty="10", filled_qty="3",
             rest_book={"min_opp_ask": "0.90", "max_touch": "0.50",
                        "polls": 5})
    assert cc.classify(f, LAT)["mode"] == cc.M4_SIZE_EXCEEDS_FLOW


# ---------- unknown is a verdict, not a bucket

def test_a_row_with_no_rest_book_is_unknown_not_never_reached():
    o = order()
    o.pop("rest_book")
    v = cc.classify(cc.attempt_features(o, D("0.01")), LAT)
    assert v["mode"] == cc.M7_UNKNOWN


def test_a_rejection_is_its_own_outcome_and_not_one_of_the_seven():
    f = feat(end_reason="rejected:post_only")
    v = cc.classify(f, LAT)
    assert v["mode"] == cc.REJECTED
    assert v["mode"] not in cc.FAILURE_MODES


def test_a_full_fill_is_not_a_failure_mode():
    assert cc.classify(feat(filled_qty="2.0"), LAT)["mode"] is None


# ---------- the sensitivity may not complete inventory we never held

def test_extra_matched_contracts_are_capped_by_the_residual_that_existed():
    arms = cc.sensitivity(
        unfilled_attempts=244, mean_qty=D("1.5"),
        marginal_pair_cost=D("0.9952"),
        maker_rebate_per_contract=D("0.0031"),
        residual_pnl_usd=D("-1.34"), residual_contracts_available=D("32.77"),
        observed_fill_rate=D("0.0615"),
        targets=[D("0.50"), D("1.00")])
    for a in arms:
        assert D(a["extra_matched_contracts_capped_by_residual"]) <= D("32.77")
        assert a["saturates_the_residual"] is True
    # once saturated, a higher rate adds nothing
    assert (arms[0]["matched_margin_usd"] == arms[1]["matched_margin_usd"])


def test_the_sensitivity_carries_the_rebate_separately_from_the_margin():
    a = cc.sensitivity(244, D("1.5"), D("0.99"), D("0.0031"), D("-1.0"),
                       D("30"), D("0.06"), [D("1.00")])[0]
    assert "maker_rebate_usd_accrual_only" in a
    assert a["matched_margin_usd"] != a["maker_rebate_usd_accrual_only"]


def test_the_pair_cost_axis_refuses_a_loss_no_pair_cost_can_cover():
    assert cc.pair_cost_needed(D("10"), D("50")) is None
    assert cc.pair_cost_needed(D("32.77"), D("1.3408")) == D("0.9591")


def test_it_reads_and_never_acts():
    """Scans CODE, not prose: a docstring that describes what the module
    refuses to do would otherwise fail its own guarantee."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cc))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""
    code = ast.unparse(tree)
    for tok in ("place_limit", "submit", "cancel_order", "post_only=False",
                "requests.", "httpx", "os.environ", "open("):
        assert tok not in code, tok
