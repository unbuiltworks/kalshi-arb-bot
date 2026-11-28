"""`unacknowledged` must mean "never reached the venue", and it did not.

MEASURED, camp1.cf28b0c9 window 3 (cpc-btc-updown-15m-2026-11-04-0400z).
12 orders submitted, 12 distinct venue_order_ids, every one carrying a
venue-confirmed terminal state. The report said acknowledged 4,
unacknowledged 8, under the label "submissions are being attempted and not
landing" -- because `acknowledged` was `bool(f)` over the LIFECYCLE stream
alone, and the 8 orders cancelled through the requote path never emit a
progress row. The metric described a transport fault that did not exist.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_window_accounting as WA

MKT = "cpc-btc-updown-15m-2026-11-04-0400z"

#: the three that filled, which the lifecycle stream did report
FILLED = [
    ("l5f-5092a0f03774606629f3a371", "DGP7BTYG13D1", "UP", "0.34"),
    ("l5f-1bbd856aae59edda7d6a9e9e", "DGP7K9KG13D3", "DOWN", "0.45"),
    ("l5f-0b9fee8f07bd2bdbfb4b65c2", "DGP7N4BG13D1", "UP", "0.45"),
]
#: the nine requote cancels, every one venue-confirmed ORDER_STATE_CANCELED
CANCELLED = [
    "l5f-28beba0e61c4f32e972ef1b8", "l5f-3d3ad5e0a14e179aed8824ec",
    "l5f-972138ef1f838b8671ed67a6", "l5f-d883cd89358f28dcc2c03df4",
    "l5f-50c6316e8feb5a187a5098b7", "l5f-0adb980c5a4aa41dcfed7fdc",
    "l5f-1e890795a8a53e45c183c0e6", "l5f-907c0f78d654d2b41828c959",
    "l5f-e5f3934bcda91e52082aa9be",
]


def _submits():
    out = [{"coid": c, "side": s, "qty": "1", "own_px": p, "mode": "MAKER"}
           for c, _v, s, p in FILLED]
    out += [{"coid": c, "side": "DOWN", "qty": "1", "own_px": "0.57",
             "mode": "MAKER"} for c in CANCELLED]
    return out


def _lifecycle():
    return [{"coid": c, "cum_qty": "1", "state": "ORDER_STATE_FILLED",
             "terminal": True, "commission": "0", "avg_px": p,
             "venue_order_id": v}
            for c, v, _s, p in FILLED]


def _cancels():
    return [{"client_order_id": c, "venue_order_id": "DGP7%s" % c[4:10],
             "market": MKT, "side": "DOWN",
             "venue_state": "ORDER_STATE_CANCELED",
             "cum_qty": "0", "leaves_qty": "0", "cancel_error": None}
            for c in CANCELLED]


def test_the_OLD_basis_reproduces_the_misleading_figure():
    """Omitting the cancel stream must change nothing, so the fix is opt-in
    and the historical records stay comparable."""
    o = WA.order_outcomes(_submits(), _lifecycle())
    assert sum(1 for r in o if r["acknowledged"]) == 3
    assert sum(1 for r in o if not r["acknowledged"]) == 9


def test_venue_confirmed_cancels_COUNT_AS_ACKNOWLEDGED():
    """All 12 reached the venue, so none is unacknowledged."""
    o = WA.order_outcomes(_submits(), _lifecycle(), _cancels())
    assert len(o) == 12
    assert sum(1 for r in o if r["acknowledged"]) == 12
    assert sum(1 for r in o if not r["acknowledged"]) == 0


def test_a_cancel_adds_NO_FILL_NO_QUANTITY_AND_NO_PNL():
    """Acknowledgement only. The filled count and contracts are untouched."""
    o = WA.order_outcomes(_submits(), _lifecycle(), _cancels())
    filled = [r for r in o if (r["qty_filled"] or D(0)) > 0]
    assert len(filled) == 3
    assert sum((r["qty_filled"] or D(0)) for r in filled) == D(3)
    for r in o:
        if r["coid"] in CANCELLED:
            assert (r["qty_filled"] or D(0)) == D(0)
            assert r["state"] == "CANCELED"


def test_an_UNVERIFIED_cancel_is_NOT_acknowledgement():
    """A row with no venue_order_id or no venue_state is a failure that marks
    the book uncertain. It is never admitted as evidence."""
    bad = [{"client_order_id": CANCELLED[0], "venue_order_id": "",
            "venue_state": "ORDER_STATE_CANCELED"},
           {"client_order_id": CANCELLED[1], "venue_order_id": "DGPX",
            "venue_state": ""}]
    assert WA.venue_confirmed_cancels(bad) == {}
    o = WA.order_outcomes(_submits(), _lifecycle(), bad)
    assert sum(1 for r in o if r["acknowledged"]) == 3


def test_window_record_threads_the_cancel_stream():
    rec = WA.window_record(market=MKT, submits=_submits(),
                           lifecycle=_lifecycle(), cancels=_cancels())
    assert rec["orders"]["submitted"] == 12
    assert rec["orders"]["acknowledged"] == 12
    assert rec["orders"]["unacknowledged"] == 0
    assert rec["orders"]["filled_orders"] == 3
    assert D(str(rec["contracts"]["filled_total"])) == D(3)
