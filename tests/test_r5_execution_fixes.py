"""P0-1 completion execution mode and P0-2 bounded cancel recovery.

Both fixtures are the real R5 1115z window, from the authoritative audit.
No threshold, theta, fee coefficient, clip or risk bound is changed by
either fix; the frozen criterion is called exactly as frozen defines it.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_book_feed as F
from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E
from src import pmus_native_money_path as MP
from src import pmus_order_lifecycle as LC

THETA = D("0.96")
#: UP basis when the three UP fills (0.50, 0.45, 0.39) were held
UP_BASIS = D("13.4000") / D(30)

#: the arrival book for each order, from native_tick rows
BOOKS = {
    5: {"bid": "0.3900", "ask": "0.4000", "bid_qty": "104", "ask_qty": "2079.74"},
    7: {"bid": "0.4600", "ask": "0.4700", "bid_qty": "1628.63", "ask_qty": "1444"},
    9: {"bid": "0.4800", "ask": "0.4900", "bid_qty": "500", "ask_qty": "20"},
    13: {"bid": "0.5400", "ask": "0.5500", "bid_qty": "343.24", "ask_qty": "24"},
}


def frozen_verdict(book, comp, want, basis):
    """The frozen completion criterion, called exactly as frozen calls it."""
    lv = S.buy_levels(F.touch_ladder(book), comp)
    n, cost, fee, depth_limited = S.marginal_complete(lv, want, basis, THETA)
    return {"n": n, "cost": cost, "fee": fee, "levels": lv,
            "limit": E.worst_admitted_price(lv, want, basis, THETA),
            "depth_limited": depth_limited}


# ---- P0-1: the dead seam that made the certified path unreachable --------

def test_the_feed_supplies_the_ladder_the_engine_reads():
    """THE FOURTH DEAD SEAM.

    `on_book_state` passes `getattr(feed, "ladder", None)` and the engine
    decides completion from it. The feed had no such attribute, so the
    ladder was always None, `buy_levels` always [], `marginal_complete`
    always n=0, and the certified theta-gated taker completion path could
    not fire once in production.
    """
    assert hasattr(F.AuthenticatedBookFeed, "ladder")
    lad = F.touch_ladder(BOOKS[13])
    assert lad == {"bids": [["0.5400", "343.24"]],
                   "offers": [["0.5500", "24"]]}
    assert S.buy_levels(lad, "down") == [["0.4600", "343.24"]]


def test_no_ladder_means_no_completion_which_is_what_r5_did():
    """Proves the seam was the cause, not a coincidence."""
    # the engine guards a None ladder into no levels at all...
    import inspect
    src = inspect.getsource(E.NativeEngine.on_book)
    assert 'S.buy_levels(self.ladder, comp) if self.ladder else []' in src
    # ...and no levels admits no completion, whatever the economics would be
    n, _, _, _ = S.marginal_complete([], D(20), UP_BASIS, THETA)
    assert n == 0


def test_touch_ladder_invents_no_depth_below_the_touch():
    lad = F.touch_ladder(BOOKS[9])
    assert len(lad["bids"]) == 1 and len(lad["offers"]) == 1
    # only the 500 the venue actually showed resting at the bid
    assert S.buy_levels(lad, "down") == [["0.5200", "500"]]


def test_unusable_row_yields_no_ladder_and_decides_nothing():
    for row in (None, {}, {"bid": None, "ask": None}):
        assert F.touch_ladder(row) is None


# ---- P0-1 regression lock, exactly as specified -------------------------

def test_r5_13_is_not_a_completion_at_all_and_stays_maker():
    """CORRECTION, established by replaying the engine's own residual.

    At #13 (11:17:12) the book held UP 30 / DOWN 50 -- net long DOWN -- so
    the complement was UP and #13, another DOWN order, was a SAME-SIDE
    EXTEND. It was never a completion, so the completion criterion does not
    apply to it and it stays MAKER structurally.

    Evaluating it against the held side that actually obtained confirms the
    economics would refuse it anyway: comp=up at the 0.55 ask on a 0.498
    DOWN basis is an all-in of 1.0652, far above theta.
    """
    residual = D(30) - D(50)               # UP 30, DOWN 50
    assert residual < 0
    held = "down"
    assert S.complement(held) == "up"      # so a DOWN order is not the comp
    v = frozen_verdict(BOOKS[13], "up", D(10), D("0.498"))
    assert v["n"] == 0


def test_the_completion_path_does_fire_once_it_has_a_ladder():
    """The seam fix has to be demonstrable, not just argued.

    Same frozen criterion, same R5 book, a basis cheap enough to admit:
    comp=down at own 0.54 on a 0.30 basis is an all-in of 0.8573 <= 0.96, so
    the certified theta walk admits the full 20 and hands back an executable
    worst-admitted limit. Before the ladder fix this returned n=0 for every
    basis, because there were no levels to walk.
    """
    v = frozen_verdict(BOOKS[7], "down", D(20), D("0.30"))
    assert v["n"] == D(20)
    assert v["limit"] == D("0.5400")
    assert v["cost"] == D("10.8000")
    px = D("0.5400")
    assert D("0.30") + px + S.THETA_TAKER * px * (D(1) - px) < THETA


def test_r5_7_does_not_become_a_taker_completion():
    """A GENUINE completion: residual was net long UP 20 at 11:16:47.

    own 0.54 on a 0.44667 UP basis is an all-in of 1.00393 > theta 0.96, so
    it must NOT be crossed merely to reduce residual.
    """
    v = frozen_verdict(BOOKS[7], "down", D(20), UP_BASIS)
    assert v["n"] == 0
    px = D("0.5400")
    assert UP_BASIS + px + S.THETA_TAKER * px * (D(1) - px) > THETA


def test_r5_9_does_not_become_a_taker_completion():
    """A GENUINE completion: residual was net long UP 10 at 11:16:49.

    own 0.52 on a 0.44667 UP basis is an all-in of 0.98401 > theta 0.96. It
    is close, which is exactly why it is regression-locked.
    """
    v = frozen_verdict(BOOKS[9], "down", D(20), UP_BASIS)
    assert v["n"] == 0
    px = D("0.5200")
    assert UP_BASIS + px + S.THETA_TAKER * px * (D(1) - px) > THETA


def test_r5_5_remains_maker_structurally_not_economically():
    """#5 was a same-side UP acquisition while the residual was net long UP.

    The completion branch only ever emits the COMPLEMENT of the held side, so
    an acquisition order on the held side cannot be upgraded at all. That is
    a structural guarantee, stronger than an economic one, and it is what
    preserves passive/PDI execution for normal inventory acquisition.
    """
    residual = D(10)                       # net long UP at 11:16:02
    held = "up" if residual > 0 else "down"
    assert held == "up"
    assert S.complement(held) == "down"    # never "up", so #5 is untouched


def test_the_frozen_constants_are_untouched():
    assert S.THETA_TAKER == D("0.0695")
    assert E.THETA == D("0.96")
    assert E.CLIP == D(50)


# ---- P0-2: bounded recovery for a genuinely live order ------------------

class _Venue:
    """Cancels always fail; status answers whatever the test scripts."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = 0

    def order_status(self, oid, requested_qty=None, side=None):
        self.asked += 1
        a = self.answers[min(self.asked - 1, len(self.answers) - 1)]
        if isinstance(a, BaseException):
            raise a
        return a


