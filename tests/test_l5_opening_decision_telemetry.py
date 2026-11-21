"""Per-tick opening-decision telemetry, and proof it is not silently swallowed.

WHY THIS FILE IS SHAPED THIS WAY. `_note_open_skip` emits inside one
`except BaseException` swallow, because telemetry must never be able to change
which tick opens. That swallow is also the hazard: a single wrong attribute
name makes the record silently never appear, and the only evidence is a
counter nobody reads. This file's central assertion is therefore NEGATIVE --
`open_skip_emit_failed:*` must stay EMPTY -- and it caught exactly that bug
while this telemetry was being written (`T.utcnow_iso` does not exist; the
real helper is the module-level `now_utc`).

The sample these records exist to make possible: Campaign E's 0245z window has
no decision record at all between 02:45:00 and 02:53:45, the span holding all
six of its submissions, because every tick in it returned from
`maybe_controller_open` before `executor.handle` was reached. The
`controller_open:*` counters did record those ticks, but counters are
CUMULATIVE over a process lifetime and cannot be attributed to a window after
the fact -- when deployment 05884c9c was replaced, the one surviving snapshot
spanned 02:18-03:01 across two windows.
"""
import collections

import pytest

from src import l5_pmus_live_runner as R

#: every early return in `maybe_controller_open` that decides a tick without
#: the executor ever seeing it.
SKIP_REASONS = (
    "NOT_AUTHORIZED_MARKET",
    "WINDOW_START_UNKNOWN",
    "LEDGER_UNREADABLE",
    "ORDER_ALREADY_RESTING",
    "NO_AUTHENTICATED_BOOK",
    "PROVISIONAL_PRICE_UNREADABLE",
    "OPENING_CAP_UNREADABLE",
    # the bounded replenishment gate: a re-open with no fill or cancellation
    # behind it, or with the window's rounds already spent. One literal
    # reason on purpose -- the specific cause rides in the detail, because
    # an interpolated reason could not be checked by this enumeration.
    "REPLENISH_UNPAID",
    # the portfolio controller's steer (L5_PORTFOLIO_CONTROLLER=STEER
    # only): flat, nothing resting, and no side it would pay to open.
    "PORTFOLIO_NOTHING_WANTED",
)


class Stub:
    """The minimum `_note_open_skip` touches: a stats counter."""

    def __init__(self):
        self.stats = collections.Counter()


@pytest.fixture()
def cap(monkeypatch):
    out = []
    monkeypatch.setattr(R, "emit", lambda label, rec: out.append((label, rec)))
    return out


def call(stub, reason, slug="cpc-btc-updown-15m-2026-11-05-0245z",
         ws=None, detail=None):
    return R.Runner._note_open_skip(stub, reason, slug, ws, detail)


# ---------------------------------------------------------------- it emits

@pytest.mark.parametrize("reason", SKIP_REASONS)
def test_every_skip_reason_emits_one_record(cap, reason):
    stub = Stub()
    call(stub, reason)
    assert len(cap) == 1
    label, rec = cap[0]
    assert label == "L5_OPENING_DECISION"
    assert rec["reason"] == reason


@pytest.mark.parametrize("reason", SKIP_REASONS)
def test_the_swallow_never_fires(cap, reason):
    """THE ASSERTION THAT MATTERS. A swallowed telemetry bug is invisible."""
    stub = Stub()
    call(stub, reason)
    failed = [k for k in stub.stats if k.startswith("open_skip_emit_failed")]
    assert failed == [], "telemetry raised and was swallowed: %r" % (
        dict(stub.stats),)
    assert len(cap) == 1


def test_record_says_no_opening_was_considered(cap):
    call(Stub(), "ORDER_ALREADY_RESTING")
    rec = cap[0][1]
    assert rec["considered"] is False
    assert rec["market"] == "cpc-btc-updown-15m-2026-11-05-0245z"
    # a real, parseable timestamp -- this is what T.utcnow_iso() got wrong
    assert "utc" in rec and rec["utc"]
    import datetime as dt
    parsed = dt.datetime.fromisoformat(rec["utc"])
    assert parsed.tzinfo is not None


