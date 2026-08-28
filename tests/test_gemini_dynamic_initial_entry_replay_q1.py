import json
from pathlib import Path

from src import gemini_dynamic_initial_entry_replay_q1 as replay


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "capture.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _plan(ticker: str, bid: str, ask: str, asset: str = "ETH", horizon: str = "5m") -> dict:
    return {
        "type": "SHADOW_PLAN",
        "ticker": ticker,
        "symbol": f"GEMI-{ticker}-UP",
        "asset": asset,
        "horizon": horizon,
        "yes_bid": bid,
        "yes_ask": ask,
        "observed_at_utc": "2026-08-28T12:00:00Z",
    }


def _trade(ticker: str, price: str, buyer_maker: bool, asset: str = "ETH", horizon: str = "5m") -> dict:
    return {
        "type": "PUBLIC_TRADE",
        "ticker": ticker,
        "symbol": f"GEMI-{ticker}-UP",
        "asset": asset,
        "horizon": horizon,
        "price": price,
        "quantity": "3",
        "buyer_maker": buyer_maker,
        "observed_at_utc": "2026-08-28T12:00:01Z",
    }


def test_aggressive_entry_creates_incremental_first_fill_evidence(tmp_path):
    path = _write(tmp_path, [
        _plan("ETH05M-A", "0.23", "0.76"),
        _trade("ETH05M-A", "0.40", True),
    ])
    out = replay.replay(path, evidence_mode="STRICT_TRADE_THROUGH")
    assert out["eligible_markets"] == 1
    assert out["raw_markets_with_first_fill_evidence"] == 0
    assert out["aggressive_markets_with_first_fill_evidence"] == 1
    assert out["incremental_first_fill_markets"] == 1
    detail = out["markets_detail"][0]
    assert detail["aggressive_first_fill"]["side"] == "YES"
    assert detail["aggressive_first_fill"]["quote"] == "0.44"


def test_trade_rows_without_family_metadata_join_by_ticker_after_plan(tmp_path):
    trade = _trade("ETH05M-NOFAMILY", "0.40", True)
    trade.pop("asset")
    trade.pop("horizon")
    path = _write(tmp_path, [
        _plan("ETH05M-NOFAMILY", "0.23", "0.76"),
        trade,
    ])
    out = replay.replay(path, evidence_mode="STRICT_TRADE_THROUGH")
    assert out["source_trade_rows_target_family"] == 1
    assert out["aggressive_markets_with_first_fill_evidence"] == 1
    assert out["incremental_first_fill_markets"] == 1


def test_trade_before_first_plan_does_not_backfill_a_virtual_quote(tmp_path):
    early = _trade("ETH05M-EARLY", "0.40", True)
    early.pop("asset")
    early.pop("horizon")
    path = _write(tmp_path, [
        early,
        _plan("ETH05M-EARLY", "0.23", "0.76"),
    ])
    out = replay.replay(path, evidence_mode="TAPE_TOUCH_OPTIMISTIC")
    assert out["source_trade_rows_target_family"] == 0
    assert out["aggressive_markets_with_first_fill_evidence"] == 0


def test_touch_and_strict_trade_through_are_kept_separate(tmp_path):
    path = _write(tmp_path, [
        _plan("ETH05M-B", "0.23", "0.76"),
        _trade("ETH05M-B", "0.44", True),
    ])
    touch = replay.replay(path, evidence_mode="TAPE_TOUCH_OPTIMISTIC")
    strict = replay.replay(path, evidence_mode="STRICT_TRADE_THROUGH")
    assert touch["aggressive_markets_with_first_fill_evidence"] == 1
    assert strict["aggressive_markets_with_first_fill_evidence"] == 0


def test_no_side_trade_mapping_uses_yes_equivalent_price(tmp_path):
    path = _write(tmp_path, [
        _plan("ETH05M-C", "0.23", "0.76"),
        _trade("ETH05M-C", "0.60", False),
    ])
    out = replay.replay(path, evidence_mode="STRICT_TRADE_THROUGH")
    assert out["raw_markets_with_first_fill_evidence"] == 0
    assert out["aggressive_markets_with_first_fill_evidence"] == 1
    assert out["markets_detail"][0]["aggressive_first_fill"]["side"] == "NO"


def test_when_raw_edge_is_between_five_and_ten_cents_aggressive_holds_raw(tmp_path):
    path = _write(tmp_path, [
        _plan("ETH05M-D", "0.42", "0.51"),
        _trade("ETH05M-D", "0.41", True),
    ])
    out = replay.replay(path, evidence_mode="STRICT_TRADE_THROUGH")
    detail = out["markets_detail"][0]
    assert detail["aggressive_improved_updates"] == 0
    assert detail["aggressive_hold_raw_updates"] == 1
    assert out["raw_markets_with_first_fill_evidence"] == 1
    assert out["aggressive_markets_with_first_fill_evidence"] == 1
    assert out["incremental_first_fill_markets"] == 0


def test_target_family_filter_excludes_non_eth5(tmp_path):
    path = _write(tmp_path, [
        _plan("BTC05M-X", "0.23", "0.76", asset="BTC", horizon="5m"),
        _trade("BTC05M-X", "0.40", True, asset="BTC", horizon="5m"),
        _plan("ETH15M-X", "0.23", "0.76", asset="ETH", horizon="15m"),
        _trade("ETH15M-X", "0.40", True, asset="ETH", horizon="15m"),
    ])
    out = replay.replay(path, evidence_mode="TAPE_TOUCH_OPTIMISTIC", asset="ETH", horizon="5m")
    assert out["target_markets_seen"] == 0
    assert out["eligible_markets"] == 0


def test_replay_is_explicitly_shadow_and_never_reports_realized_pnl(tmp_path):
    path = _write(tmp_path, [_plan("ETH05M-E", "0.23", "0.76")])
    out = replay.replay(path, evidence_mode="TAPE_TOUCH_OPTIMISTIC")
    assert out["classification"] == "SHADOW_REPLAY_NOT_REALIZED"
    assert out["order_entry"] is False
    assert out["credentials_loaded"] is False
    assert out["realized_pnl"] is None
