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


# ============ 5. THE MOST A WINDOW CAN LOSE, UNDER AN ADVERSARY
#
# A window settles UP or DOWN, so its loss is
#       total cost  -  min(UP shares, DOWN shares).
# NOT "the unmatched cap plus a clip", which an earlier proposal claimed:
# that reasoning assumed a matched pair costs what was QUOTED together, and
# a leg filled at 0.97 before a reversal pairs with a leg filled at 0.97
# after it. The adversary below controls the book AND chooses which of our
# resting orders fill, in full or in part, including fills that beat a
# cancel. Figures are read from the executor's own ledger and the venue.

import random                                           # noqa: E402

SIDE_CAP, UNM_CAP, LOSS_CAP = D("1.20"), D("0.45"), D("0.45")


@pytest.fixture
def probe(monkeypatch, bonereaper):
    """The profile proposed for the one-window probe, minus the loss gate."""
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.10")
    monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, str(SIDE_CAP))
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, str(UNM_CAP))
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "3.00")


@pytest.fixture
def gated(monkeypatch, probe):
    monkeypatch.setenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV, str(LOSS_CAP))


def _held(rig):
    inv = rig.ex.inventory(MARKET)
    return {s: (D(str(inv[s]["qty"])), D(str(inv[s]["cost"])))
            for s in C.SIDES}


def _open(rig):
    """(side, price, remaining qty) of everything the venue still works."""
    out = []
    for o in rig.working():
        i = rig.venue.by_oid[o]
        left = D(str(i["qty"])) - D(rig.venue.cum[o])
        if left > 0:
            out.append((i["side"], D(i["price"]), left))
    return out


def realized_worst(rig):
    """Loss if the window settled NOW against us."""
    return C.worst_case_loss_usd(_held(rig), [])


def potential_worst(rig):
    """Loss if any subset of what is still working ALSO filled first."""
    return C.worst_case_loss_usd(_held(rig), _open(rig))


def gross(rig):
    h = _held(rig)
    return (h[C.UP][1] + h[C.DOWN][1]
            + sum((p * q for _, p, q in _open(rig)), D(0)))


def whipsaw(rig, steps=120):
    """Fill whichever side is quoted HIGH, then reverse the market."""
    peak = pot = exp = D(0)
    hi = True
    rig.go(t=2.0, bid="0.96", ask="0.98")
    for i in range(steps):
        side = C.UP if hi else C.DOWN
        hit = [o for o in working(rig, side, LEG)
               if px(rig, o) >= D("0.90")]
        for o in hit:
            rig.venue.fill(o)
        if hit:
            hi = not hi
        bid = "0.96" if hi else "0.02"
        at(rig, 4.0 + 2 * i, bid, "%.2f" % (float(bid) + 0.02))
        peak, pot, exp = (max(peak, realized_worst(rig)),
                          max(pot, potential_worst(rig)),
                          max(exp, gross(rig)))
    return peak, pot, exp


def test_without_the_loss_gate_a_reversal_loses_far_more_than_the_unmatched_cap(
        rig, armed, probe):
    """THE CORRECTION. Every existing check passes at every step, nothing is
    unmatched beyond its cap, and the window still loses about a whole
    side's cap. The provable bound without the gate is the per-side cap:
    cost_up + cost_down - min(shares) <= max(cost_up, cost_down), because
    every share costs under a dollar."""
    peak, pot, exp = whipsaw(rig)
    assert peak > D("0.55"), "the old 0.55 claim would have held: %s" % peak
    assert peak > UNM_CAP * 2
    assert pot <= SIDE_CAP, pot
    assert exp <= SIDE_CAP * 2
    assert all(s["post_only"] for s in rig.venue.sent)


