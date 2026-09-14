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


# ---------------------------------------------------------------------------
# WINDOW-CLOSE DIGEST
#
# THE DEFECT THESE PIN: the canonical trajectory rows are written to a Railway
# volume that only the collector container can read. A window could complete
# with perfect coverage and still be invisible to every analysis running
# anywhere else -- which is exactly what blocked the first complete-window
# report. The digest is the read path, so its arithmetic has to match the
# offline scorers rather than merely look plausible.
# ---------------------------------------------------------------------------
import json


def _series(n=300, t0=299.0, step=1.0, bid=0.48, ask=0.50, bs=500.0, asz=250.0):
    return [(round(t0 - i * step, 2), bid, ask, bs, asz) for i in range(n)]


def test_digest_refuses_a_window_it_cannot_characterise():
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    assert C._window_digest("X", meta, [], 0, 0) is None
    assert C._window_digest("X", meta, _series(1), 0, 0) is None


def test_digest_span_and_completeness_use_the_windows_own_clock():
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    d = C._window_digest("GEMI-BTC05M-UP", meta, _series(300), 7, 0)
    assert d["obs"] == 300
    assert d["tte_first"] == 299.0 and d["tte_last"] == 0.0
    assert d["span_s"] == 299.0
    assert d["complete"] is True          # >= 300 - 10
    assert d["prints"] == 7
    # 299 s of 1 Hz samples is ~60/min
    assert 59.0 <= d["obs_per_min"] <= 61.0

    short = C._window_digest("X", meta, _series(100, t0=299.0), 0, 0)
    assert short["span_s"] == 99.0
    assert short["complete"] is False


def test_digest_is_order_independent():
    """Observations arrive chronologically, but the digest must not depend on
    that: a reordered buffer has to produce the identical summary."""
    meta = {"asset": "ETH", "window_minutes": 5, "expiry_t": 1000.0}
    ser = _series(50)
    a = C._window_digest("X", meta, list(ser), 3, 1)
    b = C._window_digest("X", meta, list(reversed(ser)), 3, 1)
    assert a == b


def test_two_sided_pct_counts_the_refused_samples_in_its_denominator():
    """_canonical_bbo returns None for a one-sided book, so those samples
    never reach the buffer. If they were dropped from the denominator too,
    a window quoted one-sided 90% of the time would report 100% two-sided."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    d = C._window_digest("X", meta, _series(10), 0, 90)
    assert d["obs"] == 10
    assert d["one_sided_obs"] == 90
    assert d["two_sided_pct"] == 10.0

    clean = C._window_digest("X", meta, _series(10), 0, 0)
    assert clean["two_sided_pct"] == 100.0


def test_two_sided_matches_the_habitat_scorer_convention():
    """habitat_expanded.py treats bid > 0 and ask > 0 as two-sided; a
    zero-priced side present in the book is NOT a two-sided state."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    ser = _series(5) + [(10.0, 0.0, 0.50, 100.0, 100.0),
                        (9.0, 0.0, 0.50, 100.0, 100.0)]
    d = C._window_digest("X", meta, ser, 0, 0)
    assert d["obs"] == 7
    assert d["two_sided_pct"] == round(100.0 * 5 / 7, 2)


def test_depth_p50_is_depth_at_touch_not_one_side():
    """habitat_expanded.py uses min(bid_sz, ask_sz): the binding side for a
    maker. Reporting bid_sz alone would overstate executable depth."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    d = C._window_digest("X", meta, _series(4, bs=500.0, asz=250.0), 0, 0)
    assert d["depth_p50"] == 250.0


def test_spread_p50_is_in_price_units():
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    d = C._window_digest("X", meta, _series(4, bid=0.48, ask=0.50), 0, 0)
    assert d["spread_p50"] == 0.02


def test_digest_lines_round_trip_the_whole_series_in_chunks():
    """A 300-observation series exceeds what a single log line can carry, so
    it is chunked. Reassembly must return every observation, in order, with
    no loss and no duplication."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    ser = _series(300)
    d = C._window_digest("GEMI-BTC05M2609141200-UP", meta, ser, 4, 0)
    lines = C._digest_lines(d, ser)

    head = [x for x in lines if x.startswith("GEMINI_WINDOW_CLOSE ")]
    body = [x for x in lines if x.startswith("GEMINI_WINDOW_SERIES ")]
    assert len(head) == 1
    assert len(body) == 6            # 300 / 50

    assert json.loads(head[0].split(" ", 1)[1])["obs"] == 300
    rebuilt = []
    for i, line in enumerate(body, start=1):
        o = json.loads(line.split(" ", 1)[1])
        assert o["market"] == d["market"]
        assert o["part"] == i and o["parts"] == 6
        rebuilt.extend(o["s"])
    assert len(rebuilt) == 300
    assert [x[0] for x in rebuilt] == sorted((x[0] for x in ser), reverse=True)


