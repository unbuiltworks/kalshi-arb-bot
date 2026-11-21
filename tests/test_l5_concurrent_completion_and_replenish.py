"""Simultaneous maker orders, inventory-aware completion, bounded replenishment.

THE THREE CLAIMS UNDER TEST, and they are separate claims.

(1) A COMPLETION MAY SHARE THE BOOK WITH AN UNRELATED OPENING. The old guard
    was `if resting:` on `still_fillable_order_count`, which is ACCOUNT-WIDE,
    so a resting UP opening refused the DOWN completion of UP inventory we
    already held -- the one order that reduces exposure. The slot ceiling is
    now the executor's own `max_live_orders()`, and with that unset it is 1,
    so `resting >= 1` IS `if resting:` and nothing changes.

(2) THE HEDGE SIDE IS RESERVED. `need` was `held - deficient` from POSITIONS
    only, so a DOWN order already working counted for nothing and a second
    full DOWN order could be sized on top of it. Nothing merges them, so both
    could fill and leave double the hedge -- a fresh directional position on
    the side we were flattening. Reserved quantity is now subtracted, and an
    unreadable size is charged at the residual ceiling rather than at zero.

(3) REPLENISHMENT IS EVENT-DRIVEN AND BOUNDED. `maybe_controller_open` has no
    cadence: it re-opens on every tick whenever a slot is free, which is
    neither event-driven nor bounded. A confirmed fill or a terminal
    cancellation now issues one credit, one opening attempt spends one, and a
    window may spend at most `RPL.max_rounds()`.

WHAT IS NOT CLAIMED ANYWHERE IN THIS FILE. Not that any of this is more
profitable. More fills is not more profit, and a TERMINAL_CANCEL credit exists
precisely because nothing filled. These tests assert BEHAVIOUR ONLY.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_live_execution as LX
from src import l5_dual_quote as DQ
from src import l5_pmus_live_runner as R
from src import l5_replenish as RPL


# =====================================================================
# fixtures
# =====================================================================
class _Store:
    """The ledger surface the completion path reads, and nothing else."""

    def __init__(self, up="2", dn="0", resting=0, legs=None, live=None,
                 raise_live=False):
        self._up, self._dn = D(up), D(dn)
        self._resting = resting
        self._legs = legs or {"UP": D("0.40"), "DOWN": D("0")}
        self._live = live or {}
        self._raise_live = raise_live

    def position(self, market, side):
        return self._up if side == "UP" else self._dn

    def still_fillable_order_count(self):
        return self._resting

    def leg_cost(self, market):
        return dict(self._legs)

    def live_orders(self):
        if self._raise_live:
            raise RuntimeError("ledger read failed")
        return dict(self._live)

    # THE STORE'S OWN CONVENTION, borrowed rather than restated, so this
    # fixture cannot drift from the production reading of a row's size.
    worst_case_remaining = staticmethod(LX.InventoryStore.worst_case_remaining)


class _Book:
    def __init__(self, snap):
        self._snap = snap

    def snapshot(self, slug):
        return dict(self._snap)


class _T:
    market_slug = "cpc-btc-updown-15m-2026-10-29-0200z"
    tick_size = "0.01"
    quantity_increment = "0.01"


WS = 1_793_000_000.0
NOW = 1_793_000_120.0


def _runner(monkeypatch, *, store=None, snap=None):
    """A Runner whose executor RECORDS instead of submitting."""
    r = R.Runner.__new__(R.Runner)
    r.transport = _T()
    r.store = store or _Store()
    r.book = _Book(snap or {"bid": "0.79", "ask": "0.80",
                            "stream_age_s": "0.1",
                            "bid_shares": "500", "ask_shares": "500"})
    _vg = type("VG", (), {"max_worst_case_deficit_usd": D("3")})()
    controller_calls = []
    r.executor = type("E", (), {
        "validation_gates": _vg,
        "handle": lambda self, row, book, **kw: controller_calls.append(
            (row, dict(kw))) or {"ok": True},
    })()
    r.controller_calls = controller_calls
    r.stats = collections.Counter()
    r._book_completion_t = None
    r.now_t = NOW
    r._last_action_row = {}
    calls = []
    r.handle = lambda row, now: calls.append((dict(row), now))
    monkeypatch.setattr(R.AB, "slug_start", lambda s: WS)
    monkeypatch.setattr(R, "emit", lambda *a, **k: None)
    monkeypatch.setattr(R.AUTH, "max_submissions", lambda: 72)
    return r, calls


def _order(side, price, remaining, market=_T.market_slug):
    return {"market": market, "side": side, "price": price,
            "remaining": remaining}


# =====================================================================
# (1) CONCURRENT OPENINGS: an unrelated resting order no longer blocks
# =====================================================================
def test_with_one_slot_the_old_behaviour_is_exact(monkeypatch):
    """`L5_MAX_LIVE_ORDERS` unset means one slot, which IS `if resting:`.

    This is the parity anchor. Everything permissive below depends on the
    operator having explicitly set a second slot.
    """
    monkeypatch.delenv("L5_MAX_LIVE_ORDERS", raising=False)
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch, store=_Store(
        up="2", dn="0", resting=1,
        live={"o1": _order("UP", "0.40", "1")}))
    out = r.maybe_book_completion(NOW)
    assert out["ok"] is False
    assert out["reason"] == "ORDER_ALREADY_RESTING"
    assert calls == [] and r.controller_calls == []


def test_an_unrelated_resting_opening_no_longer_blocks_completion(monkeypatch):
    """TWO SLOTS, a resting UP opening, and UP inventory to complete.

    This is the behaviour the change exists for: the completion is the order
    that REDUCES exposure, and it was being refused by the presence of the
    opening that created the exposure.
    """
    monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "2")
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch, store=_Store(
        up="2", dn="0", resting=1,
        live={"o1": _order("UP", "0.40", "1")}))
    out = r.maybe_book_completion(NOW)
    assert out["ok"] is True, out
    assert len(r.controller_calls) == 1
    assert r.stats["book_completion:ORDER_ALREADY_RESTING"] == 0


def test_the_slot_ceiling_is_the_executors_own_not_a_hardcoded_one(
        monkeypatch):
    """At the ceiling it still refuses, and the record names the ceiling."""
    monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "2")
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch, store=_Store(
        up="2", dn="0", resting=2,
        live={"o1": _order("UP", "0.40", "1"),
              "o2": _order("UP", "0.41", "1")}))
    out = r.maybe_book_completion(NOW)
    assert out["reason"] == "ORDER_ALREADY_RESTING"
    assert out["max_live_orders"] == 2
    assert out["still_fillable_orders"] == 2
    assert r.controller_calls == []


def test_a_third_slot_is_never_granted_by_this_path(monkeypatch):
    """`max_live_orders` is the only source of the number, so an operator
    raising it is the ONLY way more orders rest -- this path invents none."""
    monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "2")
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, _ = _runner(monkeypatch, store=_Store(
        up="2", dn="0", resting=5,
        live={"o%d" % i: _order("UP", "0.40", "1") for i in range(5)}))
    out = r.maybe_book_completion(NOW)
    assert out["reason"] == "ORDER_ALREADY_RESTING"
    assert r.controller_calls == []


# =====================================================================
# (2) DUPLICATE HEDGE PREVENTION
# =====================================================================
def test_a_resting_hedge_side_order_refuses_the_completion(monkeypatch):
    """The duplicate hedge: a DOWN order is already working on DOWN."""
    monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "2")
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch, store=_Store(
        up="2", dn="0", resting=1,
        live={"o1": _order("DOWN", "0.21", "2")}))
    out = r.maybe_book_completion(NOW)
    assert out["ok"] is False
    # Either the reservation closed the need upstream in `decide`, or the
    # runner's own per-market hedge guard refused it. Both are correct and
    # both are duplicate-hedge prevention; what must NEVER happen is an
    # order.
    assert out["reason"] in ("HEDGE_ALREADY_RESTING", "ALREADY_RESERVED",
                            "ALREADY_RESTING_AT_OR_BETTER")
    assert calls == [] and r.controller_calls == []


def test_the_hedge_guard_fires_even_when_the_reservation_does_not(
        monkeypatch):
    """A hedge order too SMALL to close the need is still not duplicated.

    Holding 3 UP with a DOWN order for 1 leaves `need` at 2, so the
    reservation alone would permit a second DOWN order. The per-market hedge
    guard is what refuses it, and this is the case that proves the guard is
    load-bearing rather than redundant.
    """
    monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "2")
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch, store=_Store(
        up="3", dn="0", resting=1,
        live={"o1": _order("DOWN", "0.21", "1")}))
    out = r.maybe_book_completion(NOW)
    assert out["ok"] is False
    assert out["reason"] == "HEDGE_ALREADY_RESTING"
    assert out["hedge_side"] == "DOWN"
    assert out["hedge_orders_resting"] == 1
    assert out["reserved_contracts"] == "1"
    assert calls == [] and r.controller_calls == []


def test_the_hedge_guard_is_per_market_not_account_wide(monkeypatch):
    """A DOWN order in ANOTHER market is not this market's hedge.

    This is the scope distinction the old guard collapsed:
    `still_fillable_order_count` is account-wide and cannot tell a duplicate
    hedge from an unrelated order somewhere else.
    """
    monkeypatch.setenv("L5_MAX_LIVE_ORDERS", "2")
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, _ = _runner(monkeypatch, store=_Store(
        up="2", dn="0", resting=1,
        live={"o1": _order("DOWN", "0.21", "2", market="some-other-market")}))
    out = r.maybe_book_completion(NOW)
    assert out["ok"] is True, out
    assert len(r.controller_calls) == 1


def test_the_hedge_guard_is_checked_before_the_slot_ceiling():
    """Order matters: with slots full AND a resting hedge, the record must
    name the hedge, which is the more specific and more serious fault."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_book_completion)
    assert src.index("HEDGE_ALREADY_RESTING") < \
        src.index('self.stats["book_completion:ORDER_ALREADY_RESTING"]')


