"""PMUS_WS_SERIES: rolling a recurring crypto series through WS discovery.

A recurring 15-minute series creates every window hours ahead and expires each
on a hard clock, so PMUS_WS_MARKETS cannot express "the one trading now": each
window is its own market, the list dies within fifteen minutes, and because
explicit discovery returns EARLY it takes the sports slate down with it while
it lives.

These tests pin the three properties that matter: the sports default is
untouched, the universe follows the wall clock, and nothing about the
recorder's read-only outbound contract changes.
"""
from __future__ import annotations

import datetime as dt

from src import pmus_ws_evidence_recorder as R

NOW = dt.datetime(2026, 9, 23, 14, 32, tzinfo=dt.timezone.utc)


def _ev(hhmm, closed=False):
    h, m = int(hhmm[:2]), int(hhmm[2:])
    return {"slug": "btc-updown-15m-%s" % hhmm, "closed": closed,
            "startTime": "2026-09-23T%02d:%02d:00Z" % (h, m),
            "markets": [{"slug": "cpc-btc-updown-15m-%s" % hhmm}]}


def _patch(monkeypatch, events, now=NOW):
    series = [{"id": "375", "slug": "btc-updown-15m"}]

    def fake_get(path):
        off = int(path.split("offset=")[1]) if "offset=" in path else 0
        if path.startswith("/v1/series"):
            return {"series": series if off == 0 else []}
        if path.startswith("/v1/events"):
            return {"events": events if off == 0 else []}
        return {}

    from src import pmus_sports_collector as col
    monkeypatch.setattr(col, "http_get_json", fake_get)

    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(dt, "datetime", _DT)


def test_sports_discovery_is_the_default_and_is_untouched(monkeypatch):
    monkeypatch.delenv(R.SERIES_ENV, raising=False)
    monkeypatch.delenv(R.MARKETS_ENV, raising=False)
    called = {}

    def fake(*a, **k):
        called["sports"] = True
        return {}
    from src import pmus_sports_collector as col
    monkeypatch.setattr(col, "http_get_json", fake)
    R.discover_universe()
    assert called.get("sports") is True


def test_the_series_universe_is_the_window_trading_now(monkeypatch):
    monkeypatch.setenv(R.SERIES_ENV, "btc-updown-15m")
    _patch(monkeypatch, [_ev("1415", closed=True), _ev("1430"), _ev("1445")])
    got = [r["slug"] for r in R.discover_universe()]
    assert got == ["cpc-btc-updown-15m-1430"]


def test_a_closed_window_is_never_subscribed(monkeypatch):
    monkeypatch.setenv(R.SERIES_ENV, "btc-updown-15m")
    _patch(monkeypatch, [_ev("1400", closed=True), _ev("1415", closed=True),
                         _ev("1430")])
    assert [r["slug"] for r in R.discover_universe()] == [
        "cpc-btc-updown-15m-1430"]


def test_the_next_window_joins_inside_the_lead(monkeypatch):
    """At 14:44:30 the 14:45 window is about to open and must already be
    subscribed, or its first seconds are lost at rollover."""
    late = dt.datetime(2026, 9, 23, 14, 44, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setenv(R.SERIES_ENV, "btc-updown-15m")
    _patch(monkeypatch, [_ev("1430"), _ev("1445")], now=late)
    assert [r["slug"] for r in R.discover_universe()] == [
        "cpc-btc-updown-15m-1430", "cpc-btc-updown-15m-1445"]


def test_rows_carry_their_provenance(monkeypatch):
    monkeypatch.setenv(R.SERIES_ENV, "btc-updown-15m")
    _patch(monkeypatch, [_ev("1430")])
    row = R.discover_universe()[0]
    assert row["why"] == "series:btc-updown-15m"
    assert row["market_meta"]["series"] == "btc-updown-15m"


def test_an_unknown_series_yields_nothing_not_the_sports_slate(monkeypatch):
    monkeypatch.setenv(R.SERIES_ENV, "no-such-series")
    _patch(monkeypatch, [_ev("1430")])
    assert R.discover_universe() == []


def test_explicit_markets_still_win(monkeypatch):
    monkeypatch.setenv(R.MARKETS_ENV, "aec-one,aec-two")
    monkeypatch.setenv(R.SERIES_ENV, "btc-updown-15m")
    assert [r["slug"] for r in R.discover_universe()] == ["aec-one", "aec-two"]


def test_the_read_only_outbound_contract_is_unchanged():
    """This change subscribes to different markets. It must not make the
    recorder capable of sending anything new."""
    assert R.OUTBOUND_FRAME_KEYS == frozenset(
        {"subscribe", "unsubscribe", "heartbeat"})
    for frame in R.subscribe_frames(["cpc-btc-updown-15m-1430"]):
        assert set(frame) <= R.OUTBOUND_FRAME_KEYS
        R.guard_outbound(frame)


def test_both_market_data_and_the_trade_tape_are_subscribed():
    types = {f["subscribe"]["subscription_type"]
             for f in R.subscribe_frames(["cpc-btc-updown-15m-1430"])}
    assert types == {R.SUB_MARKET_DATA, R.SUB_TRADE}
