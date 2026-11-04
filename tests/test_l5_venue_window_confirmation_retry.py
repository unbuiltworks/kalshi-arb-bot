"""A venue window must not be lost to one unanswered lookup.

WHAT HAPPENED, 2026-11-03. Three consecutive BTC15m windows were refused and
the campaign submitted zero orders. 2300z was refused 241 times
(`arm_refused_window_not_usable`) with a perfectly healthy book -- subscribed
before open, 8,540 book updates, 500 trades, a 0.46/0.59 market. The cause
was not the book, the strategy, or a safety gate doing its job: one
`venue_window_bounds` lookup failed at 23:01 and was cached as None for the
remaining 900 seconds.

TWO DEFECTS, and they compounded.

1. THE LOOKUP COULD NOT REACH A CURRENT WINDOW. `verify_alignment` walked
   `/v1/events?series_id=375` OLDEST FIRST under `while off < 4000`, so it
   saw only the series' oldest 4,000 events. At 96 windows a day the series
   outgrew that cap and every current window read "not listed". The
   identical defect had already been found and fixed once, in
   `l5_pmus_transport.resolve_market`, for the /v1/markets catalogue -- that
   docstring says a bounded walk "can never reach" a continuously created
   15m window. This read never got the fix.

2. "COULD NOT READ" WAS TREATED AS "VENUE DISAGREES". The cache was written
   on every outcome, with the stated rationale that an unconfirmed window
   "must not become confirmed by retrying". That is right for a venue that
   states a DIFFERENT start and wrong for a venue that said nothing.

WHAT IS UNCHANGED, and these tests pin it: the clock-derived window is never
substituted, a real disagreement is never retried, and a window that cannot
be confirmed still refuses.
"""
from __future__ import annotations

import datetime as dt

import pytest

from src import l5_pmus_live_runner as R
from src import l5_pmus_realtime_observer as OBS

WS = 1792000800
_W = dt.datetime.fromtimestamp(WS, dt.timezone.utc)
START = _W.isoformat()
OTHER = dt.datetime.fromtimestamp(WS + 900, dt.timezone.utc).isoformat()
#: the event slug `verify_alignment` builds for WS -- derived, never guessed,
#: so the fixture cannot drift from the code under test.
WANT_EV = "btc-updown-15m-%s-%02d%02dz" % (
    _W.strftime("%Y-%m-%d"), _W.hour, _W.minute)


class Stub:
    """Only what `Runner.venue_window_bounds` reads."""

    def __init__(self, now=0.0):
        self.ws = WS
        self.window_bounds = {}
        self.window_bounds_retry = {}
        self.now_t = now


def bounds(stub):
    return R.Runner.venue_window_bounds(stub)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    seen = []
    monkeypatch.setattr(R, "emit", lambda k, v: seen.append((k, v)))
    return seen


# ===================================================== 1. the lookup reaches

def test_the_direct_slug_resource_is_used_before_any_listing(monkeypatch):
    """THE FIX. The event is addressed BY NAME, so series age is irrelevant."""
    urls = []

    def fake_get(url, *a, **kw):
        urls.append(url)
        return {"event": {"slug": WANT_EV, "startTime": START}}

    monkeypatch.setattr(OBS, "_get", fake_get)
    rep = OBS.verify_alignment(WS)
    assert rep["classification"] == OBS.BOUNDS_CONFIRMED
    assert rep["aligned"] is True
    assert len(urls) == 1, "the listing must not be walked when slug answers"
    assert "/v1/events/slug/" in urls[0]
    assert "offset=" not in urls[0]


def test_PAGINATION_EXHAUSTION_is_transient_not_a_disagreement(monkeypatch):
    """THE ORIGINAL DEFECT, reproduced. The listing is walked to its cap and
    never finds a current window; that must be retryable, not definitive."""
    pages = []

    def fake_get(url, *a, **kw):
        if "/v1/events/slug/" in url:
            raise RuntimeError("404")
        pages.append(url)
        # a full page of OTHER events, forever: the walk exhausts its cap
        return {"events": [{"slug": "btc-updown-15m-2026-03-19-0000z",
                            "startTime": START} for _ in range(100)]}

    monkeypatch.setattr(OBS, "_get", fake_get)
    rep = OBS.verify_alignment(WS)
    assert rep["classification"] == OBS.BOUNDS_TRANSIENT
    assert rep["aligned"] is not True
    assert "not served" in rep["why"]
    # bounded: it stops at the cap rather than walking for ever
    assert len(pages) == OBS.EVENTS_WALK_MAX_OFFSET // 100


