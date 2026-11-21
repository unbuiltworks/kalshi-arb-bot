"""The live BTC reference and the exact market strike.

No network: the feed is driven with a scripted fetch and a scripted clock.
The payload shapes are the venue's own, captured 2026-11-10.
"""
import ast
import math
import os
import random

import pytest

from src import l5_btc_reference as BR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = 1794334500.0                       # 2026-11-10T18:15:00Z
SLUG = "cpc-btc-updown-15m-2026-11-10-1815z"


def market(**over):
    terms = {"marketType": "ASSET_PRICE_MARKET_TYPE_UP_DOWN",
             "asset": {"assetClass": "ASSET_CLASS_CRYPTO", "symbol": "btc"},
             "indexSymbol": "BRTI", "horizon": "15m",
             "windowStart": "2026-11-10T18:15:00Z",
             "windowEnd": "2026-11-10T18:30:00Z",
             "priceToBeat": {"value": "73118.92", "currency": "USD"}}
    slug = over.pop("slug", SLUG)
    terms.update(over)
    return {"market": {"slug": slug, "assetPriceTerms": terms}}


def walk(n, start=73000.0, step=6.0, seed=1, t0=None):
    """A 60-second average of a random walk, as the venue publishes it."""
    rng = random.Random(seed)
    t0 = int(WS) - n if t0 is None else t0
    raw, r = [], start
    for _ in range(n + 60):
        r += rng.gauss(0.0, step)
        raw.append(r)
    return [(t0 + i, sum(raw[i:i + 60]) / 60.0) for i in range(n)]


def flat(n, value=73000.0, end=None):
    end = int(WS) + 300 if end is None else end
    return [(end - n + 1 + i, value) for i in range(n)]


# ================================================================== strike
class TestTheStrike:
    def test_the_venues_own_strike_for_this_exact_window(self):
        k = BR.parse_strike(market(), slug=SLUG, window_start=WS)
        assert k["ok"] and k["strike"] == 73118.92
        assert k["window_start"] == WS and k["window_end"] == WS + 900
        assert k["source"] == "VENUE_PRICE_TO_BEAT" and k["index"] == "BRTI"

    @pytest.mark.parametrize("payload,reason", [
        (None, "MARKET_RECORD_UNREADABLE"),
        ({}, "MARKET_RECORD_UNREADABLE"),
        (market(slug="cpc-btc-updown-15m-2026-11-10-1800z"),
         "STRIKE_IS_FOR_ANOTHER_MARKET"),
        ({"market": {"slug": SLUG}}, "NO_ASSET_PRICE_TERMS"),
        (market(indexSymbol="CME_CF_BRR"), "UNEXPECTED_INDEX"),
        (market(marketType="ASSET_PRICE_MARKET_TYPE_RANGE"),
         "NOT_AN_UP_DOWN_MARKET"),
        (market(asset={"symbol": "eth"}), "UNEXPECTED_ASSET"),
        (market(windowStart="garbage"), "WINDOW_UNREADABLE"),
        (market(windowStart="2026-11-10T18:00:00Z"),
         "STRIKE_WINDOW_MISALIGNED"),
        (market(windowEnd="2026-11-10T18:20:00Z"),
         "WINDOW_IS_NOT_FIFTEEN_MINUTES"),
        (market(priceToBeat=None), "STRIKE_NOT_PUBLISHED_YET"),
        (market(priceToBeat={"value": "0", "currency": "USD"}),
         "STRIKE_NOT_PUBLISHED_YET"),
        (market(priceToBeat={"value": "nan", "currency": "USD"}),
         "STRIKE_NOT_PUBLISHED_YET"),
        (market(priceToBeat={"value": "73118.92", "currency": "EUR"}),
         "STRIKE_NOT_IN_USD"),
    ])
    def test_a_strike_that_could_belong_to_something_else_is_refused(
            self, payload, reason):
        k = BR.parse_strike(payload, slug=SLUG, window_start=WS)
        assert k["ok"] is False and k["reason"] == reason
        assert "strike" not in k

    def test_a_strike_for_the_next_window_is_misaligned(self):
        k = BR.parse_strike(market(), slug=SLUG, window_start=WS + 900)
        assert k["reason"] == "STRIKE_WINDOW_MISALIGNED"


