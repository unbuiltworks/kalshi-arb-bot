"""Discovery against the activity-row layout PRODUCTION actually serves.

Read-only evidence, 2026-11-26 (ledger release c111abd67):

* `L5_VENUE_BASIS_DETAIL` lines print, verbatim and by dotted path, the
  allow-listed identity fields of real activity trade rows. On 12 distinct
  trade rows across 7 BTC5m markets the layout is always:

    trade.isAggressor                                   bool
    trade.<aggressorExecution|passiveExecution>.tradeId (ours only printed)
    trade.<...Execution>.order.manualOrderIndicator     (ours only printed)
    trade.aggressor.manualOrderIndicator                (party summary)
    trade.passive.manualOrderIndicator                  (party summary)

  with the order id at `<ours>Execution.order.id` (`venue_order_id_ours`,
  source "flag") and the trade id at `trade.id`. Indicator values seen:
  MANUAL_ORDER_INDICATOR_MANUAL, MANUAL_ORDER_INDICATOR_AUTOMATIC and, on
  the counterparty's summary only, MANUAL_ORDER_INDICATOR_UNDEFINED.
  `L5_VENUE_BASIS_ORDERS` confirms each order's own row says the same.

* The owner's manual UConn round trip (aec-cbb-duke-uconn-2026-11-25)
  appears in `L5_COST_BASIS_AUDIT` with exactly these ids and times and was
  attested by the round-trip form (-469.00 -> -20.63). UConn is not in the
  detail-market list, so its indicator VALUE is not in the logs; the owner
  stated these orders are manual and the round-trip reader parsed the same
  rows through the same own-execution selection.

The rows below copy those field names/values (no credentials, no account
fields). The counterparty's EXECUTION order is not printed by production
(only ours is); it is filled here from the row's own party summary.
"""
from __future__ import annotations

import datetime as dt

import pytest

from src import l5_manual_commitment as MC

MANUAL = "MANUAL_ORDER_INDICATOR_MANUAL"
AUTOMATIC = "MANUAL_ORDER_INDICATOR_AUTOMATIC"
UNDEFINED = "MANUAL_ORDER_INDICATOR_UNDEFINED"
UCONN = "aec-cbb-duke-uconn-2026-11-25"


def real_row(market, trade_id, order_id, t, is_aggressor, qty, *,
             ours_indicator, aggressor_summary, passive_summary,
             intent="ORDER_INTENT_BUY_LONG"):
    """One activity trade row in the production layout (see module doc)."""
    other_indicator = passive_summary if is_aggressor else aggressor_summary
    mine = {"tradeId": trade_id, "lastShares": qty,
            "order": {"id": order_id, "intent": intent,
                      "manualOrderIndicator": ours_indicator}}
    other = {"tradeId": trade_id, "lastShares": qty,
             "order": {"id": "CPTY" + order_id[4:],
                       "manualOrderIndicator": other_indicator}}
    return {"type": "activity_type_trade", "trade": {
        "id": trade_id, "marketSlug": market, "qty": qty, "time": t,
        "isAggressor": is_aggressor,
        "aggressor": {"manualOrderIndicator": aggressor_summary},
        "passive": {"manualOrderIndicator": passive_summary},
        "aggressorExecution": mine if is_aggressor else other,
        "passiveExecution": other if is_aggressor else mine}}


# (market, trade id, OUR order id, time, isAggressor, qty, ours, agg, pas)
PRODUCTION_ROWS = [
    ("cpc-btc-updown-5m-2026-11-16-0335z", "DRD2QBS093YH", "DRD2MJK033ZD",
     "2026-11-16T03:38:04.132332Z", False, "2.0000",
     AUTOMATIC, UNDEFINED, AUTOMATIC),
    ("cpc-btc-updown-5m-2026-11-16-0335z", "DRD2JDCGD3YJ", "DRD1B96014F1",
     "2026-11-16T03:37:43.859766Z", False, "2.0000",
     AUTOMATIC, MANUAL, AUTOMATIC),
    ("cpc-btc-updown-5m-2026-11-18-1535z", "DT0HYFT0D3YJ", "DT0HYFT013YB",
     "2026-11-18T15:36:28.905768Z", True, "8.8400",
     MANUAL, MANUAL, UNDEFINED),
    ("cpc-btc-updown-5m-2026-11-18-1600z", "DT0WVG0GQ3YJ", "DT0WT2X014F0",
     "2026-11-18T16:00:18.435758Z", False, "1.0000",
     MANUAL, UNDEFINED, MANUAL),
    ("cpc-btc-updown-5m-2026-11-18-1605z", "DT0Z7BM033YG", "DT0Z7BKG13YB",
     "2026-11-18T16:05:29.168151Z", True, "1.2200",
     MANUAL, MANUAL, MANUAL),
]