def test_the_listing_is_still_a_fallback_when_slug_has_no_start(monkeypatch):
    def fake_get(url, *a, **kw):
        if "/v1/events/slug/" in url:
            return {"event": {"slug": "x", "startTime": None}}
        return {"events": [{"slug": WANT_EV, "startTime": START}]}

    monkeypatch.setattr(OBS, "_get", fake_get)
    assert OBS.verify_alignment(WS)["classification"] == OBS.BOUNDS_CONFIRMED


# ============================================ 2. the three classifications

def test_a_matching_start_confirms():
    rep = OBS.venue_window_bounds_report(
        WS, 900, verify=lambda ws: {"aligned": True, "pmus_start_utc": START,
                                    "classification": OBS.BOUNDS_CONFIRMED})
    assert rep["classification"] == OBS.BOUNDS_CONFIRMED
    assert rep["bounds"]["source"] == "VENUE"


def test_a_DISAGREEING_start_is_a_definitive_mismatch():
    rep = OBS.venue_window_bounds_report(
        WS, 900, verify=lambda ws: {"aligned": False, "pmus_start_utc": OTHER,
                                    "classification": OBS.BOUNDS_MISMATCH})
    assert rep["classification"] == OBS.BOUNDS_MISMATCH
    assert rep["bounds"] is None


def test_an_unparseable_start_is_transient_not_confirmed():
    rep = OBS.venue_window_bounds_report(
        WS, 900, verify=lambda ws: {"aligned": True,
                                    "pmus_start_utc": "not-a-time"})
    assert rep["classification"] == OBS.BOUNDS_TRANSIENT
    assert rep["bounds"] is None


def test_an_older_verify_without_a_classification_is_inferred_safely():
    """Backward compatibility: a caller's fake that predates the field must
    not be read as confirmed."""
    assert OBS.venue_window_bounds_report(
        WS, 900, verify=lambda ws: {"aligned": False, "why": "x"}
    )["classification"] == OBS.BOUNDS_TRANSIENT
    assert OBS.venue_window_bounds_report(
        WS, 900, verify=lambda ws: {"aligned": False, "why": "x",
                                    "pmus_start_utc": OTHER}
    )["classification"] == OBS.BOUNDS_MISMATCH


# ======================= 3. SAME-WINDOW RECOVERY WITHOUT A RESTART

def test_a_transient_failure_then_success_confirms_the_SAME_window(monkeypatch):
    """THE REQUIREMENT. One unanswered lookup must not cost the window, and
    recovery must not need a restart -- the same process, the same window
    object, becomes eligible once the venue answers."""
    calls = {"n": 0}

    def flaky(ws, window_s=900, verify=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"bounds": None, "classification": OBS.BOUNDS_TRANSIENT,
                    "why": "gateway timeout"}
        return {"bounds": {"source": "VENUE", "start": START, "end": START,
                           "confirmed_by": "verify_alignment"},
                "classification": OBS.BOUNDS_CONFIRMED}

    monkeypatch.setattr(OBS, "venue_window_bounds_report", flaky)
    stub = Stub(now=1000.0)
    assert bounds(stub) is None            # first read fails
    assert WS not in stub.window_bounds, "a transient miss must NOT cache"
    # comfortably past the first backoff: the scheduled instant includes
    # the measured lookup latency, so advancing by exactly the interval can
    # land a hair short of it.
    stub.now_t = 1000.0 + 5.0 + 1.0
    got = bounds(stub)
    assert got is not None and got["source"] == "VENUE"
    assert stub.window_bounds[WS] == got, "a confirmation IS cached"
    assert calls["n"] == 2


def test_the_backoff_is_respected_between_attempts(monkeypatch):
    calls = {"n": 0}

    def always_transient(ws, window_s=900, verify=None):
        calls["n"] += 1
        return {"bounds": None, "classification": OBS.BOUNDS_TRANSIENT}

    monkeypatch.setattr(OBS, "venue_window_bounds_report", always_transient)
    stub = Stub(now=0.0)
    assert bounds(stub) is None
    assert calls["n"] == 1
    for _ in range(5):                     # no time passes
        assert bounds(stub) is None
    assert calls["n"] == 1, "it must back off, not hammer the gateway"