def test_digest_lines_stay_under_the_log_transport_limit():
    """Long log lines are truncated in transit; a truncated line is not
    recoverable JSON, so the chunk size has to keep every line small."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    ser = _series(400, bid=0.4812, ask=0.5037, bs=123456.7, asz=98765.4)
    d = C._window_digest("GEMI-BTC05M2609141200-UP", meta, ser, 40, 12)
    for line in C._digest_lines(d, ser):
        assert len(line) < 4000, (line[:120], len(line))


def test_digest_tolerates_a_window_whose_metadata_was_lost():
    d = C._window_digest("X", {}, _series(10), 0, 0)
    assert d["asset"] is None and d["expiry_t"] is None
    # a missing window_minutes must not crash completeness, it defaults to 5m
    assert d["window_minutes"] is None
    assert d["complete"] is False      # 9 s of span is not a complete window


def test_digest_carries_print_rows_not_only_a_count():
    """A shadow run against quotes alone cannot tell STRICT from OPTIMISTIC:
    the distinction IS the prints. Shipping only a count is the same class of
    read-path gap as writing the rows to an unreadable volume."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    ser = _series(20)
    prs = [(290.0, 0.49, 5.0, True), (120.0, 0.50, 2.0, False),
           (17.0, 0.52, 9.0, True)]
    d = C._window_digest("GEMI-BTC05M-UP", meta, ser, prs, 0)
    assert d["prints"] == 3                    # count still derived

    lines = C._digest_lines(d, ser, prints=prs)
    pl = [x for x in lines if x.startswith("GEMINI_WINDOW_PRINTS ")]
    assert len(pl) == 1
    got = json.loads(pl[0].split(" ", 1)[1])
    assert got["market"] == "GEMI-BTC05M-UP" and got["parts"] == 1
    assert got["p"] == [[290.0, 0.49, 5.0, True],
                        [120.0, 0.50, 2.0, False],
                        [17.0, 0.52, 9.0, True]]


def test_digest_emits_no_print_lines_when_the_window_had_none():
    meta = {"asset": "ETH", "window_minutes": 5, "expiry_t": 1000.0}
    lines = C._digest_lines(C._window_digest("X", meta, _series(5), [], 0),
                            _series(5), prints=[])
    assert not [x for x in lines if x.startswith("GEMINI_WINDOW_PRINTS ")]


def test_digest_still_accepts_a_plain_print_count():
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    assert C._window_digest("X", meta, _series(5), 7, 0)["prints"] == 7


def test_digest_buffer_is_capped():
    """A window that is never unsubscribed must not grow the buffer without
    bound: the collector is meant to stay up 24/7."""
    assert C.GEMINI_DIGEST_MAX_OBS >= 300        # a full 1 Hz 5m window fits
    assert C.GEMINI_DIGEST_MAX_OBS <= 1000
    # the historical tape held 735 prints IN TOTAL; the cap is a backstop,
    # never a sampler
    assert C.GEMINI_DIGEST_MAX_PRINTS >= 1000


# ---------------------------------------------------------------------------
# END-TO-END WIRING
#
# The digest arithmetic being right is worth nothing if the session never
# calls it, or calls it with an empty buffer. This drives the real session
# loop over a scripted websocket and asserts the lines actually come out.
# ---------------------------------------------------------------------------
import asyncio


class _FakeWS:
    """Scripted websocket. Each entry advances the collector's clock, so the
    window lifecycle runs on the window's own time rather than wall time."""

    def __init__(self, script, clock, stop):
        self.script = list(script)
        self.clock = clock
        self.stop = stop
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        if not self.script:
            self.stop.set()
            return "{}"
        t, msg = self.script.pop(0)
        self.clock[0] = t
        return json.dumps(msg)


class _MemSink:
    def __init__(self):
        self.rows = []

    async def write(self, event):
        self.rows.append(event)