# ============================================================= probability
def est(series, strike=73000.0, now=None, **kw):
    now = series[-1][0] + 1.5 if now is None else now
    return BR.probability_up(series=series, strike=strike,
                             window_end=kw.pop("window_end", WS + 900),
                             now=now, **kw)


class TestTheProbability:
    def test_at_the_strike_it_is_a_coin_flip(self):
        e = est(flat(1200))
        assert e["ok"] and e["p_up"] == pytest.approx(0.5)
        assert e["distance"] == 0.0 and e["seconds_left"] == 599.0

    def test_strike_distance_moves_it_the_right_way(self):
        s = walk(1200, seed=3)
        a = s[-1][1]
        below = est(s, strike=a - 80)["p_up"]
        at = est(s, strike=a)["p_up"]
        above = est(s, strike=a + 80)["p_up"]
        assert below > at > above
        assert below > 0.6 and above < 0.4

    def test_the_same_distance_is_more_certain_with_less_time(self):
        s = flat(1200, 73000.0, end=int(WS) + 300)
        late = flat(1200, 73000.0, end=int(WS) + 800)
        assert est(late, strike=72950)["p_up"] > est(s, strike=72950)["p_up"]
        assert est(late, strike=73050)["p_up"] < est(s, strike=73050)["p_up"]

    def test_more_volatility_pulls_it_toward_a_half(self):
        """Same distance from the EXPECTED settlement, different volatility."""
        calm, wild = walk(1200, step=2.0, seed=5), walk(1200, step=20.0,
                                                        seed=5)
        out = []
        for s in (calm, wild):
            mid = est(s, strike=s[-1][1])["expected_settlement"]
            out.append(est(s, strike=mid - 40.0))
        pc, pw = out[0]["p_up"], out[1]["p_up"]
        assert out[0]["sd"] < out[1]["sd"]
        assert pc > pw > 0.5

    def test_the_volatility_floor_and_inflation_are_applied(self):
        e = est(flat(1200), strike=72990.0)
        assert e["sigma_per_second"] == 0.0
        assert e["sigma_used"] == pytest.approx(
            BR.SIGMA_REL_FLOOR * 73000.0 * BR.VOL_INFLATION)
        assert 0.5 < e["p_up"] < 1.0

    def test_the_raw_index_is_ahead_of_its_average(self):
        """A rising average means the raw index is above it: the expected
        settlement is above the published value by about 29.5 steps."""
        s = flat(1199) + [(int(WS) + 301, 73001.0)]
        s = [(t - 1, v) for t, v in s[:-1]] + [s[-1]]
        s = [(int(WS) + 301 - (len(s) - 1 - i), v) for i, (_t, v)
             in enumerate(s)]
        e = est(s, strike=73000.0)
        assert e["raw_ahead_of_average"] == pytest.approx(29.5)
        assert e["expected_settlement"] == pytest.approx(73001.0 + 29.5)

    def test_inside_the_last_minute_the_outcome_is_nearly_known(self):
        s = flat(1200, 73000.0, end=int(WS) + 889)
        e = est(s, strike=72990.0)
        assert e["seconds_left"] == 10.0
        assert e["p_up"] > 0.99
        # ten seconds out, far less variance than ten seconds of a walk
        assert e["sd"] < e["sigma_used"] * math.sqrt(10.0)

    def test_the_averaging_removes_about_forty_seconds_of_variance(self):
        d = BR.settlement_distribution(a_now=100.0, a_prev=100.0, sigma=1.0,
                                       seconds_left=600.0)
        assert d["sd"] == pytest.approx(math.sqrt(600.0 - 39.5 + 5.0))
        assert d["mean"] == 100.0

    def test_the_two_regimes_meet_at_one_minute(self):
        a = BR.settlement_distribution(a_now=100.0, a_prev=99.0, sigma=1.0,
                                       seconds_left=60.0)
        b = BR.settlement_distribution(a_now=100.0, a_prev=99.0, sigma=1.0,
                                       seconds_left=59.999)
        assert a["mean"] == pytest.approx(b["mean"], abs=0.01)
        assert a["sd"] == pytest.approx(b["sd"], rel=0.05)

    def test_the_volatility_estimate_recovers_the_walks_step(self):
        got = []
        for seed in range(6):
            s = walk(1200, step=6.0, seed=seed)
            got.append(BR.sigma_per_second(s, now_bucket=s[-1][0]))
        assert sum(got) / len(got) == pytest.approx(6.0, rel=0.25)

    def test_it_is_calibrated_on_simulated_windows(self):
        """Simulate the venue's own rule and check the forecast against it."""
        rng = random.Random(11)
        hits = {"lo": [0, 0], "mid": [0, 0], "hi": [0, 0]}
        for i in range(600):
            raw, r = [], 73000.0
            for _ in range(1300 + 900 + 60):
                r += rng.gauss(0.0, 6.0)
                raw.append(r)
            avg = [sum(raw[j:j + 60]) / 60.0 for j in range(1300 + 900)]
            t0 = int(WS) - 1300
            series = [(t0 + j, v) for j, v in enumerate(avg)]
            strike = avg[1299]                    # the bucket before WS
            settle = avg[1300 + 899]
            off = rng.choice((120, 400, 700, 850))
            cut = [x for x in series if x[0] <= int(WS) + off]
            e = est(cut, strike=strike)
            assert e["ok"], e
            b = "lo" if e["p_up"] < 0.3 else "hi" if e["p_up"] > 0.7 \
                else "mid"
            hits[b][0] += 1
            hits[b][1] += 1 if settle >= strike else 0
        assert hits["lo"][1] / hits["lo"][0] < 0.25
        assert hits["hi"][1] / hits["hi"][0] > 0.75
        assert 0.3 < hits["mid"][1] / hits["mid"][0] < 0.7