def test_retries_are_bounded_and_then_refuse_permanently(monkeypatch):
    """A dead gateway is read a bounded number of times, then the window is
    refused for good -- so it is not re-read for 900 seconds."""
    calls = {"n": 0}

    def always_transient(ws, window_s=900, verify=None):
        calls["n"] += 1
        return {"bounds": None, "classification": OBS.BOUNDS_TRANSIENT}

    monkeypatch.setattr(OBS, "venue_window_bounds_report", always_transient)
    stub = Stub(now=0.0)
    t = 0.0
    for _ in range(R.VENUE_BOUNDS_MAX_ATTEMPTS + 4):
        t += 1000.0                        # always past the backoff
        stub.now_t = t
        assert bounds(stub) is None
    assert calls["n"] == R.VENUE_BOUNDS_MAX_ATTEMPTS
    assert stub.window_bounds[WS] is None, "exhaustion caches the refusal"


def test_a_MISMATCH_is_never_retried(monkeypatch, _quiet):
    """Preserved from the original design, and the reason it existed: two
    different fifteen minutes cannot be retried into one."""
    calls = {"n": 0}

    def mismatch(ws, window_s=900, verify=None):
        calls["n"] += 1
        return {"bounds": None, "classification": OBS.BOUNDS_MISMATCH,
                "why": "PMUS startTime != L5", "pmus_start_utc": OTHER}

    monkeypatch.setattr(OBS, "venue_window_bounds_report", mismatch)
    stub = Stub(now=0.0)
    for i in range(5):
        stub.now_t = 1000.0 * (i + 1)
        assert bounds(stub) is None
    assert calls["n"] == 1
    assert stub.window_bounds[WS] is None
    why = [v for k, v in _quiet if k == "L5_LIVE_WINDOW_UNCONFIRMED"]
    assert why and why[0]["retryable"] is False
    assert why[0]["reason"] == "VENUE_DISAGREES"


# ============================================ 4. the reason is preserved

def test_the_exact_failure_reason_reaches_the_emission(monkeypatch, _quiet):
    """REQUIREMENT 1. `verify_alignment` computed a precise `why` and the
    emission threw it away, so production could not tell cap-exhaustion from
    a gateway error. It is carried now."""
    monkeypatch.setattr(
        OBS, "venue_window_bounds_report",
        lambda ws, window_s=900, verify=None: {
            "bounds": None, "classification": OBS.BOUNDS_TRANSIENT,
            "why": "PMUS event not served for btc-updown-15m-x by "
                   "/v1/events/slug/ or the bounded listing"})
    stub = Stub(now=0.0)
    assert bounds(stub) is None
    rec = [v for k, v in _quiet if k == "L5_LIVE_WINDOW_UNCONFIRMED"][0]
    assert "not served" in rec["why"]
    assert rec["classification"] == OBS.BOUNDS_TRANSIENT
    assert rec["retryable"] is True
    assert rec["attempts"] == 1
    assert rec["max_attempts"] == R.VENUE_BOUNDS_MAX_ATTEMPTS
    assert "never" in rec["labels"]


def test_a_confirmation_is_announced_with_its_attempt_count(monkeypatch,
                                                            _quiet):
    monkeypatch.setattr(
        OBS, "venue_window_bounds_report",
        lambda ws, window_s=900, verify=None: {
            "bounds": {"source": "VENUE", "start": START, "end": START},
            "classification": OBS.BOUNDS_CONFIRMED})
    stub = Stub(now=0.0)
    assert bounds(stub) is not None
    rec = [v for k, v in _quiet if k == "L5_LIVE_WINDOW_CONFIRMED"][0]
    assert rec["attempts"] == 1
    assert rec["bounds"]["source"] == "VENUE"


# ================================= 5. what must NEVER change

def test_the_clock_derived_window_is_never_substituted(monkeypatch):
    """Every non-confirming path returns None. There is no branch that
    answers with our own 900-second grid."""
    import inspect
    src = inspect.getsource(R.Runner.venue_window_bounds)
    assert "AB.WINDOW_S" in src          # only ever passed INTO the venue read
    for forbidden in ("slug_for(int(ws)), \"start\"", "clock_bounds",
                      "fallback_bounds"):
        assert forbidden not in src, forbidden
    for cls in (OBS.BOUNDS_TRANSIENT, OBS.BOUNDS_MISMATCH):
        monkeypatch.setattr(
            OBS, "venue_window_bounds_report",
            lambda ws, window_s=900, verify=None, _c=cls: {
                "bounds": None, "classification": _c})
        assert bounds(Stub(now=0.0)) is None


