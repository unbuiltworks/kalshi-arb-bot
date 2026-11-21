"""The rules a desire for fills would erode, tested first.

Probe 06 executed 6 of 52 L5 actions and refused 43 STRICT_NOT_EVALUABLE.
Every one of the 6 was a TAKER and every one of the 43 was a MAKER, so the
clone currently reproduces only L5's aggressive behaviour -- the opposite of
the liquidity provider it is supposed to be replicating.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

import src.l5_maker_execution as M
from src.l5_live_execution import floor_to_increment

LAM = D("0.04518752824220514525")


def passive(price="0.42", size="30", **over):
    rec = {"action_key": "a1",
           "L5_NATIVE": {"side": "UP", "price": price, "l5_size": size},
           "PMUS_TRANSLATION": {"mode": "MAKER", "price": None},
           "refused_by_layer": "PMUS_TRANSLATION",
           "refused_reason": "STRICT_NOT_EVALUABLE"}
    rec.update(over)
    return rec


# ------------------------------------------------------ which actions

def test_the_passive_refusal_is_the_one_we_consume():
    assert M.is_passive_refusal(passive()) is True


def test_a_taker_action_is_not_a_maker_candidate():
    assert M.is_passive_refusal(passive(
        PMUS_TRANSLATION={"mode": "TAKER"})) is False


@pytest.mark.parametrize("reason", ["STALE_ACTION:12.0", "NO_BOOK",
                                    "MAX_LIVE_ORDERS", "NO_DEPTH", None])
def test_only_the_unevaluable_refusal_qualifies(reason):
    # a stale action, a missing book or an overlay refusal are NOT
    # "we could rest here but replay cannot prove it"
    assert M.is_passive_refusal(passive(refused_reason=reason)) is False


def test_an_executed_action_is_not_reconsidered():
    assert M.is_passive_refusal(
        {"PMUS_TRANSLATION": {"mode": "MAKER"}, "refused_reason": None}
    ) is False


# ------------------------------------------------------------- P1, P3

def test_the_intent_rests_at_l5s_own_price_not_our_touch():
    i = M.maker_intent(passive(price="0.42"), lam=LAM)
    assert i["price"] == "0.42"
    assert i["mode"] == "MAKER"
    assert i["state"] == M.INTENT


def test_the_intent_never_improves_on_l5():
    # one tick better would be a different policy wearing the same name
    for px in ("0.01", "0.42", "0.99"):
        assert M.maker_intent(passive(price=px), lam=LAM)["price"] == px


def test_the_intent_is_post_only():
    assert M.maker_intent(passive(), lam=LAM)["post_only"] is True


def test_size_is_lambda_scaled_and_floored_down():
    i = M.maker_intent(passive(size="30"), lam=LAM,
                       quantity_increment=D("0.01"),
                       floor_fn=floor_to_increment)
    assert i["qty_desired"] == str(D("30") * LAM)
    assert D(i["qty"]) == D("1.35")
    assert D(i["quantization_delta"]) > 0
    assert D(i["qty"]) <= D(i["qty_desired"])


def test_a_size_that_floors_to_nothing_is_not_placeable():
    i = M.maker_intent(passive(size="0.1"), lam=LAM,
                       quantity_increment=D("0.01"),
                       floor_fn=floor_to_increment)
    assert i["placeable"] is False


def test_an_unreadable_action_yields_no_intent():
    assert M.maker_intent(passive(price=None), lam=LAM) is None
    assert M.maker_intent({"refused_reason": "STRICT_NOT_EVALUABLE"},
                          lam=LAM) is None


# -------------------------------------------------- the ladder, in order

def order(now=1000.0, touch="0.50", **kw):
    return M.MakerOrder(M.maker_intent(passive(**kw), lam=LAM),
                        now=now, touch_at_placement=D(touch))


def test_a_placed_order_has_not_filled_anything():
    o = order()
    assert o.state == M.PLACED
    assert o.cum_qty == D(0)


def test_the_touch_reaching_our_price_is_executability_not_a_fill():
    o = order(touch="0.50")
    o.observe_touch(D("0.42"))
    assert o.book_reached is True
    assert o.state == M.PLACED          # still not filled
    assert o.cum_qty == D(0)


def test_a_print_through_our_price_is_flow_not_a_fill():
    o = order()
    o.observe_print(D("0.41"))
    assert o.trade_flow is True
    assert o.state == M.PLACED
    assert o.cum_qty == D(0)


def test_only_the_venue_moves_inventory():
    o = order()
    o.observe_touch(D("0.40"))
    o.observe_print(D("0.40"))
    assert o.cum_qty == D(0)
    o.apply_progress({"cum_qty": "1.35", "leaves": "0", "terminal": True,
                      "avg_px": "0.42", "commission": "0",
                      "liquidity": "MAKER"}, now=1005.0)
    assert o.state == M.FILLED
    assert o.cum_qty == D("1.35")
    assert o.liquidity == "MAKER"


def test_a_partial_stays_partial():
    o = order()
    o.apply_progress({"cum_qty": "0.5", "leaves": "0.85", "terminal": False},
                     now=1002.0)
    assert o.state == M.PARTIAL
    assert o.leaves_qty == D("0.85")


def test_a_terminal_order_with_no_fill_is_cancelled_not_filled():
    o = order()
    o.apply_progress({"cum_qty": "0", "leaves": "0", "terminal": True},
                     now=1003.0)
    assert o.state == M.CANCELLED


def test_rest_duration_is_recorded_at_both_ends():
    o = order(now=1000.0)
    o.apply_progress({"cum_qty": "1.35", "terminal": True}, now=1042.5)
    assert o.rest_seconds() == 42.5


# --------------------------------------------------------- L2, the rule

def test_an_unfilled_maker_order_never_earns_a_cross():
    o = order()
    o.observe_touch(D("0.40"))       # the book came to us and left again
    assert M.never_cross(o, None) is False
    assert M.never_cross(o, "MAKER") is False


def test_only_a_new_taker_action_permits_aggression():
    assert M.never_cross(order(), "TAKER") is True


# ------------------------------------------------- L3 and L4, and no timer

def test_there_is_no_impatience_timer():
    o = order(now=1000.0)
    # a long rest, far from the deadline, with L5 unmoved: keep resting
    assert o.should_cancel(now=1600.0, deadline=9999.0, guard_s=60.0) is None


def test_l5s_own_reversal_cancels():
    o = order()
    assert o.should_cancel(now=1001.0, deadline=9999.0, guard_s=60.0,
                           l5_reversed=True) == M.CANCEL_L5_REVERSAL


def test_the_window_guard_cancels():
    o = order()
    assert o.should_cancel(now=9950.0, deadline=9999.0,
                           guard_s=60.0) == M.CANCEL_WINDOW_GUARD


def test_a_terminal_order_is_never_cancelled():
    o = order()
    o.apply_progress({"cum_qty": "1.35", "terminal": True}, now=1005.0)
    assert o.should_cancel(now=9990.0, deadline=9999.0, guard_s=60.0,
                           l5_reversed=True) is None


# ------------------------------------------------------- the evidence

def test_every_required_field_is_preserved_separately():
    o = order()
    o.venue_order_id = "CQZ1"
    o.observe_touch(D("0.42"))
    o.observe_print(D("0.42"))
    o.apply_progress({"cum_qty": "1.35", "leaves": "0", "terminal": True,
                      "avg_px": "0.42", "commission": "0.01",
                      "liquidity": "MAKER"}, now=1030.0)
    d = o.as_dict()
    for field in ("l5_native", "translation", "rest_seconds",
                  "touch_at_placement", "best_touch_while_resting",
                  "book_reached", "trade_flow", "cum_qty", "avg_px",
                  "commission", "liquidity_at_fill", "venue_order_id"):
        assert field in d, field
    assert d["l5_native"]["price"] == "0.42"
    assert d["translation"]["post_only"] is True
    assert d["liquidity_at_fill"] == "MAKER"


# ------------------------------------------------------- obstruction

def test_obstruction_counts_what_the_envelope_prevented():
    ob = M.Obstruction()
    ob.record(None)
    ob.record("MAX_LIVE_ORDERS")
    ob.record("MAX_LIVE_ORDERS")
    ob.record("MAX_USD_PER_MARKET")
    ob.record("BAND")
    d = ob.as_dict()
    assert d["maker_intents"] == 5
    assert d["maker_placed"] == 1
    assert d["maker_blocked_by_one_live"] == 2
    assert d["maker_blocked_by_envelope"] == 1
    assert d["maker_blocked_other"] == {"BAND": 1}


# ============ the treatment driving the REAL order path, end to end ========

def _maker_row():
    """An L5 action strictly inside our touch: restable, hence unevaluable."""
    import tests.test_l5_live_execution as L
    # L5 at 0.42 with our ask at 0.60 -> restable, so the frozen rule calls it
    # MAKER and refuses STRICT_NOT_EVALUABLE for want of interaction evidence
    return L.row(l5px="0.42", ask="0.60", bid="0.30")


def _drive(tmp_path, **flags):
    import tests.test_l5_quantity_quantization as Q
    import tests.test_l5_live_execution as L
    import src.l5_live_execution as EX
    from src import l5_probe_manifest as PM
    tp = Q.GridTransport()
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    ex = EX.FaithfulExecutor(store, PM.probe_limits(), transport=tp,
                             lam=PM.LAMBDA, now_fn=L.NOW, **flags)
    rec = ex.handle(_maker_row(), {}, market=Q.MARKET, window="0215z")
    return rec, tp, store


def test_without_the_treatment_a_passive_action_is_still_refused(tmp_path):
    rec, tp, _ = _drive(tmp_path)
    assert rec["refused_reason"] == "STRICT_NOT_EVALUABLE"
    assert tp.sent == []


def test_with_the_treatment_it_reaches_the_order_post(tmp_path):
    rec, tp, store = _drive(tmp_path, maker_treatment=True)
    assert rec["refused_reason"] is None, rec["refused_reason"]
    assert rec["ORDER"]["submitted"] is True
    assert tp.paths == ["/v1/order/preview", "/v1/orders"], "preview first"
    assert store.live_order_count() == 1


def test_the_order_rests_at_l5s_own_price(tmp_path):
    rec, tp, _ = _drive(tmp_path, maker_treatment=True)
    assert D(str(tp.sent[0]["price"])) == D("0.42")
    assert rec["MAKER_TREATMENT"]["rested_at_l5_price"] == "0.42"


def test_the_order_is_post_only_on_the_wire(tmp_path):
    from src import l5_order_body as OB
    import tests.test_l5_quantity_quantization as Q
    rec, tp, _ = _drive(tmp_path, maker_treatment=True)
    body = OB.from_intent(tp.sent[0], Q.MARKET)
    assert body["participateDontInitiate"] is True


def test_the_quantity_is_floored_not_rounded_up(tmp_path):
    rec, tp, _ = _drive(tmp_path, maker_treatment=True)
    sent = D(str(tp.sent[0]["qty"]))
    desired = D(rec["MAKER_TREATMENT"]["qty_desired"])
    assert sent <= desired
    assert (sent / D("0.01")) == (sent / D("0.01")).to_integral_value()


def test_maker_only_does_not_block_the_maker_treatment(tmp_path):
    rec, tp, _ = _drive(tmp_path, maker_treatment=True, maker_only=True)
    assert rec["ORDER"]["submitted"] is True


def test_maker_only_blocks_a_crossing_action(tmp_path):
    import tests.test_l5_quantity_quantization as Q
    import tests.test_l5_live_execution as L
    import src.l5_live_execution as EX
    from src import l5_probe_manifest as PM
    tp = Q.GridTransport()
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    ex = EX.FaithfulExecutor(store, PM.probe_limits(), transport=tp,
                             lam=PM.LAMBDA, now_fn=L.NOW,
                             maker_treatment=True, maker_only=True)
    rec = ex.handle(L.row(), {}, market=Q.MARKET, window="0215z")
    # L.row() crosses, so the frozen rule calls it TAKER
    assert rec["refused_reason"] == "MAKER_ONLY_NO_TAKER"
    assert tp.sent == [], "no taker fallback, by construction"


def test_the_treatment_does_not_rescue_any_other_refusal(tmp_path):
    """Stale, no-book and depth refusals stand exactly as the rule made them."""
    import tests.test_l5_quantity_quantization as Q
    import tests.test_l5_live_execution as L
    import src.l5_live_execution as EX
    from src import l5_probe_manifest as PM
    tp = Q.GridTransport()
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    ex = EX.FaithfulExecutor(store, PM.probe_limits(), transport=tp,
                             lam=PM.LAMBDA, now_fn=L.NOW,
                             maker_treatment=True)
    row = _maker_row()
    row["pmus"]["bbo"] = {"bid": None, "ask": None}       # no book
    rec = ex.handle(row, {}, market=Q.MARKET, window="0215z")
    assert rec["refused_reason"] != "STRICT_NOT_EVALUABLE"
    assert tp.sent == []
