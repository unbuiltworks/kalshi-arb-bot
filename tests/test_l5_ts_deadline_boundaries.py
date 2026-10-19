"""The probe's terminal deadline, at the exact boundaries.

THE BUG THESE PIN. `AB.slug_start` returns the window START and
`cancel_cutoff` takes the window END, so passing the start gave
`start - 180`: a deadline 180 seconds BEFORE the window opened, already
expired on the first tick, which cancelled the probe's legs the moment they
rested. Found by independent review. These call the REAL
Runner.maybe_ts_deadline, not a source string.
"""
from decimal import Decimal as D

import pytest

from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TSP

SLUG = "cpc-btc-updown-15m-2026-10-15-2000z"
WS = 1_792_094_400.0            # the window START
WE = WS + TSP.WINDOW_S          # ... and its END
CUT = WE - TSP.CANCEL_MARGIN_S  # the terminal deadline: start + 720


class FakeTransport:
    def __init__(self):
        self.market_slug = SLUG
        self.armed = False
        self.cancelled = []
        self.reread = 0

    def cancel(self, venue_order_id, market_slug=None):
        self.cancelled.append(venue_order_id)
        return {"ok": True, "venue_order_id": venue_order_id}

    def order(self, venue_order_id):
        self.reread += 1
        return {"id": venue_order_id, "state": "ORDER_STATE_CANCELED",
                "cumQuantity": 0, "leavesQuantity": 0}


class FakeStore:
    def __init__(self, orders=None):
        self._orders = orders or {}
        self.uncertain = []

    def live_orders(self):
        return dict(self._orders)

    def mark_uncertain(self, why):
        self.uncertain.append(why)


class Stub:
    """Only what maybe_ts_deadline touches, so the real method runs."""
    def __init__(self, orders=None):
        self.transport = FakeTransport()
        self.store = FakeStore(orders)


def leg(vid, side, qty="5", price="0.30"):
    return {"venue_order_id": vid, "market": SLUG, "side": side,
            "qty": qty, "price": price, "remaining": qty}


@pytest.fixture(autouse=True)
def armed_probe(monkeypatch):
    monkeypatch.setenv(TSP.EXPERIMENT_ENV, "TS-DEADLINE")
    monkeypatch.setenv(TSP.MARKET_ENV, SLUG)
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: WS)
    calls = []

    def fake_deadman(store, transport, *, reason):
        # exercise the real cancellation shape: by VENUE id, proved by re-read
        out = []
        for coid, o in (store.live_orders() or {}).items():
            vid = o.get("venue_order_id")
            transport.cancel(vid)
            st = transport.order(vid)
            out.append({"venue_order_id": vid, "state": st["state"],
                        "remaining": st["leavesQuantity"],
                        "resolved_by_reread": True})
        calls.append({"reason": reason, "orders": out})
        return {"reason": reason, "cancelled": out}
    monkeypatch.setattr(R.LX, "deadman", fake_deadman)
    return calls


# --------------------------------------------------------- the boundaries

def test_the_cutoff_is_start_plus_720_not_start_minus_180():
    assert TSP.cancel_cutoff_from_start(WS) == WS + 720.0
    assert TSP.cancel_cutoff_from_start(WS) == CUT
    assert TSP.cancel_cutoff_from_start(WS) > WS       # the whole bug


@pytest.mark.parametrize("offset", [0.0, 300.0, 719.0, 720.0])
def test_an_order_may_REST_at_or_before_the_cutoff(armed_probe, offset):
    st = Stub({"c1": leg("V1", "UP")})
    assert R.Runner.maybe_ts_deadline(st, WS + offset) is None
    assert st.transport.cancelled == []
    assert armed_probe == []


@pytest.mark.parametrize("offset", [721.0, 800.0, 900.0, 1200.0])
def test_after_the_cutoff_it_cancels_by_exact_venue_id_with_a_reread(
        armed_probe, offset):
    st = Stub({"c1": leg("V1", "UP")})
    out = R.Runner.maybe_ts_deadline(st, WS + offset)
    assert out is not None
    assert st.transport.cancelled == ["V1"]           # by VENUE id
    assert st.transport.reread == 1                   # proved by re-read
    assert out["cancelled"][0]["state"] == "ORDER_STATE_CANCELED"
    assert out["cancelled"][0]["remaining"] == 0
    assert out["cancelled"][0]["resolved_by_reread"] is True


