"""Read-only PMUS combo (parlay) study: fair value, fees, flow, no order path."""
from src import pmus_sports_combo_study as CS
from src import pmus_sports_two_sided as M


def test_published_combo_fee_formulas():
    # docs.polymarket.us/fees: 100 lots at $0.10 -> maker $0.27
    assert round(CS.combo_maker_fee(0.10, 100), 2) == 0.27
    # Tier 4 theta 0.0695, 100 lots at $0.10 -> taker $1.02 (published table)
    assert round(CS.combo_taker_fee(0.10, 100, theta=0.0695), 2) == 1.02
    assert round(CS.combo_taker_fee(0.30, 100, theta=0.0695), 2) == 1.89


def test_event_key_groups_markets_of_one_game():
    assert CS.event_key("aec-nba-ny-tor-2026-11-15") == "nba-ny-tor-2026-11-15"
    assert CS.event_key("asc-nba-ny-tor-2026-11-15-pos-3pt5") == \
        "nba-ny-tor-2026-11-15"


def test_fair_value_is_the_product_of_leg_prices_with_sell_legs_inverted():
    legs = [{"symbol": "a", "side": "SIDE_BUY"},
            {"symbol": "b", "side": "SIDE_SELL"}]
    assert abs(CS.fair_value(legs, {"a": 0.6, "b": 0.3}) - 0.6 * 0.7) < 1e-12
    assert CS.fair_value(legs, {"a": 0.6, "b": None}) is None


def test_same_game_flag():
    assert CS.same_game([{"symbol": "aec-nba-ny-tor-2026-11-15"},
                         {"symbol": "asc-nba-ny-tor-2026-11-15-pos-3pt5"}])
    assert not CS.same_game([{"symbol": "aec-nba-ny-tor-2026-11-15"},
                             {"symbol": "aec-nhl-edm-phi-2026-11-15"}])


def fake_get(trades, rfqs=(), combos=None):
    combos = combos or {}

    def get(path):
        if path.startswith("/v1/rfqs/trades"):
            return {"trades": list(trades)}
        if path.startswith("/v1/rfqs"):
            return {"rfqs": list(rfqs)}
        if path.startswith("/v1/combos?symbol="):
            sym = path.split("=", 1)[1]
            return {"combos": [{"legs": combos[sym]}]} if sym in combos \
                else {"combos": []}
        raise AssertionError(path)
    return get


def pm_get(mids):
    def g(path):
        sym = path.split("/")[3]
        m = mids[sym]
        return {"marketData": {"bestBid": {"value": str(m - 0.005)},
                               "bestAsk": {"value": str(m + 0.005)}}}
    return g


def test_combo_print_is_priced_against_its_legs_and_logged():
    legs = [{"symbol": "aec-nba-a-b-2026-11-15", "side": "SIDE_BUY"},
            {"symbol": "aec-nhl-c-d-2026-11-15", "side": "SIDE_BUY"}]
    logs = []
    cs = CS.ComboStudy(
        fake_get([{"tradeId": "t1", "symbol": "combo-1", "price": "0.30",
                   "qtyDecimal": "10", "executedTime":
                       "2026-11-16T01:00:00Z"},
                  {"tradeId": "t2", "symbol": "aec-nba-a-b-2026-11-15",
                   "price": "0.5", "qtyDecimal": "1",
                   "executedTime": "2026-11-16T01:00:01Z"}],
                 combos={"combo-1": legs}),
        pm_get({"aec-nba-a-b-2026-11-15": 0.5,
                "aec-nhl-c-d-2026-11-15": 0.5}),
        lambda k, **kw: logs.append((k, kw)))
    cs.poll_trades()
    rec = [kw for k, kw in logs if k == "COMBO_STUDY_TRADE"]
    assert len(rec) == 1                       # the single-market fill is not
    r = rec[0]
    assert r["legs"] == 2 and abs(r["fair_now"] - 0.25) < 1e-9
    assert abs(r["maker_margin"] - 0.05) < 1e-9
    assert abs(r["maker_margin_after_fee"] -
               (0.05 - CS.combo_maker_fee(0.30))) < 1e-4
    s = cs.summary()
    assert s["combo_trades"] == 1 and s["trades"] == 2
    cs.poll_trades()                           # same prints: not re-counted
    assert cs.summary()["trades"] == 2


def test_rfq_flow_counts_legs_and_same_game():
    legs = [{"symbol": "aec-nba-ny-tor-2026-11-15", "side": "SIDE_BUY"},
            {"symbol": "asc-nba-ny-tor-2026-11-15-pos-3pt5",
             "side": "SIDE_BUY"}]
    logs = []
    cs = CS.ComboStudy(fake_get([], rfqs=[{"id": "r1", "symbol": "c",
                                           "comboLegs": legs}]),
                       pm_get({}), lambda k, **kw: logs.append((k, kw)))
    cs.poll_rfqs()
    cs.poll_rfqs()
    s = cs.summary()
    assert s["rfqs"] == 1 and s["rfq_legs"] == {"2": 1}
    assert s["rfq_same_game"] == 1


def test_unreadable_endpoints_are_recorded_not_raised():
    def boom(path):
        raise PermissionError("403 not enabled for RFQ")
    cs = CS.ComboStudy(boom, pm_get({}), lambda *a, **k: 0)
    cs.poll_rfqs()
    cs.poll_trades()
    assert "rfqs" in cs.summary()["errors"]
    assert "trades" in cs.summary()["errors"]


def test_study_has_no_order_path_and_is_off_by_default(monkeypatch):
    cs = CS.ComboStudy(lambda p: {}, lambda p: {}, lambda *a, **k: 0)
    for name in ("place", "cancel", "quote", "accept", "v", "venue", "t"):
        assert not hasattr(cs, name)
    monkeypatch.delenv(M.COMBO_STUDY_ENV, raising=False)
    assert M.start_combo_study(object()) is None


def test_unreadable_leg_is_unpriced_not_fatal():
    # live 11:46Z: a leg BBO read raised and killed the study thread
    legs = [{"symbol": "aec-gone-a-b-2026-11-15", "side": "SIDE_BUY"},
            {"symbol": "aec-nhl-c-d-2026-11-15", "side": "SIDE_BUY"}]

    def pm(path):
        if "gone" in path:
            raise RuntimeError("404 market not found")
        return {"marketData": {"bestBid": {"value": "0.49"},
                               "bestAsk": {"value": "0.51"}}}
    logs = []
    cs = CS.ComboStudy(
        fake_get([{"tradeId": "t9", "symbol": "c9", "price": "0.2",
                   "qtyDecimal": "1", "executedTime": "2026-11-16T11:46:00Z"}],
                 combos={"c9": legs}),
        pm, lambda k, **kw: logs.append((k, kw)))
    cs.poll_trades()
    rec = [kw for k, kw in logs if k == "COMBO_STUDY_TRADE"][0]
    assert rec["fair_now"] is None and "leg_bbo" in cs.summary()["errors"]
