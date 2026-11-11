"""Send the venue a quantity it can actually create.

2026-09-25T02:15:50Z, Probe 03, armed with real money. The translation asked
for 0.40668775417984630725 shares. PMUS advertised a quantity increment of
0.01 -- read and passed in that same run's preflight -- and the preview came
back as 0.40. The exact-echo gate refused, because an order resting at a size
the engine did not choose is worse than no order.

The gate was right. The defect is upstream: the increment was READ every
window and never APPLIED. So the quantity is floored onto the venue's own
grid before anything else sees it, and the SAME executable quantity is what
gets reserved, previewed, echoed, submitted, filled and reconciled. Nothing
is ever reserved at one size and sent at another.

What is deliberately NOT done here: loosening require_echo, rounding up,
defaulting the increment to 0.01, or rewriting L5_NATIVE to pretend L5 asked
for a round lot.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import l5_order_body as OB
from src import l5_preview_gate as PG
from src import l5_pmus_transport as TP
from src import l5_probe_manifest as PM
import tests.test_l5_live_execution as L

MARKET = "cpc-btc-updown-15m-2026-09-25-0215z"

#: exactly what the translation produced for Probe 03
PROBE_03_DESIRED = D("0.40668775417984630725")
#: and exactly what PMUS said it could create
PROBE_03_INCREMENT = D("0.01")


# ------------------------------------------------------ the grid arithmetic

def test_the_probe_03_quantity_floors_to_exactly_four_tenths():
    got = EX.floor_to_increment(PROBE_03_DESIRED, PROBE_03_INCREMENT)
    assert got == D("0.40")
    assert got * 100 == D(40), "must land exactly on the grid, not near it"


@pytest.mark.parametrize("desired,increment,expected", [
    ("0.40668775417984630725", "0.01", "0.40"),   # Probe 03
    ("0.4066877541798463", "0.01", "0.40"),       # the float that went on the wire
    ("0.4099999999", "0.01", "0.40"),             # never up to 0.41
    ("0.409999999999999999999", "0.01", "0.40"),
    ("0.40", "0.01", "0.40"),                     # already on grid: unchanged
    ("0.4066877541798463", "0.05", "0.40"),       # not a power of ten
    ("0.4066877541798463", "0.25", "0.25"),       # quarter grid
    ("0.99", "0.3", "0.9"),                       # thirds
    ("9.999999", "1", "9"),                       # whole shares
    ("0.009", "0.01", "0"),                       # below one increment
])
def test_flooring_never_exceeds_and_always_lands_on_the_grid(
        desired, increment, expected):
    inc = D(increment)
    got = EX.floor_to_increment(D(desired), inc)
    assert got == D(expected)
    assert got <= D(desired), "rounded UP; that would exceed the reservation"
    assert (got / inc) == (got / inc).to_integral_value(), "off the grid"


def test_a_non_positive_increment_is_refused_rather_than_divided_by():
    with pytest.raises(ValueError):
        EX.floor_to_increment(D("1"), D("0"))


def test_the_increment_is_read_from_the_transport_and_never_defaulted():
    class Bare:
        pass

    assert EX.venue_quantity_increment(Bare()) is None
    assert EX.venue_quantity_increment(None) is None

    class Venueish:
        quantity_increment = D("0.05")

    assert EX.venue_quantity_increment(Venueish()) == D("0.05")

    class Zero:
        quantity_increment = D("0")

    assert EX.venue_quantity_increment(Zero()) is None, "0 is not a grid"


def test_preflight_keeps_the_venues_own_numbers_not_just_the_verdict():
    tp = TP.PmusTransport(market_slug=MARKET)
    assert tp.quantity_increment is None, "nothing is assumed before preflight"
    assert tp.tick_size is None
    # the venue sends these as JSON numbers; they must not inherit float error
    assert TP._as_decimal(0.01) == D("0.01")
    assert TP._as_decimal("0.01") == D("0.01")
    assert TP._as_decimal(None) is None
    assert TP._as_decimal(0) is None


# --------------------------------------------- the executor, end to end

class GridTransport:
    """The armed transport double, carrying the venue's grid."""

    name = "GRID"

    def __init__(self, increment="0.01", echo_quantity=None, tick="0.01"):
        self.armed = True
        self.quantity_increment = None if increment is None else D(increment)
        # the real PmusTransport keeps BOTH grids off preflight; a double
        # that carries only one lets a price defect through, which is how
        # Probe 07 sent 0.7399999979 and had the echo gate refuse it
        self.tick_size = None if tick is None else D(tick)
        self.sent = []
        self.paths = []
        self.echo_quantity = echo_quantity

    def submit(self, intent):
        # the real preview -> echo -> post sequence, against this intent,
        # including the stage markers PmusTransport records as it goes
        self.submit_stage = "SERIALIZE"
        self.preview_attempted = False
        self.preview_status = None
        self.order_post_attempted = False
        body = OB.from_intent(intent, MARKET)
        self.submit_stage = "PREVIEW_REQUEST"
        self.preview_attempted = True
        self.paths.append("/v1/order/preview")
        echoed = dict(body)
        if self.echo_quantity is not None:      # a venue that changes it
            echoed["quantity"] = self.echo_quantity
        self.preview_status = 200
        self.submit_stage = "PREVIEW_ECHO_VALIDATION"
        PG.require_echo(body, {"order": echoed})
        self.submit_stage = "ORDER_POST"
        self.order_post_attempted = True
        self.paths.append("/v1/orders")
        self.sent.append(dict(intent))
        return {"order_id": "v%d" % len(self.sent), "filled_qty": "0"}

    def open_orders(self):
        return []

    def positions(self):
        return []