def _run_scripted_session(monkeypatch, script, expiry=1000.0, asset="BTC",
                          discover_seconds=0.0, state=None):
    clock = [script[0][0]]
    stop = asyncio.Event()
    ws = _FakeWS(script, clock, stop)
    meta = {"asset": asset, "window_minutes": 5, "expiry_t": expiry,
            "duration": 5}
    sym = "GEMI-BTC05M2609141200-UP"

    monkeypatch.setattr(C, "DISCOVER_SECONDS", discover_seconds)
    monkeypatch.setattr(C, "GEMINI_BBO_RECORD_SECONDS", 0.0)
    monkeypatch.setattr(C, "GEMINI_WS_SESSION_SECONDS", 1e9)
    monkeypatch.setattr(C, "_now", lambda: clock[0])
    monkeypatch.setattr(C.websockets, "connect", lambda *a, **k: ws)

    async def _fake_discover(_http):
        return {sym: dict(meta)}

    monkeypatch.setattr(C, "_gemini_active_symbols", _fake_discover)

    sink, canon = _MemSink(), _MemSink()
    asyncio.run(C._gemini_ws_session(sink, stop, [sym], canon=canon, health={},
                                     state=state))
    return sym, ws, sink, canon


def _depth(sym, bid, ask, bid_sz=500.0, ask_sz=250.0):
    return {"s": sym, "bids": [[str(bid), str(bid_sz)]],
            "asks": [[str(ask), str(ask_sz)]]}


def test_session_emits_a_window_close_digest_when_the_window_drops(monkeypatch,
                                                                   capsys):
    sym = "GEMI-BTC05M2609141200-UP"
    script = [(700.0 + i, _depth(sym, 0.48, 0.50)) for i in range(300)]
    script.append((1040.0, {}))          # past expiry + RETAIN -> next pass drops
    script.append((1041.0, {}))          # the pass that drops
    _run_scripted_session(monkeypatch, script)

    out = capsys.readouterr().out.splitlines()
    head = [x for x in out if x.startswith("GEMINI_WINDOW_CLOSE ")]
    body = [x for x in out if x.startswith("GEMINI_WINDOW_SERIES ")]
    assert len(head) == 1, out[:4]

    d = json.loads(head[0].split(" ", 1)[1])
    assert d["market"] == sym and d["asset"] == "BTC"
    assert d["obs"] == 300
    assert d["tte_first"] == 300.0 and d["tte_last"] == 1.0
    assert d["span_s"] == 299.0
    assert d["complete"] is True
    assert d["two_sided_pct"] == 100.0
    assert d["spread_p50"] == 0.02 and d["depth_p50"] == 250.0

    rebuilt = []
    for line in body:
        rebuilt.extend(json.loads(line.split(" ", 1)[1])["s"])
    assert len(rebuilt) == 300


def test_session_digest_excludes_out_of_window_observations(monkeypatch,
                                                            capsys):
    """The collector attaches 330 s BEFORE expiry so it never misses the open.
    Those pre-open quotes are real, but they are NOT in-window state and must
    not inflate coverage -- counting them is the exact bug that produced a
    historical median of zero in-window seconds."""
    sym = "GEMI-BTC05M2609141200-UP"
    script = [(670.0 + i, _depth(sym, 0.48, 0.50)) for i in range(30)]   # tte 330..301
    script += [(700.0 + i, _depth(sym, 0.48, 0.50)) for i in range(300)]  # tte 300..1
    script += [(1040.0, {}), (1041.0, {})]
    _run_scripted_session(monkeypatch, script)

    out = capsys.readouterr().out.splitlines()
    d = json.loads([x for x in out
                    if x.startswith("GEMINI_WINDOW_CLOSE ")][0].split(" ", 1)[1])
    assert d["obs"] == 300               # not 330
    assert d["tte_first"] == 300.0


def test_session_digest_counts_one_sided_books_it_could_not_canonicalise(
        monkeypatch, capsys):
    sym = "GEMI-BTC05M2609141200-UP"
    script = [(700.0 + i, _depth(sym, 0.48, 0.50)) for i in range(10)]
    script += [(710.0 + i, {"s": sym, "bids": [], "asks": [["0.50", "9"]]})
               for i in range(10)]
    script += [(1040.0, {}), (1041.0, {})]
    _run_scripted_session(monkeypatch, script)

    out = capsys.readouterr().out.splitlines()
    d = json.loads([x for x in out
                    if x.startswith("GEMINI_WINDOW_CLOSE ")][0].split(" ", 1)[1])
    assert d["obs"] == 10
    assert d["one_sided_obs"] == 10
    assert d["two_sided_pct"] == 50.0