def test_with_the_loss_gate_the_same_adversary_is_held_to_the_ceiling(
        rig, armed, gated):
    peak, pot, exp = whipsaw(rig)
    assert pot <= LOSS_CAP, pot
    assert peak <= LOSS_CAP
    #: and it was the gate that stopped it, by name
    rep = at(rig, 262.0, "0.96", "0.98")
    assert rep["why"] == "WINDOW_LOSS_CEILING", rep["why"]
    assert rig.working() == [] or potential_worst(rig) <= LOSS_CAP
    #: it did not simply refuse to trade: most of the budget was used
    assert peak > D("0.30"), "the gate stopped the mode from trading at all"


def test_the_loss_gate_counts_orders_already_resting(rig, armed, stacking,
                                                     monkeypatch):
    """Without requoting, quotes stack. Each new one alone is inside the
    ceiling; together with what already rests they are not. The gate must
    see the resting book, and then every fill the adversary can choose
    stays inside it."""
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.10")
    monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "1.20")
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "2.00")
    monkeypatch.setenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV, "0.25")
    rig.go(t=2.0, bid="0.50", ask="0.52")
    whys = []
    for i, b in enumerate(["0.55", "0.60", "0.65", "0.70", "0.75"]):
        whys.append(at(rig, 4.0 + 2 * i, b,
                       "%.2f" % (float(b) + 0.02))["why"])
        assert potential_worst(rig) <= D("0.25"), (b, _open(rig))
    assert "WINDOW_LOSS_CEILING" in whys
    assert len(working(rig, C.UP)) >= 2, "nothing stacked; the test is vacuous"
    #: the adversary takes every UP order and no DOWN order
    for o in working(rig, C.UP):
        rig.venue.fill(o)
    at(rig, 20.0, "0.75", "0.77")
    assert realized_worst(rig) <= D("0.25")


@pytest.mark.parametrize("seed", range(40))
def test_no_fill_order_race_or_partial_fill_exceeds_the_ceiling(
        tmp_path, armed, gated, seed):
    """A random adversary: random walk of the book over the whole range,
    each resting order filled in full, in part or not at all, and a fill
    armed to beat any cancel. Checked at EVERY step, on realized inventory
    and on everything still working."""
    rng = random.Random(seed)
    rig = Rig6(tmp_path, max_live_orders=6)
    bid = rng.randint(3, 95)
    rig.go(t=2.0, bid="0.%02d" % bid, ask="0.%02d" % (bid + 2))
    fills = 0
    for i in range(130):
        for o in list(rig.working()):
            r = rng.random()
            left = (D(str(rig.venue.by_oid[o]["qty"]))
                    - D(rig.venue.cum[o]))
            if r < 0.30:
                rig.venue.fill(o)
                fills += 1
            elif r < 0.40 and left > D("0.01"):
                rig.venue.fill(o, qty=D(rig.venue.cum[o]) + D("0.01"))
                fills += 1
            elif r < 0.55:
                rig.venue.fill_on_cancel.add(o)
        bid = max(2, min(96, bid + rng.choice(
            [-40, -9, -3, -1, 0, 0, 1, 3, 9, 40])))
        at(rig, 4.0 + 2 * i, "0.%02d" % bid, "0.%02d" % (bid + 2))
        assert potential_worst(rig) <= LOSS_CAP, (seed, i, _held(rig),
                                                  _open(rig))
        assert gross(rig) <= SIDE_CAP * 2, (seed, i)
        for side in C.SIDES:
            assert len(working(rig, side, LEG)) <= 1, (seed, i)
    rig.tick(2).go(t=301.0, bid="0.50", ask="0.52")
    assert rig.working() == []
    assert realized_worst(rig) <= LOSS_CAP
    assert all(s["post_only"] and s["action"] == "BUY"
               for s in rig.venue.sent)
    coids = [s["client_order_id"] for s in rig.venue.sent]
    assert len(coids) == len(set(coids))
    assert fills >= 5, "seed %d never filled anything" % seed