# =====================================================================
# (2b) THE RESERVATION ITSELF, asserted directly on `decide`
# =====================================================================
_BASE = dict(market="m", held_side="UP", held_cost_usd="1.20",
             deficient_cost_usd="0",
             book={"bid": "0.79", "ask": "0.80", "stream_age_s": "0.1",
                   "bid_shares": "500", "ask_shares": "500"},
             now_t=NOW, window_start=WS)


def _decide(**kw):
    return BC.decide(**dict(_BASE, **kw))


def test_a_fully_reserved_need_is_named_apart_from_a_matched_one():
    """ALREADY_MATCHED is a finished pair; ALREADY_RESERVED is one in flight.

    A reader who cannot tell them apart cannot tell a completed round from a
    round waiting on a fill, which is the difference between inventory that
    is safe and inventory that is not yet.
    """
    assert _decide(held_qty="3", deficient_qty="3").get("reason") \
        == "ALREADY_MATCHED"
    r = _decide(held_qty="3", deficient_qty="0", outstanding_qty="3")
    assert r["reason"] == "ALREADY_RESERVED"
    assert r["reserved"] == "3"
    assert r.get("ok") is not True


def test_a_partial_fill_reserves_only_the_still_working_remainder():
    """Both halves of a partial are spoken for, and only their sum is.

    Holding 3 UP with a DOWN order that has filled 1 and has 1 working
    leaves exactly 1 to complete -- not 2 (ignoring the working part) and
    not 3 (ignoring both).
    """
    r = _decide(held_qty="3", deficient_qty="1", outstanding_qty="1")
    assert r["need"] == "1"
    assert r["reserved"] == "1"


