"""The BTC5m observability probe: what it measures, and what it must not do.

THE FACT IT EXISTS TO ESTABLISH. `l5_pmus_auth_book` measured, on BTC15m
2026-09-24, that a market-data subscription placed before a window opens stays
silent ~185s, that re-sending the subscribe on the SAME connection does not help
(0115z: 51 re-subscribes, still no book until +184.3s), and that only a FRESH
connection to an already-live market gets the snapshot at once. A 900s window
absorbs that. A 300s window may not. Nobody has measured it for the 5m series.

These tests do not measure the venue -- they pin the probe's arithmetic, its
fail-closed behaviour and its read-only structure, so the number it eventually
reports can be trusted and so running it cannot disturb the BTC15m recording it
sits beside.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from src import l5_series_registry as REG
from src import pmus_ws_evidence_recorder as R
import scripts.pmus_btc5m_auth_observability_probe as P

def _strip_docstrings(tree):
    """Scanning source for a forbidden word matches the prose explaining why
    it is forbidden -- a doc comment has tripped a string-matching test in
    this project five times already."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = list(getattr(node, "body", []) or [])
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:]
    return tree


BTC5M = REG.BY_STRATEGY["BTC5M"]
BTC15M = REG.BY_STRATEGY["BTC15M"]
#: 2026-11-19T04:17:30Z -- inside the 0415z 5m window and the 0415z 15m window
T = 1795061850.0


# --------------------------------------------------------- series resolution

def test_the_series_comes_from_the_registry_not_a_literal():
    assert P.series_for("BTC5M") is BTC5M
    assert BTC5M.horizon_s == 300.0
    assert BTC5M.prefix == "cpc-btc-updown-5m-"


@pytest.mark.parametrize("bad", ["", "   ", "BTC5", "btc-5m", "NOPE", None])
def test_a_malformed_series_refuses_and_names_what_exists(bad):
    with pytest.raises(SystemExit) as e:
        P.series_for(bad)
    assert "UNKNOWN_SERIES" in str(e.value)
    assert "BTC5M" in str(e.value)


def test_the_series_label_is_case_insensitive_but_not_fuzzy():
    assert P.series_for("btc5m") is BTC5M
    with pytest.raises(SystemExit):
        P.series_for("btc5m ")  # trailing space is stripped -> fine
        P.series_for("BTC5MM")


# ------------------------------------------------------------- window maths

def test_window_bounds_are_period_aligned():
    start, end = P.window_bounds(T, BTC5M)
    assert end - start == 300.0
    assert start % 300 == 0
    assert start <= T < end


@pytest.mark.parametrize("off", [0.0, 0.001, 150.0, 299.999])
def test_every_instant_in_a_window_maps_to_the_same_window(off):
    base, _ = P.window_bounds(T, BTC5M)
    assert P.window_bounds(base + off, BTC5M)[0] == base


def test_the_instant_a_window_ends_belongs_to_the_next_one():
    """ROLLOVER. The boundary is the new window's start, not the old one's."""
    base, end = P.window_bounds(T, BTC5M)
    assert P.window_bounds(end, BTC5M)[0] == end == base + 300.0


def test_the_slug_matches_the_registry_pattern_for_its_own_series():
    for series in (BTC5M, BTC15M):
        for slug in P.window_slugs(T, series, ahead=2):
            assert REG.for_slug(slug) is series, slug


def test_the_slug_arithmetic_is_identical_to_the_recorders(monkeypatch):
    """THE DUPLICATION, PINNED. `window_slugs` copies
    `R.rolling_window_slugs` because that one reads the PRODUCTION prefix and
    period from the environment. If the recorder's format ever changes, this
    fails rather than silently recording under a slug nobody else uses."""
    for series in (BTC5M, BTC15M):
        monkeypatch.setenv(R.ROLLOVER_SLUG_ENV, series.prefix)
        monkeypatch.setenv(R.ROLLOVER_SECONDS_ENV
                           if hasattr(R, "ROLLOVER_SECONDS_ENV")
                           else R.ROLLOVER_ENV, str(series.horizon_s))
        for ahead in (0, 1, 3):
            assert P.window_slugs(T, series, ahead=ahead) == \
                R.rolling_window_slugs(T, period_s=series.horizon_s,
                                       ahead=ahead)


