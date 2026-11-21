"""The realtime probe must handle an action WHEN OBSERVED, not replay it.

The close-of-window batch run refused all four real L5 actions with
STALE_ACTION at 229-348s against the executor's 30s bound. That was not the
executor being wrong; it was being fed stale input. These tests pin that the
input is now fresh and that nothing about the bounds moved.
"""
from __future__ import annotations

import datetime as dt
import time
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import l5_probe_manifest as PM
from scripts import l5_pmus_realtime_probe as RT


def test_the_action_receipt_is_now_not_the_l5_event_time(monkeypatch):
    """An action seen now is handled now; the venue's own timestamp is kept
    separately so nothing about when L5 acted is lost."""
    ws = int(time.time() // RT.WINDOW_S) * RT.WINDOW_S
    slug = RT.current_slug()
    # bind the window to a market carrying this row's token, so the identity
    # gate has something authoritative to match against without a network call
    from src import l5_market_binding as MB
    from src import l5_window_actions as WA
    WA._BINDINGS[int(ws)] = MB.Binding(
        window_start=int(ws), condition_id="c",
        token_up="77777777", token_down="88888888", slug="bound")

    class FakeObs:
        @staticmethod
        def l5_activity(limit=100):
            return [{"timestamp": ws + 5.0, "outcome": "Up", "side": "BUY",
                     "price": "0.70", "size": "22.13", "usdcSize": "15",
                     "transactionHash": "0xaa", "conditionId": "c",
                     # a real activity row carries the token it traded, and
                     # the identity gate admits on that token
                     "asset": "77777777"}]

        @staticmethod
        def action_key(r):
            return "k-aa"

        @staticmethod
        def l5_to_pmus_action(r):
            return {"pmus_side": "UP", "l5_price": "0.70",
                    "l5_size": "22.13"}

    import sys
    import src
    monkeypatch.setitem(sys.modules, "src.l5_pmus_realtime_observer", FakeObs)
    monkeypatch.setattr(src, "l5_pmus_realtime_observer", FakeObs,
                        raising=False)
    seen = set()
    out, feed_ok = RT.new_l5_actions(slug, seen)
    assert feed_ok is True
    assert len(out) == 1
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
        out[0]["receipt_utc"])).total_seconds()
    assert age < EX.STALE_ACTION_MAX_S, "handled action must not start stale"
    assert out[0]["l5_event_utc"]          # L5's own time is preserved
    # and it is not handed out twice
    assert RT.new_l5_actions(slug, seen) == ([], True)


def test_the_stale_bound_itself_is_unchanged():
    assert EX.STALE_ACTION_MAX_S == 30.0


def test_the_probe_loads_the_authorised_envelope_and_nothing_wider():
    d = PM.probe_limits().as_dict()
    assert d["max_usd_per_market"] == "5"
    assert d["max_total_exposure_usd"] == "5"
    assert d["daily_realized_loss_halt_usd"] == "5"
    assert d["max_abs_residual"] == "10"
    assert d["max_concurrent_markets"] == 1
    assert d["band"] is None
    assert PM.worst_case()["worst_case_total_usd"] == "5.2375"


def test_a_one_sided_live_book_is_not_offered_as_usable(monkeypatch):
    from src import pmus_ws_evidence_recorder as R
    slug = "cpc-btc-updown-15m-2026-09-24-0000z"
    monkeypatch.setattr(R, "LIVE_MSTATE", {slug: {"bbo_history": [
        {"t": 1.0, "bid": "0.4", "ask": None, "bid_qty": "1"},
        {"t": 2.0, "bid": "0.4", "ask": "0.6", "bid_qty": "1",
         "ask_qty": "2"}]}}, raising=False)
    books = RT.live_books(slug)
    assert [b["status"] for b in books] == ["ONE_SIDED", "OK"]


def test_no_live_state_yields_no_book_rather_than_an_exception(monkeypatch):
    from src import pmus_ws_evidence_recorder as R
    monkeypatch.setattr(R, "LIVE_MSTATE", None, raising=False)
    assert RT.live_books("cpc-btc-updown-15m-2026-09-24-0000z") == []
    assert RT.live_trades("cpc-btc-updown-15m-2026-09-24-0000z") == []


def test_the_poll_is_a_small_fraction_of_the_stale_bound():
    # otherwise the probe manufactures the staleness it is meant to avoid
    assert RT.POLL_S * 3 < EX.STALE_ACTION_MAX_S