def since(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def rows():
    return [real_row(m, tid, oid, t, agg, q, ours_indicator=o,
                     aggressor_summary=a, passive_summary=p)
            for (m, tid, oid, t, agg, q, o, a, p) in PRODUCTION_ROWS]


def test_production_layout_bot_fills_stay_bot_manual_fills_are_found():
    got = MC.manual_rows(rows(), since("2026-11-01T00:00:00Z"))
    assert got["ids"] == ["DT0HYFT013YB", "DT0WT2X014F0", "DT0Z7BKG13YB"]
    assert got["unclassified"] == []
    # the AUTOMATIC (bot) orders are never found as manual ...
    assert "DRD2MJK033ZD" not in got["ids"]
    # ... even where the COUNTERPARTY's summary says MANUAL (DRD2JDCGD3YJ:
    # we were passive/AUTOMATIC, the aggressor was someone's manual order)
    assert "DRD1B96014F1" not in got["ids"]


def test_counterparty_manual_summary_never_classifies_our_bot_fill():
    """The real 0335z row: ours passive AUTOMATIC, aggressor MANUAL."""
    r = real_row("cpc-btc-updown-5m-2026-11-16-0335z", "DRD2JDCGD3YJ",
                 "DRD1B96014F1", "2026-11-16T03:37:43.859766Z", False,
                 "2.0000", ours_indicator=AUTOMATIC,
                 aggressor_summary=MANUAL, passive_summary=AUTOMATIC)
    assert MC.manual_rows([r], 0.0)["ids"] == []
    # isAggressor missing on the same row: ours unknown -> not manual
    del r["trade"]["isAggressor"]
    assert MC.manual_rows([r], 0.0)["ids"] == []


@pytest.mark.parametrize("is_aggressor", [True, False])
def test_owner_uconn_round_trip_is_manual_and_respects_since(is_aggressor):
    """The owner's 26 Nov UConn buy (DYSS79F015P6) and sell (DYSW1R1G15P6),
    ids/times/qty as production logged them; isAggressor is not in the
    logs for these rows, so both values are exercised."""
    buy = real_row(UCONN, "DYSS79FG35Q2", "DYSS79F015P6",
                   "2026-11-26T02:07:50.974242327Z", is_aggressor, "469",
                   ours_indicator=MANUAL, aggressor_summary=MANUAL,
                   passive_summary=MANUAL, intent="ORDER_INTENT_BUY_SHORT")
    sell = real_row(UCONN, "DYSW1R2055Q1", "DYSW1R1G15P6",
                    "2026-11-26T02:14:01.480265478Z", is_aggressor, "469",
                    ours_indicator=MANUAL, aggressor_summary=MANUAL,
                    passive_summary=MANUAL, intent="ORDER_INTENT_SELL_SHORT")
    feed = [sell, buy]                                   # newest first
    got = MC.manual_rows(feed, since("2026-11-26T00:00:00Z"))
    assert got["ids"] == ["DYSW1R1G15P6", "DYSS79F015P6"]
    # a SINCE after the buy keeps only the sell
    got = MC.manual_rows(feed, since("2026-11-26T02:10:00Z"))
    assert got["ids"] == ["DYSW1R1G15P6"]
    # the same rows sent AUTOMATIC by our own execution are bot fills
    for r in feed:
        side = "aggressorExecution" if is_aggressor else "passiveExecution"
        r["trade"][side]["order"]["manualOrderIndicator"] = AUTOMATIC
    assert MC.manual_rows(feed, 0.0)["ids"] == []


def test_production_rows_through_discovery_walk():
    """The same rows paged through Discovery.step (newest first)."""
    feed = sorted(rows(), key=lambda r: r["trade"]["time"], reverse=True)

    class Venue:
        def _get(self, path):
            return {"activities": feed, "eof": True, "nextCursor": ""}

    d = MC.Discovery()
    now = since("2026-11-26T22:00:00Z")
    d.step(Venue(), now, since("2026-11-01T00:00:00Z"))
    assert d.status(now) is None
    assert sorted(d.ids) == ["DT0HYFT013YB", "DT0WT2X014F0", "DT0Z7BKG13YB"]