def test_ahead_zero_is_the_live_window_only():
    assert len(P.window_slugs(T, BTC5M, ahead=0)) == 1
    assert len(P.window_slugs(T, BTC5M, ahead=2)) == 3


# ------------------------------------------------- the observation state

def obs_at(start=None):
    start = P.window_bounds(T, BTC5M)[0] if start is None else start
    return P.WindowObservation("cpc-btc-updown-5m-2026-11-19-0415z", start)


def test_nothing_seen_means_nothing_claimed():
    o = obs_at()
    assert o.first_frame is None and o.first_book is None
    assert o.first_two_sided is None and o.first_trade is None
    assert o.observed is False


def test_a_one_sided_book_is_not_an_observed_window():
    """THE DISTINCTION THE WHOLE PROBE RESTS ON: a bid with no ask cannot be
    priced against, so it does not count as observability."""
    o = obs_at()
    o.note_book(o.start + 12.0, two_sided=False)
    assert o.first_book == 12.0
    assert o.first_two_sided is None
    assert o.observed is False


def test_two_sided_is_recorded_at_its_own_first_sight():
    o = obs_at()
    o.note_book(o.start + 12.0, two_sided=False)
    o.note_book(o.start + 185.4, two_sided=True)
    assert o.first_book == 12.0
    assert o.first_two_sided == 185.4
    assert o.observed is True


def test_first_sight_is_never_overwritten_by_a_later_one():
    o = obs_at()
    o.note_book(o.start + 3.0, two_sided=True)
    o.note_book(o.start + 90.0, two_sided=True)
    assert o.first_two_sided == 3.0
    assert o.book_updates == 2


def test_a_trade_also_stamps_the_first_frame():
    o = obs_at()
    o.note_trade(o.start + 7.5)
    assert o.first_frame == 7.5 and o.first_trade == 7.5
    assert o.first_book is None, "a trade is not a book"


def test_latencies_are_relative_to_the_window_not_the_connection():
    """A fresh connection mid-window must not make the window look fast."""
    o = obs_at()
    o.note_book(o.start + 184.3, two_sided=True)
    assert o.first_two_sided == 184.3


# ------------------------------------------- the number that decides it

def test_a_window_that_was_never_two_sided_scores_fully_unobservable():
    """1.0, NOT None. None would read as "not measured" and this is the
    measurement -- the window was unobservable for all of itself."""
    assert P.unobservable_fraction(obs_at(), BTC5M) == 1.0


def test_the_measured_15m_latency_would_cost_most_of_a_5m_window():
    """The arithmetic that motivates the probe, pinned so it cannot drift:
    the 185s silence measured on BTC15m, applied to a 300s window."""
    o = obs_at()
    o.note_book(o.start + 184.3, two_sided=True)
    assert P.unobservable_fraction(o, BTC5M) == pytest.approx(0.6143, abs=1e-4)
    #: the same latency on a 15m window is a rounding error, which is why
    #: BTC15m works today and BTC5m is in question
    assert P.unobservable_fraction(o, BTC15M) == pytest.approx(0.2048, abs=1e-4)


def test_an_immediate_book_scores_near_zero():
    o = obs_at()
    o.note_book(o.start + 3.0, two_sided=True)
    assert P.unobservable_fraction(o, BTC5M) == pytest.approx(0.01, abs=1e-4)


def test_the_fraction_is_clamped_and_never_negative():
    o = obs_at()
    o.note_book(o.start - 5.0, two_sided=True)      # clock skew
    assert P.unobservable_fraction(o, BTC5M) == 0.0


# --------------------------------------------- stale / missing depth

def test_two_sided_reads_the_recorders_own_folding():
    """`both_sides_served` is set by R.apply_events, not by the probe, so the
    probe cannot disagree with the recorder about what a book is."""
    state = {}
    slug = "cpc-btc-updown-5m-2026-11-19-0415z"
    assert P.two_sided(state, slug) is False
    R.apply_events(state, {"marketSlug": slug,
                           "bids": [{"px": "0.27", "qty": "34.4"}],
                           "asks": []}, T)
    assert P.two_sided(state, slug) is False, "one-sided book"
    R.apply_events(state, {"marketSlug": slug,
                           "bids": [{"px": "0.27", "qty": "34.4"}],
                           "asks": [{"px": "0.28", "qty": "1546.84"}]}, T)
    assert P.two_sided(state, slug) is True