def test_session_digest_counts_only_in_window_prints(monkeypatch, capsys):
    """Prints are the scarce STRICT-fill evidence; a print outside the window
    is not evidence about that window."""
    sym = "GEMI-BTC05M2609141200-UP"

    def _pr(px):
        return {"s": sym, "p": str(px), "q": "5", "m": True, "t": 1, "E": 1}

    script = [(680.0, _pr(0.49))]                                  # tte 320: outside
    script += [(700.0 + i, _depth(sym, 0.48, 0.50)) for i in range(5)]
    script += [(720.0, _pr(0.49)), (721.0, _pr(0.50))]             # in-window
    script += [(1040.0, {}), (1041.0, {})]
    _run_scripted_session(monkeypatch, script)

    out = capsys.readouterr().out.splitlines()
    d = json.loads([x for x in out
                    if x.startswith("GEMINI_WINDOW_CLOSE ")][0].split(" ", 1)[1])
    assert d["prints"] == 2

    pl = [x for x in out if x.startswith("GEMINI_WINDOW_PRINTS ")]
    assert len(pl) == 1
    rows = json.loads(pl[0].split(" ", 1)[1])["p"]
    # tte 280 and 279, chronological; the tte-320 print is outside the window
    assert rows == [[280.0, 0.49, 5.0, True], [279.0, 0.5, 5.0, True]]


def test_session_still_writes_canonical_rows_and_no_orders(monkeypatch, capsys):
    """The digest is an ADDITIONAL read path. It must not displace the
    canonical volume write, which stays the system of record."""
    sym = "GEMI-BTC05M2609141200-UP"
    script = [(700.0 + i, _depth(sym, 0.48, 0.50)) for i in range(5)]
    script += [(1040.0, {}), (1041.0, {})]
    _sym, ws, _sink, canon = _run_scripted_session(monkeypatch, script)

    bbo = [r for r in canon.rows if r.get("kind") == "bbo"]
    assert len(bbo) == 5
    assert bbo[0]["tte_s"] == 300.0
    assert all(r["expiry_source"] == "gemini_events_rest" for r in bbo)

    methods = {m.get("method") for m in ws.sent}
    assert methods == {"SUBSCRIBE", "UNSUBSCRIBE"}


def test_digest_separates_sampling_span_from_two_sided_span():
    """THE FULL-WINDOW DIAGNOSIS DEPENDS ON THIS. `span_s` counts only
    TWO-SIDED samples, so a window we kept sampling while the book sat
    one-sided is indistinguishable from one where collection stopped. The
    ANY-sample span separates genuine venue behaviour from a collector
    defect, and we must never 'fix' the former."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    # two-sided only to tte 150, but we kept sampling down to tte 2
    d = C._window_digest("X", meta, _series(150, t0=299.0), [], 148,
                         [299.0, 2.0])
    assert d["span_s"] == 149.0            # two-sided span
    assert d["span_any_s"] == 297.0        # sampling span
    assert d["tte_last_any"] == 2.0
    assert d["complete"] is False


def test_digest_any_span_absent_is_none_not_zero():
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    d = C._window_digest("X", meta, _series(10), [], 0, None)
    assert d["tte_last_any"] is None and d["span_any_s"] is None


def test_digest_carries_the_six_subscribe_path_timestamps():
    """CAUSATION BEFORE FIX. A late first frame can come from the venue not
    LISTING the window, from our discovery tick, from a slow SUBSCRIBE ack,
    or from the venue's first frame. The digest must time each stage
    separately or the cause cannot be attributed."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    life = {"window_discovered_at": 600.0,
            "window_due_at": 670.0,
            "subscription_requested_at": 672.0,
            "subscription_ack_at": 672.4,
            "first_frame_at": 673.0,
            "discovery_tick_index": 3,
            "session_age_at_discovery_s": 180.0,
            "session_index": 7}
    d = C._window_digest("X", meta, _series(10), [], 0, None, life)
    assert d["window_discovered_at"] == 600.0
    assert d["window_due_at"] == 670.0
    assert d["subscription_requested_at"] == 672.0
    assert d["subscription_ack_at"] == 672.4
    assert d["first_frame_at"] == 673.0
    assert d["expiry"] == 1000.0
    # positive lead == the window was known BEFORE it was due to subscribe
    assert d["lat_discovery_to_due_s"] == 70.0
    assert d["lat_due_to_subscribe_s"] == 2.0
    assert d["lat_subscribe_to_ack_s"] == 0.4
    assert d["lat_ack_to_first_frame_s"] == 0.6