def test_the_reservation_can_only_ever_shrink_the_need():
    """A property, over a grid: there is no input that enlarges an order."""
    for held in ("1", "2", "3", "5"):
        for dq in ("0", "1", "2"):
            base = _decide(held_qty=held, deficient_qty=dq)
            if base.get("need") is None:
                continue
            for res in ("0", "1", "2", "3", "5"):
                got = _decide(held_qty=held, deficient_qty=dq,
                              outstanding_qty=res)
                if got.get("need") is None:
                    continue           # refused outright, which is smaller
                assert D(got["need"]) <= D(base["need"]), (held, dq, res)


def test_an_absent_reservation_is_exactly_todays_behaviour():
    """Parity: every existing caller passes nothing and must be unaffected."""
    a = _decide(held_qty="3", deficient_qty="1")
    b = _decide(held_qty="3", deficient_qty="1", outstanding_qty=None)
    c = _decide(held_qty="3", deficient_qty="1", outstanding_qty="0")
    assert a.get("need") == b.get("need") == c.get("need") == "2"
    assert a.get("reason") == b.get("reason") == c.get("reason")


@pytest.mark.parametrize("bad", ["abc", "", float("nan"), float("inf"),
                                 "NaN", "-Infinity", object()])
def test_an_unreadable_reservation_refuses_rather_than_reserving_zero(bad):
    """FAIL CLOSED. "We cannot read how much is working" must never be read
    as "nothing is working" -- that is the one reading that duplicates."""
    r = _decide(held_qty="3", deficient_qty="0", outstanding_qty=bad)
    assert r["reason"] == "OUTSTANDING_UNREADABLE"
    assert r.get("ok") is not True


