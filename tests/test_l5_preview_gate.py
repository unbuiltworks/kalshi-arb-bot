"""Preview-before-submit must REFUSE, never reprice, never retry.

The live event this exists for: on 2026-09-24T14:40Z the venue accepted an
off-tick price of 0.0150 and returned 0.01. It did not reject the order, it
silently changed it. Every test here is a way that could put a real order on
the book at a price, size, side or market the engine did not choose.
"""
from __future__ import annotations

import copy

import pytest

from src import l5_order_body as OB
from src import l5_preview_gate as PG

SLUG = "cpc-btc-updown-15m-2026-09-24-1430z"
INTENT = {"client_order_id": "c1", "market": SLUG, "side": "UP",
          "price": "0.47", "qty": "0.05", "mode": "MAKER"}


def body():
    return OB.from_intent(INTENT, SLUG)


def echoed(**over):
    """The venue's real preview response shape, measured 2026-09-24T14:40Z."""
    b = body()
    o = {"id": "", "marketSlug": b["marketSlug"], "side": "ORDER_SIDE_BUY",
         "type": b["type"], "price": dict(b["price"]),
         "quantity": b["quantity"], "cumQuantity": 0,
         "leavesQuantity": b["quantity"], "tif": b["tif"],
         "intent": "ORDER_INTENT_BUY_LONG",
         "state": "ORDER_STATE_PENDING_NEW",
         "outcomeSide": b["outcomeSide"], "action": b["action"],
         "manualOrderIndicator": b["manualOrderIndicator"]}
    o.update(over)
    return {"order": o}


# ------------------------------------------------- the happy path is exact

def test_an_exact_echo_is_accepted():
    v = PG.require_echo(body(), echoed())
    assert v["ok"] and v["mismatches"] == []


def test_a_numerically_equal_price_is_not_a_mismatch():
    """0.01 and 0.0100 are the same price; the venue formats it its own way."""
    v = PG.require_echo(body(), echoed(price={"value": "0.4700000",
                                              "currency": "USD"}))
    assert v["ok"]


# ------------------------------- every execution-critical field must match

def test_a_silently_normalized_price_REFUSES():
    """The exact live failure: sent 0.0150, echoed 0.01, HTTP 200."""
    with pytest.raises(PG.PreviewRefused) as e:
        PG.require_echo(body(), echoed(price={"value": "0.01",
                                              "currency": "USD"}))
    assert "price" in str(e.value)


def test_a_changed_quantity_REFUSES():
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body(), echoed(quantity=0.04, leavesQuantity=0.04))


def test_a_changed_market_REFUSES():
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body(), echoed(marketSlug="cpc-btc-updown-15m-OTHER"))


def test_a_changed_outcome_side_REFUSES():
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body(), echoed(outcomeSide="OUTCOME_SIDE_NO"))


def test_a_changed_action_REFUSES():
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body(), echoed(action="ORDER_ACTION_SELL"))


def test_a_changed_order_type_REFUSES():
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body(), echoed(type="ORDER_TYPE_MARKET"))


def test_a_changed_time_in_force_REFUSES():
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body(), echoed(tif="TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"))


def test_a_rejected_preview_state_REFUSES():
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body(), echoed(state="ORDER_STATE_REJECTED"))


def test_a_missing_field_REFUSES_rather_than_defaulting():
    o = echoed()
    del o["order"]["price"]
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body(), o)


def test_an_empty_preview_REFUSES():
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(body(), {})


# -------------------------------------------- maker intent, by consequence

def test_a_maker_order_that_would_fill_on_preview_REFUSES():
    """participateDontInitiate is not echoed by this venue, so maker intent
    is checked by consequence: a post-only order showing any filled quantity
    would not have rested."""
    with pytest.raises(PG.PreviewRefused) as e:
        PG.require_echo(body(), echoed(cumQuantity=0.02, leavesQuantity=0.03))
    assert "participateDontInitiate" in str(e.value) or "quantity" in str(e.value)


def test_the_unechoed_fields_are_declared_not_silently_ignored():
    v = PG.echo_verdict(body(), echoed()["order"])
    assert "participateDontInitiate" in v["not_echoed"]