def test_late_discovery_reports_a_negative_lead_not_a_missing_one():
    """The suspected cause is discovery AFTER the window was already due.
    That must read as a NEGATIVE lead, not as None -- None means UNKNOWN and
    would hide exactly the defect we are hunting."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    life = {"window_discovered_at": 752.0, "window_due_at": 670.0}
    d = C._window_digest("X", meta, _series(10), [], 0, None, life)
    assert d["lat_discovery_to_due_s"] == -82.0


def test_absent_lifecycle_reports_unknown_never_zero():
    """A missing stamp is UNKNOWN. Reporting 0 would read as 'instant' and
    would silently exonerate the stage that is actually late."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    d = C._window_digest("X", meta, _series(10), [], 0, None, None)
    for k in ("window_discovered_at", "subscription_requested_at",
              "subscription_ack_at", "first_frame_at",
              "lat_discovery_to_due_s", "lat_due_to_subscribe_s",
              "lat_subscribe_to_ack_s", "lat_ack_to_first_frame_s",
              "discovery_tick_index", "session_index"):
        assert d[k] is None, k
    # due is DERIVABLE from expiry alone, so it is known even with no life
    assert d["window_due_at"] == 1000.0 - C.GEMINI_SUBSCRIBE_LEAD_S
    assert d["expiry"] == 1000.0


def test_window_is_subscribed_at_its_due_time_not_at_the_next_discovery(
        monkeypatch, capsys):
    """THE ~82 s LATE-SUBSCRIBE DEFECT. The subscription roll used to live
    INSIDE the DISCOVER_SECONDS branch, so a window that became due at
    expiry-330 waited for the next REST tick -- up to a further 45 s, on a
    tick grid that stayed phase-locked to the window clock because the 600 s
    session period is an exact multiple of the 300 s window period. The
    venue publishes windows ~890 s ahead, so every second of that wait was
    self-inflicted.

    Here discovery happens once and then never again (DISCOVER_SECONDS huge).
    The window must still be SUBSCRIBEd the moment it comes due."""
    sym = "GEMI-BTC05M2609141200-UP"
    # expiry 1000 -> due at 670. Nothing is due for the first three passes.
    script = [(500.0, {}), (600.0, {}), (669.0, {}),
              (680.0, _depth(sym, 0.48, 0.50)), (681.0, {})]
    _, ws, _, _ = _run_scripted_session(monkeypatch, script,
                                        discover_seconds=1e9)
    subs = [m for m in ws.sent if m.get("method") == "SUBSCRIBE"]
    assert len(subs) == 1, ws.sent
    assert f"{sym}@trade" in subs[0]["params"]


def test_first_discovery_is_immediate_so_no_session_starts_blind(monkeypatch):
    """`last_discover` was seeded with monotonic(), making the first
    DISCOVER_SECONDS of EVERY session blind with an empty catalogue -- the
    window could come due while we still knew nothing. kalshi_task has always
    seeded 0.0; this makes the gemini session agree."""
    sym = "GEMI-BTC05M2609141200-UP"
    script = [(700.0, _depth(sym, 0.48, 0.50)), (701.0, {})]
    # DISCOVER_SECONDS huge: if the first pass were not immediate, discovery
    # would never run at all and nothing could ever be subscribed.
    _, ws, sink, _ = _run_scripted_session(monkeypatch, script,
                                           discover_seconds=1e9)
    assert [m for m in ws.sent if m.get("method") == "SUBSCRIBE"]
    assert [r for r in sink.rows if r.get("kind") == "window"]