def test_elapsed_is_window_relative_so_a_record_can_be_windowed(cap):
    """The whole point: a counter cannot be attributed to a window, this can."""
    import time
    ws = time.time() - 525.0
    call(Stub(), "ORDER_ALREADY_RESTING", ws=ws)
    rec = cap[0][1]
    assert rec["window"] == str(ws)
    assert 520.0 <= rec["elapsed_s"] <= 530.0


def test_absent_window_start_omits_elapsed_rather_than_guessing(cap):
    call(Stub(), "WINDOW_START_UNKNOWN", ws=None)
    rec = cap[0][1]
    assert "elapsed_s" not in rec
    assert "window" not in rec


def test_unparseable_window_start_does_not_lose_the_record(cap):
    """A bad ws must cost the elapsed field, never the whole row."""
    call(Stub(), "ORDER_ALREADY_RESTING", ws="not-a-number")
    assert len(cap) == 1
    rec = cap[0][1]
    assert rec["reason"] == "ORDER_ALREADY_RESTING"
    assert "elapsed_s" not in rec


def test_detail_is_merged(cap):
    call(Stub(), "ORDER_ALREADY_RESTING", detail={"still_fillable_orders": 1})
    assert cap[0][1]["still_fillable_orders"] == 1


def test_detail_cannot_overwrite_the_reason_silently(cap):
    """If a caller shadows a field the record must still name its reason."""
    call(Stub(), "ORDER_ALREADY_RESTING", detail={"considered": True})
    rec = cap[0][1]
    assert rec["reason"] == "ORDER_ALREADY_RESTING"


def test_a_none_slug_is_recorded_as_none_not_the_string(cap):
    call(Stub(), "WINDOW_START_UNKNOWN", slug=None)
    assert cap[0][1]["market"] is None


# ------------------------------------------------- it changes no behaviour

def test_it_returns_none(cap):
    assert call(Stub(), "ORDER_ALREADY_RESTING") is None


def test_an_emit_that_raises_is_swallowed_and_counted(monkeypatch):
    """Telemetry must never raise into the opening path."""
    def boom(label, rec):
        raise RuntimeError("tape down")
    monkeypatch.setattr(R, "emit", boom)
    stub = Stub()
    assert call(stub, "ORDER_ALREADY_RESTING") is None
    assert stub.stats["open_skip_emit_failed:RuntimeError"] == 1


def test_keyboard_interrupt_is_not_swallowed(monkeypatch):
    def boom(label, rec):
        raise KeyboardInterrupt
    monkeypatch.setattr(R, "emit", boom)
    with pytest.raises(KeyboardInterrupt):
        call(Stub(), "ORDER_ALREADY_RESTING")


# ------------------------------------------------ every site is wired up

def test_all_seven_early_returns_call_the_helper():
    """A counter without a record is the blindness this removes."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_controller_open)
    for reason in SKIP_REASONS:
        assert 'controller_open:%s' % reason in src, reason
    assert src.count("_note_open_skip(") == len(SKIP_REASONS)


def test_the_helper_is_called_before_each_return_not_after():
    """Called after `return None` it would be dead code."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_controller_open)
    lines = [l.strip() for l in src.splitlines()]
    for i, l in enumerate(lines):
        if l.startswith("self._note_open_skip("):
            tail = [x for x in lines[i:i + 6] if x]
            assert any(x == "return None" for x in tail), (
                "no return follows the skip note at line %d" % i)


def test_counters_are_still_incremented_alongside_the_records():
    """The records are additive; removing a counter would break dashboards."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_controller_open)
    assert src.count("self.stats[\"controller_open:") >= len(SKIP_REASONS) - 1