def test_a_nan_reservation_refuses_instead_of_raising():
    """`Decimal("NaN") < 0` RAISES InvalidOperation. A completion path that
    raises takes the tick down instead of declining it."""
    r = _decide(held_qty="3", deficient_qty="0",
                outstanding_qty=D("NaN"))
    assert r["reason"] == "OUTSTANDING_UNREADABLE"


def test_a_negative_reservation_is_clamped_not_trusted():
    """A negative outstanding is nonsense and must not ENLARGE the need."""
    r = _decide(held_qty="3", deficient_qty="0", outstanding_qty="-5")
    assert r["need"] == "3"
    assert r["reserved"] == "0"


# =====================================================================
# (2c) WHERE THE RESERVATION COMES FROM: row sizing, failing closed
# =====================================================================
def test_resting_rows_carry_a_size_and_it_is_the_stores_own_reading():
    store = _Store(live={
        "a": _order("DOWN", "0.40", "2"),
        "b": _order("UP", "0.55", "1"),
    })
    rows = R._resting_rows(store, _T.market_slug)
    assert {r["side"]: r["remaining"] for r in rows} == {
        "DOWN": D("2"), "UP": D("1")}


def test_an_unreadable_row_size_is_charged_at_the_residual_ceiling():
    """`worst_case_remaining`'s convention, not a new one: unreadable is not
    zero. A row we cannot size makes the reservation LARGER."""
    store = _Store(live={"a": _order("DOWN", "0.40", None)})
    rows = R._resting_rows(store, _T.market_slug)
    assert rows[0]["remaining"] == LX.ABS_RESIDUAL_CAP_CONTRACTS
    assert R._outstanding_on_side(rows, "DOWN") == \
        LX.ABS_RESIDUAL_CAP_CONTRACTS


def test_an_unsizable_row_is_also_charged_at_the_ceiling():
    """A row whose `remaining` is actively hostile, not merely absent."""
    rows = ({"side": "DOWN", "price": "0.40", "remaining": "not-a-number"},)
    assert R._outstanding_on_side(rows, "DOWN") == \
        LX.ABS_RESIDUAL_CAP_CONTRACTS


def test_outstanding_sums_only_the_side_asked_for():
    rows = (
        {"side": "DOWN", "remaining": D("2")},
        {"side": "down", "remaining": D("1")},        # case-insensitive
        {"side": "UP", "remaining": D("7")},
    )
    assert R._outstanding_on_side(rows, "DOWN") == D("3")
    assert R._outstanding_on_side(rows, "UP") == D("7")
    assert R._outstanding_on_side(rows, "SIDEWAYS") == D("0")


def test_outstanding_is_never_negative_and_ignores_dead_rows():
    rows = ({"side": "DOWN", "remaining": D("-4")},
            {"side": "DOWN", "remaining": D("0")},
            {"side": "DOWN", "remaining": D("2")})
    assert R._outstanding_on_side(rows, "DOWN") == D("2")