def test_a_raising_report_is_transient_and_still_refuses(monkeypatch):
    def boom(ws, window_s=900, verify=None):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(OBS, "venue_window_bounds_report", boom)
    stub = Stub(now=0.0)
    assert bounds(stub) is None
    assert WS not in stub.window_bounds   # retryable, so not cached


def test_a_confirmed_window_is_read_once_per_window(monkeypatch):
    calls = {"n": 0}

    def ok(ws, window_s=900, verify=None):
        calls["n"] += 1
        return {"bounds": {"source": "VENUE", "start": START, "end": START},
                "classification": OBS.BOUNDS_CONFIRMED}

    monkeypatch.setattr(OBS, "venue_window_bounds_report", ok)
    stub = Stub(now=0.0)
    for i in range(10):
        stub.now_t = 1000.0 * i
        assert bounds(stub)["source"] == "VENUE"
    assert calls["n"] == 1


def test_the_retry_budget_fits_inside_the_entry_cutoff():
    """A window recovered on the LAST attempt must still be able to open a
    leg, or the retry buys nothing."""
    from src import l5_two_sided_probe as TS
    assert sum(R.VENUE_BOUNDS_BACKOFF_S[:R.VENUE_BOUNDS_MAX_ATTEMPTS]) \
        < TS.entry_cutoff_s()


# ============== 6. THE REAL BOUND: the runway left in THIS window
#
# THE GAP THIS CLOSES, and it was the owner's catch. The first version
# bounded retries only by comparing the SUM of the backoff schedule (195s)
# to the entry cutoff. That proves nothing about a window whose first
# attempt lands late -- a restart at t+400, a slow first tick -- and it
# ignored lookup latency entirely, where a 40-page fallback walk at _get's
# 20s default is up to 800 seconds. A late confirmation must never bypass
# the entry cutoff, and a retry that cannot land before it must not be
# scheduled at all.

def _at(offset_s):
    """A stub positioned `offset_s` into the window, in wall-clock epoch."""
    return Stub(now=float(WS) + float(offset_s))


def _transient(monkeypatch, calls=None):
    def t(ws, window_s=900, verify=None):
        if calls is not None:
            calls["n"] += 1
        return {"bounds": None, "classification": OBS.BOUNDS_TRANSIENT,
                "why": "gateway timeout"}
    monkeypatch.setattr(OBS, "venue_window_bounds_report", t)


def test_no_attempt_is_made_once_the_entry_runway_is_gone(monkeypatch, _quiet):
    from src import l5_two_sided_probe as TS
    calls = {"n": 0}
    _transient(monkeypatch, calls)
    stub = _at(TS.entry_cutoff_s())        # exactly AT the cutoff
    assert bounds(stub) is None
    assert calls["n"] == 0, "the venue must not be read with no runway left"
    assert stub.window_bounds[WS] is None
    rec = [v for k, v in _quiet if k == "L5_LIVE_WINDOW_UNCONFIRMED"][-1]
    assert rec["reason"] == "ENTRY_CUTOFF_REACHED"
    assert rec["retryable"] is False


def test_the_floor_reserves_runway_a_confirmation_could_use(monkeypatch):
    """Just inside the cutoff but inside the floor is still given up: a
    confirmation there could not produce a placed order."""
    from src import l5_two_sided_probe as TS
    calls = {"n": 0}
    _transient(monkeypatch, calls)
    stub = _at(TS.entry_cutoff_s() - R.VENUE_BOUNDS_MIN_RUNWAY_S / 2.0)
    assert bounds(stub) is None
    assert calls["n"] == 0
    assert stub.window_bounds[WS] is None


def test_a_retry_that_would_land_past_the_deadline_is_not_scheduled(
        monkeypatch, _quiet):
    """THE REQUIREMENT. One attempt is made, and because the next would fall
    past the usable deadline it is refused by name instead of scheduled."""
    from src import l5_two_sided_probe as TS
    calls = {"n": 0}
    _transient(monkeypatch, calls)
    # positioned so an attempt fits but the 5s first backoff does not
    stub = _at(TS.entry_cutoff_s() - R.VENUE_BOUNDS_MIN_RUNWAY_S - 2.0)
    assert bounds(stub) is None
    assert calls["n"] == 1, "one attempt should still be made"
    assert stub.window_bounds[WS] is None, "and then given up, not scheduled"
    rec = [v for k, v in _quiet if k == "L5_LIVE_WINDOW_UNCONFIRMED"][-1]
    assert rec["reason"] == "NO_RUNWAY_FOR_RETRY"
    assert rec["retryable"] is False
    assert rec["would_retry_in_s"] == R.VENUE_BOUNDS_BACKOFF_S[0]


