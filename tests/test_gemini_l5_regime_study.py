import json
from decimal import Decimal as D

from src import gemini_l5_regime_study as rs
from src.polymarket_l5 import transfer_backtest as tb

SYM = "GEMI-BTC05M2609051200-UP"
EXP = tb.ticker_expiry_epoch("BTC05M2609051200")
T0 = EXP - 290.0


def _bbo(t, bid, ask, sz="100"):
    return {"symbol": SYM, "t": t, "bid": bid, "bid_sz": sz, "ask": ask, "ask_sz": sz}


def test_entry_features_use_only_data_at_or_before_entry():
    rows = [_bbo(T0 - 70, "0.40", "0.42"), _bbo(T0 - 5, "0.48", "0.50", "250"),
            _bbo(T0 + 10, "0.90", "0.95")]          # the future must not leak
    prints = [{"timestampms": int((T0 - 30) * 1000), "type": "buy", "price": "0.5", "amount": "1"},
              {"timestampms": int((T0 - 20) * 1000), "type": "sell", "price": "0.47", "amount": "1"},
              {"timestampms": int((T0 + 5) * 1000), "type": "buy", "price": "0.9", "amount": "1"}]
    f = rs.entry_features(rows, prints, T0)
    assert abs(f["spread"] - 0.02) < 1e-9 and f["depth_min"] == 250.0
    assert abs(f["move_60s"] - (0.49 - 0.41)) < 1e-9
    assert f["trailing_prints_120s"] == 2 and f["trailing_two_sided_120s"] is True
    assert abs(f["tte_s"] - 290.0) < 1e-6


def test_rules_are_frozen_and_split_is_by_day():
    c = json.loads(rs.CANDIDATES.read_text())
    assert c["frozen_before_validation_numbers_were_read"] is True
    assert {r["name"] for r in c["rules"]} >= {"tight_spread_5c", "tight_calm_flow"}
    assert rs._day("GEMI-BTC05M2608302330-UP") == "260830"
    assert set(rs.TRAIN_DAYS).isdisjoint(rs.VALID_DAYS)
    rule = next(r for r in c["rules"] if r["name"] == "tight_calm_flow")
    assert rs.rule_admits(rule, {"spread": 0.04, "move_60s": 0.01,
                                 "trailing_two_sided_120s": True}) is True
    assert rs.rule_admits(rule, {"spread": 0.04, "move_60s": 0.05,
                                 "trailing_two_sided_120s": True}) is False
    assert rs.rule_admits(rule, {"spread": 0.04}) is None      # unobservable -> excluded
