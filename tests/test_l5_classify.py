"""Parsing and window-classification tests.

The load-bearing rule under test: window length comes from market timestamps,
and a title that disagrees is flagged, not obeyed.
"""

import pytest

from src.l5.classify import (
    classify_asset,
    classify_market,
    parse_ts,
    window_minutes_from_duration,
    window_minutes_from_text,
)
from tests import l5_fixtures as fx


class TestParseTs:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("2026-08-29T20:00:00Z", 1788033600),
            ("2026-08-29T20:00:00+00:00", 1788033600),
            ("2026-08-29T20:00:00", 1788033600),  # naive is treated as UTC
            (1788033600, 1788033600),
            (1788033600000, 1788033600),          # milliseconds
            ("1788033600", 1788033600),
        ],
    )
    def test_accepts_every_shape_polymarket_emits(self, value, expected):
        assert parse_ts(value) == expected

    @pytest.mark.parametrize("value", [None, "", "not a date", 0, -5, True, {}])
    def test_rejects_unusable(self, value):
        assert parse_ts(value) is None


class TestClassifyAsset:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("bitcoin-up-or-down-5m", "BTC"),
            ("BTC Up or Down", "BTC"),
            ("xbt-up-or-down", "BTC"),
            ("ethereum-up-or-down-15m", "ETH"),
            ("ETH Up or Down", "ETH"),
        ],
    )
    def test_recognises_in_scope_assets(self, text, expected):
        assert classify_asset(text) == (expected, False)

    @pytest.mark.parametrize(
        "text", ["solana-up-or-down-5m", "xrp-up-or-down", "doge-up-or-down", "will it rain"]
    )
    def test_rejects_out_of_scope_assets(self, text):
        asset, ambiguous = classify_asset(text)
        assert asset is None
        assert ambiguous is False

    def test_two_in_scope_assets_is_ambiguous(self):
        assert classify_asset("btc vs eth flippening") == (None, True)

    def test_in_scope_asset_next_to_out_of_scope_asset_is_ambiguous(self):
        assert classify_asset("btc vs sol relative performance") == (None, True)

    def test_does_not_match_substrings(self):
        # "ethereum" must not be read as a bare "eth" token match plus noise,
        # and unrelated words containing the letters must not match at all.
        assert classify_asset("bethesda methods") == (None, False)


class TestWindowFromText:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("btc-up-or-down-5m", 5),
            ("BTC Up or Down - 5 minute", 5),
            ("five minute btc", 5),
            ("btc-up-or-down-15m", 15),
            ("BTC Up or Down - 15 minutes", 15),
            ("fifteen minute eth", 15),
        ],
    )
    def test_reads_both_lengths(self, text, expected):
        assert window_minutes_from_text(text) == expected

    def test_15m_is_not_read_as_5m(self):
        assert window_minutes_from_text("btc-up-or-down-15m") == 15

    @pytest.mark.parametrize("text", ["btc-up-or-down-august-29-8pm-et", "btc hourly", ""])
    def test_returns_none_without_a_minute_token(self, text):
        assert window_minutes_from_text(text) is None

    def test_both_lengths_present_is_unusable(self):
        assert window_minutes_from_text("5 minute and 15 minute") is None


class TestWindowFromDuration:
    @pytest.mark.parametrize("seconds,expected", [(300, 5), (270, 5), (330, 5),
                                                  (900, 15), (840, 15), (960, 15)])
    def test_inside_the_bands(self, seconds, expected):
        assert window_minutes_from_duration(seconds) == expected

    @pytest.mark.parametrize("seconds", [269, 331, 600, 839, 961, 3600, 0, -1, None])
    def test_outside_the_bands(self, seconds):
        assert window_minutes_from_duration(seconds) is None


class TestClassifyMarket:
    def test_btc_5m_from_metadata(self):
        c = classify_market(fx.BTC_5M_MARKET)
        assert (c.asset, c.window_minutes, c.window_source) == ("BTC", 5, "metadata")
        assert c.duration_s == 300
        assert c.window_start_ts == fx.BTC5M_START and c.window_end_ts == fx.BTC5M_END
        assert c.outcomes == ("Up", "Down")
        assert c.token_ids == ("tok_up", "tok_down")
        assert c.in_scope

    def test_eth_15m_from_metadata(self):
        c = classify_market(fx.ETH_15M_MARKET)
        assert (c.asset, c.window_minutes, c.duration_s) == ("ETH", 15, 900)
        assert c.in_scope

    def test_timestamps_beat_a_contradicting_title(self):
        c = classify_market(fx.CONFLICT_MARKET)
        assert c.window_minutes == 5, "metadata must win over the title"
        assert c.window_minutes_from_metadata == 5
        assert c.window_minutes_from_text == 15
        assert c.window_conflict is True
        assert c.in_scope

    def test_text_fallback_reconstructs_bounds_and_flags_it(self):
        c = classify_market(fx.NO_START_MARKET)
        assert c.window_minutes == 5
        assert c.window_source == "text_fallback"
        assert c.bounds_reconstructed is True
        assert c.window_start_ts == fx.BTC5M_END - 300
        assert c.in_scope

    def test_hourly_market_is_out_of_scope(self):
        c = classify_market(fx.BTC_HOURLY_MARKET)
        assert c.window_minutes is None
        assert c.window_source == "unclassified"
        assert not c.in_scope

    def test_wrong_asset_is_out_of_scope_even_with_the_right_window(self):
        c = classify_market(fx.SOL_5M_MARKET)
        assert c.window_minutes == 5
        assert c.asset is None
        assert not c.in_scope

    def test_missing_everything_is_unclassified_not_a_crash(self):
        c = classify_market({})
        assert not c.in_scope
        assert c.window_source == "unclassified"