def _path(venue, oid="CSPERKXEYYB5", side="up", qty=D(10), price=D("0.46")):
    mp = MP.NativeMoneyPath.__new__(MP.NativeMoneyPath)
    mp.venue = venue
    mp.journal = None
    mp._cancel_failures = {}
    # pass-scoped caches, as production initialises them. TERMINAL verdicts
    # only, because terminal is monotonic and a WORKING verdict must never be
    # served across a cancel attempt.
    mp._resolved_this_pass = set()
    mp._terminal_verdicts = {}
    mp._status_reads = 0
    mp._status_cache_hits = 0
    mp.timing = {}
    mp.registry = MP.SessionOrderRegistry()
    mp.registry.record(oid, side=side, price=price, qty=qty, kind=E.REST)
    mp.release_terminal = lambda *a, **k: None
    return mp


WORKING = {"classification": LC.WORKING, "state": "ORDER_STATE_WORKING",
           "terminal": False, "filled_qty": "0"}
UNREADABLE = {"classification": LC.UNKNOWN, "terminal": False,
              "why": "HTTPError: HTTP Error 404: Not Found"}
EXPIRED = {"classification": LC.CANCELLED_NO_FILL, "terminal": True,
           "state": "ORDER_STATE_EXPIRED", "filled_qty": "0"}
FILLED = {"classification": LC.FILLED, "terminal": True,
          "state": "ORDER_STATE_FILLED", "filled_qty": "10"}


