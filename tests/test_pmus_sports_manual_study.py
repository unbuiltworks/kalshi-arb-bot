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