def test_state_survives_a_session_recycle_and_the_digest_is_not_lost(
        monkeypatch, capsys):
    """A 600 s session recycle used to destroy `known` and every per-window
    digest buffer. Because 600 is an exact multiple of the 300 s window
    period, the loss was phase-locked rather than averaged: one 5m window in
    two produced NO digest at all. The carried state must preserve the
    earlier session's observations and still emit ONE digest at drop."""
    sym = "GEMI-BTC05M2609141200-UP"
    state = {}

    # session A: observes tte 300..201, then its script ends
    first = [(700.0 + i, _depth(sym, 0.48, 0.50)) for i in range(100)]
    _run_scripted_session(monkeypatch, first, state=state)
    assert capsys.readouterr().out == ""          # window still open: no digest
    assert sym in state["known"]
    assert len(state["closed"][sym]) == 100

    # session B: fresh socket, observes tte 200..1, then the window drops
    second = [(800.0 + i, _depth(sym, 0.48, 0.50)) for i in range(200)]
    second += [(1040.0, {}), (1041.0, {})]
    _, ws2, _, _ = _run_scripted_session(monkeypatch, second, state=state)

    # a NEW socket has no subscriptions, so it must re-SUBSCRIBE
    assert [m for m in ws2.sent if m.get("method") == "SUBSCRIBE"], ws2.sent

    out = capsys.readouterr().out.splitlines()
    head = [x for x in out if x.startswith("GEMINI_WINDOW_CLOSE ")]
    assert len(head) == 1, out[:4]
    d = json.loads(head[0].split(" ", 1)[1])
    # BOTH sessions' observations are in the one digest
    assert d["obs"] == 300
    assert d["tte_first"] == 300.0 and d["tte_last"] == 1.0
    assert d["span_s"] == 299.0 and d["complete"] is True
    # and the buffers are released once the digest is out
    assert sym not in state["closed"] and sym not in state["any_tte"]


def test_carried_lifecycle_keeps_the_first_subscribe_not_the_resubscribe(
        monkeypatch, capsys):
    """After a recycle the window is SUBSCRIBEd again on the new socket. The
    digest must still report when we FIRST asked, or the re-subscribe would
    disguise the original latency as small."""
    sym = "GEMI-BTC05M2609141200-UP"
    state = {}
    _run_scripted_session(monkeypatch,
                          [(700.0 + i, _depth(sym, 0.48, 0.50))
                           for i in range(50)], state=state)
    first_req = state["life"][sym]["subscription_requested_at"]
    assert first_req == 700.0

    second = [(800.0 + i, _depth(sym, 0.48, 0.50)) for i in range(200)]
    second += [(1040.0, {}), (1041.0, {})]
    _run_scripted_session(monkeypatch, second, state=state)
    out = [x for x in capsys.readouterr().out.splitlines()
           if x.startswith("GEMINI_WINDOW_CLOSE ")]
    d = json.loads(out[0].split(" ", 1)[1])
    assert d["subscription_requested_at"] == first_req
    assert d["first_frame_at"] == 700.0


def test_the_latency_sign_convention_is_not_uniform_and_is_pinned():
    """THREE OF THE FOUR WOULD BE READ BACKWARDS under a single
    "positive means on time" rule. `lat_discovery_to_due_s` is a LEAD;
    `lat_due_to_subscribe_s` is an ELAPSED DELAY where positive means LATE.
    This is the real cold-start observation from deployment 04b0b253, where
    both describe the same 96.74 s from opposite sides."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1789408500.0}
    life = {"window_discovered_at": 1789408266.739,
            "window_due_at": 1789408170.0,
            "subscription_requested_at": 1789408266.739,
            "subscription_ack_at": 1789408266.746,
            "first_frame_at": 1789408266.803}
    d = C._window_digest("X", meta, _series(10), [], 0, None, life)
    # no lead at all: the window was already past due when first discovered
    assert d["lat_discovery_to_due_s"] == -96.739
    # and the SAME 96.739 s, as a delay, is POSITIVE
    assert d["lat_due_to_subscribe_s"] == 96.739
    # venue-side stages are elapsed durations, smaller is better
    assert d["lat_subscribe_to_ack_s"] == 0.007
    assert d["lat_ack_to_first_frame_s"] == 0.057
    # the unclamped first-frame lead, which tte_first_any cannot express
    assert round(d["expiry"] - d["first_frame_at"], 2) == 233.20


def test_on_time_subscribe_reports_zero_delay_not_a_negative_one():
    """`_due` forbids subscribing before expiry-LEAD, so the best achievable
    lat_due_to_subscribe_s is 0. A negative value would mean the roll had
    bypassed the window clock."""
    meta = {"asset": "BTC", "window_minutes": 5, "expiry_t": 1000.0}
    life = {"window_discovered_at": 100.0, "window_due_at": 670.0,
            "subscription_requested_at": 670.0, "first_frame_at": 670.1}
    d = C._window_digest("X", meta, _series(10), [], 0, None, life)
    assert d["lat_discovery_to_due_s"] == 570.0     # a real lead
    assert d["lat_due_to_subscribe_s"] == 0.0       # on time
    assert round(d["expiry"] - d["first_frame_at"], 2) == 329.90
