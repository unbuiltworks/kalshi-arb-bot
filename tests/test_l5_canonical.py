"""Deterministic tests for the canonical L5 pipeline.

Covers the consolidation spec: normalization, sequential inventory
accounting, pair matching, residual P&L, pagination/deep-history handling,
and replay reproducibility. All fixtures are synthetic and in-memory —
no network, no live data dependencies.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path

import pytest

from src.polymarket_l5 import canonical_sequence as cs
from src.polymarket_l5.realtime_mirror import WindowState, canonical_row, parse_window
from src.polymarket_l5.ledger import _finalize, WindowAgg
from src.polymarket_l5.fetch import fetch_activity_windowed
from src.polymarket_l5.cache import RawCache


# ---------- normalization (mirror path == canonical vocabulary) ----------

def _trade(ts, outcome, side, price, size, slug="btc-updown-5m-1000000"):
    return {"type": "TRADE", "timestamp": ts, "outcome": outcome, "side": side,
            "price": price, "size": size, "usdcSize": price * size,
            "slug": slug, "transactionHash": f"0x{ts:x}"}


def test_parse_window_accepts_both_durations_and_assets():
    for slug, asset, dur in (("btc-updown-5m-1000000", "BTC", 5),
                             ("eth-updown-15m-2000000", "ETH", 15)):
        win = parse_window({"slug": slug})
        assert win["asset"] == asset and win["duration_minutes"] == dur
        assert win["window_end"] - win["window_start"] == dur * 60
    assert parse_window({"slug": "sol-updown-5m-1"}) is None
    assert parse_window({"slug": "will-x-happen"}) is None


def test_sequential_inventory_and_pair_matching():
    win = parse_window({"slug": "btc-updown-5m-1000000"})
    st = WindowState()
    r1 = canonical_row(_trade(1000060, "Up", "BUY", 0.60, 10), win, st)
    assert r1["cum_up_qty"] == 10 and r1["matched_qty"] == 0
    assert r1["matched_pair_unit_cost"] is None
    assert r1["residual_qty_signed"] == 10 and r1["residual_side"] == "Up"
    assert r1["seconds_to_settlement"] == 1000300 - 1000060

    r2 = canonical_row(_trade(1000070, "Down", "BUY", 0.35, 4), win, st)
    assert r2["matched_qty"] == 4
    assert r2["matched_pair_unit_cost"] == pytest.approx(0.95)
    assert r2["residual_qty_signed"] == 6 and r2["residual_side"] == "Up"

    # SELL reduces inventory and flips residual once it crosses
    r3 = canonical_row(_trade(1000080, "Up", "SELL", 0.62, 8), win, st)
    assert r3["cum_up_qty"] == pytest.approx(2)
    assert r3["residual_qty_signed"] == pytest.approx(-2)
    assert r3["residual_side"] == "Down"


def test_outcome_case_normalization():
    win = parse_window({"slug": "btc-updown-5m-1000000"})
    st = WindowState()
    assert canonical_row(_trade(1, "UP", "BUY", 0.5, 1), win, st) is not None
    assert canonical_row(_trade(2, "down", "BUY", 0.5, 1), win, st) is not None
    # yes/no labels map to Up/Down by design (some updown tokens use them)
    assert canonical_row(_trade(3, "Yes", "BUY", 0.5, 1), win, st)["outcome"] == "Up"
    assert canonical_row(_trade(4, "Maybe", "BUY", 0.5, 1), win, st) is None


# ---------- residual P&L via ledger finalize ----------

def test_ledger_finalize_decomposition_is_exactly_additive():
    agg = WindowAgg("0xabc")
    agg.slug = "btc-updown-5m-1000000"
    agg.asset, agg.horizon_m, agg.window_start = "BTC", 5, 1000000
    # buy 10 Up @0.60, 4 Down @0.35 -> matched 4 @0.95 pair, residual 6 Up
    agg.fills = [(1000060, 0, 1, 10, 0.60, 6.0), (1000070, 1, 1, 4, 0.35, 1.4)]
    agg.redeem_usdc, agg.redeem_qty, agg.redeem_n = 10.0, 10.0, 1  # Up won
    row = _finalize(agg)
    assert row["matched_qty"] == 4
    assert row["pair_cost"] == pytest.approx(0.95)
    assert row["pair_locked"] == pytest.approx(4 * 0.05)
    assert row["net_trading_cash"] == pytest.approx(10.0 - 7.4)
    assert row["residual_and_timing"] == pytest.approx(
        row["net_trading_cash"] - row["pair_locked"])


# ---------- pagination / deep-history (offset cap) ----------

class _FakeOpener:
    """Simulates the venue: DESC pages, hard 413-row history, offset cap 100."""

    def __init__(self, n_rows=413, offset_cap=100):
        self.rows = [{"timestamp": 2_000_000 - i, "type": "TRADE",
                      "transactionHash": f"0x{i:x}", "conditionId": "0xc",
                      "outcomeIndex": 0, "side": "BUY", "size": 1.0,
                      "price": 0.5, "usdcSize": 0.5}
                     for i in range(n_rows)]
        self.offset_cap = offset_cap

    def __call__(self, full_url: str, timeout: float):
        import urllib.parse
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(full_url).query))
        offset = int(q.get("offset", 0))
        limit = int(q.get("limit", 500))
        end = int(q["end"]) if "end" in q else None
        if offset > self.offset_cap:
            return 400, None, "max historical activity offset exceeded"
        rows = self.rows
        if end is not None:
            rows = [r for r in rows if r["timestamp"] <= end]
        return 200, rows[offset:offset + limit], ""


def test_windowed_walk_recovers_full_history_past_offset_cap(tmp_path):
    cache = RawCache(tmp_path)
    opener = _FakeOpener(n_rows=413, offset_cap=100)
    report = fetch_activity_windowed(
        "0xwallet", cache=cache, opener=opener, limit=50,
        max_total_pages=100, window_page_cap=2)   # 2*50=100 <= cap
    assert report["stopped_because"] in ("history_exhausted", "reached_stop_ts")
    # every unique timestamp recovered despite the offset cap
    seen = set()
    for rec in cache.records("activity"):
        for item in rec.get("payload") or []:
            seen.add(item["timestamp"])
    assert len(seen) == 413


# ---------- replay reproducibility ----------

def test_canonical_sequence_build_is_deterministic(tmp_path, monkeypatch):
    # tiny synthetic harvest: 2 windows, 3 trades
    trades = tmp_path / "trades.csv"
    seqs = tmp_path / "seqs.csv"
    summ = tmp_path / "summary.csv"
    tcols = ["timestamp", "outcome", "side", "price", "shares",
             "transaction_hash", "condition_id", "token_id"]
    with open(trades, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=tcols); w.writeheader()
        w.writerow({"timestamp": "100", "outcome": "Up", "side": "BUY",
                    "price": "0.6", "shares": "10", "transaction_hash": "0x1",
                    "condition_id": "0xa", "token_id": "1"})
    scols = ["window_id", "market_slug", "asset", "window_minutes", "trade_seq",
             "timestamp", "datetime_utc", "seconds_from_window_start",
             "seconds_to_expiry", "outcome", "side", "price", "shares",
             "dollars", "cum_up_qty", "cum_up_spend", "cum_down_qty",
             "cum_down_spend", "matched_qty", "matched_pair_unit_cost",
             "excess_qty", "transaction_hash"]
    with open(seqs, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=scols); w.writeheader()
        w.writerow({"window_id": "0xa", "market_slug": "btc-updown-5m-1",
                    "asset": "BTC", "window_minutes": "5", "trade_seq": "1",
                    "timestamp": "100", "datetime_utc": "t",
                    "seconds_from_window_start": "40",
                    "seconds_to_expiry": "260", "outcome": "Up",
                    "side": "BUY", "price": "0.6", "shares": "10",
                    "dollars": "6", "cum_up_qty": "10", "cum_up_spend": "6",
                    "cum_down_qty": "0", "cum_down_spend": "0",
                    "matched_qty": "0", "matched_pair_unit_cost": "",
                    "excess_qty": "10", "transaction_hash": "0x1"})
    with open(summ, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["window_id", "winning_outcome",
                                           "resolution_source"])
        w.writeheader()
        w.writerow({"window_id": "0xa", "winning_outcome": "Up",
                    "resolution_source": "test"})

    monkeypatch.setattr(cs.config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cs.config, "RAW_DIR", tmp_path)   # empty takeronly
    monkeypatch.setattr(cs.config, "TRADES_CSV", "trades.csv")
    monkeypatch.setattr(cs.config, "SEQUENCES_CSV", "seqs.csv")
    monkeypatch.setattr(cs.config, "SUMMARY_CSV", "summary.csv")
    monkeypatch.setattr(cs, "OUT_CSV", tmp_path / "canon.csv.gz")
    monkeypatch.setattr(cs, "OUT_META", tmp_path / "canon.meta.json")

    meta1 = cs.build()
    meta2 = cs.build()
    assert meta1["content_sha256_16"] == meta2["content_sha256_16"]
    assert meta1["rows"] == 1
    # no takeronly coverage -> honesty rule: unknown, never maker
    assert meta1["maker_taker_counts"] == {"taker": 0, "maker": 0, "unknown": 1}
    with gzip.open(tmp_path / "canon.csv.gz", "rt") as fh:
        row = list(csv.DictReader(fh))[0]
    assert row["settlement_outcome"] == "Up"
    assert row["residual_side"] == "Up"
    assert row["source"] == "polymarket_public_activity"
