"""Trade parsing, outcome resolution, scope filtering and deduplication."""

import pytest

from src.l5.normalize import (
    NormalizeStats,
    build_market_index,
    condition_ids_in,
    dedupe_records,
    extract_non_trade_activity,
    normalize,
    resolve_outcome,
)
from src.l5.classify import classify_market
from tests import l5_fixtures as fx


@pytest.fixture
def index():
    return build_market_index(fx.ALL_MARKETS)


class TestMarketIndex:
    def test_indexes_every_market_by_condition_id(self, index):
        assert len(index) == len(fx.ALL_MARKETS)
        assert index["0xbtc5m01"].asset == "BTC"

    def test_marks_only_in_scope_markets(self, index):
        in_scope = {cid for cid, m in index.items() if m.in_scope}
        assert "0xbtc1h01" not in in_scope   # hourly
        assert "0xsol5m01" not in in_scope   # wrong asset
        assert {"0xbtc5m01", "0xeth15m01", "0xbtc5m02"} <= in_scope

    def test_condition_ids_in_reads_both_key_spellings(self):
        assert condition_ids_in(
            [{"conditionId": "a"}, {"condition_id": "b"}, {}]
        ) == {"a", "b"}


class TestResolveOutcome:
    def test_explicit_up_down_text(self, index):
        market = index["0xbtc5m01"]
        assert resolve_outcome({"outcome": "Up"}, market) == "UP"
        assert resolve_outcome({"outcome": "down"}, market) == "DOWN"

    def test_yes_no_resolved_through_the_markets_own_outcome_list(self, index):
        market = index["0xbtc5m05"]
        assert resolve_outcome({"outcome": "Yes", "outcomeIndex": 0}, market) == "UP"
        assert resolve_outcome({"outcome": "No", "outcomeIndex": 1}, market) == "DOWN"

    def test_falls_back_to_token_id(self, index):
        market = index["0xbtc5m01"]
        assert resolve_outcome({"asset": "tok_down"}, market) == "DOWN"

    def test_unresolvable_returns_none_rather_than_guessing(self, index):
        market = index["0xbtc5m01"]
        assert resolve_outcome({"outcome": "Maybe", "asset": "tok_other"}, market) is None


