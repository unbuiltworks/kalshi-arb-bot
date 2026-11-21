"""End-to-end: raw cache -> CSVs -> stats -> findings, all offline."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.polymarket_l5 import config
from src.polymarket_l5.analyze import analyze
from src.polymarket_l5.export import build, group_by_window, price_band_name
from src.polymarket_l5.findings import render
from src.polymarket_l5.http_client import DisallowedRequest, get_json
from src.polymarket_l5.fetch import paginate
from src.polymarket_l5.cache import RawCache
from tests.fixtures.l5_synthetic import standard_scenario


def read(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture()
def built(tmp_path):
    cache = standard_scenario(tmp_path / "raw")
    output = tmp_path / "out"
    report = build(cache=cache, output_dir=output)
    return report, output


def test_price_band_assignment():
    assert price_band_name(0.49) == "lt50"
    assert price_band_name(0.50) == "50_59"
    assert price_band_name(0.70) == "70_79"
    assert price_band_name(0.899) == "80_89"
    assert price_band_name(0.90) == "90_plus"
    assert price_band_name(None) == ""


def test_grouping_splits_windows_and_orders_by_time():
    trades = [
        {"window_id": "a", "timestamp": 30, "transaction_hash": "t2"},
        {"window_id": "b", "timestamp": 20, "transaction_hash": "t3"},
        {"window_id": "a", "timestamp": 10, "transaction_hash": "t1"},
    ]
    groups = group_by_window(trades)
    assert set(groups) == {"a", "b"}
    assert [row["timestamp"] for row in groups["a"]] == [10, 30]


def test_build_writes_all_csvs_with_expected_row_counts(built):
    report, output = built
    assert report["rows_written"][config.TRADES_CSV] == 5
    assert report["rows_written"][config.SEQUENCES_CSV] == 5
    assert report["rows_written"][config.SUMMARY_CSV] == 2
    assert report["rows_written"][config.EXCLUSIONS_CSV] == 2
    assert report["windows"] == 2


def test_trade_rows_carry_window_timing_and_ids(built):
    _report, output = built
    rows = read(output / config.TRADES_CSV)
    first = next(row for row in rows if row["transaction_hash"] == "0xa1")
    assert first["asset"] == "BTC"
    assert first["window_minutes"] == "5"
    assert first["outcome"] == "Up"
    assert first["side"] == "BUY"
    assert first["seconds_from_window_start"] == "30"
    assert first["seconds_to_expiry"] == "270"
    assert first["condition_id"] == "0xbtc5m"
    assert first["window_source"] == "both_agree"


def test_sequence_rows_are_cumulative_within_each_window(built):
    _report, output = built
    rows = read(output / config.SEQUENCES_CSV)
    btc = [row for row in rows if row["window_id"] == "0xbtc5m"]
    assert [row["trade_seq"] for row in btc] == ["1", "2", "3"]
    assert float(btc[-1]["cum_up_qty"]) == 150
    assert float(btc[-1]["cum_down_qty"]) == 100
    assert float(btc[-1]["matched_qty"]) == 100
    assert float(btc[-1]["excess_qty"]) == 50
    assert btc[-1]["dominant_side"] == "Up"
    # 45 + 41 spent on Up over 150 shares = 0.573333 average.
    assert float(btc[-1]["wavg_up_price"]) == pytest.approx(0.573333, abs=1e-5)

    eth = [row for row in rows if row["window_id"] == "0xeth15m"]
    assert eth[-1]["dominant_side"] == "BALANCED"
    assert float(eth[-1]["matched_pair_unit_cost"]) == pytest.approx(0.95)


def test_summary_row_captures_bands_thresholds_and_pnl(built):
    _report, output = built
    rows = {row["window_id"]: row for row in read(output / config.SUMMARY_CSV)}

    btc = rows["0xbtc5m"]
    assert btc["asset"] == "BTC" and btc["window_minutes"] == "5"
    assert btc["first_side"] == "Up" and float(btc["first_price"]) == 0.45
    assert btc["cheaper_side_first"] == "yes"
    assert btc["total_trades"] == "3"
    assert float(btc["total_dollars_bought"]) == pytest.approx(45 + 50 + 41)
    assert float(btc["max_price_bought"]) == 0.82
    assert btc["any_buy_ge_70c"] == "yes"
    assert btc["any_buy_ge_80c"] == "yes"
    assert btc["any_buy_ge_90c"] == "no"
    assert float(btc["qty_band_lt50"]) == 100
    assert float(btc["qty_band_50_59"]) == 100
    assert float(btc["qty_band_80_89"]) == 50
    assert btc["seconds_first_to_opposite_side"] == "60"
    assert btc["opposite_side_ever_bought"] == "yes"
    assert btc["balanced_at_end"] == "no"
    assert btc["winning_outcome"] == "Up"
    # Up wins: 150 shares pay $150 against $136 spent.
    assert float(btc["pnl_derived_resolution"]) == pytest.approx(14.0)
    assert btc["pnl_source"] == "derived_resolution"

    eth = rows["0xeth15m"]
    assert eth["asset"] == "ETH" and eth["window_minutes"] == "15"
    assert eth["first_side"] == "Down" and eth["cheaper_side_first"] == "yes"
    assert eth["balanced_at_end"] == "yes"
    assert float(eth["matched_pair_unit_cost"]) == pytest.approx(0.95)
    # Down wins: 200 shares pay $200 against $190 spent.
    assert float(eth["pnl_derived_resolution"]) == pytest.approx(10.0)


def test_out_of_scope_markets_are_audited_not_dropped(built):
    _report, output = built
    rows = read(output / config.EXCLUSIONS_CSV)
    reasons = {row["exclusion_reason"] for row in rows}
    assert reasons == {"window_not_5_or_15:none", "asset_not_btc_eth"}
    assert len(rows) == 2


def test_analyze_and_findings_run_on_the_built_csvs(built):
    _report, output = built
    results = analyze(output)
    assert results["coverage"]["n_trades"] == 5
    assert results["coverage"]["n_windows"] == 2
    assert results["coverage"]["windows_by_asset"] == {"BTC": 1, "ETH": 1}
    assert results["q4_cheaper_side"]["first_buy_cheaper_side"]["successes"] == 2
    assert results["q5_opposite_latency"]["n_windows_with_opposite_buy"] == 2
    assert results["q7_balanced_finish"]["balanced_windows"] == 1
    assert results["q8_profit_vs_loss"]["n_profitable"] == 2
    assert json.loads((output / config.STATS_JSON).read_text())["coverage"]["n_windows"] == 2

    from src.polymarket_l5.analyze import read_csv

    markdown = render(results, read_csv(output / config.SUMMARY_CSV), {"fetch_status": "test"})
    assert "L5 Strategy Findings" in markdown
    assert "Candidate behavioural rules" in markdown
    assert "STATUS: NO DATA" not in markdown


def test_findings_refuses_to_conclude_without_data(tmp_path):
    empty = analyze(tmp_path / "nothing")
    markdown = render(empty, [], {"fetch_status": "failed", "fetch_note": "blocked"})
    assert "STATUS: NO DATA" in markdown
    assert "every question below is" in markdown


def test_http_client_rejects_non_public_targets():
    with pytest.raises(DisallowedRequest):
        get_json("https://example.com/whatever")
    with pytest.raises(DisallowedRequest):
        get_json("http://data-api.polymarket.com/activity")


def test_paginate_stops_when_offset_is_ignored(tmp_path):
    page = [{"transactionHash": "0x1", "timestamp": 1, "asset": "a", "side": "BUY", "size": 1, "price": 0.5}]
    calls = []

    def fake_opener(url, timeout):
        calls.append(url)
        return 200, page * config.PAGE_LIMIT, ""

    cache = RawCache(tmp_path / "raw")
    report = paginate(
        "https://data-api.polymarket.com/activity",
        {"user": "0xabc"},
        cache=cache,
        tag="activity",
        opener=fake_opener,
    )
    assert report["stopped_because"] == "offset_ignored_repeat_page"
    assert len(calls) == 2


def test_paginate_stops_on_short_page_and_records_raw(tmp_path):
    def fake_opener(url, timeout):
        return 200, [{"transactionHash": "0x1", "timestamp": 1}], ""

    cache = RawCache(tmp_path / "raw")
    report = paginate(
        "https://data-api.polymarket.com/activity",
        {"user": "0xabc"},
        cache=cache,
        tag="activity",
        opener=fake_opener,
    )
    assert report["stopped_because"] == "short_page"
    assert report["items"] == 1
    assert len(list(cache.records("activity"))) == 1


def test_paginate_surfaces_http_failure(tmp_path):
    def fake_opener(url, timeout):
        return 403, None, "http 403: blocked"

    cache = RawCache(tmp_path / "raw")
    report = paginate(
        "https://data-api.polymarket.com/activity",
        {"user": "0xabc"},
        cache=cache,
        tag="activity",
        opener=fake_opener,
    )
    assert report["stopped_because"].startswith("http_403")
    assert report["items"] == 0


def test_normalized_jsonl_is_written_beside_the_raw_cache(tmp_path):
    cache = standard_scenario(tmp_path / "raw")
    report = build(cache=cache, output_dir=tmp_path / "out")
    normalized = tmp_path / "normalized"
    # Normalized output follows the cache, so a test run never writes into the repo.
    assert Path(report["normalized_dir"]) == normalized
    assert (normalized / "fills_in_scope.jsonl").exists()
    assert (normalized / "fills_out_of_scope.jsonl").exists()
    in_scope = [json.loads(line) for line in (normalized / "fills_in_scope.jsonl").read_text().splitlines()]
    assert len(in_scope) == 5
