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
from src.polymarket_l5 import action_model as am
from src.polymarket_l5.adapters import PolymarketUpDownAdapter
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


def test_ledger_settlement_loser_side_never_redeems():
    """Winner side redeems $1/share; loser side produces NO redeem row.

    A matched pair therefore realizes exactly (1 - pair_cost) per pair in
    cash whichever side wins, and the losing residual realizes -cost.
    """
    # 4 Up @0.60 and 4 Down @0.35, fully matched; Down wins.
    agg = WindowAgg("0xdef")
    agg.slug = "eth-updown-5m-2000000"
    agg.asset, agg.horizon_m, agg.window_start = "ETH", 5, 2000000
    agg.fills = [(2000060, 0, 1, 4, 0.60, 2.4), (2000070, 1, 1, 4, 0.35, 1.4)]
    agg.redeem_usdc, agg.redeem_qty, agg.redeem_n = 4.0, 4.0, 1  # Down side only
    row = _finalize(agg)
    assert row["net_trading_cash"] == pytest.approx(4.0 - 3.8)
    assert row["pair_locked"] == pytest.approx(4 * (1 - 0.95))
    # fully matched -> the whole result is the locked pair edge
    assert row["residual_and_timing"] == pytest.approx(0.0)
    # winner shares all redeemed; the 4 losing shares are worthless, not
    # "unredeemed winners"
    assert row["unredeemed_qty"] == pytest.approx(4.0)

    # Same book, but Up (the residual-heavy case): 10 Up / 4 Down, Down wins
    # -> only the 4 Down shares redeem; the 6 unmatched Up expire worthless.
    agg2 = WindowAgg("0xdef2")
    agg2.slug = "eth-updown-5m-2000300"
    agg2.asset, agg2.horizon_m, agg2.window_start = "ETH", 5, 2000300
    agg2.fills = [(2000360, 0, 1, 10, 0.60, 6.0), (2000370, 1, 1, 4, 0.35, 1.4)]
    agg2.redeem_usdc, agg2.redeem_qty, agg2.redeem_n = 4.0, 4.0, 1
    row2 = _finalize(agg2)
    assert row2["net_trading_cash"] == pytest.approx(4.0 - 7.4)
    assert row2["pair_locked"] == pytest.approx(0.2)
    # loss = locked edge minus the incinerated residual: 0.2 - 6*0.60
    assert row2["residual_and_timing"] == pytest.approx(-3.6)
    assert row2["residual_and_timing"] == pytest.approx(
        row2["net_trading_cash"] - row2["pair_locked"])


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


# ---------- venue adapter separation ----------

def test_polymarket_adapter_isolates_venue_specifics():
    ad = PolymarketUpDownAdapter()
    win = ad.parse_window({"slug": "btc-updown-15m-3000000"})
    assert (win.venue, win.asset, win.duration_minutes) == ("polymarket", "BTC", 15)
    assert win.window_end - win.window_start == 900
    assert ad.parse_window({"slug": "doge-updown-5m-1"}) is None

    fill = ad.normalize_fill(_trade(3000100, "yes", "buy", 0.4, 7))
    assert (fill.outcome, fill.side) == ("Up", "BUY")
    assert fill.dollars == pytest.approx(2.8)
    assert ad.normalize_fill(_trade(3000100, "Maybe", "BUY", 0.4, 7)) is None
    assert ad.normalize_fill({"type": "REDEEM", "outcome": "Up"}) is None
    # mirror path consumes the adapter and stays canonical
    st = WindowState()
    row = canonical_row(_trade(3000100, "up", "BUY", 0.4, 7),
                        parse_window({"slug": "btc-updown-15m-3000000"}), st)
    assert row["outcome"] == "Up" and row["cum_up_qty"] == 7
    assert row["source"] == "polymarket_public_activity_realtime"


# ---------- action-by-action comparison foundation ----------

def _seq_csv(tmp_path):
    """Two-window synthetic canonical sequence exercising all relations."""
    path = tmp_path / "seq.csv"
    cols = ["window_id", "timestamp", "trade_seq", "outcome", "side", "price",
            "quantity", "seconds_to_settlement", "residual_qty_signed"]
    rows = [
        # window A: open Up, then complete with Down, then extend Down
        ("wA", 100, 1, "Up", "BUY", 0.60, 10, 240, 10),
        ("wA", 110, 2, "Down", "BUY", 0.38, 10, 230, 0),
        ("wA", 120, 3, "Down", "BUY", 0.40, 5, 220, -5),
        # window B: same first two states/actions as A
        ("wB", 400, 1, "Up", "BUY", 0.61, 10, 240, 10),
        ("wB", 410, 2, "Down", "BUY", 0.37, 10, 230, 0),
    ]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    return path


def test_action_model_states_relations_and_lookup(tmp_path):
    model = am.build_model(_seq_csv(tmp_path))
    assert model["n_windows"] == 2
    assert model["n_decisions"] == 3   # A:1->2, A:2->3, B:1->2
    # after a 0.60 Up fill with residual 10, tte 230s: L5 completed both times
    sk = am.state_key(0.60, 230, 10)
    st = am.next_action_stats(model, sk)
    assert st["n"] == 2
    assert st["modal_action"] == "complete|q5-15"
    assert st["action_probs"]["complete|q5-15"] == 1.0
    # flat book -> "open" relation
    sk_flat = am.state_key(0.38, 220, 0)
    assert am.next_action_stats(model, sk_flat)["modal_action"] == "open|q5-15"
    # never-visited state is an explicit off-distribution signal
    assert am.next_action_stats(model, am.state_key(0.95, 10, 100)) is None
    scored = am.compare_action(model, sk, "complete|q5-15")
    assert scored["probability"] == 1.0 and scored["rank"] == 1
    assert am.compare_action(model, sk, "extend|q<5")["probability"] == 0.0


def test_action_model_policy_hook_agreement(tmp_path):
    model = am.build_model(_seq_csv(tmp_path))

    def l5_mimic(state):        # always do what L5 modally did
        st = am.next_action_stats(model, state)
        return st["modal_action"] if st else None

    perfect = am.compare_policy(model, l5_mimic)
    assert perfect["expected_agreement_exact"] == 1.0
    assert perfect["expected_agreement_relation"] == 1.0

    always_extend = am.compare_policy(model, lambda s: "extend|q>40")
    assert always_extend["expected_agreement_exact"] == 0.0

    abstainer = am.compare_policy(model, lambda s: None)
    assert abstainer["expected_agreement_exact"] is None
    assert abstainer["policy_abstained_weight"] == 3
