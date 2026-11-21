"""The synchronized BTC5m recorder: schema, causality, and fail-loud health.

Deterministic and offline. No socket is opened: every HTTP call is a scripted
double, and a test that wants a failure scripts the failure.

WHAT IS PINNED HERE
  the row carries every field a decision needed      TestRowSchema
  the probability can never see its own future        TestCausality
  depth and fills are absent HONESTLY, not guessed    TestNothingIsInvented
  missing or stale inputs read as UNHEALTHY, loudly   TestHealthIsNeverAssumed
  it holds no credential and no execution path        TestReadOnly
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_spec = importlib.util.spec_from_file_location(
    "btc5m_aligned_recorder",
    os.path.join(ROOT, "analysis", "l5", "btc5m_aligned_recorder.py"))
REC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REC)

from src import l5_btc_reference as REF        # noqa: E402

UTC = dt.timezone.utc
START = dt.datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
SLUG = "cpc-btc-updown-5m-2026-11-12-1200z"
STRIKE = 73000.0


def market(*, bid="0.60", ask="0.62", strike=STRIKE, slug=SLUG, closed=False):
    return {
        "slug": slug, "closed": closed,
        "bestBidQuote": {"value": bid, "currency": "USD"},
        "bestAskQuote": {"value": ask, "currency": "USD"},
        "assetPriceTerms": {
            "indexSymbol": "BRTI", "horizon": "5m",
            "windowStart": START.isoformat(),
            "windowEnd": (START + dt.timedelta(minutes=5)).isoformat(),
            "priceToBeat": (None if strike is None
                            else {"value": str(strike), "currency": "USD"}),
        },
    }


class FakeIndex(REC.IndexFeed):
    """The real series/causality logic, a scripted set of buckets."""

    def __init__(self, *, now, seconds=1500, drift=0.0, stale_by=0.0,
                 error=None, empty=False):
        super().__init__(get=None)
        self.last_error = error
        self.last_rtt = 0.01
        self.last_read_t = now
        if empty:
            return
        newest = int(now - 1 - stale_by)
        for i in range(seconds):
            t = newest - i
            self.points[t] = STRIKE + drift * (seconds - i)

    def poll(self, now_epoch):           # never reaches a network
        return None


def row_at(*, t_in_window=30.0, mkt=None, index=None, **ikw):
    now = START + dt.timedelta(seconds=t_in_window)
    idx = index if index is not None else FakeIndex(now=now.timestamp(), **ikw)
    return REC.build_row(slug=SLUG, start=START, now=now,
                         market=(market() if mkt is None else mkt),
                         market_rtt=0.02, market_error=None, index=idx)


# ======================================================================
class TestRowSchema:

    def test_the_row_carries_every_field_the_owner_asked_for(self):
        row, _ = row_at()
        assert row["schema"] == "btc5m_aligned/1"
        # market and window id
        assert row["market"] == SLUG
        assert row["window_start_utc"] == START.isoformat()
        assert row["window_end_utc"].endswith("12:05:00+00:00")
        # UP/DOWN bid, ask, depth
        for leg in ("up", "down"):
            assert set(row[leg]) == {"bid", "ask", "depth"}
        # reference price, strike, time remaining
        assert row["index"]["a"] == pytest.approx(STRIKE)
        assert row["strike"] == STRIKE
        assert row["seconds_remaining"] == pytest.approx(270.0)
        assert row["t_in_window_s"] == pytest.approx(30.0)
        # independent probability WITH a model version
        assert row["p_up"]["ok"] is True
        assert 0.0 <= row["p_up"]["value"] <= 1.0
        assert "l5_btc_reference" in row["p_up"]["model_version"]
        # source timestamps and freshness
        assert row["quote_utc"].startswith("2026-11-12T12:00:30")
        assert isinstance(row["index"]["bucket_t"], int)
        assert row["index"]["age_s"] is not None
        assert row["quote_rtt_s"] == 0.02
        # fills
        assert row["fills"] == []

    def test_down_is_the_complement_of_up_in_own_price_terms(self):
        row, _ = row_at()
        assert row["up"]["bid"] == pytest.approx(0.60)
        assert row["up"]["ask"] == pytest.approx(0.62)
        assert row["down"]["bid"] == pytest.approx(0.38)   # 1 - up ask
        assert row["down"]["ask"] == pytest.approx(0.40)   # 1 - up bid

    def test_the_model_version_names_the_parameters_that_move_the_answer(self):
        mv = REC.model_version()
        for part in ("vol_inflation", "sigma_rel_floor", "max_index_age_s",
                     "averaging_s"):
            assert part in mv

    def test_the_row_is_json_serialisable_as_written(self):
        row, health = row_at()
        assert json.loads(json.dumps(row))["market"] == SLUG
        assert json.loads(json.dumps(health))["ok"] is True


# ======================================================================
class TestCausality:
    """A bucket stamped t covers [t, t+1) and has ended at t+1. The series
    handed to the model must contain nothing that had not ended yet."""

    def test_a_bucket_that_has_not_ended_is_not_in_the_series(self):
        now = START.timestamp() + 30.0
        idx = FakeIndex(now=now)
        idx.points[int(now)] = 999999.0        # ends at now+1: the future
        idx.points[int(now) + 60] = 888888.0   # further future
        series = idx.series(now)
        assert series, "the past should still be there"
        assert all(t + 1.0 <= now for t, _ in series)
        assert 999999.0 not in [v for _, v in series]
        assert 888888.0 not in [v for _, v in series]

    def test_the_probability_never_reads_a_future_bucket(self):
        now = START.timestamp() + 30.0
        idx = FakeIndex(now=now)
        clean, _ = REC.build_row(slug=SLUG, start=START,
                                 now=dt.datetime.fromtimestamp(now, UTC),
                                 market=market(), market_rtt=0.01,
                                 market_error=None, index=idx)
        # plant a future bucket that would, if read, flip the answer hard
        idx.points[int(now)] = STRIKE + 10_000.0
        idx.points[int(now) + 120] = STRIKE + 50_000.0
        polluted, _ = REC.build_row(slug=SLUG, start=START,
                                    now=dt.datetime.fromtimestamp(now, UTC),
                                    market=market(), market_rtt=0.01,
                                    market_error=None, index=idx)
        assert polluted["p_up"]["value"] == clean["p_up"]["value"]
        assert polluted["index"]["a"] == clean["index"]["a"]

    def test_the_newest_usable_bucket_is_the_one_reported(self):
        now = START.timestamp() + 30.0
        idx = FakeIndex(now=now)
        row, _ = REC.build_row(slug=SLUG, start=START,
                               now=dt.datetime.fromtimestamp(now, UTC),
                               market=market(), market_rtt=0.01,
                               market_error=None, index=idx)
        assert row["index"]["bucket_t"] == max(
            t for t, _ in idx.series(now))


# ======================================================================
class TestNothingIsInvented:

    def test_depth_is_null_and_says_why(self):
        row, _ = row_at()
        assert row["up"]["depth"] is None
        assert row["down"]["depth"] is None
        assert "no size at any level" in row["depth_unavailable_reason"]

    def test_depth_is_read_if_the_venue_ever_publishes_it(self):
        """The one place that would carry it, so the claim is falsifiable."""
        m = dict(market(), bestBidSize={"value": "250"},
                 bestAskSize={"value": "17.5"})
        row, _ = row_at(mkt=m)
        assert row["up"]["depth"] == pytest.approx(250.0)
        assert row["down"]["depth"] == pytest.approx(17.5)

    def test_fills_are_empty_and_say_why(self):
        row, _ = row_at()
        assert row["fills"] == []
        assert "holds no account" in row["fills_reason"]

    def test_a_missing_strike_yields_no_probability_rather_than_a_guess(self):
        row, health = row_at(mkt=market(strike=None))
        assert row["strike"] is None
        assert row["p_up"]["ok"] is False
        assert row["p_up"]["value"] is None
        assert row["p_up"]["reason"] == "NO_STRIKE"
        assert health["ok"] is False

    def test_too_little_index_history_yields_no_probability(self):
        row, health = row_at(seconds=60)      # under MIN_HISTORY_S
        assert row["p_up"]["ok"] is False
        assert row["p_up"]["reason"] == "TOO_LITTLE_HISTORY_FOR_VOLATILITY"
        assert health["ok"] is False


# ======================================================================
class TestHealthIsNeverAssumed:
    """The requirement: missing or stale reference data must read as
    unhealthy, not as silence and not as healthy."""

    def test_a_clean_poll_is_healthy_and_names_nothing(self):
        _, health = row_at()
        assert health == {"ok": True, "bad": [], "market": SLUG,
                          "utc": health["utc"]}

    def test_a_stale_index_is_unhealthy_and_named(self):
        row, health = row_at(stale_by=REF.MAX_AGE_S + 5.0)
        assert row["p_up"]["ok"] is False
        assert row["p_up"]["reason"] == "INDEX_STALE"
        assert health["ok"] is False
        assert any(b.startswith("probability:INDEX_STALE")
                   for b in health["bad"]), health

    def test_an_index_read_error_is_unhealthy_and_named(self):
        _, health = row_at(error="URLError")
        assert health["ok"] is False
        assert "index:URLError" in health["bad"]

    def test_no_index_buckets_at_all_is_unhealthy_and_named(self):
        _, health = row_at(empty=True)
        assert health["ok"] is False
        assert "index:NO_BUCKETS" in health["bad"]

    def test_a_market_read_error_is_unhealthy_and_named(self):
        now = START + dt.timedelta(seconds=30)
        _, health = REC.build_row(
            slug=SLUG, start=START, now=now, market=None, market_rtt=None,
            market_error="HTTPError", index=FakeIndex(now=now.timestamp()))
        assert health["ok"] is False
        assert "market:HTTPError" in health["bad"]

    def test_a_one_sided_quote_is_unhealthy_and_named(self):
        m = market()
        m["bestBidQuote"] = None
        _, health = row_at(mkt=m)
        assert health["ok"] is False
        assert "quote:ONE_SIDED_OR_ABSENT" in health["bad"]

    def test_a_record_for_another_market_is_unhealthy_and_named(self):
        _, health = row_at(mkt=market(slug="cpc-btc-updown-5m-2026-01-01-0000z"))
        assert health["ok"] is False
        assert "market:SLUG_MISMATCH" in health["bad"]

    def test_several_faults_are_all_named_not_just_the_first(self):
        _, health = row_at(mkt=market(strike=None), error="timeout")
        assert health["ok"] is False
        assert "strike:ABSENT" in health["bad"]
        assert "index:timeout" in health["bad"]
        assert len(health["bad"]) >= 3

    def test_health_is_false_whenever_the_probability_is_absent(self):
        """The invariant that makes the monitor trustworthy: a row without a
        usable probability can never be reported healthy."""
        for kw in ({"stale_by": 99.0}, {"empty": True}, {"seconds": 10},
                   {"error": "URLError"}):
            row, health = row_at(**kw)
            if not row["p_up"]["ok"]:
                assert health["ok"] is False, kw


# ======================================================================
class TestReadOnly:

    def test_it_holds_no_credential_and_imports_no_execution_path(self):
        src = open(os.path.join(ROOT, "analysis", "l5",
                                "btc5m_aligned_recorder.py"),
                   encoding="utf-8").read()
        for forbidden in ("pmus_auth", "l5_btc5m_execution",
                          "l5_live_execution", "l5_pmus_transport",
                          "l5_live_authorization", "API_SECRET", "submit("):
            assert forbidden not in src, forbidden

    def test_the_only_two_endpoints_are_public_and_unauthenticated(self):
        assert REC.MARKET_URL.startswith("https://gateway.polymarket.us/v1/")
        assert REC.INDEX_URL.startswith("https://gateway.polymarket.us/v1/")
        src = open(os.path.join(ROOT, "analysis", "l5",
                                "btc5m_aligned_recorder.py"),
                   encoding="utf-8").read()
        assert src.count("urllib.request.urlopen") == 1
        assert "Authorization" not in src

    def test_building_a_row_opens_no_socket(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("the recorder reached the network")
        monkeypatch.setattr(REC, "_get", boom)
        row, health = row_at()
        assert row["market"] == SLUG and health["ok"] is True


# ===================================================================
# THE LATE-FAVOURITE CANDIDATES: the unbiased denominator
# ===================================================================
class TestTheRecordedCandidates:
    """A competitor's +$310 over 40 SELECTED WINNING positions
    establishes nothing: the losing and never-filled windows are missing
    from it. These rows are written every poll of every window whatever
    happens, so those windows are in the sample by construction.
    """

    @staticmethod
    def _cand(p_up, up_bid, up_ask):
        from decimal import Decimal as D
        return REC._candidates({"ok": True, "p_up": p_up}, up_bid, up_ask)

    def test_all_three_levels_are_priced(self):
        c = self._cand(0.999, 0.90, 0.92)
        assert [l["price"] for l in c["levels"]] == ["0.97", "0.98", "0.99"]

    def test_the_ev_matches_the_live_gates_own_formula(self):
        """One formula, not two: a second copy here would be a second
        answer, and the recorded economics would drift from the gate."""
        from decimal import Decimal as D
        from src import l5_late_favorite as LF
        c = self._cand(0.995, 0.90, 0.92)
        for lvl in c["levels"]:
            want = LF.ev_per_share(D("0.995"), D(lvl["price"]), D("0"))
            assert D(lvl["ev_per_share"]) == want

    def test_break_even_is_the_price_when_there_is_no_fee(self):
        c = self._cand(0.999, 0.90, 0.92)
        assert all(l["break_even_p"] == l["price"] for l in c["levels"])

    def test_a_level_at_or_above_the_ask_is_marked_crossing(self):
        c = self._cand(0.999, 0.96, 0.98)
        by = {l["price"]: l for l in c["levels"]}
        assert by["0.97"]["would_cross"] is False
        assert by["0.98"]["would_cross"] is True
        assert by["0.99"]["would_cross"] is True
        assert by["0.98"]["inferred_opportunity"] is False

    def test_a_thin_edge_is_not_an_opportunity(self):
        """0.98 win on a 0.98 price is zero EV, so it is recorded and NOT
        flagged."""
        c = self._cand(0.98, 0.90, 0.995)
        by = {l["price"]: l for l in c["levels"]}
        assert by["0.98"]["inferred_opportunity"] is False
        assert by["0.97"]["inferred_opportunity"] is True

    def test_the_down_side_is_priced_off_the_up_bid(self):
        c = self._cand(0.001, 0.02, 0.05)
        assert c["side"] == "DOWN"
        assert c["favourite_ask"] == "0.98"

    def test_fillability_is_never_claimed(self):
        """The public feed publishes no size, so queue position and fill
        probability are unobservable. Writing a number here would
        manufacture the evidence these rows exist to supply honestly."""
        c = self._cand(0.999, 0.90, 0.92)
        assert c["fillable"] is None
        assert "no public size" in c["fillable_reason"]

    def test_our_own_fill_is_never_claimed(self):
        c = self._cand(0.999, 0.90, 0.92)
        assert c["our_fill"] is None
        assert "holds no account" in c["our_fill_reason"]

    def test_the_three_are_distinct_fields(self):
        """An inferred opportunity is a statement about the BOOK. It is
        not a fill, and it is not OUR fill."""
        c = self._cand(0.999, 0.90, 0.92)
        #: the ask must sit ABOVE the level for a maker bid to rest
        c = self._cand(0.9999, 0.98, 0.995)
        assert any(l["inferred_opportunity"] for l in c["levels"])
        assert c["fillable"] is None and c["our_fill"] is None

    @pytest.mark.parametrize("p,bid,ask,reason", [
        (None, 0.90, 0.92, "NO_PROBABILITY"),
        (0.99, None, 0.92, "NO_TWO_SIDED_QUOTE"),
        (0.99, 0.90, None, "NO_TWO_SIDED_QUOTE"),
    ])
    def test_a_missing_input_is_named_not_guessed(self, p, bid, ask, reason):
        c = REC._candidates({"ok": p is not None, "p_up": p}, bid, ask)
        assert c["reason"] == reason and c["levels"] == []
        assert c["fillable"] is None

    def test_an_unusable_probability_yields_no_levels(self):
        c = REC._candidates({"ok": False, "p_up": 0.99}, 0.90, 0.92)
        assert c["levels"] == [] and c["reason"] == "NO_PROBABILITY"