def drive(tmp_path, transport, **rowkw):
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    limits = EX.SafetyLimits(max_usd_per_market=D("5"),
                             max_abs_residual=D("10"),
                             max_total_exposure_usd=D("5"),
                             max_concurrent_markets=1,
                             max_live_orders=1,
                             daily_realized_loss_halt_usd=D("5"))
    ex = EX.FaithfulExecutor(store, limits, transport=transport,
                             lam=PM.LAMBDA, now_fn=L.NOW)
    rec = ex.handle(L.row(**rowkw), {}, market=MARKET, window="0215z")
    return rec, store


def test_the_probe_03_order_now_reaches_the_order_post(tmp_path):
    """The whole point: preview echoes the executable quantity and passes."""
    tp = GridTransport()
    rec, _ = drive(tmp_path, tp)

    assert rec["refused_reason"] is None, rec["refused_reason"]
    assert rec["ORDER"]["submitted"] is True
    assert "/v1/orders" in tp.paths, "the echo gate no longer blocks it"
    assert tp.paths == ["/v1/order/preview", "/v1/orders"], "preview first"

    sent_qty = D(str(tp.sent[0]["qty"]))
    inc = D("0.01")
    assert (sent_qty / inc) == (sent_qty / inc).to_integral_value()


def test_the_translation_keeps_l5s_number_beside_the_executable_one(tmp_path):
    """Behavioural fidelity: L5 is not rewritten to have asked for a lot."""
    rec, _ = drive(tmp_path, GridTransport())
    t = rec["PMUS_TRANSLATION"]

    desired, executable = D(t["qty_desired"]), D(t["qty"])
    assert executable < desired, "this case does floor"
    assert D(t["quantization_delta"]) == desired - executable
    assert t["quantity_increment"] == "0.01"
    assert t["quantized"] is True
    # L5_NATIVE is untouched: his size is still his size
    assert rec["L5_NATIVE"]["l5_size"] == 10.0


def test_risk_and_the_wire_agree_on_one_quantity(tmp_path):
    """Never reserve 0.40668... while submitting 0.40."""
    tp = GridTransport()
    rec, store = drive(tmp_path, tp)

    executable = D(rec["PMUS_TRANSLATION"]["qty"])
    coid = rec["ORDER"]["client_order_id"]

    assert D(str(tp.sent[0]["qty"])) == executable, "wire"
    live = store.live_orders()[coid]
    assert D(str(live["remaining"])) == executable, "reservation"
    price = D(rec["PMUS_TRANSLATION"]["price"])
    assert store.outstanding_by_market()[MARKET] == price * executable