def test_early_in_the_window_retries_ARE_scheduled(monkeypatch, _quiet):
    """The bound must not be so tight that it forbids the retry it exists
    for: at t+0 there is a full cutoff of runway."""
    calls = {"n": 0}
    _transient(monkeypatch, calls)
    stub = _at(0.0)
    assert bounds(stub) is None
    rec = [v for k, v in _quiet if k == "L5_LIVE_WINDOW_UNCONFIRMED"][-1]
    assert rec["reason"] == "TRANSIENT_WILL_RETRY"
    assert rec["retryable"] is True
    assert WS not in stub.window_bounds, "a retryable miss is not cached"


def test_a_late_confirmation_cannot_bypass_the_entry_cutoff(monkeypatch):
    """Even if the venue DOES confirm, `may_enter` is the gate on placing and
    it refuses past the cutoff. The confirmation is not a licence to enter."""
    from src import l5_two_sided_probe as TS
    monkeypatch.setattr(
        OBS, "venue_window_bounds_report",
        lambda ws, window_s=900, verify=None: {
            "bounds": {"source": "VENUE", "start": START, "end": START},
            "classification": OBS.BOUNDS_CONFIRMED})
    # a confirmation obtained with runway still left
    stub = _at(10.0)
    assert bounds(stub) is not None
    # but entry past the cutoff is refused regardless of the confirmation
    t_end = float(WS) + 900.0
    assert TS.may_enter(float(WS) + TS.entry_cutoff_s(), float(WS),
                        t_end) == "PAST_ENTRY_CUTOFF"
    assert TS.may_enter(float(WS) + TS.entry_cutoff_s() + 1.0, float(WS),
                        t_end) == "PAST_ENTRY_CUTOFF"


def test_the_runway_and_latency_are_reported(monkeypatch, _quiet):
    _transient(monkeypatch)
    stub = _at(0.0)
    assert bounds(stub) is None
    rec = [v for k, v in _quiet if k == "L5_LIVE_WINDOW_UNCONFIRMED"][-1]
    assert "runway_left_s" in rec and rec["runway_left_s"] > 0
    assert "lookup_latency_s" in rec


def test_an_unreadable_entry_cutoff_refuses_rather_than_guessing(
        monkeypatch, _quiet):
    """Defaulting to the nominal 480 would be the LOOSER answer, because the
    deployed cutoff can only be configured earlier."""
    from src import l5_two_sided_probe as TS

    def boom():
        raise RuntimeError("cutoff unreadable")

    _transient(monkeypatch)
    monkeypatch.setattr(TS, "entry_cutoff_s", boom)
    stub = _at(0.0)
    assert bounds(stub) is None
    rec = [v for k, v in _quiet if k == "L5_LIVE_WINDOW_UNCONFIRMED"][-1]
    assert rec["reason"] == "ENTRY_CUTOFF_UNREADABLE"
    assert rec["retryable"] is False


def test_the_fallback_walk_has_a_wall_clock_budget(monkeypatch):
    """One attempt must not be able to consume the window it is confirming:
    _get's 20s default over a 40-page walk is up to 800 seconds."""
    assert OBS.EVENTS_WALK_BUDGET_S <= 30.0
    assert OBS.VENUE_LOOKUP_TIMEOUT_S <= 10.0
    import inspect
    src = inspect.getsource(OBS.verify_alignment)
    assert "EVENTS_WALK_BUDGET_S" in src
    assert "VENUE_LOOKUP_TIMEOUT_S" in src


def test_the_walk_budget_aborts_the_walk(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(OBS.time, "time", lambda: clock["t"])

    def fake_get(url, *a, **kw):
        if "/v1/events/slug/" in url:
            raise RuntimeError("404")
        clock["t"] += 10.0                 # each page burns 10s
        return {"events": [{"slug": "other", "startTime": START}
                           for _ in range(100)]}

    monkeypatch.setattr(OBS, "_get", fake_get)
    rep = OBS.verify_alignment(WS)
    assert rep["classification"] == OBS.BOUNDS_TRANSIENT
    assert "budget" in rep["why"]