class TestStaleAndMisalignedDataIsRejected:
    def test_a_stale_index_is_no_reference(self):
        s = flat(1200)
        e = est(s, now=s[-1][0] + 1 + BR.MAX_AGE_S + 0.1)
        assert e["ok"] is False and e["p_up"] is None
        assert e["reason"] == "INDEX_STALE"
        assert e["index_age_s"] == pytest.approx(BR.MAX_AGE_S + 0.1)

    def test_a_clock_that_disagrees_with_the_venues_is_no_reference(self):
        s = flat(1200)
        e = est(s, now=s[-1][0] + 1 - BR.CLOCK_SKEW_S - 0.5)
        assert e["reason"] == "CLOCK_MISALIGNED" and e["p_up"] is None

    def test_just_inside_both_bounds_is_accepted(self):
        s = flat(1200)
        assert est(s, now=s[-1][0] + 1 + BR.MAX_AGE_S - 0.1)["ok"]
        assert est(s, now=s[-1][0] + 1 - BR.CLOCK_SKEW_S + 0.1)["ok"]

    @pytest.mark.parametrize("kw,reason", [
        (dict(series=[]), "NO_INDEX"),
        (dict(strike=None), "NO_STRIKE"),
        (dict(strike=0), "NO_STRIKE"),
        (dict(strike=float("nan")), "NO_STRIKE"),
    ])
    def test_missing_inputs(self, kw, reason):
        s = kw.pop("series", flat(1200))
        k = kw.pop("strike", 73000.0)
        e = BR.probability_up(series=s, strike=k, window_end=WS + 900,
                              now=WS + 302.0)
        assert e["ok"] is False and e["reason"] == reason

    def test_a_gap_in_the_last_second_is_no_reference(self):
        s = flat(1200)
        s = s[:-2] + s[-1:]
        assert est(s)["reason"] == "INDEX_GAP"

    def test_too_little_history_is_no_reference(self):
        s = flat(200)
        assert est(s)["reason"] == "TOO_LITTLE_HISTORY_FOR_VOLATILITY"

    def test_a_window_that_is_over_or_not_started_is_no_reference(self):
        late = flat(1200, end=int(WS) + 899)
        assert est(late)["reason"] == "WINDOW_OVER"
        early = flat(1200, end=int(WS) - 200)
        assert est(early)["reason"] == "WINDOW_NOT_STARTED"

    def test_every_estimate_carries_its_clocks(self):
        s = flat(1200)
        e = est(s, now=s[-1][0] + 1.4, received_t=s[-1][0] + 1.2,
                rtt_s=0.15)
        assert e["decision_t"] == s[-1][0] + 1.4
        assert e["bucket_t"] == s[-1][0]
        assert e["index_age_s"] == pytest.approx(0.4)
        assert e["receive_lag_s"] == pytest.approx(0.2)
        assert e["rtt_s"] == 0.15
        assert est(s, now=s[-1][0] + 99)["decision_t"] == s[-1][0] + 99


