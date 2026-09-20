"""Event-driven retargeting: triggers, debounce, latency, touch travel."""
import pytest

from src.gemini_execution import metadata as MD
from src.gemini_execution.econ import arm as ARM
from src.gemini_execution.econ import event_driver as ED

SYM = "GEMI-BTC15M2609201200-UP"
BOOK = {"readable": True, "identity_consistent": True,
        "yes": {"bid": "0.60", "ask": "0.65"},
        "no": {"bid": "0.35", "ask": "0.40"}}
FLOW = {"prints": [{"side": "buy", "qty": "1", "price": "0.61"},
                   {"side": "sell", "qty": "2", "price": "0.60"},
                   {"side": "buy", "qty": "1", "price": "0.61"},
                   {"side": "sell", "qty": "1", "price": "0.60"},
                   {"side": "buy", "qty": "1", "price": "0.61"}]}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})


def _lane(**kw):
    a = ARM.GeminiEconArm(symbol=SYM, cohort_prefix="GEMI-BTC15M",
                          policy="GEMINI_RECYCLE", cohort_name="BTC15m")
    return ED.EventDrivenLane(arm=a, symbol=SYM, **kw)


def _book(bid, ask="0.65"):
    return {"readable": True, "identity_consistent": True,
            "yes": {"bid": bid, "ask": ask},
            "no": {"bid": str(round(1 - float(ask), 2)),
                   "ask": str(round(1 - float(bid), 2))}}


# ----------------------------------------------------------------- triggers

def test_quote_churn_at_an_unchanged_touch_triggers_nothing():
    """The socket is DOMINATED by place/cancel pairs 25ms apart. Recomputing
    on them would burn cycles to arrive at the identical surface."""
    c = ED.classify([{"reason": "place", "t": 1.0},
                     {"reason": "cancel", "t": 1.02}],
                    prev_touch={"bid": "0.60", "ask": "0.65"},
                    touch={"bid": "0.60", "ask": "0.65"})
    assert c["triggers"] == []


def test_a_print_always_triggers_because_only_a_print_can_fill_us():
    c = ED.classify([{"reason": "trade", "t": 1.0}],
                    prev_touch={"bid": "0.60", "ask": "0.65"},
                    touch={"bid": "0.60", "ask": "0.65"})
    assert ED.TRADE in c["triggers"]


def test_a_touch_move_triggers_and_names_the_side():
    c = ED.classify([], prev_touch={"bid": "0.60", "ask": "0.65"},
                    touch={"bid": "0.61", "ask": "0.65"})
    assert ED.TOUCH in c["triggers"]
    assert any("best bid moved" in r for r in c["reasons"])


def test_a_quiet_market_still_recomputes_on_the_heartbeat():
    """A debounce with no floor of activity would leave a stale rung resting
    forever in a quiet market."""
    lane = _lane(max_quiet_s=1.0)
    lane.on_events([{"reason": "trade", "t": 0.0}], book=_book("0.60"),
                   depth=None, flow=FLOW, tte=700, policy="GEMINI_RECYCLE",
                   now=100.0)
    got = lane.on_events([], book=_book("0.60"), depth=None, flow=FLOW,
                         tte=700, policy="GEMINI_RECYCLE", now=105.0)
    assert got["decided"] is True
    assert ED.HEARTBEAT in got["classification"]["triggers"]


# ----------------------------------------------------------------- debounce

def test_a_burst_produces_one_decision_not_one_per_message():
    lane = _lane()
    lane.debounce.min_gap_s = 1.0
    lane.debounce.max_defer_s = 60.0
    n = 0
    for i in range(20):
        got = lane.on_events([{"reason": "trade", "t": 200.0 + i * 0.01}],
                             book=_book("0.60"), depth=None, flow=FLOW,
                             tte=700, policy="GEMINI_RECYCLE",
                             now=200.0 + i * 0.01)
        n += 1 if got["decided"] else 0
    assert n == 1
    assert lane.debounce.suppressed == 19


def test_the_debounce_has_a_ceiling_so_it_is_not_just_a_slower_cycle():
    lane = _lane()
    lane.debounce.min_gap_s = 10.0
    lane.debounce.max_defer_s = 0.5
    lane.on_events([{"reason": "trade", "t": 300.0}], book=_book("0.60"),
                   depth=None, flow=FLOW, tte=700, policy="GEMINI_RECYCLE",
                   now=300.0)
    lane.on_events([{"reason": "trade", "t": 300.1}], book=_book("0.61"),
                   depth=None, flow=FLOW, tte=700, policy="GEMINI_RECYCLE",
                   now=300.1)                       # starts the pending clock
    got = lane.on_events([{"reason": "trade", "t": 300.9}],
                         book=_book("0.62"), depth=None, flow=FLOW, tte=700,
                         policy="GEMINI_RECYCLE", now=300.9)
    assert got["decided"] is True
    assert lane.debounce.forced_by_ceiling == 1
    assert got["waited_s"] >= 0.5


def test_what_the_debounce_swallowed_is_reported_not_hidden():
    lane = _lane()
    lane.debounce.min_gap_s = 5.0
    lane.debounce.max_defer_s = 60.0
    for i in range(5):
        lane.on_events([{"reason": "trade", "t": 400.0 + i * 0.1}],
                       book=_book("0.60"), depth=None, flow=FLOW, tte=700,
                       policy="GEMINI_RECYCLE", now=400.0 + i * 0.1)
    assert lane.report()["debounce"]["suppressed"] == 4


# ------------------------------------------------------- touch travel + latency

def test_touch_travel_between_decisions_is_measured_in_ticks():
    """THE measurement that settles whether the 8s cycle was the constraint."""
    lane = _lane()
    lane.debounce.min_gap_s = 0.0
    for i, bid in enumerate(("0.60", "0.62", "0.63", "0.67")):
        lane.on_events([{"reason": "trade", "t": 500.0 + i}],
                       book=_book(bid, "0.70"), depth=None, flow=FLOW,
                       tte=700, policy="GEMINI_RECYCLE", now=500.0 + i)
    r = lane.report()["touch_movement"]
    assert r["n"] == 3
    assert r["max_touch_move_ticks"] == 4
    assert r["median_touch_move_ticks_between_decisions"] == 2


def test_latency_is_measured_at_both_seams_and_labelled_as_shadow():
    lane = _lane()
    lane.debounce.min_gap_s = 0.0
    lane.on_events([{"reason": "trade", "t": 600.0}], book=_book("0.60"),
                   depth=None, flow=FLOW, tte=700, policy="GEMINI_RECYCLE")
    r = lane.report()["latency"]
    assert r["n"] == 1
    assert r["event_to_decision_ms_p50"] is not None
    assert "not a venue round trip" in r["labels"]


def test_the_lane_never_decides_without_a_trigger():
    lane = _lane()
    got = lane.on_events([{"reason": "place", "t": 700.0},
                          {"reason": "cancel", "t": 700.01}],
                         book=_book("0.60"), depth=None, flow=FLOW, tte=700,
                         policy="GEMINI_RECYCLE", now=700.0)
    assert got["decided"] is False
    assert lane.decisions == 0


def test_the_driver_holds_no_transport():
    src = open(ED.__file__).read()
    for forbidden in ("order.place", "private_key", "api_key", "ws_session"):
        assert forbidden not in src