# ------------------------------------------------------- inventory states

def test_both_legs_resting_are_both_withdrawn(armed_probe):
    st = Stub({"c1": leg("V1", "UP"), "c2": leg("V2", "DOWN")})
    R.Runner.maybe_ts_deadline(st, WS + 721.0)
    assert sorted(st.transport.cancelled) == ["V1", "V2"]


def test_one_leg_filled_the_other_resting_withdraws_only_the_resting_one(
        armed_probe):
    """A one-sided fill is the case the deadline exists for: the unfilled leg
    comes in and the filled one is left to settle."""
    st = Stub({"c2": leg("V2", "DOWN")})     # the UP leg already filled out
    R.Runner.maybe_ts_deadline(st, WS + 721.0)
    assert st.transport.cancelled == ["V2"]


def test_a_partially_filled_leg_is_still_cancelled_for_its_remainder(
        armed_probe):
    st = Stub({"c1": dict(leg("V1", "UP"), remaining="2")})
    R.Runner.maybe_ts_deadline(st, WS + 721.0)
    assert st.transport.cancelled == ["V1"]


def test_nothing_resting_means_nothing_to_do(armed_probe):
    st = Stub({})
    assert R.Runner.maybe_ts_deadline(st, WS + 900.0) is None
    assert armed_probe == []


# ------------------------------------------------- restart and unknowns

def test_a_restart_past_the_cutoff_still_withdraws(armed_probe):
    """The deadline keys on the WINDOW, so a process that starts late -- or
    restarts -- cannot postpone it."""
    st = Stub({"c1": leg("V1", "UP")})       # adopted after a restart
    out = R.Runner.maybe_ts_deadline(st, WS + 880.0)
    assert out is not None and st.transport.cancelled == ["V1"]


def test_unknown_market_timing_is_treated_as_ALREADY_OVERDUE(armed_probe,
                                                             monkeypatch):
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: None)
    st = Stub({"c1": leg("V1", "UP")})
    out = R.Runner.maybe_ts_deadline(st, WS + 10.0)   # early in the window
    assert out is not None
    assert st.transport.cancelled == ["V1"]


def test_it_is_inert_when_the_probe_is_not_authorized(armed_probe,
                                                      monkeypatch):
    monkeypatch.delenv(TSP.MARKET_ENV, raising=False)
    st = Stub({"c1": leg("V1", "UP")})
    assert R.Runner.maybe_ts_deadline(st, WS + 900.0) is None
    assert st.transport.cancelled == []


def test_another_market_is_not_swept(armed_probe, monkeypatch):
    monkeypatch.setenv(TSP.MARKET_ENV, "cpc-btc-updown-15m-2026-10-15-2015z")
    st = Stub({"c1": leg("V1", "UP")})
    assert R.Runner.maybe_ts_deadline(st, WS + 900.0) is None
    assert st.transport.cancelled == []


# ------------------------------------------------ no other start/end mixup

def test_no_caller_passes_a_window_START_to_cancel_cutoff():
    """The whole class of bug, pinned. cancel_cutoff takes an END."""
    import pathlib
    import re
    for f in pathlib.Path("src").glob("*.py"):
        txt = f.read_text()
        for m in re.finditer(r"cancel_cutoff\(([^)]*)\)", txt):
            arg = m.group(1)
            if "def " in arg:
                continue
            assert "slug_start" not in arg, (f.name, arg)
            assert not re.fullmatch(r"\s*float\(ws\)\s*", arg), (f.name, arg)


def test_the_queue_measurement_derives_its_end_correctly():
    import inspect
    from src import l5_queue_measurement as QM
    body = inspect.getsource(QM.record_ack_for)
    assert "WINDOW_S" in body            # ws + WINDOW_S, not ws
    assert "t_end=float(ws) + WINDOW_S" in body


def test_the_executor_passes_start_AND_end_to_may_place():
    import inspect
    from src import l5_live_execution as E
    body = inspect.getsource(E.FaithfulExecutor.handle)
    i = body.index("TS.may_place(")
    call = body[i:i + 200]
    assert "TS.WINDOW_S" in call          # the end is computed, not assumed