def test_nothing_is_ever_repriced_or_retried():
    """A refusal must not mutate the proposal: the caller's body is the
    caller's, and there is no second attempt at a different price."""
    b = body()
    before = copy.deepcopy(b)
    with pytest.raises(PG.PreviewRefused):
        PG.require_echo(b, echoed(price={"value": "0.01", "currency": "USD"}))
    assert b == before
    # and structurally: nothing in this module rounds, quantizes or loops a
    # retry. Checked on the AST so the prose explaining WHY it must not is
    # not mistaken for the thing itself.
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(PG))
    called = {n.func.attr if isinstance(n.func, ast.Attribute)
              else getattr(n.func, "id", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    # `round` is deliberately allowed: it formats the REPORTED book age.
    # What must not appear is decimal adjustment of an order value.
    for banned in ("quantize", "normalize", "scaleb"):
        assert banned not in called, "the gate must not adjust an order: %s" % banned
    # and no order field is ever written to
    written = {t.slice.value for n in ast.walk(tree)
               if isinstance(n, ast.Assign)
               for t in n.targets
               if isinstance(t, ast.Subscript)
               and isinstance(getattr(t, "slice", None), ast.Constant)}
    assert not ({"price", "quantity", "marketSlug", "outcomeSide"} & written), \
        "the gate wrote to an order field: %s" % written
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not any("retry" in n or "repric" in n for n in names), names


# -------------------------------------- a fresh two-sided book, at decision

def test_a_fresh_two_sided_book_is_executable():
    v = PG.fresh_two_sided(bid="0.46", ask="0.48", book_t=100.0, now=100.5)
    assert v["ok"] and v["age_s"] == 0.5


def test_a_stale_book_REFUSES():
    with pytest.raises(PG.PreviewRefused) as e:
        PG.require_fresh_book(bid="0.46", ask="0.48", book_t=100.0, now=110.0)
    assert "old" in str(e.value)


def test_a_one_sided_book_REFUSES():
    for bid, ask in (("0.46", None), (None, "0.48"), (None, None)):
        with pytest.raises(PG.PreviewRefused):
            PG.require_fresh_book(bid=bid, ask=ask, book_t=100.0, now=100.1)


def test_a_book_with_no_timestamp_REFUSES():
    with pytest.raises(PG.PreviewRefused):
        PG.require_fresh_book(bid="0.46", ask="0.48", book_t=None, now=100.0)


def test_the_book_bound_is_not_looser_than_the_frozen_rules():
    """The frozen rule bounds its own snapshot at 2.0s; the order path must
    not be more permissive than the decision path."""
    from src import l5_faithful_translation as FT
    assert PG.BOOK_MAX_AGE_S <= FT.STALE_MAX_S


# ------------------------------------------ the wiring, end to end

class Venue:
    """Records every call. `normalize` makes it silently alter the price,
    exactly as the live venue did."""

    def __init__(self, normalize=None):
        self.normalize = normalize
        self.calls = []

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path, body))
        if path == "/v1/order/preview":
            b = dict(body["request"])
            if self.normalize:
                b["price"] = {"value": self.normalize, "currency": "USD"}
            return {"order": {**b, "cumQuantity": 0,
                              "leavesQuantity": b["quantity"],
                              "state": "ORDER_STATE_PENDING_NEW",
                              "intent": "ORDER_INTENT_BUY_LONG"}}
        if path == "/v1/orders":
            return {"id": "venue-order-1"}
        raise AssertionError("unexpected path %s" % path)


def armed_transport(venue):
    from src import l5_pmus_transport as T
    t = T.PmusTransport(market_slug=SLUG, http=venue)
    t.armed = True                      # arming itself is tested elsewhere
    return t


def test_submit_previews_first_then_sends_the_same_body():
    v = Venue()
    out = armed_transport(v).submit(INTENT)
    paths = [p for _, p, _ in v.calls]
    assert paths == ["/v1/order/preview", "/v1/orders"]
    previewed = v.calls[0][2]["request"]
    submitted = v.calls[1][2]
    assert previewed == submitted, "preview and submit bodies differ"
    assert out["id"] == "venue-order-1"


def test_a_normalized_price_REFUSES_and_sends_no_order():
    """The live failure, end to end: the venue accepts but alters. Nothing
    may reach /v1/orders."""
    v = Venue(normalize="0.01")
    with pytest.raises(PG.PreviewRefused):
        armed_transport(v).submit(INTENT)
    assert [p for _, p, _ in v.calls] == ["/v1/order/preview"]
    assert not any(p == "/v1/orders" for _, p, _ in v.calls)


def test_a_disarmed_transport_previews_nothing_at_all():
    from src import l5_pmus_transport as T
    from src.l5_live_execution import NotArmed
    v = Venue()
    t = T.PmusTransport(market_slug=SLUG, http=v)
    with pytest.raises(NotArmed):
        t.submit(INTENT)
    assert v.calls == []


def test_the_observer_transport_is_still_get_only():
    """Adding a preview POST to submit() must not have loosened the
    disarmed transport the 24/7 observer actually holds."""
    from src import pmus_auth as Q1
    t = Q1.ReadOnlyTransport()
    for m in ("POST", "DELETE", "PUT", "PATCH"):
        with pytest.raises(PermissionError):
            t.request(m, "/v1/order/preview", {})


def test_risk_limits_are_untouched():
    from src import l5_probe_manifest as PM
    lim = PM.probe_limits()
    assert str(lim.max_usd_per_market) == "5"
    assert str(lim.max_total_exposure_usd) == "5"
    assert str(lim.daily_realized_loss_halt_usd) == "5"
    assert str(lim.max_abs_residual) == "10"
    assert lim.max_concurrent_markets == 1
    assert PM.worst_case()["worst_case_total_usd"] == "5.1738"


def test_the_frozen_engine_is_byte_identical():
    from src import l5_session_integrity as IG
    fz = IG.frozen_ok()
    assert fz["ok"], fz["mismatches"]