# =====================================================================
# (2d) STALE AND UNREADABLE LEDGER READS
# =====================================================================
def test_an_unreadable_ledger_is_none_and_not_an_empty_book():
    """UNREADABLE IS NOT EMPTY, and conflating them is a fail-OPEN.

    This returned `()` for both cases, on the argument that the separately
    read account-wide count was what refused. That held only at ONE slot:
    with `L5_MAX_LIVE_ORDERS=2` a count of 1 does not refuse, so an
    unreadable ledger presented as "no rows" would clear the per-market
    hedge guard and permit a completion on top of a hedge already working.
    """
    assert R._resting_rows(_Store(raise_live=True), _T.market_slug) is None
    assert R._resting_rows(_Store(live={}), _T.market_slug) == ()


def test_an_unreadable_row_set_reserves_the_ceiling_not_zero():
    assert R._outstanding_on_side(None, "DOWN") == \
        LX.ABS_RESIDUAL_CAP_CONTRACTS


@pytest.mark.parametrize("slots", ["1", "2"])
def test_an_unreadable_ledger_cannot_open_the_completion_path(
        monkeypatch, slots):
    """FAIL CLOSED AT EVERY SLOT COUNT. Parametrised deliberately: the
    one-slot case passed before this change only because the account-wide
    count happened to bind, which is not the same as the path refusing."""
    monkeypatch.setenv("L5_MAX_LIVE_ORDERS", slots)
    monkeypatch.setenv(BC.ENABLED_ENV, "1")
    r, calls = _runner(monkeypatch,
                       store=_Store(up="2", dn="0", resting=1,
                                    raise_live=True))
    out = r.maybe_book_completion(NOW)
    assert out is None, "an unreadable ledger decides nothing at all"
    assert calls == [] and r.controller_calls == []
    assert r.stats["book_completion:LEDGER_UNREADABLE:resting_rows"] == 1


def test_a_store_without_the_sizing_helper_charges_the_ceiling():
    """The lighter harnesses pass stores that are not InventoryStores. The
    fallback must be the most reluctant reading available, not zero."""
    class _Thin:
        def live_orders(self):
            return {"a": _order("DOWN", "0.40", "2")}
    rows = R._resting_rows(_Thin(), _T.market_slug)
    assert rows[0]["remaining"] == LX.ABS_RESIDUAL_CAP_CONTRACTS


# =====================================================================
# (3) BOUNDED, EVENT-DRIVEN REPLENISHMENT
# =====================================================================
def test_max_rounds_clamps_downward_only(monkeypatch):
    monkeypatch.delenv(RPL.ROUNDS_ENV, raising=False)
    assert RPL.max_rounds() == RPL.MAX_ROUNDS_CEILING
    monkeypatch.setenv(RPL.ROUNDS_ENV, "99")
    assert RPL.max_rounds() == RPL.MAX_ROUNDS_CEILING
    monkeypatch.setenv(RPL.ROUNDS_ENV, "2")
    assert RPL.max_rounds() == 2
    monkeypatch.setenv(RPL.ROUNDS_ENV, "0")
    assert RPL.max_rounds() == 0, "zero means no replenishment, and is honoured"
    monkeypatch.setenv(RPL.ROUNDS_ENV, "-3")
    assert RPL.max_rounds() == 0
    monkeypatch.setenv(RPL.ROUNDS_ENV, "garbage")
    assert RPL.max_rounds() == RPL.MAX_ROUNDS_CEILING


def test_the_first_opening_of_a_window_needs_no_credit():
    """There has been no fill and no cancellation yet, so requiring one would
    mean never opening at all."""
    led = RPL.Ledger()
    assert led.may_open(WS) is True
    assert led.spend(WS) is False, "the free first opening consumes nothing"


def test_a_second_opening_with_no_event_behind_it_is_refused():
    led = RPL.Ledger()
    led.spend(WS)
    assert led.may_open(WS) is False
    assert led.refuse_reason() == "NO_REPLENISH_EVENT_YET"