# ===================================================================== feed
class Venue:
    def __init__(self, series, payload=None):
        self.series, self.payload = dict(series), payload or market()
        self.calls = []
        self.fail = False

    def __call__(self, url):
        self.calls.append(url)
        if self.fail:
            raise OSError("gateway down")
        if "/v1/asset-prices/history" in url:
            q = dict(kv.split("=") for kv in url.split("?", 1)[1].split("&"))
            assert q["assets"] == "crypto:btc"
            assert q["bucket"] == "ASSET_PRICE_BUCKET_1S"
            a, b = int(q["from"]), int(q["to"])
            return {"series": [{
                "asset": {"assetClass": "ASSET_CLASS_CRYPTO",
                          "symbol": "btc"}, "currency": "USD",
                "candles": [{"timestamp": t, "close": "%.2f" % v,
                             "tickCount": 60}
                            for t, v in sorted(self.series.items())
                            if a <= t <= b]}]}
        assert url.endswith("/v1/market/slug/" + SLUG), url
        return self.payload


class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


def feed(n=1200, **kw):
    s = flat(n)
    clock = Clock(s[-1][0] + 1.3)
    venue = Venue(s, **kw)
    r = BR.Reference(fetch=venue, clock=clock)
    r.want(SLUG, WS)
    return r, venue, clock, s