def test_a_quantity_under_one_increment_is_refused_not_sent(tmp_path):
    """Flooring to zero is a refusal, never a zero-quantity order."""
    tp = GridTransport(increment="10")          # far above anything L5 sizes
    rec, store = drive(tmp_path, tp)

    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"
    assert rec["refused_reason"].startswith("QUANTITY_BELOW_MIN_INCREMENT")
    assert rec["ORDER"] is None
    assert tp.sent == [] and tp.paths == []
    assert store.live_order_count() == 0, "nothing was reserved"


def test_an_armed_transport_with_no_known_grid_sends_nothing(tmp_path):
    """Preflight cannot pass without the constraint, so this is a fault."""
    tp = GridTransport(increment=None)
    rec, store = drive(tmp_path, tp)

    assert rec["refused_reason"] == "QUANTITY_INCREMENT_UNKNOWN"
    assert tp.sent == []
    assert store.live_order_count() == 0


def test_a_disarmed_run_still_reaches_a_would_submit(tmp_path):
    """Observe-only has no preflight; it must still exercise the path."""
    rec, _ = drive(tmp_path, EX.NullTransport())
    assert rec["ORDER"]["reason"] == "NOT_ARMED"
    assert rec["PMUS_TRANSLATION"]["quantized"] is False
    assert rec["PMUS_TRANSLATION"]["quantity_increment"] is None


def test_the_echo_gate_is_not_loosened(tmp_path):
    """A venue that changes the EXECUTABLE quantity must still be refused.

    The GATE is untouched: the order is refused and the POST never happens.
    What changed is the CONSEQUENCE. A refusal at the preview stage did not
    create an order -- the transport records order_post_attempted False --
    so the outcome is terminal and KNOWN, not unknown. Probe 07 lost nine
    subsequent actions to ORDER_STATE_UNCERTAIN on exactly this path.
    """
    tp = GridTransport(echo_quantity=0.39)      # not what we asked for
    rec, store = drive(tmp_path, tp)

    assert rec["ORDER"]["submitted"] is False
    assert rec["ORDER"]["reason"] == "REFUSED_BEFORE_ORDER_POST"
    assert "/v1/orders" not in tp.paths, "refused before the order post"
    assert rec["ORDER"]["failure_stage"] == "PREVIEW_ECHO_VALIDATION"
    assert rec["ORDER"]["order_post_attempted"] is False
    # nothing is outstanding, so nothing is uncertain and the slot is free
    assert not store.orders_uncertain()
    assert store.live_order_count() == 0


def test_a_failure_after_the_post_still_fails_closed(tmp_path):
    """The distinction, from the other side: once the POST is attempted we
    genuinely do not know, and the session stops until the venue says."""
    class PostBlewUp(GridTransport):
        def submit(self, intent):
            self.submit_stage = "ORDER_POST"
            self.preview_attempted = True
            self.preview_status = 200
            self.order_post_attempted = True
            raise OSError("connection reset after the POST went out")

    tp = PostBlewUp()
    rec, store = drive(tmp_path, tp)
    assert rec["ORDER"]["reason"] == "SUBMIT_OUTCOME_UNKNOWN"
    assert store.orders_uncertain(), "an unknown outcome still fails closed"
    assert store.live_order_count() == 1, "the reservation stands"


def test_the_exact_probe_03_echo_would_now_be_accepted():
    """The venue's 0.40 against our executable 0.40: no mismatch."""
    intent = {"client_order_id": "c", "market": MARKET, "side": "UP",
              "price": D("0.4200"),
              "qty": EX.floor_to_increment(PROBE_03_DESIRED,
                                           PROBE_03_INCREMENT),
              "mode": "TAKER"}
    body = OB.from_intent(intent, MARKET)
    assert body["quantity"] == 0.4
    # the venue echoing "0.4" for our 0.40 is the same number
    verdict = PG.echo_verdict(body, dict(body, quantity=0.4))
    assert verdict["ok"] is True, verdict["mismatches"]

    # and the unquantized original still fails, which is what happened live
    old = OB.from_intent(dict(intent, qty=PROBE_03_DESIRED), MARKET)
    bad = PG.echo_verdict(old, dict(old, quantity=0.4))
    assert bad["ok"] is False
    assert any(m["field"] == "quantity" for m in bad["mismatches"])
