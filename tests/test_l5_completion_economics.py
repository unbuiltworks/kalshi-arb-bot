"""Completion is the profitability lever, so its denominator must be right."""
from __future__ import annotations

import json

from src import l5_completion_economics as ce


def _o(rel, outcome, oid, rest="30.0", px="0.60", opp=None):
    o = {"order_id": oid, "stage": f"spring_{rel}_1", "relation": rel,
         "px": px, "qty": "1", "rest_s": rest,
         "filled_qty": "1" if outcome == "filled" else "0",
         "end_reason": ("rejected:ORDER_REJECTED_ZERO_FILL"
                        if outcome == "rejected" else "cancelled")}
    if opp is not None:
        o["fresh_opp_ask"] = opp
    return o


def test_the_denominator_is_every_order_not_the_failures():
    """THE ERROR THIS PREVENTS. Reading the composition of rejections as the
    composition of completion failures said rejection was the problem; on
    all orders, no-fill dominates it."""
    orders = ([_o("complete", "no_fill_cancelled", f"n{i}") for i in range(7)]
              + [_o("complete", "rejected", f"r{i}") for i in range(2)]
              + [_o("complete", "filled", "f1")])
    r = ce.outcomes_by_relation(orders)["complete"]
    assert r["orders"] == 10
    assert r["no_fill_share"] == 0.7 and r["rejected_share"] == 0.2
    # the failure-conditioned view would say rejection is 2/2 of "explained"
    assert r["no_fill_cancelled"] > r["rejected"]


def test_rejections_are_excluded_from_the_resting_profile():
    """A rejected order never rested, so its rest_s is not evidence about
    how long we wait for a fill."""
    orders = [_o("complete", "rejected", "r1", rest="0.1"),
              _o("complete", "filled", "f1", rest="8.6"),
              _o("complete", "no_fill_cancelled", "n1", rest="30.8")]
    p = ce.fill_profile(orders, "complete")
    assert p["orders"] == 2
    assert p["filled"]["median_rest_s"] == 8.6
    assert p["no_fill_cancelled"]["median_rest_s"] == 30.8


def test_distance_from_touch_refuses_the_biased_sample():
    """Before the control snapshot, fresh_opp_ask exists only on
    rejections. Answering from those would reintroduce exactly the
    selection bias this module exists to avoid."""
    orders = [_o("complete", "rejected", "r1", opp="0.70"),
              _o("complete", "no_fill_cancelled", "n1")]
    d = ce.distance_from_touch(orders, "complete")
    assert d["available"] is False
    assert "selection bias" in d["reason"]
    assert d["orders_with_a_touch"] == 0


def test_distance_from_touch_works_once_controls_exist():
    orders = [_o("complete", "filled", "f1", px="0.69", opp="0.70"),
              _o("complete", "no_fill_cancelled", "n1", px="0.60", opp="0.70"),
              _o("complete", "no_fill_cancelled", "n2", px="0.60", opp="0.70")]
    d = ce.distance_from_touch(orders, "complete")
    assert d["available"] is True
    t = d["fill_rate_by_ticks_inside_the_opposite_ask"]
    assert t[1]["fill_rate"] == 1.0        # one tick inside: filled
    assert t[10]["fill_rate"] == 0.0       # ten ticks away: never filled


def test_orders_are_deduplicated_across_repeated_harvests(tmp_path):
    """The same session_end row appears in overlapping log pulls; counting
    it twice would inflate every denominator here."""
    msg = "PMUS_L5_CAMPAIGN " + json.dumps(
        {"kind": "session_end", "slug": "s1",
         "orders": [_o("complete", "filled", "f1")]})
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"deploymentId": "d", "deploy": [
        {"message": msg}, {"message": msg}]}))
    assert len(ce.all_orders([str(p)])) == 1


def test_it_reads_and_never_acts():
    """Completion pricing and the rest window are live money parameters."""
    import inspect
    src = inspect.getsource(ce)
    body = src.split('"""', 2)[-1]
    for forbidden in ("place_limit_buy", "session_runner", "run_campaign(",
                      "os.environ[", "requests."):
        assert forbidden not in body, forbidden