def test_a_confirmed_fill_issues_exactly_one_credit():
    led = RPL.Ledger()
    led.spend(WS)
    assert led.note_progress({"delta": "1", "terminal": False},
                             window_start=WS) is True
    assert led.may_open() is True
    assert led.spend() is True
    assert led.may_open() is False
    assert led.refuse_reason() == "REPLENISH_CREDITS_SPENT"


def test_a_terminal_cancellation_issues_a_credit_too():
    """A slot freed by a cancellation is a freed slot. The credit records
    that nothing filled -- it is not evidence that anything did."""
    led = RPL.Ledger()
    led.spend(WS)
    assert led.note_progress({"delta": "0", "terminal": True},
                             window_start=WS) is True
    assert led.telemetry()["events"][-1]["kind"] == RPL.TERMINAL_CANCEL
    assert led.may_open() is True


def test_a_zero_delta_progress_poll_issues_nothing():
    """A poll that saw nothing new is not a fill and frees no slot."""
    led = RPL.Ledger()
    led.spend(WS)
    assert led.note_progress({"delta": "0", "terminal": False},
                             window_start=WS) is False
    assert led.may_open() is False


def test_a_terminal_row_that_did_book_credits_once_as_a_fill():
    led = RPL.Ledger()
    led.spend(WS)
    assert led.note_progress({"delta": "2", "terminal": True},
                             window_start=WS) is True
    t = led.telemetry()
    assert t["credits_issued"] == 1, "one freed slot is one credit"
    assert t["events"][-1]["kind"] == RPL.FILL


def test_credits_are_bounded_by_max_rounds(monkeypatch):
    monkeypatch.setenv(RPL.ROUNDS_ENV, "2")
    led = RPL.Ledger()
    led.spend(WS)
    for _ in range(10):
        led.note_progress({"delta": "1", "terminal": False}, window_start=WS)
    assert led.telemetry()["credits_issued"] == 2
    assert led.spend() is True
    assert led.spend() is True
    assert led.may_open() is False
    assert led.refuse_reason() == "REPLENISH_ROUNDS_EXHAUSTED"


def test_an_unknown_event_kind_issues_nothing():
    """A future event type must not silently widen replenishment."""
    led = RPL.Ledger()
    led.spend(WS)
    assert led.note("SOMETHING_NEW", window_start=WS) is False
    assert led.may_open() is False
    assert led.telemetry()["events"][-1]["reason"] == "UNKNOWN_KIND"


def test_a_window_rollover_discards_the_previous_windows_credits():
    led = RPL.Ledger()
    led.spend(WS)
    led.note_progress({"delta": "1", "terminal": False}, window_start=WS)
    assert led.may_open(WS) is True
    assert led.roll(WS + 900.0) is True
    t = led.telemetry()
    assert t["credits_issued"] == 0 and t["credits_spent"] == 0
    assert t["openings_attempted"] == 0
    assert led.may_open(WS + 900.0) is True, "a new window opens freely once"


def test_an_unreadable_window_start_does_not_refresh_the_allowance():
    """A reading we cannot trust must not hand the current window a fresh
    allowance -- that would be a way to mint credits by corrupting a slug."""
    led = RPL.Ledger()
    led.roll(WS)
    led.spend(WS)
    for bad in (None, "not-a-number", float("nan")):
        assert led.roll(bad) is False
    assert led.telemetry()["openings_attempted"] == 1
    assert led.may_open() is False


def test_a_restart_begins_with_no_credits():
    """RESTART RECOVERY. The ledger is process-local on purpose: a crash
    mid-window must not inherit an allowance it cannot account for. A fresh
    Ledger gets the one free opening and nothing else."""
    fresh = RPL.Ledger()
    assert fresh.telemetry()["credits_issued"] == 0
    assert fresh.may_open(WS) is True
    fresh.spend(WS)
    assert fresh.may_open() is False
    assert fresh.refuse_reason() == "NO_REPLENISH_EVENT_YET"


def test_the_runners_ledger_survives_a_runner_without_init(monkeypatch):
    """Lazily initialised, because several construction paths skip __init__
    and an accessor a lifecycle poll depends on must never raise."""
    r, _ = _runner(monkeypatch)
    assert isinstance(r.replenish(), RPL.Ledger)
    assert r.replenish() is r.replenish(), "one ledger per process"