class TestTheFeed:
    def test_it_is_off_unless_the_owner_names_the_source(self, monkeypatch):
        for v in ("", "ON", "1", "venue_index", "COINBASE", "VENUE_INDEX "):
            monkeypatch.setenv("L5_BTC_REFERENCE", v)
            assert BR.enabled() is False
        monkeypatch.delenv("L5_BTC_REFERENCE")
        assert BR.enabled() is False
        monkeypatch.setenv("L5_BTC_REFERENCE", "VENUE_INDEX")
        assert BR.enabled() is True

    def test_one_poll_gives_history_strike_and_an_estimate(self):
        r, venue, clock, s = feed()
        assert r.estimate(SLUG, WS)["reason"] == "STRIKE_NOT_FETCHED_YET"
        assert venue.calls == []            # an estimate never fetches
        r.poll_once()
        assert len(r.series()) == 1200
        assert r.strike(SLUG)["strike"] == 73118.92
        e = r.estimate(SLUG, WS)
        assert e["ok"] and e["strike"] == 73118.92
        assert e["strike_source"] == "VENUE_PRICE_TO_BEAT"
        assert e["index_age_s"] == pytest.approx(0.3)

    def test_later_polls_ask_only_for_what_is_new(self):
        r, venue, clock, s = feed()
        r.poll_once()
        venue.series[s[-1][0] + 1] = 73001.0
        clock.t += 1.0
        r.poll_once()
        url = [u for u in venue.calls if "asset-prices" in u][-1]
        assert "from=%d" % (s[-1][0] + 1) in url
        assert r.series()[-1] == (s[-1][0] + 1, 73001.0)
        assert len([u for u in venue.calls if "/market/slug/" in u]) == 1

    def test_a_bucket_not_built_from_the_full_minute_is_not_the_series(self):
        r, venue, clock, s = feed()
        real = venue.__call__

        def short(url):
            doc = real(url)
            if "asset-prices" in url:
                doc["series"][0]["candles"][-1]["tickCount"] = 37
            return doc
        r._fetch = short
        r.poll_once()
        assert r.series()[-1][0] == s[-2][0]
        assert r.stats["index_bucket_rejected"] == 1

    def test_another_assets_series_is_ignored(self):
        r, venue, clock, s = feed()
        real = venue.__call__

        def eth(url):
            doc = real(url)
            if "asset-prices" in url:
                doc["series"][0]["asset"]["symbol"] = "eth"
            return doc
        r._fetch = eth
        r.poll_once()
        assert r.series() == [] and r.stats["index_series_rejected"] == 1

    def test_a_feed_that_stops_goes_stale_and_says_so(self):
        r, venue, clock, s = feed()
        r.poll_once()
        venue.fail = True
        clock.t += 10.0
        with pytest.raises(OSError):
            r.poll_once()
        e = r.estimate(SLUG, WS)
        assert e["ok"] is False and e["reason"] == "INDEX_STALE"

    def test_a_strike_not_yet_published_is_asked_for_again(self):
        r, venue, clock, s = feed(payload=market(priceToBeat=None))
        r.poll_once()
        assert r.estimate(SLUG, WS)["reason"] == "STRIKE_NOT_PUBLISHED_YET"
        venue.payload = market()
        clock.t += 0.5
        r.poll_once()                       # too soon to ask again
        assert r.estimate(SLUG, WS)["ok"] is False
        clock.t += 2.5
        venue.series[int(clock.t) - 1] = 73000.0
        venue.series[int(clock.t) - 2] = 73000.0
        venue.series[int(clock.t) - 3] = 73000.0
        r.poll_once()
        assert r.strike(SLUG)["ok"] is True

    def test_the_strike_of_one_window_is_never_used_for_another(self):
        r, venue, clock, s = feed()
        r.poll_once()
        e = r.estimate(SLUG, WS + 900)
        assert e["ok"] is False
        assert e["reason"] == "STRIKE_WINDOW_MISALIGNED"

    def test_the_thread_survives_a_failing_gateway(self):
        r, venue, clock, s = feed()
        venue.fail = True
        r._poll_s = 0.01
        r.start()
        import time
        for _ in range(100):
            if r.stats["poll_error:OSError"] >= 2:
                break
            time.sleep(0.01)
        r.stop()
        assert r.stats["poll_error:OSError"] >= 2
        assert r._thread.daemon is True


def test_the_module_reads_and_nothing_else():
    src = open(BR.__file__).read()
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= {"__future__", "collections", "datetime", "json", "math",
                    "os", "threading", "time", "urllib.request", "typing"}
    code = src.split('"""', 2)[2]
    for needle in ("POST", "data=", "Authorization", "api_key", "secret",
                   "/v1/order", "method="):
        assert needle not in code, needle
    assert BR.GATEWAY == "https://gateway.polymarket.us"


def test_the_module_is_in_the_executor_image():
    df = open(os.path.join(ROOT, "deploy", "l5_pmus_live",
                           "Dockerfile")).read()
    assert "COPY src/l5_btc_reference.py src/l5_btc_reference.py" in df
