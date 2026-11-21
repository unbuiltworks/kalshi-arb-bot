"""Every order of a window is on the record, with how it ended.

Observed 2026-11-21 (0620z): the report said 20 cancelled, the step lines
named seven, and nothing recorded post-only or the venue's own words.
"""
from src import l5_btc5m_execution as X
from src import l5_btc5m_runner as RUN


def test_evidence_keeps_descriptive_scalars_only():
    got = {"state": "ORDER_STATE_CANCELED", "transactTime": "2026-11-21T06:20:14Z",
           "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
           "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL", "type": "ORDER_TYPE_LIMIT",
           "cancelReason": "SELF_MATCH", "participateDontInitiate": True,
           "accountId": "A-SECRET", "userId": "U", "clientOrderId": "b5m-x",
           "positionId": "P", "cumQuantity": "0", "price": {"value": "0.41"},
           "legs": [1, 2], "apiKeyType": "leak", "x" * 3: None}
    ev = X.venue_order_evidence(got)
    assert ev["state"] == "ORDER_STATE_CANCELED"
    assert ev["cancelReason"] == "SELF_MATCH"
    assert ev["participateDontInitiate"] is True
    assert ev["manualOrderIndicator"].endswith("AUTOMATIC")
    assert ev["tif"] and ev["type"] and ev["transactTime"]
    for k in ("accountId", "userId", "clientOrderId", "positionId",
              "apiKeyType", "cumQuantity", "price", "legs"):
        assert k not in ev


def test_evidence_never_raises_and_is_bounded():
    assert X.venue_order_evidence(None) == {}
    assert X.venue_order_evidence("row") == {}
    big = {"time%03d" % i: "v" * 500 for i in range(100)}
    ev = X.venue_order_evidence(big)
    assert len(ev) == X.EVIDENCE_MAX_KEYS
    assert all(len(v) == X.EVIDENCE_MAX_CHARS for v in ev.values())


ROWS = {
    "b5m-1": {"market": "m-0620z", "venue_order_id": "V1", "side": "UP",
              "price": "0.58", "qty": "0.08", "filled_qty": "0",
              "state": X.CANCELLED, "venue_state": "ORDER_STATE_CANCELED",
              "post_only": True},
    "b5m-2": {"market": "m-0620z", "venue_order_id": "V2", "side": "DOWN",
              "price": "0.40", "qty": "0.08", "filled_qty": "0.08",
              "state": X.FILLED, "venue_fees": "0.0000",
              "venue_evidence": {"state": "ORDER_STATE_FILLED"}},
    "b5m-3": {"market": "m-0620z", "venue_order_id": "V3", "side": "UP",
              "price": "0.62", "qty": "0.08", "filled_qty": "0",
              "state": X.CANCELLED, "cancel_requested": True,
              "cancel_sent_at": "2026-11-21T06:20:34+00:00"},
    "b5m-0": {"market": "m-0615z", "venue_order_id": "V0", "side": "UP",
              "price": "0.5", "qty": "0.08", "filled_qty": "0",
              "state": X.CANCELLED},
    "b5m-9": {"market": "m-0625z", "venue_order_id": None, "side": "UP",
              "price": "0.5", "qty": "0.08", "filled_qty": "0",
              "state": X.CANCELLED},
}


def test_order_table_has_one_row_per_order_and_separates_our_cancels():
    mine = {c: r for c, r in ROWS.items() if r["market"] == "m-0620z"}
    t = RUN.order_table(mine)
    assert [r["venue_order_id"] for r in t] == ["V1", "V2", "V3"]
    assert [r["cancel_requested"] for r in t] == [False, False, True]
    assert t[0]["sent_post_only"] is True
    assert t[1]["venue"] == {"state": "ORDER_STATE_FILLED"}
    assert t[1]["venue_fees"] == "0.0000"


def test_order_table_survives_a_bad_row():
    t = RUN.order_table({"a": None})
    assert t == [{"client_order_id_unreadable": True,
                  "error": "AttributeError"}]


class Ex:
    def __init__(self, rows):
        self.rows = rows

    def orders(self):
        return {c: dict(r) for c, r in self.rows.items()}


def test_last_traded_orders_picks_the_latest_market_that_sent_orders():
    out = RUN.last_traded_orders(Ex(ROWS))
    assert out["market"] == "m-0620z"         # 0625z never reached the venue
    assert out["count"] == 3 and out["cancel_requested"] == 1
    assert out["ended_without_our_cancel"] == ["V1"]


def test_last_traded_orders_with_nothing_sent():
    assert RUN.last_traded_orders(Ex({})) == {"market": None, "orders": []}
