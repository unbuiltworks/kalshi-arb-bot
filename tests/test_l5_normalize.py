"""Parsing and deduplication of raw Polymarket activity rows."""

from __future__ import annotations

from src.polymarket_l5.normalize import (
    canonical_outcome,
    dedupe_batches,
    fingerprint,
    load_trade_records,
    normalize_trade_item,
)
from src.polymarket_l5.timeutil import to_epoch_s
from tests.fixtures.l5_synthetic import fill, standard_scenario


def test_epoch_parsing_handles_seconds_millis_and_iso():
    assert to_epoch_s(1_750_000_000) == 1_750_000_000
    assert to_epoch_s(1_750_000_000_000) == 1_750_000_000
    assert to_epoch_s("2025-06-15T15:06:40Z") == 1_750_000_000
    assert to_epoch_s("") is None
    assert to_epoch_s(None) is None
    assert to_epoch_s("not-a-time") is None


def test_normalize_extracts_core_fields():
    record = normalize_trade_item(fill("0xc", 1000, "Up", "BUY", 0.42, 50, tx="0xdead"), "activity")
    assert record["outcome"] == "Up"
    assert record["side"] == "BUY"
    assert record["price"] == 0.42
    assert record["shares"] == 50
    assert record["dollars"] == 21.0
    assert record["transaction_hash"] == "0xdead"
    assert record["condition_id"] == "0xc"


def test_normalize_derives_dollars_when_usdc_size_missing():
    item = fill("0xc", 1000, "Down", "BUY", 0.3, 10)
    del item["usdcSize"]
    record = normalize_trade_item(item, "activity")
    assert record["dollars"] == 3.0


def test_normalize_skips_non_trade_activity():
    item = fill("0xc", 1000, "Up", "BUY", 0.5, 1)
    item["type"] = "REDEEM"
    assert normalize_trade_item(item, "activity") is None


def test_canonical_outcome_mapping():
    assert canonical_outcome("Up", 0)[0] == "Up"
    assert canonical_outcome("Down", 1)[0] == "Down"
    assert canonical_outcome("Yes", 0) == ("Up", "yes_no_mapped")
    assert canonical_outcome("No", 1) == ("Down", "yes_no_mapped")
    assert canonical_outcome("", 0, ["Up", "Down"]) == ("Up", "from_market_outcomes")
    assert canonical_outcome("weird", None)[0] == ""


def test_dedupe_collapses_overlapping_pages():
    a = normalize_trade_item(fill("0xc", 1000, "Up", "BUY", 0.4, 10, tx="0x1"), "activity")
    b = normalize_trade_item(fill("0xc", 1010, "Down", "BUY", 0.5, 10, tx="0x2"), "activity")
    # Two pages overlapping on `a` must not double count it.
    merged = dedupe_batches([[a, b], [a]])
    assert len(merged) == 2
    assert {fingerprint(row) for row in merged} == {fingerprint(a), fingerprint(b)}


def test_dedupe_preserves_genuine_duplicate_fills_inside_one_page():
    a = normalize_trade_item(fill("0xc", 1000, "Up", "BUY", 0.4, 10, tx="0x1"), "activity")
    # Same tx, same price/size, same second: two real partial fills, not a page repeat.
    merged = dedupe_batches([[a, dict(a)], [a]])
    assert len(merged) == 2


def test_load_trade_records_reports_counts(tmp_path):
    cache = standard_scenario(tmp_path / "raw")
    records, report = load_trade_records(cache)
    assert report["total_fills"] == len(records) == 7
    assert report["supplemented_from_trades"] == 0
    assert all(record["source"] == "activity" for record in records)
    assert [record["timestamp"] for record in records] == sorted(
        record["timestamp"] for record in records
    )
