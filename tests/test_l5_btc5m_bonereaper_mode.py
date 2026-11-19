"""Bonereaper-style BTC5m execution, at our scale (issue #247).

WHAT THE TARGET DOES, measured from the 304-window harvest
(analysis/traders/bonereaper/BEHAVIOUR.txt): quotes BOTH sides at the market
across the whole price range (median fill 0.46-0.47, not a cheap fixed rung),
in clips whose size does NOT change with inventory, 50.5 fills a window; 57%
of fills ADD to the side already heavy; a median 28% of what is held at the
end is unmatched and is carried to settlement.

WHAT THIS FILE PINS. That behaviour is the existing book-relative two-sided
mode plus the one thing it lacked: following the book. Without
`straddle_requote_ticks` every move left the old quote resting, six stale
orders filled the order limit after four moves, and a resting UP 0.60 sat
beside a resting DOWN 0.53 -- a 1.13 pair under a 0.99 ceiling.

WHAT IT DOES NOT CLAIM. Nothing here is evidence the behaviour is profitable:
that wallet lost $9,727 on these windows. The EV rates below are SYNTHETIC and
mechanism-only, as in test_l5_btc5m_continuous_participation.py. No taker
order, no pair at or above 1.00 and no limit change is introduced.

Driven through the real `l5_btc5m_runner.public_book`, the real
`Btc5mExecutor`, the strict Venue double and a ledger on disk.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_venue_constraints as VC

from tests.test_l5_btc5m_execution import (          # noqa: E402
    ARMED, MARKET, Rig, armed, clean_env,            # noqa: F401
)
from tests.test_l5_btc5m_continuous_participation import (   # noqa: E402
    Rig6, market_doc, rig, two_sided,                # noqa: F401
)

LEG, COMP = "STRADDLE_LEG", "STRADDLE_COMPLETION"


@pytest.fixture(autouse=True)
def _no_requote_leak(monkeypatch):
    monkeypatch.delenv(X.STRADDLE_REQUOTE_TICKS_ENV, raising=False)


@pytest.fixture
def stacking(monkeypatch, two_sided):
    """Book-relative two-sided quoting as it was: no requote."""
    monkeypatch.setenv(X.STRADDLE_BOOK_RELATIVE_ENV, "YES")
    monkeypatch.setenv(X.STRADDLE_PAIR_CEILING_ENV, "0.99")
    monkeypatch.setenv(X.STRADDLE_FILL_MODEL_ENV, X.FILL_MODEL_TOKEN)
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.01")
    monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "1.00")
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "0.05")


@pytest.fixture
def bonereaper(monkeypatch, stacking):
    monkeypatch.setenv(X.STRADDLE_REQUOTE_TICKS_ENV, "2")


def working(rig, side=None, reason=None):
    return [o for o in rig.working()
            if (side is None or rig.venue.by_oid[o]["side"] == side)
            and (reason is None or rig.venue.by_oid[o]["reason"] == reason)]


def px(rig, oid):
    return D(rig.venue.by_oid[oid]["price"])


def resting(rig):
    return sorted((rig.venue.by_oid[o]["side"], px(rig, o))
                  for o in rig.working())


def cancelled(rig):
    return [oid for oid, _ in rig.venue.cancelled]


def qty(rig, side):
    return D(str(rig.ex.inventory(MARKET)[side]["qty"]))


def at(rig, t, bid, ask):
    return rig.tick(2).go(t=t, bid=bid, ask=ask)


# ============ 0. THE DEFECT, pinned as the old mode still behaves

def test_without_requote_the_old_quotes_stack_and_breach_the_pair_ceiling(
        rig, armed, stacking):
    """Not a behaviour to keep -- the reason the requote exists. Pinned so
    the default stays byte-identical and the fix is shown to be the cause."""
    rig.go(t=5.0, bid="0.50", ask="0.52")
    at(rig, 8.0, "0.55", "0.57")
    at(rig, 10.0, "0.60", "0.62")
    at(rig, 12.0, "0.45", "0.47")
    rep = at(rig, 14.0, "0.40", "0.42")
    assert cancelled(rig) == []
    assert len(rig.working()) == 6
    assert any(r["reason"] == "MAX_LIVE_ORDERS" for r in rep["refused"])
    up = max(px(rig, o) for o in working(rig, C.UP))
    dn = max(px(rig, o) for o in working(rig, C.DOWN))
    assert up + dn > D("1.00"), (up, dn)


# ============ 1. ONE QUOTE A SIDE, AT THE MARKET, EARLY

def test_both_sides_are_quoted_at_the_book_from_the_first_step(rig, armed,
                                                               bonereaper):
    rig.go(t=2.0, bid="0.50", ask="0.52")
    assert resting(rig) == [(C.DOWN, D("0.48")), (C.UP, D("0.50"))]
    sent = rig.venue.sent
    assert all(s["post_only"] and s["mode"] == "MAKER"
               and s["action"] == "BUY" for s in sent)
    assert len({s["qty"] for s in sent}) == 1, "the clips are not equal"


def test_an_unchanged_book_cancels_and_reissues_nothing(rig, armed,
                                                        bonereaper):
    rig.go(t=2.0, bid="0.50", ask="0.52")
    for i in range(5):
        at(rig, 5.0 + i, "0.50", "0.52")
    assert cancelled(rig) == [] and len(rig.venue.sent) == 2


# ============ 2. REQUOTE: REAL CANCEL BY EXACT ID, THEN REPLACE

def test_a_moved_book_cancels_exactly_the_two_stale_quotes_first(
        rig, armed, bonereaper):
    rig.go(t=2.0, bid="0.50", ask="0.52")
    old = set(rig.working())
    rep = at(rig, 5.0, "0.55", "0.57")
    #: THE CANCEL STEP: both stale ids, nothing placed beside them
    assert set(cancelled(rig)) == old and len(cancelled(rig)) == 2
    assert len(rig.venue.sent) == 2, "replaced in the same step as the cancel"
    assert rig.working() == []
    assert rep["why"] != "PARAMS_UNREADABLE"
    #: THE REPLACE STEP: one fresh quote a side at the new book
    at(rig, 7.0, "0.55", "0.57")
    assert resting(rig) == [(C.DOWN, D("0.43")), (C.UP, D("0.55"))]
    assert not set(rig.working()) & old


def test_a_one_tick_drift_keeps_queue_position_on_the_trailing_side(
        rig, armed, bonereaper):
    """QUEUE-AWARE. The book ticks up one: UP now trails by one tick, inside
    the two-tick tolerance, and KEEPS its place. DOWN is now bidding ABOVE
    what we would quote, which has no tolerance, and is withdrawn."""
    rig.go(t=2.0, bid="0.50", ask="0.52")
    up, dn = working(rig, C.UP)[0], working(rig, C.DOWN)[0]
    at(rig, 5.0, "0.51", "0.53")
    assert cancelled(rig) == [dn]
    assert up in rig.working()
    at(rig, 7.0, "0.51", "0.53")
    assert resting(rig) == [(C.DOWN, D("0.47")), (C.UP, D("0.50"))]


def test_walking_the_book_never_stacks_never_breaches_and_never_hits_the_limit(
        rig, armed, bonereaper):
    path = ["0.50", "0.55", "0.60", "0.58", "0.45", "0.40", "0.47", "0.62",
            "0.30", "0.71", "0.52", "0.52"]
    rig.go(t=2.0, bid="0.50", ask="0.52")
    seen_two = 0
    for i, b in enumerate(path * 2):
        rep = at(rig, 4.0 + 2 * i, b, "%.2f" % (float(b) + 0.02))
        assert not [r for r in rep["refused"]
                    if r["reason"] == "MAX_LIVE_ORDERS"], i
        for side in C.SIDES:
            assert len(working(rig, side)) <= 1, (i, resting(rig))
        if len(rig.working()) == 2:
            seen_two += 1
            assert sum(p for _, p in resting(rig)) <= D("0.99"), resting(rig)
    ids = cancelled(rig)
    assert len(ids) >= 10, "the book moved and almost nothing was requoted"
    assert len(ids) == len(set(ids)) and set(ids) <= set(rig.venue.by_oid)
    assert seen_two >= 8, "two-sided quoting did not resume after requotes"
    coids = [s["client_order_id"] for s in rig.venue.sent]
    assert len(coids) == len(set(coids))
    spec = VC.from_market(market_doc()["market"])
    for s in rig.venue.sent:
        assert VC.check_order(s["price"], s["qty"], spec) is None, s


def test_a_fill_that_beats_the_requote_cancel_is_booked_and_not_rebought(
        rig, armed, bonereaper):
    """THE RACE on the requote path. The cancel loses to a fill: the fill is
    inventory, and the step that found it sends nothing."""
    rig.go(t=2.0, bid="0.50", ask="0.52")
    up = working(rig, C.UP)[0]
    q = D(str(rig.venue.by_oid[up]["qty"]))
    rig.venue.fill_on_cancel.add(up)
    n = len(rig.venue.sent)
    rep = at(rig, 5.0, "0.55", "0.57")
    assert up in cancelled(rig)
    assert qty(rig, C.UP) == q, "the racing fill was lost"
    assert len(rig.venue.sent) == n
    #: and the next step quotes from the inventory that fill created
    at(rig, 7.0, "0.55", "0.57")
    assert len(working(rig, C.UP)) <= 1 and len(working(rig, C.DOWN)) == 1


def test_a_pair_that_can_no_longer_be_quoted_withdraws_what_rests(
        rig, armed, bonereaper, monkeypatch):
    rig.go(t=2.0, bid="0.50", ask="0.52")
    old = set(rig.working())
    monkeypatch.setenv(X.STRADDLE_PAIR_CEILING_ENV, "0.97")
    rep = at(rig, 5.0, "0.50", "0.52")          # 0.50 + 0.48 = 0.98 > 0.97
    assert rep["why"] == "PAIR_ABOVE_CEILING"
    assert set(cancelled(rig)) == old and rig.working() == []


def test_the_requote_survives_a_restart(rig, armed, bonereaper):
    """Leg-versus-completion is read from the LEDGER, so a rebuilt executor
    still knows which orders it may move."""
    rig.go(t=2.0, bid="0.50", ask="0.52")
    old = set(rig.working())
    rig.restart()
    at(rig, 5.0, "0.56", "0.58")
    assert set(cancelled(rig)) == old


# ============ 3. ASYMMETRIC ACCUMULATION, HELD, NEVER FORCED FLAT

def _fill(rig, side):
    n = 0
    for o in working(rig, side, LEG):
        rig.venue.fill(o)
        n += 1
    return n


def test_the_heavy_side_keeps_being_bought_until_the_unmatched_cap(
        rig, armed, bonereaper):
    """57% of the target's fills ADD to the heavy side. Ours may too, up to
    the unmatched ceiling and not a cent past one clip beyond it."""
    rig.go(t=2.0, bid="0.50", ask="0.52")
    fills = 0
    for i in range(12):
        fills += _fill(rig, C.UP)
        rep = at(rig, 5.0 + 2 * i, "0.50", "0.52")
    assert fills >= 3, "the heavy side was bought once and locked out"
    inv = rig.ex.inventory(MARKET)
    assert qty(rig, C.DOWN) == 0
    assert D(str(inv[C.UP]["cost"])) <= D("0.05") + D("0.01")
    assert rep["why"] == "UNMATCHED_CEILING"
    #: the heavy side stopped; the light side is STILL quoted
    assert working(rig, C.UP) == [] and len(working(rig, C.DOWN)) == 1
    #: and nothing tried to sell or pay up to get flat
    assert all(s["action"] == "BUY" and s["reason"] == LEG
               for s in rig.venue.sent)


def test_both_sides_fill_repeatedly_and_the_residual_is_carried_to_the_end(
        rig, armed, bonereaper):
    rig.go(t=2.0, bid="0.50", ask="0.52")
    seq = [C.UP, C.UP, C.DOWN, C.UP, C.DOWN, C.DOWN, C.UP, C.UP, C.DOWN,
           C.UP]
    for i, side in enumerate(seq):
        _fill(rig, side)
        at(rig, 5.0 + 2 * i, "0.50", "0.52")
    up, dn = qty(rig, C.UP), qty(rig, C.DOWN)
    assert up > 0 and dn > 0 and up != dn, (up, dn)
    live = set(rig.working())
    rep = rig.tick(2).go(t=301.0, bid="0.50", ask="0.52")
    assert rep["why"] == "WINDOW_OVER"
    assert set(cancelled(rig)) >= live and rig.working() == []
    #: THE RESIDUAL IS HELD: the window's end cancels orders, not inventory
    assert (qty(rig, C.UP), qty(rig, C.DOWN)) == (up, dn)
    assert all(s["action"] == "BUY" for s in rig.venue.sent)


def test_a_completion_is_never_moved_by_the_requote(rig, armed, bonereaper,
                                                    monkeypatch):
    """The requote moves STRADDLE_LEG orders only. A resting completion is
    priced by the completion block and must not be withdrawn by this pass
    merely because it sits far from the book-relative quote."""
    monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, "0.90")
    monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "NO")
    rig.go(t=2.0, bid="0.50", ask="0.52")
    _fill(rig, C.UP)
    at(rig, 40.0, "0.50", "0.52")
    comp = working(rig, reason=COMP)
    assert len(comp) == 1, [rig.venue.by_oid[o] for o in rig.working()]
    #: unchanged book, several steps: the completion's distance from the
    #: DOWN quote (0.48) is far more than two ticks, and it stays
    for i in range(4):
        at(rig, 42.0 + 2 * i, "0.50", "0.52")
    assert comp[0] in rig.working()
    assert comp[0] not in cancelled(rig)


# ============ 4. THE GATES THAT STAY SHUT

def test_requote_is_off_unless_asked_for(rig, armed, stacking):
    assert X.straddle_requote_ticks() == 0
    rig.go(t=2.0, bid="0.50", ask="0.52")
    at(rig, 5.0, "0.60", "0.62")
    assert cancelled(rig) == []


@pytest.mark.parametrize("raw,want", [("2", 2), ("99", 5), ("-1", 0),
                                      ("x", 0), ("", 0)])
def test_the_requote_setting_is_clamped_and_fails_off(monkeypatch, raw, want):
    monkeypatch.setenv(X.STRADDLE_REQUOTE_TICKS_ENV, raw)
    assert X.straddle_requote_ticks() == want


def test_a_ninety_cent_leg_needs_the_ev_gate_like_any_other(rig, armed,
                                                            bonereaper,
                                                            monkeypatch):
    """35% of the target's dollars were bought at 0.90 or above. We quote
    there only if the entry-EV gate admits it; with its rates withdrawn,
    nothing opens at any price."""
    for k in (X.STRADDLE_P_PAIR_NATURAL_ENV, X.STRADDLE_P_COMPLETE_ENV,
              X.STRADDLE_P_LONE_WIN_ENV):
        monkeypatch.delenv(k, raising=False)
    rep = rig.go(t=2.0, bid="0.92", ask="0.94")
    assert rig.venue.sent == [] and "NOT_SET" in str(rep["why"])


def test_no_order_is_ever_a_taker_and_no_pair_reaches_one_dollar(
        rig, armed, bonereaper):
    rig.go(t=2.0, bid="0.50", ask="0.52")
    for i, b in enumerate(["0.92", "0.92", "0.08", "0.08", "0.50", "0.50"]):
        _fill(rig, C.UP if i % 2 else C.DOWN)
        at(rig, 5.0 + 2 * i, b, "%.2f" % (float(b) + 0.02))
    assert all(s["post_only"] and s["mode"] == "MAKER" for s in rig.venue.sent)
    assert X.STRADDLE_PAIR_CEILING_MAX < D("1.00")