class TestNormalize:
    def test_parses_a_clean_window(self, index):
        trades, stats = normalize({"activity": fx.simple_window_activity()}, index)
        assert stats.parsed == 4
        assert [t.outcome for t in trades] == ["UP", "DOWN", "UP", "DOWN"]
        assert [t.side for t in trades] == ["BUY"] * 4
        first = trades[0]
        assert first.asset == "BTC" and first.window_minutes == 5
        assert first.seconds_from_window_start == 30
        assert first.seconds_to_expiry == 270
        assert first.dollars == pytest.approx(42.0)
        assert first.dollars_derived is False

    def test_derives_dollars_when_usdc_size_is_absent(self, index):
        record = fx.trade("0xbtc5m01", "Up", "BUY", 0.4, 50, fx.BTC5M_START + 10, "0xd1")
        trades, _ = normalize({"activity": [record]}, index)
        assert trades[0].dollars == pytest.approx(20.0)
        assert trades[0].dollars_derived is True

    def test_drops_non_trade_activity(self, index):
        records = fx.simple_window_activity() + [
            fx.trade("0xbtc5m01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 60, "0xm1",
                     activity_type="MERGE")
        ]
        trades, stats = normalize({"activity": records}, index)
        assert stats.parsed == 4
        assert stats.dropped_non_trade == 1

    def test_drops_out_of_scope_and_unknown_markets(self, index):
        records = [
            fx.trade("0xbtc1h01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 10, "0xh1"),
            fx.trade("0xsol5m01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 10, "0xs1"),
            fx.trade("0xunknown", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 10, "0xu1"),
        ]
        trades, stats = normalize({"activity": records}, index)
        assert trades == []
        assert stats.dropped_out_of_scope == 2
        assert stats.dropped_no_market == 1

    def test_drops_rows_with_unusable_numbers(self, index):
        bad = fx.trade("0xbtc5m01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 10, "0xb1")
        bad["price"] = "not a number"
        trades, stats = normalize({"activity": [bad]}, index)
        assert trades == []
        assert stats.dropped_bad_numbers == 1

    def test_flags_window_conflicts_and_reconstructed_bounds(self, index):
        records = [
            fx.trade("0xbtc5m03", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 10, "0xc1"),
            fx.trade("0xbtc5m04", "Up", "BUY", 0.5, 10, fx.BTC5M_END - 10, "0xr1"),
        ]
        trades, stats = normalize({"activity": records}, index)
        assert stats.window_conflicts == 1
        assert stats.bounds_reconstructed == 1
        assert {t.window_source for t in trades} == {"metadata", "text_fallback"}

    def test_output_is_chronological(self, index):
        records = list(reversed(fx.simple_window_activity()))
        trades, _ = normalize({"activity": records}, index)
        assert [t.timestamp for t in trades] == sorted(t.timestamp for t in trades)


class TestDeduplication:
    def test_identical_records_within_a_source_collapse(self):
        record = fx.trade("0xbtc5m01", "Up", "BUY", 0.42, 100, fx.BTC5M_START + 30, "0xa1")
        stats = NormalizeStats()
        merged = dedupe_records({"activity": [record, dict(record), dict(record)]}, stats)
        assert len(merged) == 1
        assert stats.deduped_within_source == 2

    def test_the_same_fill_from_two_endpoints_is_counted_once(self):
        activity = fx.trade("0xbtc5m01", "Up", "BUY", 0.42, 100, fx.BTC5M_START + 30, "0xa1")
        # /trades serves the same fill with a different field set.
        trades = dict(activity)
        trades.pop("type")
        trades["title"] = "Bitcoin Up or Down"
        stats = NormalizeStats()
        merged = dedupe_records({"activity": [activity], "trades": [trades]}, stats)
        assert len(merged) == 1
        assert stats.deduped_across_sources == 1

    def test_a_genuinely_repeated_fill_survives(self):
        # Two distinct fills sharing a transaction, price, size and second.
        # One endpoint reports both, the other reports one -- we must keep two.
        base = fx.trade("0xbtc5m01", "Up", "BUY", 0.42, 100, fx.BTC5M_START + 30, "0xa1")
        leg_a = dict(base, slug="leg-a")
        leg_b = dict(base, slug="leg-b")
        stats = NormalizeStats()
        merged = dedupe_records({"trades": [leg_a, leg_b], "activity": [dict(base)]}, stats)
        assert len(merged) == 2, "multiplicity must come from the richest source"

    def test_end_to_end_dedup_through_normalize(self, index):
        activity = fx.simple_window_activity()
        trades_endpoint = []
        for record in activity:
            copy = dict(record)
            copy.pop("type")
            trades_endpoint.append(copy)
        trades, stats = normalize(
            {"activity": activity, "trades": trades_endpoint}, index
        )
        assert stats.parsed == 4
        assert stats.deduped_across_sources == 4


class TestNonTradeActivity:
    def test_extracts_merges_on_in_scope_markets_only(self, index):
        records = [
            fx.trade("0xbtc5m01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 60, "0xm1",
                     activity_type="MERGE"),
            fx.trade("0xbtc5m01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 61, "0xr1",
                     activity_type="REDEEM"),
            fx.trade("0xbtc1h01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 62, "0xm2",
                     activity_type="MERGE"),
            fx.trade("0xbtc5m01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 63, "0xt1"),
        ]
        rows = extract_non_trade_activity(records, index)
        assert [r["activity_type"] for r in rows] == ["MERGE", "REDEEM"]
        assert all(r["asset"] == "BTC" for r in rows)

    def test_overlapping_pages_do_not_double_count_merges(self, index):
        merge = fx.trade("0xbtc5m01", "Up", "BUY", 0.5, 10, fx.BTC5M_START + 60, "0xm1",
                         activity_type="MERGE")
        rows = extract_non_trade_activity([merge, dict(merge), dict(merge)], index)
        assert len(rows) == 1
