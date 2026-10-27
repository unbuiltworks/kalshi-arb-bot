"""The expiry a concurrency decision rests on must come from the venue.

WHAT THE VENUE ACTUALLY STATES, and what we derive. Three different
instants get called "the end of the window" and only two of them are the
venue's:

  * the venue states the window's START (`/v1/events` -> `startTime`), and
    `OBS.venue_window_bounds` offers bounds ONLY when it confirms;
  * the venue states the TRADING CUTOFF by moving `state` off
    MARKET_STATE_OPEN -- observed on 1500z at 2026-10-27T15:15:00.449Z,
    the `transact_time` of the first CLOSED book;
  * the venue states SETTLEMENT separately and later, as a terminal state
    carrying a binary `settlementPx` -- read for that same 1500z at
    15:16:26.35Z, 86s after the cutoff.

The CLOSE we currently use is none of those: it is `start + AB.WINDOW_S`,
arithmetic anchored on a venue-confirmed start. That is fine for a margin
on a window we are already trading and it is NOT an authoritative expiry,
which is why nothing here lets it become one.

These tests pin the refusals, not the happy path: every way the expiry can
be unknown or self-inconsistent has to end in a named refusal or in the
market KEEPING its concurrency slot. A market we cannot date is a market
we go on treating as live.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_live_execution as EX
from src import l5_pmus_realtime_observer as OBS

WS = 1761580800                      # 2026-10-27T15:20:00Z grid point
NOW = dt.datetime(2026, 10, 27, 15, 5, tzinfo=dt.timezone.utc)


# ------------------------------------------- the venue must CONFIRM the start

def test_bounds_are_none_when_the_venue_does_not_confirm_alignment():
    """`aligned: False` is a DISAGREEMENT, and it yields no bounds at all."""
    rep = {"aligned": False, "pmus_start_utc": "2026-10-27T15:30:00+00:00",
           "why": "PMUS startTime != L5 window epoch"}
    assert OBS.venue_window_bounds(WS, 900, verify=lambda _ws: rep) is None


def test_bounds_are_none_when_alignment_is_undecided():
    """`aligned: None` is the shape a FAILED read takes. Not a fallback."""
    rep = {"aligned": None, "why": "alignment check failed: URLError"}
    assert OBS.venue_window_bounds(WS, 900, verify=lambda _ws: rep) is None


def test_bounds_are_none_when_the_start_is_missing():
    """Aligned with no `pmus_start_utc` is incoherent, so it is refused."""
    rep = {"aligned": True}
    assert OBS.venue_window_bounds(WS, 900, verify=lambda _ws: rep) is None


def test_confirmed_bounds_are_stamped_VENUE_and_span_the_nominal_window():
    rep = {"aligned": True, "pmus_start_utc": "2026-10-27T15:20:00+00:00"}
    out = OBS.venue_window_bounds(WS, 900, verify=lambda _ws: rep)
    assert out["source"] == "VENUE"
    assert out["start"] == "2026-10-27T15:20:00+00:00"
    # DERIVED, not stated: the venue gave the start and nothing else.
    assert out["end"] == "2026-10-27T15:35:00+00:00"


# --------------------------------------------- and arming must refuse by name

def _expiry(bounds, nominal=900.0):
    return AUTH.window_expiry(bounds, now=NOW, ttl_s=900.0,
                              nominal_s=nominal)


@pytest.mark.parametrize("bounds,reason", [
    (None, "WINDOW_BOUNDS_UNKNOWN"),
    ({}, "WINDOW_BOUNDS_UNKNOWN"),
    ({"source": "CLOCK_GRID", "start": "2026-10-27T15:20:00+00:00",
      "end": "2026-10-27T15:35:00+00:00"}, "WINDOW_BOUNDS_UNCONFIRMED"),
    ({"source": "VENUE", "start": "not-a-time",
      "end": "2026-10-27T15:35:00+00:00"}, "WINDOW_BOUNDS_UNREADABLE"),
    ({"source": "VENUE", "start": "2026-10-27T15:35:00+00:00",
      "end": "2026-10-27T15:20:00+00:00"}, "WINDOW_BOUNDS_IMPLAUSIBLE"),
])
def test_every_unusable_expiry_refuses_by_name(bounds, reason):
    with pytest.raises(AUTH.ArmingRefusal) as exc:
        _expiry(bounds)
    assert exc.value.args[0] == reason


def test_a_contract_expiry_is_not_a_window_close():
    """A span that is not this series' window is refused, not rescaled.

    The guard exists for a producer that hands over a real venue-stated
    end. It cannot fire for `venue_window_bounds`, which BUILDS the end as
    start + nominal -- so this is the only place the check is exercised at
    all, and it is why that producer's end is not an authoritative expiry.
    """
    with pytest.raises(AUTH.ArmingRefusal) as exc:
        _expiry({"source": "VENUE", "start": "2026-10-27T15:20:00+00:00",
                 "end": "2026-10-28T15:20:00+00:00"})
    assert exc.value.args[0] == "WINDOW_BOUNDS_IMPLAUSIBLE"


# ---------------------------------- and the concurrency count stays conservative

def limits():
    return EX.SafetyLimits(
        max_usd_per_market=D("20"), max_abs_residual=D("10"),
        max_total_exposure_usd=D("20"), max_concurrent_markets=1,
        max_live_orders=8, daily_realized_loss_halt_usd=D("5"))


A = "cpc-btc-updown-15m-2026-10-27-1500z"
B = "cpc-btc-updown-15m-2026-10-27-1515z"


@pytest.mark.parametrize("expired", [None, False, "yes", 1, "MARKET_STATE_EXPIRED"])
def test_only_a_literal_True_expiry_frees_the_slot(tmp_path, expired):
    """`is True`, not truthiness.

    A producer that reports the venue's state string, or a 1 from a
    database column, must not free a slot by accident. Anything that is
    not the boolean True leaves the market exactly where it was.
    """
    store = EX.InventoryStore(str(tmp_path / "book.json"))
    store.state["committed"] = {A: "1.00"}
    assert store.settlement_pending_markets(expired={A: expired}) == []
    assert limits().refuse(
        price=D("0.50"), qty=D("1"), market=B,
        committed_by_market={A: D("1.00")}, residual_after=D(0),
        realized_today=D(0), open_markets=[A],
        outstanding_by_market={}, live_order_count=0,
        settlement_pending=store.settlement_pending_markets(
            expired={A: expired})) == "MAX_CONCURRENT_MARKETS"
