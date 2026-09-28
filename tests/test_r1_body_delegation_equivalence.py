"""3b: the micro executor's order body must be BYTE-IDENTICAL after delegation.

`src/pmus_micro_executor._body` used to hold its own copy of the order body and
now delegates to `src/l5_order_body.build`. That is a change to the SUBMISSION
path, which the lineage gate treats as a forbidden class -- so it is not enough to
call it a refactor and label it. This proves the claim instead, by reconstructing
the pre-delegation implementation verbatim from 03f881ca and asserting the two
agree on every field across a grid of prices, quantities, sides and post_only.

If they ever diverge, the delta is a real behavioural change and must go back to
the owner rather than into a reviewed baseline.
"""
from __future__ import annotations

import itertools
import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_micro_executor as MX                      # noqa: E402


def _body_as_reviewed(slug: str, outcome_side: str, price: D, qty: D,
                      post_only: bool) -> dict:
    """The 03f881ca implementation, copied verbatim (comments stripped)."""
    qty = D(str(qty))
    if qty <= 0:
        raise MX.Halt("MICRO_NONPOSITIVE_QTY")
    return {
        "marketSlug": slug,
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(MX.venue_price(outcome_side, price)),
                  "currency": "USD"},
        "quantity": float(qty),
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "participateDontInitiate": bool(post_only),
        "outcomeSide": outcome_side,
        "action": "ORDER_ACTION_BUY",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }


PRICES = [D(x) for x in ("0.0100", "0.0500", "0.2300", "0.4400", "0.5000",
                         "0.5600", "0.7900", "0.9500", "0.9900")]
QTYS = [D(x) for x in ("1", "5", "5.31", "10", "25", "50", "0.5", "1.234563")]
SIDES = ["OUTCOME_SIDE_YES", "OUTCOME_SIDE_NO"]


@pytest.mark.parametrize("side,post_only", list(itertools.product(SIDES,
                                                                 [True, False])))
def test_every_field_matches_the_reviewed_body(side, post_only):
    slug = "cpc-btc-updown-15m-2026-09-27-2245z"
    for px in PRICES:
        for qty in QTYS:
            a = _body_as_reviewed(slug, side, px, qty, post_only)
            b = MX._body(slug, side, px, qty, post_only)
            assert b == a, (side, str(px), str(qty), post_only, a, b)


def test_the_no_side_wire_mapping_is_still_one_minus_x():
    """The 2026-09-05/06 live lesson: 24/24 NO-side post-only orders were
    rejected because the venue reads NO at 1-X. The mapping must survive."""
    slug = "s"
    b = MX._body(slug, "OUTCOME_SIDE_NO", D("0.4300"), D(1), True)
    assert b["price"]["value"] == str(D("1") - D("0.4300"))
    y = MX._body(slug, "OUTCOME_SIDE_YES", D("0.4300"), D(1), True)
    assert y["price"]["value"] == str(D("0.4300"))


def test_the_nonpositive_halt_code_is_preserved():
    for qty in (D(0), D("-1")):
        for impl in (_body_as_reviewed, MX._body):
            with pytest.raises(MX.Halt) as e:
                impl("s", "OUTCOME_SIDE_YES", D("0.5"), qty, True)
            assert "MICRO_NONPOSITIVE_QTY" in str(e.value)


def test_quantity_stays_a_float_because_the_venue_documents_double():
    b = MX._body("s", "OUTCOME_SIDE_YES", D("0.5"), D("5.31"), True)
    assert isinstance(b["quantity"], float) and b["quantity"] == 5.31