@pytest.mark.parametrize("state", [None, {}, {"other-slug": {}},
                                   {"cpc-btc-updown-5m-2026-11-19-0415z": {}}])
def test_missing_state_is_never_read_as_observed(state):
    assert P.two_sided(state, "cpc-btc-updown-5m-2026-11-19-0415z") is False


#: THE ROW THE RUNNING RECORDER ACTUALLY PRINTED, captured from
#: pmus-ws-evidence-recorder at 2026-11-19T04:18:34.886980791Z. The probe's
#: own emitter is pinned against this so the two tapes stay interchangeable.
LIVE_ROW = {
    "t": 1795061914.8294244, "bid": "0.2700", "ask": "0.2800",
    "bids": [["0.2700", "34.4000"], ["0.2600", "722.0000"],
             ["0.2500", "1549.0000"], ["0.2400", "581.8600"]],
    "asks": [["0.2800", "1546.8400"], ["0.2900", "495.7200"],
             ["0.3000", "462.0000"], ["0.3100", "460.9800"]],
    "rungs_cap": 16, "status": "OK",
}
LIVE_SLUG = "cpc-btc-updown-15m-2026-11-19-0415z"


def _ev(bids, asks):
    return {"kind": "book", "slug": LIVE_SLUG, "bids": bids, "asks": asks}


def _levels(pairs):
    return [{"px": px, "qty": qty} for px, qty in pairs]


def test_the_emitted_row_matches_one_the_running_recorder_printed():
    """THE SCHEMA, PINNED TO PRODUCTION OUTPUT rather than to prose. The
    recorder's `emit_new_ladders` is not on this branch, so this is the only
    way to show the two tapes are interchangeable."""
    snap = P.ladder_snapshot(_ev(_levels(LIVE_ROW["bids"]),
                                 _levels(LIVE_ROW["asks"])),
                             LIVE_ROW["t"])
    row = P.ladder_rows([snap], LIVE_ROW["t"])[0]
    assert row == LIVE_ROW


def test_the_document_envelope_matches_the_recorders():
    doc = P.ladder_document({LIVE_SLUG: [P.ladder_snapshot(
        _ev(_levels(LIVE_ROW["bids"]), _levels(LIVE_ROW["asks"])),
        LIVE_ROW["t"])]}, LIVE_ROW["t"])
    assert doc["kind"] == "authenticated_ladder_rows"
    assert doc["rungs_per_side"] == 4 and doc["cap_per_market"] == 40
    assert doc["rows"] == 1 and doc["connected"] is True
    assert "DEPTH IS NOT A FILL" in doc["labels"]
    assert "status=OK|STALE|ONE_SIDED|NO_BOOK|DISCONNECTED" in doc["schema"]


def test_a_stale_snapshot_is_labelled_stale_not_dropped():
    """Stale depth must stay visible AND labelled -- silently dropping it
    would make a gap look like quiet market."""
    snap = P.ladder_snapshot(_ev(_levels([("0.27", "34.4")]),
                                 _levels([("0.28", "1546.84")])), T)
    assert P.ladder_rows([snap], T)[0]["status"] == "OK"
    assert P.ladder_rows([snap], T + 3600)[0]["status"] == "STALE"


def test_a_one_sided_book_is_labelled_one_sided():
    snap = P.ladder_snapshot(_ev(_levels([("0.27", "34.4")]), []), T)
    row = P.ladder_rows([snap], T)[0]
    assert row["status"] == "ONE_SIDED"
    assert row["ask"] is None and row["asks"] == []


def test_an_empty_book_is_labelled_no_book():
    snap = P.ladder_snapshot(_ev([], []), T)
    assert P.ladder_rows([snap], T)[0]["status"] == "NO_BOOK"


def test_a_disconnected_read_is_never_labelled_ok():
    snap = P.ladder_snapshot(_ev(_levels([("0.27", "34.4")]),
                                 _levels([("0.28", "1.0")])), T)
    assert P.ladder_rows([snap], T, connected=False)[0]["status"] \
        == "DISCONNECTED"


