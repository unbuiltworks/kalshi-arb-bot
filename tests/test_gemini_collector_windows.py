"""Window-aware subscription lifecycle for the read-only Gemini collector.

THE DEFECT THESE PIN: the historical tape carried a median of 170 two-sided
observations per 5m market and a MEDIAN OF ZERO SECONDS inside that market's
own 300 s window, because the events feed returns windows as active long
before they open and nothing was ever unsubscribed. 69.3% of windows had no
usable in-window quote at all.
"""
from datetime import datetime, timezone

from src.policy_d_port import collector as C


def test_expiry_epoch_round_trips_and_never_raises():
    t = C._expiry_epoch("2026-08-30T23:20:00.000Z")
    assert t is not None
    assert datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M") == "23:20"
    for bad in (None, "", "not-a-date", 17, []):
        assert C._expiry_epoch(bad) is None


def test_subscription_lead_covers_the_whole_window():
    """A 5m window is 300 s; the lead must cover it with margin, or the
    collector re-creates the coverage hole it exists to close."""
    assert C.GEMINI_SUBSCRIBE_LEAD_S >= 300.0
    assert C.GEMINI_RETAIN_AFTER_S > 0


def test_bbo_cadence_resolves_l5_timescale():
    """Canonical L5 acts on a 1-4 s cadence; a 5 s sample cannot see it."""
    assert C.GEMINI_BBO_RECORD_SECONDS <= 1.0


def test_canonical_bbo_carries_tte_and_provenance():
    meta = {"asset": "BTC", "window_minutes": 5,
            "expiry_t": 1000.0}
    row = C._canonical_bbo("GEMI-BTC05M-UP", meta, 900.0,
                           [["0.48", "500"]], [["0.50", "250"]])
    assert row["tte_s"] == 100.0
    assert row["window_start_t"] == 700.0
    assert row["bid"] == 0.48 and row["ask"] == 0.50
    assert row["bid_sz"] == 500.0 and row["ask_sz"] == 250.0
    assert round(row["spread"], 6) == 0.02
    assert row["asset"] == "BTC"
    assert row["expiry_source"] == "gemini_events_rest"
    assert len(row["depth"]["bids"]) == 1


def test_canonical_bbo_refuses_one_sided_books():
    """A one-sided book is recorded raw but is NOT a state: returning a row
    here would let a habitat/trajectory consumer treat it as executable."""
    meta = {"asset": "ETH", "window_minutes": 5, "expiry_t": 1000.0}
    assert C._canonical_bbo("X", meta, 900.0, [], [["0.5", "1"]]) is None
    assert C._canonical_bbo("X", meta, 900.0, [["0.4", "1"]], []) is None
    assert C._canonical_bbo("X", meta, 900.0, None, None) is None


def test_canonical_bbo_tolerates_missing_expiry_without_faking_tte():
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": None}
    row = C._canonical_bbo("X", meta, 900.0, [["0.4", "1"]], [["0.5", "1"]])
    assert row["tte_s"] is None and row["window_start_t"] is None


def test_health_alarm_fires_when_active_windows_are_unsubscribed():
    """The exact silent failure: windows are live and we are attached to none."""
    class DU:
        free, used, total = 50e9, 1e9, 100e9
    assert C._health_alarm({"expected_active": 3, "subscribed": 0},
                           1000.0, DU()) == "ACTIVE_WINDOWS_BUT_NONE_SUBSCRIBED"


def test_health_alarm_fires_on_quote_silence_and_on_disk():
    class DU:
        free, used, total = 50e9, 1e9, 100e9

    class Full:
        free, used, total = 1e9, 99e9, 100e9
    assert C._health_alarm({"last_bbo_t": 800.0}, 1000.0, DU()) == "NO_BBO_FOR_120S"
    assert C._health_alarm({"reconnects": 5}, 1000.0, DU()) == \
        "CONNECTED_BUT_NEVER_RECEIVED_A_QUOTE"
    assert C._health_alarm({"last_bbo_t": 999.0}, 1000.0, Full()) == "DISK_BELOW_2GB"


def test_health_alarm_silent_when_healthy():
    class DU:
        free, used, total = 50e9, 1e9, 100e9
    assert C._health_alarm({"expected_active": 2, "subscribed": 2,
                            "last_bbo_t": 999.5}, 1000.0, DU()) is None


def test_discovery_attaches_expiry_epoch_for_the_lifecycle():
    """Without expiry_t the lifecycle cannot decide when to attach, and the
    code must treat that as UNKNOWN rather than subscribing blindly."""
    import inspect
    src = inspect.getsource(C._gemini_active_symbols)
    assert "expiry_t" in src
    sess = inspect.getsource(C._gemini_ws_session)
    assert "UNSUBSCRIBE" in sess          # the half that was missing entirely
    assert "GEMINI_SUBSCRIBE_LEAD_S" in sess


def test_collector_has_no_order_path():
    """Read-only forever: no order verb may appear in this module."""
    import inspect
    src = inspect.getsource(C)
    for forbidden in ("place_order", "new_order", "cancel_order",
                      "/v1/order", "createOrder", "submit_order"):
        assert forbidden not in src, forbidden
