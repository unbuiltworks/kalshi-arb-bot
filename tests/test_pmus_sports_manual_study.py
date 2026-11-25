"""Read-only study of the owner's manual sports fills."""
from decimal import Decimal as D

from src import pmus_sports_manual_study as MS


def trade(slug, px, q, agg, rp=None, t="2026-11-18T01:00:00Z"):
    return {"trade": {"marketSlug": slug, "price": {"value": px},
                      "qtyDecimal": q, "isAggressor": agg,
                      "realizedPnl": rp, "createTime": t}}


def resolution(slug, before, after):
    return {"positionResolution": {
        "marketSlug": slug, "beforePosition": {"realized": before},
        "afterPosition": {"realized": after}}}


ROWS = [
    trade("aec-nba-a-b-2026-11-17", "0.40", "10", False, "0",
          "2026-11-18T00:10:00Z"),
    trade("aec-nba-a-b-2026-11-17", "0.55", "10", False, "0",
          "2026-11-18T00:30:00Z"),
    resolution("aec-nba-a-b-2026-11-17", "0", "0.50"),
    trade("aec-nhl-c-d-2026-11-17", "0.30", "5", True, "0"),
    trade("aec-cs2-bot-2026-11-17", "0.45", "5", False, "0"),   # bot's
    trade("cpc-btc-updown-15m-x", "0.50", "4", False, "0"),     # BTC lane
]


def test_only_sports_markets_the_bot_never_traded_count_as_manual():
    rows = MS.manual_rows(ROWS, ours={"aec-cs2-bot-2026-11-17"})
    slugs = {(r.get("trade") or r.get("positionResolution"))["marketSlug"]
             for r in rows}
    assert slugs == {"aec-nba-a-b-2026-11-17", "aec-nhl-c-d-2026-11-17"}


def test_summary_per_market_and_only_settled_is_realized():
    s = MS.summarize(ROWS, ours={"aec-cs2-bot-2026-11-17"})
    assert s["markets"] == 2 and s["trades"] == 3
    assert s["settled_markets"] == 1
    assert D(s["settled_realized_usd"]) == D("0.50")
    nba = next(m for m in s["by_market"] if m["sport"] == "nba")
    assert D(nba["avg_px"]) == D("0.475") and nba["passive_share"] == 1.0
    assert nba["px_range"] == ["0.40", "0.55"] and nba["settled"]
    nhl = next(m for m in s["by_market"] if m["sport"] == "nhl")
    assert nhl["passive_share"] == 0.0 and not nhl["settled"]
    assert s["passive_share"] == round(2 / 3, 3)
    assert "price" in s["trade_fields"]


def test_bad_rows_never_raise():
    s = MS.summarize([None, {}, {"trade": "x"}, {"trade": {}}], ours=())
    assert s["markets"] in (0, 1)


def _trade(slug, oid, aggressor, px, qty, when, indicator, intent):
    ex = {"order": {"id": oid, "manualOrderIndicator": indicator,
                    "intent": intent, "cumQuantity": qty,
                    "avgPx": {"currency": "USD", "value": px},
                    "createTime": when}}
    other = {"order": {"id": "SOMEONE-ELSE", "manualOrderIndicator": "x"}}
    return {"trade": {"marketSlug": slug, "isAggressor": aggressor,
                      "price": px, "qty": str(qty), "createTime": when,
                      "aggressorExecution": ex if aggressor else other,
                      "passiveExecution": other if aggressor else ex}}


def test_manual_orders_lists_our_side_order_ids_newest_first():
    """The 25 Nov case: a hand buy that took liquidity, then a resting hand
    sell that was filled. Each order once, from OUR side of the trade."""
    nhl = "aec-nhl-veg-edm-2026-11-24"
    rows = [
        _trade(nhl, "BUY1", True, "0.6900", 35.46, "2026-11-25T02:18:46Z",
               "MANUAL_ORDER_INDICATOR_MANUAL", "ORDER_INTENT_BUY_LONG"),
        _trade(nhl, "SELL1", False, "0.8900", 20.0, "2026-11-25T03:10:20Z",
               "MANUAL_ORDER_INDICATOR_MANUAL", "ORDER_INTENT_SELL_LONG"),
        _trade(nhl, "SELL1", False, "0.8900", 15.46, "2026-11-25T03:10:21Z",
               "MANUAL_ORDER_INDICATOR_MANUAL", "ORDER_INTENT_SELL_LONG"),
        # a market the bot traded: never attributed to the owner
        _trade("aec-utr-a-b-2026-11-25", "BOT1", False, "0.45", 1,
               "2026-11-25T01:00:00Z", "MANUAL_ORDER_INDICATOR_UNDEFINED",
               "ORDER_INTENT_BUY_LONG"),
        # not a trade, and a trade with no order record: skipped quietly
        {"positionResolution": {"marketSlug": nhl}},
        {"trade": {"marketSlug": nhl, "isAggressor": True,
                   "aggressorExecution": {}}},
    ]
    got = MS.manual_orders(rows, ours={"aec-utr-a-b-2026-11-25"})
    assert [o["order_id"] for o in got] == ["SELL1", "BUY1"]
    sell, buy = got
    assert sell["trades"] == 2 and sell["aggressor"] is False
    assert sell["intent"] == "ORDER_INTENT_SELL_LONG"
    assert buy["avg_px"] == "0.6900" and buy["filled"] == "35.46"
    assert buy["manual_indicator"] == "MANUAL_ORDER_INDICATOR_MANUAL"
    assert buy["market"] == nhl
    assert "SOMEONE-ELSE" not in str(got) and "BOT1" not in str(got)
    assert MS.manual_orders(rows, ours=set(), max_orders=1)[0]["order_id"] \
        == "SELL1"
    assert MS.manual_orders([], ours=set()) == []