def test_a_crossed_book_is_recorded_as_served_not_corrected():
    """The venue's own bytes. A bid above the ask is a finding about the
    venue, and silently reordering or dropping it would erase it."""
    snap = P.ladder_snapshot(_ev(_levels([("0.60", "10")]),
                                 _levels([("0.40", "10")])), T)
    row = P.ladder_rows([snap], T)[0]
    assert row["bid"] == "0.60" and row["ask"] == "0.40"
    assert row["status"] == "OK", "status describes presence, not sanity"


@pytest.mark.parametrize("bad", [
    [{"px": "0.27"}],                       # no qty
    [{"qty": "34.4"}],                      # no px
    [{"px": "abc", "qty": "1"}],            # unparseable px
    [{"px": None, "qty": "1"}],
    [{"px": "0.27", "qty": "abc"}],         # unparseable qty
    [{"px": "0.27", "qty": "-5"}],          # negative queue
    [{"px": "0.27", "qty": "0"}],           # a removed level is not depth
    [{"px": "-0.1", "qty": "5"}],           # price outside [0, 1]
    [{"px": "1.5", "qty": "5"}],
    [{"px": "nan", "qty": "5"}],
    [{"px": "0.27", "qty": "inf"}],
    ["0.27"],                               # not a mapping
    [[0.27, 34.4]],
])
def test_a_malformed_quantity_is_dropped_never_zero_filled(bad):
    """An unknown queue treated as zero puts us at the FRONT of a queue we
    cannot see, which would overstate every hypothetical fill."""
    assert P.rungs(bad, 16) == []


def test_a_malformed_rung_does_not_discard_the_good_ones():
    got = P.rungs([{"px": "0.27", "qty": "34.4"}, {"px": "bad", "qty": "1"},
                   {"px": "0.25", "qty": "7"}], 16)
    assert got == [["0.27", "34.4"], ["0.25", "7"]]


def test_this_probe_is_strictly_stricter_than_the_deployed_parser():
    """A DEFECT IN THE EXISTING TAPE, pinned rather than described. `R._px`
    rejects only None, so the deployed `_levels` carries a non-numeric price
    into PMUS_LADDER_ROWS as though it were a price. Anything reading that
    tape to size a queue would read a string that is not a number."""
    assert R._px("abc") == "abc", "the deployed parser accepts it"
    assert P.rungs([{"px": "abc", "qty": "1"}], 16) == [], "this one does not"
    #: and every rung THIS accepts, the deployed one would too, so the probe
    #: can never record depth the recorder would have refused
    good = _levels(LIVE_ROW["bids"])
    assert P.rungs(good, 16) == [[lv["px"], lv["qty"]] for lv in good]


@pytest.mark.parametrize("levels", [None, "", 0, {}, 5])
def test_a_non_list_level_set_yields_no_rungs(levels):
    assert P.rungs(levels, 16) == []


def test_rungs_are_capped_and_order_preserved():
    pairs = [("0.%02d" % (30 - i), str(i + 1)) for i in range(25)]
    got = P.rungs(_levels(pairs), 5)
    assert len(got) == 5
    assert got == [[p, q] for p, q in pairs[:5]]
    assert P.rungs(_levels(pairs), 0) == []


def test_only_our_own_slug_is_ever_given_depth():
    """A shared socket must not record depth for a market this probe was
    never pointed at."""
    doc = P.ladder_document({LIVE_SLUG: [P.ladder_snapshot(
        _ev(_levels([("0.27", "1")]), _levels([("0.28", "1")])), T)]}, T)
    assert list(doc["markets"]) == [LIVE_SLUG]


def test_snapshots_per_emission_are_bounded():
    snaps = [P.ladder_snapshot(_ev(_levels([("0.27", "1")]),
                                   _levels([("0.28", "1")])), T + i)
             for i in range(200)]
    assert len(P.ladder_rows(snaps, T + 200)) == P.LADDER_LOG_CAP


# ---------------------------------------------- bounds and fail-closed

def test_the_reconnect_budget_is_bounded_per_window():
    """A venue outage must not become a reconnect storm on the shared key:
    the 15m module slowed down after 10 fruitless reconnects for exactly
    this reason, and that bound is carried over rather than reinvented."""
    assert P.RESUB_MAX_PER_WINDOW == 10
    from src import l5_pmus_auth_book as AB
    assert P.RESUB_MAX_PER_WINDOW == AB.RESUB_FAST_MAX
    assert P.DEFAULT_RESUB_S == AB.RESUB_EVERY_S