def test_the_loss_arithmetic_is_the_settlement_arithmetic(rig, armed, gated):
    """`worst_case_loss_usd` on held inventory equals the worse of the
    executor's own two settlement results, so the bound is on the number
    the ledger will actually report."""
    whipsaw(rig, steps=40)
    up = rig.ex.settlement_arithmetic(MARKET, True)
    dn = rig.ex.settlement_arithmetic(MARKET, False)
    key = [k for k in up if "pnl" in k.lower() or "net" in k.lower()]
    assert key, sorted(up)
    worst = min(D(str(up[key[0]])), D(str(dn[key[0]])))
    assert -worst == realized_worst(rig), (up, dn)


def test_a_maker_fill_pays_no_fee_and_earns_no_rebate_at_our_tier():
    """So cost is price x quantity and nothing else. If the venue ever
    charges makers, this fails and the bound must be re-derived."""
    from src import pmus_fees as F
    for p in ("0.02", "0.50", "0.98"):
        assert F.maker_rebate(D(p), D("1")) == 0


@pytest.mark.parametrize("raw,want", [
    ("0.45", "0.45"), ("99", str(X.STRADDLE_MAX_WINDOW_LOSS_USD_CEILING)),
    ("0", "0"), ("-1", "0"), ("x", "0"), ("", "0"), ("NaN", "0")])
def test_the_loss_ceiling_setting_is_clamped_and_fails_off(monkeypatch, raw,
                                                           want):
    monkeypatch.setenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV, raw)
    assert X.straddle_max_window_loss_usd() == D(want)


def test_worst_case_loss_is_exhaustive_not_greedy():
    """Adding a light-side fill can LOWER the loss, so the worst subset is
    not 'everything fills'. Hand-checked."""
    inv = {C.UP: (D("1"), D("0.90")), C.DOWN: (D("0"), D("0"))}
    could = [(C.DOWN, D("0.05"), D("1")), (C.UP, D("0.90"), D("1"))]
    #: nothing more fills: 0.90. Only DOWN: 0.95 - 1 = -0.05. Only UP: 1.80.
    #: Both: 1.85 - 1 = 0.85.
    assert C.worst_case_loss_usd(inv, could) == D("1.80")
    assert C.worst_case_loss_usd(inv, could[:1]) == D("0.90")
    assert C.worst_case_loss_usd(inv, []) == D("0.90")


def test_the_loss_gate_counts_orders_queued_in_the_same_step(rig, armed,
                                                             two_sided,
                                                             monkeypatch):
    """A LADDER queues several same-side rungs in ONE decision, before any
    of them rests. Each alone is inside the ceiling; the gate must add up
    what this step has already asked for."""
    monkeypatch.setenv(X.STRADDLE_RUNGS_ENV, "0.10,0.20,0.30,0.40")
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.10")
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "1.00")
    monkeypatch.setenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV, "0.05")
    rep = rig.go(t=5.0)
    assert len(rig.venue.sent) >= 2, rep["why"]
    assert len(working(rig, C.UP)) < 4, "every rung was admitted"
    assert potential_worst(rig) <= D("0.05"), _open(rig)


# ============ 6. THE SETTINGS REACH THE CONTROLLER IN PRODUCTION

import os                                               # noqa: E402
import re                                               # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_both_settings_default_off_in_the_dataclass_and_the_executor(rig):
    assert C.Params().straddle_requote_ticks == 0
    assert C.Params().straddle_max_window_loss_usd == 0
    ep = rig.ex.effective_params()
    assert ep.straddle_requote_ticks == 0
    assert ep.straddle_max_window_loss_usd == 0


