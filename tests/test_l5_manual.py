"""Screenshot-transcription ingestion: titles, seams, timestamps, prices."""

import pytest

from src.l5.classify import window_minutes_from_time_range
from src.l5.manual import (
    ManualParseError,
    convert_batch,
    dedupe_seam,
    parse_market_title,
    timestamp_interval,
)

ET = -4.0  # the batch file states its own offset; no timezone database here


def _row(title, action, outcome, cents, shares, dollars, relative):
    return {
        "market_title": title,
        "action": action,
        "outcome": outcome,
        "price_cents_displayed": cents,
        "shares": shares,
        "dollars": dollars,
        "relative_minutes": relative,
    }


BTC15 = "Bitcoin Up or Down - August 29, 5:00PM-5:15PM ET"
BTC5 = "Bitcoin Up or Down - August 29, 5:10PM-5:15PM ET"


class TestTitleParsing:
    def test_reads_asset_and_exact_window_bounds(self):
        market = parse_market_title(BTC15, 2026, ET)
        assert market.asset == "BTC"
        assert market.window_minutes == 15
        assert market.end_ts - market.start_ts == 900

    def test_five_minute_window(self):
        assert parse_market_title(BTC5, 2026, ET).window_minutes == 5

    def test_ethereum(self):
        market = parse_market_title(
            "Ethereum Up or Down - August 29, 5:00PM-5:05PM ET", 2026, ET
        )
        assert market.asset == "ETH" and market.window_minutes == 5

    def test_span_crossing_midnight(self):
        market = parse_market_title(
            "Bitcoin Up or Down - August 29, 11:55PM-12:10AM ET", 2026, ET
        )
        assert market.window_minutes == 15
        assert market.end_ts > market.start_ts

    def test_condition_id_is_deterministic_across_batches(self):
        a = parse_market_title(BTC15, 2026, ET)
        b = parse_market_title(BTC15, 2026, ET)
        assert a.condition_id == b.condition_id
        assert a.condition_id != parse_market_title(BTC5, 2026, ET).condition_id

    def test_hourly_market_parses_but_is_not_a_5_or_15_window(self):
        market = parse_market_title(
            "Bitcoin Up or Down - August 29, 5:00PM-6:00PM ET", 2026, ET
        )
        assert market.window_minutes == 60

    @pytest.mark.parametrize(
        "title",
        [
            "Solana Up or Down - August 29, 5:00PM-5:15PM ET",   # out-of-scope asset
            "Bitcoin Up or Down - August 29, 5PM ET",             # no span
            "Some other market",
        ],
    )
    def test_rejects_what_it_cannot_read(self, title):
        with pytest.raises(ManualParseError):
            parse_market_title(title, 2026, ET)


class TestTitleTimeRangeInClassifier:
    """The gamma titles carry the same span, so the classifier must read it."""

    def test_reads_the_span_the_label_regex_misses(self):
        assert window_minutes_from_time_range(BTC15) == 15
        assert window_minutes_from_time_range(BTC5) == 5

    def test_rejects_an_hourly_span(self):
        assert window_minutes_from_time_range(
            "Bitcoin Up or Down - August 29, 5:00PM-6:00PM ET"
        ) is None


class TestTimestampInterval:
    def test_a_relative_label_bounds_a_two_minute_interval(self):
        # Clock legible only to the minute (C..C+60), label "4m ago"
        # (elapsed 240..300) => fill in (C-300, C-180).
        lo, hi = timestamp_interval(1000, 4)
        assert (lo, hi) == (700, 820)
        assert hi - lo == 120

    def test_later_screenshot_of_the_same_fill_shifts_the_interval(self):
        early = timestamp_interval(1000, 4)
        late = timestamp_interval(1060, 4)
        assert late[0] > early[0]
        # They still overlap, and the intersection is tighter than either.
        lo, hi = max(early[0], late[0]), min(early[1], late[1])
        assert lo < hi and (hi - lo) < 120


class TestSeamDedup:
    def test_overlapping_scroll_capture_is_stitched_once(self):
        page_a = {"image": 1, "screenshot_clock_local": "2026-08-29T17:13:00-04:00",
                  "rows": [_row(BTC15, "Buy", "Up", 46, 5.0, 2.30, 4),
                           _row(BTC15, "Buy", "Down", 52, 10.0, 5.20, 4)]}
        page_b = {"image": 2, "screenshot_clock_local": "2026-08-29T17:14:00-04:00",
                  "rows": [_row(BTC15, "Buy", "Down", 52, 10.0, 5.20, 5),
                           _row(BTC15, "Buy", "Up", 73, 18.5, 13.52, 5)]}
        rows, dropped = dedupe_seam([page_a, page_b])
        assert dropped == 1
        assert len(rows) == 3

    def test_a_genuinely_repeated_fill_is_not_collapsed(self):
        # Two identical cards in the same screenshot are two fills.
        page = {"image": 1, "screenshot_clock_local": "2026-08-29T17:14:00-04:00",
                "rows": [_row(BTC15, "Buy", "Up", 73, 18.5, 13.52, 5),
                         _row(BTC15, "Buy", "Up", 73, 18.5, 13.52, 5)]}
        rows, dropped = dedupe_seam([page])
        assert dropped == 0 and len(rows) == 2

    def test_non_contiguous_pages_are_flagged_not_silently_joined(self):
        page_a = {"image": 1, "screenshot_clock_local": "2026-08-29T17:13:00-04:00",
                  "rows": [_row(BTC15, "Buy", "Up", 46, 5.0, 2.30, 4)]}
        page_b = {"image": 2, "screenshot_clock_local": "2026-08-29T17:14:00-04:00",
                  "rows": [_row(BTC15, "Buy", "Up", 73, 18.5, 13.52, 9)]}
        rows, dropped = dedupe_seam([page_a, page_b])
        assert dropped == 0
        assert rows[1].get("_seam_gap") is True