# ---------------------------------------------------------------- wiring
def test_replenishment_is_not_consulted_when_it_is_off(monkeypatch):
    """PARITY. Production holds L5_DUAL_REPLENISH=OFF, so the gate must not
    be reached at all and the opening path behaves exactly as today."""
    monkeypatch.delenv(DQ.DUAL_ENV, raising=False)
    monkeypatch.delenv(DQ.REPLENISH_ENV, raising=False)
    assert DQ.replenish_enabled(WS) is False
    r, _ = _runner(monkeypatch)
    led = r.replenish()
    led.spend(WS)                       # the ledger WOULD now refuse
    assert led.may_open() is False
    # and the opening path never asks it, so the counter stays clean
    assert r.stats["controller_open:REPLENISH_UNPAID:"
                   "NO_REPLENISH_EVENT_YET"] == 0


def test_the_gate_is_guarded_by_the_dual_quote_switch():
    """Source: the ledger is consulted only inside `replenish_enabled`."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_controller_open)
    i = src.index("DQ.replenish_enabled(ws)")
    j = src.index("REPLENISH_UNPAID")
    assert i < j, "the switch must be tested before the ledger is consulted"


def test_the_credit_is_spent_only_after_a_placement_is_attempted():
    """Source: `spend` sits after the executor call, so a decision refused by
    a later gate does not burn the window's allowance on a nonexistent order.
    """
    import inspect
    src = inspect.getsource(R.Runner.maybe_controller_open)
    assert src.index("self.executor.handle(") < \
        src.index("self.replenish().spend(ws)")


def test_the_lifecycle_hook_credits_from_the_event_stream():
    """Source: credits are issued where confirmed fills and terminal rows are
    already observed, not from this method's own memory of what it did."""
    import inspect
    src = inspect.getsource(R.Runner.lifecycle)
    assert "self.replenish().note_progress(" in src
    assert "replenish_note_error" in src, "inside a swallow"


def test_replenishment_can_only_refuse_never_permit():
    """THE SAFETY CLAIM. The gate is reached only AFTER the slot test, so
    there is no path on which it permits an opening the slot gate refused."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_controller_open)
    assert src.index('controller_open:ORDER_ALREADY_RESTING') < \
        src.index("DQ.replenish_enabled(ws)")


# =====================================================================
# (5) THE CONTROLS THAT MUST SURVIVE ALL OF THIS
# =====================================================================
def test_the_completion_path_still_withdraws_nothing():
    """It places and declines. It has never cancelled, and must not start:
    freeing a slot by withdrawing a good quote is a behaviour change nobody
    authorized."""
    import inspect
    src = inspect.getsource(R.Runner.maybe_book_completion)
    for forbidden in ("cancel_order", "cancel_all", ".cancel("):
        assert forbidden not in src, forbidden


def test_replenishment_reads_no_safety_control():
    """`SAFETY` names what this module must never touch, and the module
    asserts the disjointness at import. This checks the list is real."""
    for k in ("max_live_orders", "ABS_RESIDUAL_CAP_CONTRACTS", "L5_LIVE_HALT",
              "max_total_exposure_usd", "campaign_approval",
              "max_cumulative_loss_usd", "exact_order_cancellation",
              "watchdog_deadman", "reconciliation"):
        assert k in RPL.SAFETY, k
    import inspect
    src = inspect.getsource(RPL)
    body = src.split("SAFETY: Tuple[str, ...] = (", 1)[1].split(")", 1)[1]
    for k in ("os.getenv(\"L5_LIVE_HALT",
              "max_live_orders()", "ABS_RESIDUAL_CAP"):
        assert k not in body, k


def test_the_new_module_is_in_both_images():
    """IMAGE CLOSURE. An unconditional import absent from the image fails
    every suite with ModuleNotFoundError at runtime, not at test time."""
    import io
    for p in ("deploy/l5_pmus_live/Dockerfile",
              "deploy/l5_live_probe/Dockerfile"):
        s = io.open(p, encoding="utf-8").read()
        assert "COPY src/l5_replenish.py src/l5_replenish.py" in s, p
