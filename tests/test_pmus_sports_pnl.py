from decimal import Decimal as D

from src import pmus_sports_pnl as P


def _trade(slug, rp):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"marketSlug": slug, "realizedPnl": rp}}


def _res(slug, before, after):
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
            "positionResolution": {"marketSlug": slug,
                                   "beforePosition": {"realized": before},
                                   "afterPosition": {"realized": after}}}


def test_settlement_realized_comes_from_after_position_not_realizedpnl():
    # the live bug: a resolution row has no realizedPnl, so it read blank
    rows = [_trade("aec-cs2-a-b-2026-11-15", None),
            _res("aec-cs2-a-b-2026-11-15", "0", "-0.60")]
    v = P.venue_realized(rows, {"aec-cs2-a-b-2026-11-15"})
    assert v["settled_markets"] == 1
    assert v["settled_realized_usd"] == D("-0.60")
    assert v["trades_missing_realized"] == 1


def test_pair_closed_by_netting_realizes_on_the_trade_row():
    rows = [_trade("aec-atp-x-y-2026-11-15", "0"),
            _trade("aec-atp-x-y-2026-11-15", {"value": "0.04"})]
    v = P.venue_realized(rows, {"aec-atp-x-y-2026-11-15"})
    assert v["settled_markets"] == 0
    assert v["unsettled_trade_realized_usd"] == D("0.04")


def test_resolution_delta_not_cumulative_and_foreign_markets_ignored():
    rows = [_trade("aec-nba-a-b-2026-11-14", "0.10"),
            _res("aec-nba-a-b-2026-11-14", "0.10", "1.74"),
            _res("cpc-btc-updown-5m-2026-11-15-1435z", "0", "-5")]
    v = P.venue_realized(rows, {"aec-nba-a-b-2026-11-14"})
    assert v["settled_realized_usd"] == D("1.74")      # 0.10 + (1.74-0.10)
    assert v["settled_by_sport"] == {"nba": "1.74"}
    assert "cpc-btc-updown-5m-2026-11-15-1435z" not in v["by_market"]


def test_book_economics_pairs_margin_and_unmatched():
    mk = {"a": {"orders": {"o1": {"filled": "2"}, "o2": {"filled": "3"}},
                "pos": {"YES": "3", "NO": "2"},
                "cost": {"YES": "1.38", "NO": "1.04"}}}
    b = P.book_economics(mk)
    assert b["orders_submitted"] == 2 and b["filled_contracts"] == "5"
    assert b["completed_pairs"] == "2"
    # avg YES 0.46 + avg NO 0.52 = 0.98 -> 2 pairs * 0.02
    assert b["gross_pair_margin_usd"] == "0.0400"
    assert b["unmatched_cost_usd"] == "0.4600"