class TestConvertBatch:
    @pytest.fixture
    def batch(self):
        return {
            "profile_header": {"screenshot_clock_local": "2026-08-29T17:13:00-04:00"},
            "pages": [
                {"image": 1, "screenshot_clock_local": "2026-08-29T17:13:00-04:00",
                 "rows": [
                     _row(BTC5, "Buy", "Up", 92, 30.5, 28.17, 1),
                     _row(BTC15, "Redeem", None, None, 60.0, 60.00, 2),
                     _row(BTC15, "Buy", "Down", 52, 10.0, 5.20, 4),
                 ]},
            ],
        }

    def test_recovers_the_true_price_from_dollars_not_the_rounded_badge(self, batch):
        activity, _, stats = convert_batch(batch)
        buy = [r for r in activity if r.get("side") == "BUY" and r["size"] == 30.5][0]
        assert buy["price"] == pytest.approx(28.17 / 30.5)
        assert buy["price"] != pytest.approx(0.92)
        assert buy["priceCentsDisplayed"] == 92
        assert stats.price_rounding_corrections == 1

    def test_emits_api_shaped_records(self, batch):
        activity, markets, _ = convert_batch(batch)
        record = activity[0]
        for key in ("type", "conditionId", "timestamp", "size", "usdcSize"):
            assert key in record
        market = markets[0]
        assert market["outcomes"] == '["Up", "Down"]'
        assert market["conditionId"].startswith("screenshot:")

    def test_separates_trades_from_redemptions(self, batch):
        _, _, stats = convert_batch(batch)
        assert stats.trades == 2
        assert stats.non_trades == 1

    def test_feed_seq_counts_forward_in_time(self, batch):
        activity, _, _ = convert_batch(batch)
        seqs = [r["feedSeq"] for r in activity]
        assert seqs == sorted(seqs)
        # The feed is newest-first, so the oldest row gets feedSeq 0.
        assert activity[0]["size"] == 10.0

    def test_every_row_carries_its_timestamp_uncertainty(self, batch):
        activity, _, _ = convert_batch(batch)
        assert all(r["tsUncertaintyS"] >= 1 for r in activity)


class TestRealBatchOne:
    """Guards the actual transcription committed to data/l5/manual/."""

    @pytest.fixture
    def converted(self):
        import json
        with open("data/l5/manual/batch01.json", encoding="utf-8") as handle:
            return convert_batch(json.load(handle))

    def test_stitches_to_the_expected_row_count(self, converted):
        _, _, stats = converted
        assert stats.rows_read == 27
        assert stats.seam_duplicates_dropped == 3   # three seams, one row each
        assert stats.trades == 23 and stats.non_trades == 1
        assert stats.seam_gaps == 0
        assert stats.unparsed_titles == []

    def test_finds_all_three_windows(self, converted):
        _, markets, _ = converted
        slugs = sorted(m["slug"] for m in markets)
        assert slugs == [
            "btc-up-or-down-15m-20260829T2100z",
            "btc-up-or-down-5m-20260829T2105z",
            "btc-up-or-down-5m-20260829T2110z",
        ]


class TestCrossBatchStitching:
    """A scroll paused between batches still overlaps at the join."""

    def test_batches_are_stitched_as_one_continuous_scroll(self, tmp_path):
        import json as _json
        from src.l5.manual import load_batches

        shared = _row(BTC15, "Buy", "Down", 27, 5.2, 1.40, 5)
        first = {
            "batch": 1,
            "profile_header": {"screenshot_clock_local": "2026-08-29T17:13:00-04:00"},
            "pages": [{"image": 1, "screenshot_clock_local": "2026-08-29T17:13:00-04:00",
                       "rows": [_row(BTC15, "Buy", "Up", 46, 5.0, 2.30, 4), shared]}],
        }
        second = {
            "batch": 2,
            "profile_header": {"screenshot_clock_local": "2026-08-29T17:14:00-04:00"},
            "pages": [{"image": 2, "screenshot_clock_local": "2026-08-29T17:14:00-04:00",
                       "rows": [dict(shared, relative_minutes=6),
                                _row(BTC15, "Buy", "Down", 31, 2.0, 0.62, 6)]}],
        }
        (tmp_path / "batch01.json").write_text(_json.dumps(first))
        (tmp_path / "batch02.json").write_text(_json.dumps(second))

        activity, _, stats = load_batches(
            [str(tmp_path / "batch01.json"), str(tmp_path / "batch02.json")]
        )
        assert stats.rows_read == 4
        assert stats.seam_duplicates_dropped == 1, "the join is a seam, not new data"
        assert len(activity) == 3
        assert stats.batches == ["1", "2"]

    def test_real_batches_stitch_without_gaps(self):
        import glob
        from src.l5.manual import load_batches

        paths = sorted(glob.glob("data/l5/manual/*.json"))
        activity, markets, stats = load_batches(paths)
        assert stats.seam_gaps == 0, "screenshots must form one continuous scroll"
        assert stats.unparsed_titles == []
        assert len(activity) == stats.trades + stats.non_trades