def test_a_known_live_order_blocks_replacement_but_is_not_uncertain():
    """R5's actual case: the cancel fails, the venue says the order works."""
    mp = _path(_Venue([WORKING]))
    v = mp._cancel_failure_verdict("up", "CSPERKXEYYB5", {"state": "CANCEL_FAILED"})
    assert v["clear"] is False              # never submit replacement risk
    assert v["ownership"] == "KNOWN_LIVE"
    assert v["cancel_failures"] == 1
    assert v["escalated"] is False


def test_repeated_cancel_failure_escalates_within_a_bound():
    mp = _path(_Venue([WORKING]))
    seen = [mp._cancel_failure_verdict("up", "CSPERKXEYYB5",
                                       {"state": "CANCEL_FAILED"})
            for _ in range(MP.NativeMoneyPath.CANCEL_RETRY_BUDGET + 1)]
    assert [v["escalated"] for v in seen] == [False, False, False, True]
    # and it never unblocked replacement while the order may be live
    assert all(v["clear"] is False for v in seen)


def test_terminal_established_during_retry_unblocks_immediately():
    """The 13-minute strand: as soon as the venue says terminal, go."""
    mp = _path(_Venue([WORKING, WORKING, EXPIRED]))
    for _ in range(2):
        assert mp._cancel_failure_verdict(
            "up", "CSPERKXEYYB5", {"state": "CANCEL_FAILED"})["clear"] is False
    v = mp._cancel_failure_verdict("up", "CSPERKXEYYB5",
                                  {"state": "CANCEL_FAILED"})
    assert v["clear"] is True
    assert v["reason"] == "TERMINAL_ESTABLISHED_DURING_CANCEL_RETRY"
    assert mp.registry.orders["CSPERKXEYYB5"]["state"] == MP.TERMINAL
    assert mp._cancel_failures == {}        # budget reset, not leaked


def test_a_fill_found_during_retry_is_booked_not_treated_as_unfilled():
    mp = _path(_Venue([FILLED]))
    v = mp._cancel_failure_verdict("up", "CSPERKXEYYB5",
                                   {"state": "CANCEL_FAILED"})
    assert v["clear"] is True
    assert v["filled_qty"] == "10"
    assert mp.registry.orders["CSPERKXEYYB5"]["filled"] == D(10)


def test_unreadable_status_stays_uncertain_and_keeps_the_full_reservation():
    """UNKNOWN is never unfilled: the reservation is charged in full."""
    mp = _path(_Venue([UNREADABLE]))
    v = mp._cancel_failure_verdict("up", "CSPERKXEYYB5",
                                   {"state": "CANCEL_FAILED"})
    assert v["clear"] is False
    assert v["ownership"] == "UNCERTAIN"
    assert mp.registry.orders["CSPERKXEYYB5"]["state"] == MP.UNKNOWN
    # 10 @ 0.46 still fully reserved -- an outcome we never learned cannot
    # quietly free budget
    assert mp.registry.open_reserved == D("4.60")


def test_a_raising_status_transport_stays_uncertain():
    mp = _path(_Venue([OSError("connection reset")]))
    v = mp._cancel_failure_verdict("up", "CSPERKXEYYB5",
                                   {"state": "CANCEL_FAILED"})
    assert v["clear"] is False
    assert v["ownership"] == "UNCERTAIN"
    assert "OSError" in v["why"]


def test_transient_unreadability_recovers_without_being_called_unfilled():
    """The three R5 GET /v1/order 404 UNKNOWN events.

    Each halted its tick (fail-closed, correct) and a later tick read the
    order successfully. The order must be recoverable, and while unreadable
    it must never be booked as unfilled.
    """
    mp = _path(_Venue([UNREADABLE, UNREADABLE, WORKING, EXPIRED]))
    a = mp._cancel_failure_verdict("up", "CSPERKXEYYB5", {"state": "CANCEL_FAILED"})
    assert a["ownership"] == "UNCERTAIN"
    b = mp._cancel_failure_verdict("up", "CSPERKXEYYB5", {"state": "CANCEL_FAILED"})
    assert b["ownership"] == "UNCERTAIN"
    c = mp._cancel_failure_verdict("up", "CSPERKXEYYB5", {"state": "CANCEL_FAILED"})
    assert c["ownership"] == "KNOWN_LIVE"   # recovered from UNKNOWN
    d = mp._cancel_failure_verdict("up", "CSPERKXEYYB5", {"state": "CANCEL_FAILED"})
    assert d["clear"] is True and d["filled_qty"] == "0"
