"""One requote from the snapshot the cross guard already read.

MEASURED on the live 0015z window of 2026-10-28, armed at +17s:

  +23 -> +29s   DOWN own 0.33 filled 1.85
  +53 -> +74s   UP   own 0.61 filled 1.85   -> pair 0.94, matched in 45s
  +121 -> +140s DOWN cancelled, zero fill
  +140 -> +491s **351 SECONDS DEAD**
  +491 -> +498s UP   own 0.60 filled 1.87   -> stranded, settled -1.1313

The dead interval holds 29 decisions and 23 of them are
`PRESEND_CROSS:PRESEND_WOULD_CROSS:UP`, from 00:17:34 to 00:22:01 -- 267
seconds proposing one price the book had already left. The guard is right
to decline it. What it does not do is reprice, and it says so: "No price,
size, depth, TTL or cutoff is changed; the order is simply not sent." So
the next tick proposed the same price and was declined again.

THE SAFETY ARGUMENT, which is why this may sit AFTER the gate stack rather
than re-entering it. To stop crossing, the own price must FALL: UP crosses
at `own >= ask`, DOWN at `1 - own <= bid` i.e. `own >= 1 - bid`. A strictly
lower own price moves every check already run in the safe direction at
unchanged quantity -- pair cost down, edge up, obligation `1 + up_px` down,
worst-case deficit down, residual untouched. A price that is not strictly
lower is refused, so this can never raise a price, never cross, and never
chase a rising market. It quotes FURTHER BEHIND the new touch.

The coid is derived from `market|side` and carries no price, so a requote
cannot break idempotency or the duplicate suppression.

OFF unless L5_PRESEND_REQUOTE names it. Nothing here opens a socket, uses
a credential or submits an order.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS

MKT = "cpc-btc-updown-15m-2026-10-28-0015z"


class Ex:
    """The five attributes fresh_book_requote touches."""

    fresh_book_requote = LX.FaithfulExecutor.fresh_book_requote
    fresh_book_requote_enabled = staticmethod(
        LX.FaithfulExecutor.fresh_book_requote_enabled)
    FRESH_BOOK_REQUOTE_ENV = LX.FaithfulExecutor.FRESH_BOOK_REQUOTE_ENV

    def __init__(self, up=D(0), dn=D(0), leg_pending=None, tick="0.01"):
        self.transport = type("T", (), {"tick_size": tick})()
        self._probe_leg_pending = leg_pending
        self.store = type("S", (), {
            "position": lambda _s, _m, side: (up if side == "UP" else dn)})()


def book(bid, ask, age="0.05"):
    return {"bid": bid, "ask": ask, "stream_age_s": age}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv(LX.FaithfulExecutor.FRESH_BOOK_REQUOTE_ENV, "YES")


# ------------------------------------------------------- the default is off

def test_it_is_off_unless_named(monkeypatch):
    monkeypatch.delenv(LX.FaithfulExecutor.FRESH_BOOK_REQUOTE_ENV, raising=False)
    assert LX.FaithfulExecutor.fresh_book_requote_enabled() is False
    assert Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, book("0.47", "0.48")
    ) is None


@pytest.mark.parametrize("raw", ["", " ", "0", "no", "off", "maybe", "Y"])
def test_only_an_explicit_yes_enables_it(monkeypatch, raw):
    monkeypatch.setenv(LX.FaithfulExecutor.FRESH_BOOK_REQUOTE_ENV, raw)
    assert LX.FaithfulExecutor.fresh_book_requote_enabled() is False


# ------------------------------------------ the measured 0015z refusal

def test_the_0015z_up_refusal_becomes_a_lower_non_crossing_price():
    """The exact case: UP at own 0.61 against a book gone to 0.47/0.48."""
    out = Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, book("0.47", "0.48"))
    assert out is not None
    assert out["own_px_was"] == "0.61"
    assert out["own_px_now"] == "0.45"        # bid 0.47 less 2 ticks
    assert out["ticks_lower"] == "16"
    assert D(out["own_px_now"]) < D(out["own_px_was"])
    assert TS.leg_crosses("UP", D(out["own_px_now"]),
                          bid=D("0.47"), ask=D("0.48")) is False


def test_the_requote_lowers_the_pair_cost_rather_than_raising_it():
    """The load-bearing economics. Leg one at 0.33 was already filled, so
    the pair the ceiling judges falls from 0.94 to 0.78."""
    was, now = D("0.61"), D("0.45")
    basis = D("0.33")
    assert basis + was == D("0.94")
    assert basis + now == D("0.78")
    assert basis + now < TS.max_pair_cost()
    # and the edge grows
    assert (D(1) - (basis + now)) > (D(1) - (basis + was))


def test_the_obligation_and_deficit_both_fall():
    """1 + up_px is the gate's cash basis; a lower price can only shrink
    what the gates already approved."""
    assert (D(1) + D("0.45")) < (D(1) + D("0.61"))


# -------------------------------------- it can never raise or chase

def test_a_price_that_is_not_strictly_lower_is_refused():
    """A rising market would need the price RAISED to stop crossing, and
    that is exactly what must never happen."""
    assert Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.20")}, {}, book("0.47", "0.48")
    ) is None


def test_an_equal_price_is_refused_too():
    assert Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.45")}, {}, book("0.47", "0.48")
    ) is None


def test_the_result_never_crosses_on_either_side():
    for side, bid, ask, old in (("UP", "0.47", "0.48", "0.61"),
                                ("DOWN", "0.20", "0.21", "0.95")):
        out = Ex().fresh_book_requote(
            MKT, {"side": side, "price": D(old)}, {}, book(bid, ask))
        if out is None:
            continue
        assert TS.leg_crosses(side, D(out["own_px_now"]),
                              bid=D(bid), ask=D(ask)) is False


def test_a_down_leg_is_quoted_from_one_minus_the_ask():
    out = Ex().fresh_book_requote(
        MKT, {"side": "DOWN", "price": D("0.95")}, {}, book("0.20", "0.21"))
    assert out is not None
    # DOWN own touch is 1 - ask = 0.79, less 2 ticks
    assert out["own_px_now"] == "0.77"
    assert out["wire_px_now"] == "0.23"


# ----------------------------------------- the floors and grids still bind

def test_an_opening_requote_may_not_walk_under_the_first_leg_floor():
    """0.20 exists to stop cheap legs stranding; a requote must not slip
    beneath it."""
    out = Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, book("0.18", "0.19"))
    assert out is None                        # would be 0.16, under 0.20


def test_a_completion_is_exempt_from_that_floor():
    """Same reason first_leg_entry_refusal exempts it: refusing the leg
    that matches held inventory can only strand it."""
    ex = Ex(up=D("1.85"), dn=D(0), leg_pending="DOWN")
    out = ex.fresh_book_requote(
        MKT, {"side": "DOWN", "price": D("0.95")}, {}, book("0.80", "0.81"))
    assert out is not None
    assert out["completing"] is True
    assert D(out["own_px_now"]) < D("0.20")


def test_a_flat_book_is_an_opening_not_a_completion():
    ex = Ex(up=D("1.85"), dn=D("1.85"), leg_pending="UP")
    out = ex.fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, book("0.18", "0.19"))
    assert out is None                        # floor binds: not completing


def test_the_price_stays_on_the_venue_grid_and_in_band():
    out = Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, book("0.47", "0.48"))
    tick = D("0.01")
    px = D(out["own_px_now"])
    assert (px / tick) == (px / tick).to_integral_value()
    assert TS.MIN_PRICE <= px <= TS.MAX_PRICE


# ---------------------------------------------------- it fails CLOSED

@pytest.mark.parametrize("bk", [
    {}, {"bid": None, "ask": "0.48"}, {"bid": "0.47", "ask": None},
    {"bid": "0", "ask": "0.48"}, {"bid": "0.48", "ask": "0.47"},
    {"bid": "0.47", "ask": "0.47"}, {"bid": "junk", "ask": "0.48"},
])
def test_an_unusable_book_refuses_rather_than_sending(bk):
    """The guard before it fails OPEN because declining is free. This one
    SENDS, so anything it cannot establish must refuse."""
    assert Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, bk) is None


@pytest.mark.parametrize("bad", [None, "", "junk", "nan"])
def test_an_unreadable_old_price_refuses(bad):
    assert Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": bad}, {}, book("0.47", "0.48")) is None


@pytest.mark.parametrize("tick", [None, "0", "-0.01", "junk"])
def test_an_unreadable_tick_refuses(tick):
    assert Ex(tick=tick).fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, book("0.47", "0.48")
    ) is None


def test_an_unknown_side_refuses():
    for side in (None, "", "SIDEWAYS"):
        assert Ex().fresh_book_requote(
            MKT, {"side": side, "price": D("0.61")}, {}, book("0.47", "0.48")
        ) is None


def test_a_raising_store_refuses_rather_than_sending():
    """Only reachable with a leg pending: with none, `completing` short
    circuits on the flag and the ledger is never consulted at all."""
    ex = Ex(leg_pending="UP")

    def boom(_m, _s):
        raise RuntimeError("ledger unreadable")

    ex.store = type("S", (), {"position": lambda _s, m, s: boom(m, s)})()
    assert ex.fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, book("0.47", "0.48")
    ) is None


def test_with_no_leg_pending_the_ledger_is_never_read():
    """The flag short circuits, so an opening outside the two-sided path
    does not touch the store -- and still faces the opening floor."""
    ex = Ex()

    def boom(_m, _s):
        raise RuntimeError("must not be called")

    ex.store = type("S", (), {"position": lambda _s, m, s: boom(m, s)})()
    out = ex.fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, book("0.47", "0.48"))
    assert out is not None and out["completing"] is False


def test_the_helper_does_not_depend_on_a_name_from_another_module():
    """`_d` lives in l5_two_sided_probe, and a NameError here would be
    swallowed by the catch -- making the requote silently inert while
    every test that only asserts None still passed."""
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor.fresh_book_requote)
    assert "_d(" not in src
    assert "def _num(" in src


# ------------------------- the send boundary CHANGES NOTHING any more

def test_the_send_boundary_refuses_and_does_not_reprice():
    """Requirement: do not modify an already-approved money intent. The
    cross guard either lets the gated order through untouched or refuses."""
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor)
    blk = src[src.index("_xr = self.presend_cross_refusal"):]
    blk = blk[:blk.index("_dr = self.disarmed_refusal()")]
    assert 'return prov.refuse("PRESEND_CROSS"' in blk
    # nothing in that block touches a money field
    # the CALL, not the word -- the comment cross-references the helper
    for forbidden in ("self.fresh_book_requote(", "dict(tr, price=",
                      "dict(intent, price=", "gated_money = dict("):
        assert forbidden not in blk, forbidden
    # and the disarmed check still follows, so a disarmed transport cannot send
    assert "disarmed_refusal" in src


# ============================== THE AGE GATE, which the guard does not have
#
# `presend_cross_refusal` fails OPEN on every unknown, deliberately: it only
# ever declines an order, and a budget-saving guard that halts trading on a
# mirror hiccup would be a worse change than the waste it removes. The
# requote SENDS one, so on the same unknowns it must fail the other way.
# Without these the gate could be deleted and every test above would still
# pass -- the helper supplies a fresh age.

def test_a_stalled_mirror_refuses_rather_than_repricing():
    out = Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {},
        book("0.47", "0.48", age=str(TS.MAX_BOOK_AGE_S + 0.1)))
    assert out is None


def test_a_book_with_no_age_at_all_refuses():
    """`ts_authenticated_book` returns {} on every failure, and {} has no
    age. That must reach the original refusal, not a repriced order."""
    out = Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {},
        {"bid": "0.47", "ask": "0.48"})
    assert out is None


def test_an_empty_book_refuses():
    assert Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, {}) is None


@pytest.mark.parametrize("age", ["", "   ", "abc", "NaN", "Infinity", None])
def test_an_unreadable_age_refuses(age):
    out = Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {},
        book("0.47", "0.48", age=age))
    assert out is None, age


def test_an_age_exactly_at_the_bound_is_still_fresh():
    """`age > MAX` and not `>=`, the same test `build_orders` applies, so one
    definition of 'fresh' governs the planner and the requote alike."""
    out = Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {},
        book("0.47", "0.48", age=str(TS.MAX_BOOK_AGE_S)))
    assert out is not None


def test_the_record_states_the_bound_it_enforced():
    out = Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {}, book("0.47", "0.48"))
    assert out["stream_age_s"] == "0.05"
    assert out["max_book_age_s"] == str(TS.MAX_BOOK_AGE_S)


def test_the_gate_uses_the_shared_constant_and_not_a_local_copy():
    """A second definition of 'fresh' is how the planner and the send path
    drift apart. Tightening the shared constant must tighten this too."""
    tight = 0.001
    saved = TS.MAX_BOOK_AGE_S
    try:
        TS.MAX_BOOK_AGE_S = tight
        assert Ex().fresh_book_requote(
            MKT, {"side": "UP", "price": D("0.61")}, {},
            book("0.47", "0.48", age="0.05")) is None
    finally:
        TS.MAX_BOOK_AGE_S = saved


# ==================== REPRICING HAPPENS BEFORE THE GATES, NOT AFTER
#
# An earlier version repriced at the send boundary. `handle` captures
# `gated_money` where the gates run and compares it byte-for-byte with the
# intent reaching the wire; editing the price after that refuses the order
# AND calls `store.mark_uncertain`, which the arming and submission gates
# then refuse on. Enabling it would have halted the engine on the measured
# 0015z window rather than repriced one order.
#
# These pin the ordering that makes the exception unnecessary.

def _handle_src():
    import inspect
    return inspect.getsource(LX.FaithfulExecutor.handle)


def test_the_reprice_runs_before_the_gated_money_capture():
    src = _handle_src()
    i_rq = src.index('tr = dict(tr, price=D(_fb["own_px_now"]))')
    i_cap = src.index('gated_money = {"market"')
    assert i_rq < i_cap


def test_the_reprice_runs_before_every_economic_gate():
    """maker_only, the imbalance suppressor, the validation gates and the
    residual cap must all judge the repriced price."""
    src = _handle_src()
    i_rq = src.index('tr = dict(tr, price=D(_fb["own_px_now"]))')
    for gate in ('if self.maker_only and',
                 'IMBALANCE_INCREASE_SUPPRESSED',
                 'VALIDATION_GATES',
                 'RESIDUAL_ABOVE_CAP'):
        assert i_rq < src.index(gate), gate


def test_no_money_field_is_edited_after_the_gated_capture():
    """The integrity boundary keeps its teeth: no exception, no update."""
    src = _handle_src()
    tail = src[src.index('gated_money = {"market"'):]
    assert "gated_money = dict(" not in tail
    assert 'dict(tr, price=' not in tail
    assert 'dict(intent, price=' not in tail


def test_the_integrity_check_is_unconditional():
    src = _handle_src()
    assert "if sent_money != gated_money:" in src
    assert "INTENT_MUTATED_AFTER_GATES" in src
    assert "REQUOTE_NOT_BELOW_GATED_PRICE" not in src


def test_the_post_gate_exception_is_not_registered_as_a_refusal():
    from src import l5_intent_gate as IG
    assert "SAFETY_OVERLAY:REQUOTE_NOT_BELOW_GATED_PRICE" not in \
        IG.EXECUTOR_GATES_NOT_EVALUATED_HERE


def test_the_reprice_is_gated_on_maker_mode_at_the_call_site():
    """A taker intent is never repriced, so maker-only discipline holds by
    construction rather than by the helper's own checks alone."""
    src = _handle_src()
    i = src.index("if self.fresh_book_requote_enabled()")
    head = src[i:i + 200]
    assert "FT.MAKER" in head


def test_the_reprice_passes_the_decisions_side_not_a_stale_one():
    src = _handle_src()
    i = src.index("self.fresh_book_requote(")
    assert 'dec.get("side")' in src[i:i + 200]


def test_the_helper_accepts_the_authenticated_book_shape():
    """`authenticated` carries bid, ask and stream_age_s under exactly the
    keys the helper reads."""
    out = Ex().fresh_book_requote(
        MKT, {"side": "UP", "price": D("0.61")}, {},
        {"bid": "0.4700", "ask": "0.4800", "stream_age_s": 0.05,
         "bid_shares": "321.00", "ask_shares": "5.01"})
    assert out is not None
    assert out["own_px_now"] == "0.4500"