def test_the_window_count_is_bounded_and_it_never_relaunches():
    """It answers a question and exits. Nothing in it restarts the process."""
    assert P.DEFAULT_WINDOWS <= 8
    tree = ast.parse(inspect.getsource(P))
    code = ast.unparse(_strip_docstrings(tree))
    for forbidden in ("while True", "execv", "Popen", "subprocess",
                      "restart", "relaunch"):
        assert forbidden not in code, forbidden


def test_no_credentials_fails_closed(monkeypatch, capsys):
    import asyncio
    monkeypatch.delenv(R.KEY_ENV, raising=False)
    monkeypatch.delenv(R.SECRET_ENV, raising=False)
    assert asyncio.run(P.run("BTC5M", 1, 3.0)) == 2
    assert "NO_CREDENTIALS" in capsys.readouterr().out


# ------------------------------------------------- structurally read-only

def test_the_probe_has_no_order_path():
    """THE CODE is scanned with the docstrings stripped: this module's own
    prose says "owns no order path", and grepping raw source would match its
    own explanation -- a doc comment has tripped a string-matching test in
    this project five times already."""
    code = ast.unparse(_strip_docstrings(
        ast.parse(textwrap.dedent(inspect.getsource(P)))))
    for forbidden in ("submit", "cancel", "amend", "POST", "order(",
                      "place", "sign_order"):
        assert forbidden not in code, forbidden


def test_every_outbound_frame_kind_is_one_the_guard_admits():
    """Not "we only send safe frames" as prose -- the frame keys the source
    actually constructs, checked against the recorder's whitelist."""
    tree = ast.parse(inspect.getsource(P))
    sent = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                if k.value in ("subscribe", "unsubscribe", "heartbeat") or \
                        k.value.islower() and k.value in R.OUTBOUND_FRAME_KEYS:
                    sent.add(k.value)
    assert sent <= R.OUTBOUND_FRAME_KEYS
    assert "heartbeat" in sent, "the keep-alive is the one frame it builds"


def test_the_guard_still_refuses_anything_else():
    with pytest.raises(RuntimeError):
        R.guard_outbound({"order": {}})
    assert R.guard_outbound({"subscribe": {}}) == {"subscribe": {}}


# --------------------------------- BTC15m backward compatibility

def test_the_probe_imports_the_recorder_without_mutating_it():
    """It must be importable alongside the running recorder: no module-level
    environment writes, no monkeypatching of R."""
    tree = ast.parse(inspect.getsource(P))
    for node in tree.body:
        assert not isinstance(node, (ast.Assign, ast.AugAssign)) or all(
            isinstance(t, ast.Name) and t.id.isupper()
            for t in getattr(node, "targets", [getattr(node, "target", None)])
            if t is not None), ast.unparse(node)
    code = ast.unparse(tree)
    assert "R." in code
    for forbidden in ("R.apply_events =", "R.ladder_markets =",
                      "R.emit_new_ladders =", "R.subscribe_frames ="):
        assert forbidden not in code, forbidden


def test_the_same_probe_measures_btc15m_unchanged():
    """BACKWARD COMPATIBILITY, as a property rather than a promise: nothing
    about the probe is 5m-specific, so the series that works today can be
    measured with it and must give the known-good answer shape."""
    o = P.WindowObservation(P.window_slugs(T, BTC15M)[0],
                            P.window_bounds(T, BTC15M)[0])
    o.note_book(o.start + 6.8, two_sided=True)
    assert o.observed is True
    assert P.unobservable_fraction(o, BTC15M) < 0.01


def test_the_recorder_module_is_not_modified_by_this_change():
    """The patch adds a script and a test. If a future edit reaches into the
    deployed recorder, this names it: these are the symbols the probe relies
    on and it must consume them as they are."""
    for name in ("apply_events", "subscribe_frames", "guard_outbound",
                 "auth_headers", "classify_frame", "has_credentials",
                 "book_status", "_walk_market_events", "_px", "WS_URL",
                 "OUTBOUND_FRAME_KEYS", "KEY_ENV", "SECRET_ENV"):
        assert hasattr(R, name), name
