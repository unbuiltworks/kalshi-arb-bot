"""User-downloaded data-api pages: slug parsing, market synthesis, ingestion."""

import json

import pytest

from src.l5 import config, pipeline
from src.l5.userjson import UserJsonError, ingest_files, parse_slug, synthesize_markets
from src.l5.classify import classify_market

WALLET = config.TARGET_WALLET


def _rec(slug, title, outcome, index, token, ts=1788037300, price=0.5, size=10.0,
         rtype="TRADE", wallet=WALLET):
    return {
        "proxyWallet": wallet, "timestamp": ts, "conditionId": f"0xcond-{slug}",
        "type": rtype, "size": size, "usdcSize": round(price * size, 4),
        "transactionHash": "0xabc", "price": price, "asset": token,
        "side": "BUY", "outcomeIndex": index, "title": title, "slug": slug,
        "eventSlug": slug, "outcome": outcome,
    }


class TestParseSlug:
    @pytest.mark.parametrize("slug,expected", [
        ("btc-updown-15m-1788037200", ("BTC", 15, 1788037200)),
        ("btc-updown-5m-1788037500", ("BTC", 5, 1788037500)),
        ("eth-updown-5m-1788039000", ("ETH", 5, 1788039000)),
    ])
    def test_reads_the_machine_generated_form(self, slug, expected):
        assert parse_slug(slug) == expected

    @pytest.mark.parametrize("slug", [
        "sol-updown-5m-1788037200",      # out-of-scope asset
        "btc-updown-1788037200",          # no minutes
        "bitcoin-up-or-down-5m", "", None,
    ])
    def test_rejects_everything_else(self, slug):
        assert parse_slug(slug) is None


class TestSynthesizeMarkets:
    def test_builds_a_gamma_shaped_record_with_exact_bounds(self):
        recs = [_rec("btc-updown-15m-1788037200",
                     "Bitcoin Up or Down - August 29, 5:00PM-5:15PM ET",
                     "Up", 0, "tok-up"),
                _rec("btc-updown-15m-1788037200",
                     "Bitcoin Up or Down - August 29, 5:00PM-5:15PM ET",
                     "Down", 1, "tok-down")]
        markets, unrecognized = synthesize_markets(recs)
        assert unrecognized == []
        assert len(markets) == 1
        c = classify_market(markets[0])
        assert (c.asset, c.window_minutes, c.window_source) == ("BTC", 15, "metadata")
        assert c.window_start_ts == 1788037200 and c.window_end_ts == 1788038100
        assert c.token_ids == ("tok-up", "tok-down")
        assert not c.window_conflict, "slug and title clock-span agree"

    def test_title_disagreeing_with_slug_is_flagged_as_conflict(self):
        recs = [_rec("btc-updown-15m-1788037200",
                     "Bitcoin Up or Down - August 29, 5:00PM-5:05PM ET",  # says 5m
                     "Up", 0, "tok-up")]
        markets, _ = synthesize_markets(recs)
        c = classify_market(markets[0])
        assert c.window_minutes == 15, "slug-derived duration wins"
        assert c.window_conflict is True

    def test_unrecognized_slug_yields_no_market_and_is_reported(self):
        recs = [_rec("sol-updown-5m-1788037200", "Solana Up or Down", "Up", 0, "t")]
        markets, unrecognized = synthesize_markets(recs)
        assert markets == []
        assert unrecognized == ["sol-updown-5m-1788037200"]


class TestIngest:
    def test_end_to_end_through_the_pipeline(self, tmp_path):
        page = [
            _rec("btc-updown-5m-1788037500",
                 "Bitcoin Up or Down - August 29, 5:05PM-5:10PM ET",
                 "Up", 0, "tok-u", ts=1788037510, price=0.4, size=100.0),
            _rec("btc-updown-5m-1788037500",
                 "Bitcoin Up or Down - August 29, 5:05PM-5:10PM ET",
                 "Down", 1, "tok-d", ts=1788037540, price=0.55, size=100.0),
            _rec("btc-updown-5m-1788037500",
                 "Bitcoin Up or Down - August 29, 5:05PM-5:10PM ET",
                 "Up", 0, "tok-u", ts=1788038200, price=1.0, size=100.0,
                 rtype="REDEEM"),
        ]
        src = tmp_path / "p0.json"
        src.write_text(json.dumps(page))
        cache = tmp_path / "cache"
        manual = tmp_path / "manual"; manual.mkdir()

        stats = ingest_files([str(src)], str(cache))
        assert stats.records == 3 and stats.trades == 2 and stats.non_trades == 1
        assert stats.markets_synthesized == 1

        built = pipeline.build(str(cache), str(tmp_path / "out"), manual_dir=str(manual))
        assert built["normalize_stats"]["parsed"] == 2
        summary = built["windows"][0].summary
        assert summary.seconds_to_opposite_side == 30
        assert summary.matched_pair_cost_per_share == pytest.approx(0.95)

    def test_rows_for_another_wallet_are_refused(self, tmp_path):
        page = [_rec("btc-updown-5m-1788037500", "t", "Up", 0, "tok",
                     wallet="0x" + "9" * 40)]
        src = tmp_path / "p0.json"
        src.write_text(json.dumps(page))
        stats = ingest_files([str(src)], str(tmp_path / "cache"))
        assert stats.records == 0 and stats.wrong_wallet == 1

    def test_non_array_json_is_an_error(self, tmp_path):
        src = tmp_path / "bad.json"
        src.write_text('{"unexpected": true}')
        with pytest.raises(UserJsonError):
            ingest_files([str(src)], str(tmp_path / "cache"))


class TestSupersession:
    def test_api_window_supersedes_its_screenshot_transcription(self, tmp_path):
        # API page for the 21:00-21:15 BTC 15m window...
        page = [_rec("btc-updown-15m-1788037200",
                     "Bitcoin Up or Down - August 29, 5:00PM-5:15PM ET",
                     "Up", 0, "tok-u", ts=1788037260, price=0.6, size=10.0)]
        src = tmp_path / "p0.json"
        src.write_text(json.dumps(page))
        cache = tmp_path / "cache"
        ingest_files([str(src)], str(cache))

        # ...and a screenshot batch transcribing the same window.
        manual = tmp_path / "manual"; manual.mkdir()
        (manual / "b1.json").write_text(json.dumps({
            "batch": 1,
            "profile_header": {"screenshot_clock_local": "2026-08-29T17:13:00-04:00"},
            "pages": [{"image": 1, "screenshot_clock_local": "2026-08-29T17:13:00-04:00",
                       "rows": [{"market_title": "Bitcoin Up or Down - August 29, 5:00PM-5:15PM ET",
                                 "action": "Buy", "outcome": "Down",
                                 "price_cents_displayed": 40, "shares": 5.0,
                                 "dollars": 2.0, "relative_minutes": 4}]}],
        }))

        built = pipeline.build(str(cache), str(tmp_path / "out"), manual_dir=str(manual))
        # Only the API row survives; the screenshot row was superseded.
        assert built["normalize_stats"]["parsed"] == 1
        assert built["trades"][0].source == "activity"
        assert built["manual_stats"]["superseded_by_api"] == {"windows": 1, "rows": 1}