def test_the_environment_reaches_the_params_the_controller_is_handed(
        rig, two_sided, monkeypatch):
    """`effective_params` is what `step` passes to `decide`. STRADDLE mode
    is required: the straddle readers are only consulted in that branch."""
    monkeypatch.setenv(X.STRADDLE_REQUOTE_TICKS_ENV, "3")
    monkeypatch.setenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV, "0.45")
    ep = rig.ex.effective_params()
    assert ep.straddle_requote_ticks == 3
    assert ep.straddle_max_window_loss_usd == D("0.45")
    #: and a clamped value arrives clamped, not as typed
    monkeypatch.setenv(X.STRADDLE_REQUOTE_TICKS_ENV, "50")
    monkeypatch.setenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV, "50")
    ep = rig.ex.effective_params()
    assert ep.straddle_requote_ticks == X.STRADDLE_REQUOTE_TICKS_CEILING
    assert (ep.straddle_max_window_loss_usd
            == X.STRADDLE_MAX_WINDOW_LOSS_USD_CEILING)


def test_outside_straddle_mode_neither_setting_is_picked_up(rig,
                                                            monkeypatch):
    monkeypatch.setenv(X.STRADDLE_REQUOTE_TICKS_ENV, "3")
    monkeypatch.setenv(X.STRADDLE_MAX_WINDOW_LOSS_USD_ENV, "0.45")
    ep = rig.ex.effective_params()
    assert ep.straddle_requote_ticks == 0
    assert ep.straddle_max_window_loss_usd == 0


def test_the_loss_ceiling_cannot_be_set_looser_than_the_unmatched_ceiling():
    assert (X.STRADDLE_MAX_WINDOW_LOSS_USD_CEILING
            <= X.STRADDLE_MAX_UNMATCHED_USD_CEILING)


def test_the_runner_reports_the_resolved_settings_before_arming():
    """So "is the requote on in production, and at what value?" is read
    from the boot log, from the SAME effective params the controller gets."""
    s = _read("src/l5_btc5m_runner.py")
    i = s.index('emit("L5_BTC5M_EFFECTIVE_CONFIG"')
    j = s.index('emit("L5_BTC5M_ARM"')
    assert i < j
    block = s[i:j]
    for field in ("straddle_requote_ticks", "straddle_max_window_loss_usd",
                  "straddle_book_relative", "straddle_pair_ceiling",
                  "straddle_join_ticks", "clip_usd"):
        assert re.search(r'"%s":\s*\S*\(?\s*_ep\.%s' % (field, field),
                         block), field


def test_the_runner_builds_the_same_executor_class_and_step_these_tests_drive():
    s = _read("src/l5_btc5m_runner.py")
    assert "X.Btc5mExecutor(" in s
    assert re.search(r"\.step\(", s)


def test_the_live_image_ships_every_module_the_new_code_runs_in():
    """The image copies files one by one. A module left out is an
    ImportError at boot, which Railway reports as a crash."""
    df = _read("deploy/l5_btc5m_live/Dockerfile")
    for mod in ("l5_btc5m_controller", "l5_btc5m_execution",
                "l5_btc5m_runner", "l5_straddle_ev",
                "l5_venue_constraints"):
        assert "COPY src/%s.py" % mod in df, mod
    #: and the new code imports nothing the image lacks
    ctl = _read("src/l5_btc5m_controller.py")
    for imp in re.findall(r"^from src import (\w+)", ctl, re.M):
        assert "COPY src/%s.py" % imp in df, imp


def test_a_restart_mid_window_keeps_the_loss_ceiling_and_the_inventory(
        rig, armed, gated):
    """The bound is computed from the LEDGER, so a rebuilt executor is held
    by fills made before it existed."""
    whipsaw(rig, steps=30)
    before = _held(rig)
    assert realized_worst(rig) > 0
    rig.restart()
    assert _held(rig) == before
    for i in range(60):
        side = C.UP if i % 2 else C.DOWN
        for o in working(rig, side, LEG):
            rig.venue.fill(o)
        bid = "0.96" if i % 2 else "0.02"
        at(rig, 70.0 + 2 * i, bid, "%.2f" % (float(bid) + 0.02))
        assert potential_worst(rig) <= LOSS_CAP, i
