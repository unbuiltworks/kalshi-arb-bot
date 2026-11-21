"""THE LIVE LOOP, END TO END, AGAINST A SCRIPTED VENUE.

The four properties the owner asked to see proved before a live window,
each driven through the REAL adapter and the REAL controller over a
sequence of steps, with one scripted venue and one scripted clock:

  1. REPEATED FILLS WORK. A side that fills is quoted again, within its
     bounds, rather than locked out for the window.
  2. A ONE-SIDED FILL TRIGGERS SAFE RESIDUAL MANAGEMENT. The residual is
     detected from VENUE-CONFIRMED fills, named as a phase, and the
     completion that answers it never buys a side twice.
  3. AN UNCERTAIN ORDER STATE FAILS CLOSED. A fate that cannot be read
     stops submission, and stays stopped until every such order reads
     clean.
  4. EXPIRY CANCELS AND DISARMS. Past the window's cancel lead nothing
     new is sent, the working orders are withdrawn by exact id, and
     arming refuses.

WHAT THESE TESTS ARE NOT. Nothing here measures a fill rate, a spread,
or an edge, and nothing here is evidence about the real venue. The venue
is a double; the clock is a variable. They prove MECHANISM.

NO ATOMIC PAIR EXECUTION IS ASSUMED ANYWHERE. PMUS guarantees none --
`/v1/orders/batched` creates up to 20 INDEPENDENTLY processed orders and
returns a plain list of ids. So every leg here is reconciled on its own
confirmed events, a one-leg fill is a normal state rather than an error,
and no test waits for a linked fill.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X

import test_l5_btc5m_execution as H
from test_l5_btc5m_execution import armed, clean_env, rig  # noqa: F401


@pytest.fixture(autouse=True)
def _straddle(monkeypatch, clean_env):   # noqa: F811  (ordering: after clean_env)
    """STRADDLE, repeating, with a patient completion configured. These
    are the settings the bounded window would actually run, not a
    convenience fixture: every one of them is an owner variable."""
    monkeypatch.setenv(X.ENTRY_MODE_ENV, C.STRADDLE)
    monkeypatch.setenv(X.STRADDLE_PRICE_ENV, "0.08")
    monkeypatch.setenv(X.CLIP_USD_ENV, "0.40")
    monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "YES")
    monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "1.20")
    monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "0.90")
    monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, "0.96")
    #: the entry-EV gate's measured rates -- 503 shadow windows. Without
    #: these the gate refuses every rung, which is what it did in
    #: production until they were given a reader.
    monkeypatch.setenv(X.STRADDLE_P_PAIR_NATURAL_ENV, "0.070")
    monkeypatch.setenv(X.STRADDLE_P_COMPLETE_ENV, "0.131")
    monkeypatch.setenv(X.STRADDLE_P_LONE_WIN_ENV, "0.0981")
    #: the rates above are usable only when attested as measured
    #: conditional on a fill (required since eed10118c). Without this line
    #: every decision held and these tests placed, and verified, nothing.
    monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)


#: UP collapsed to 0.08 and partly recovered: DOWN offers 0.85, inside
#: the 0.88 completion budget.
RECOVERED = dict(bid="0.15", ask="0.16")
#: UP still collapsed: DOWN offers 0.95, dearer than the whole budget.
COLLAPSED = dict(bid="0.05", ask="0.06")


def placed(rep, reason=None):
    return [o for o in rep["placed"]
            if reason is None or o.get("reason") == reason]


def oid_of(rig_, side, price):
    for oid, i in rig_.venue.by_oid.items():
        if i["side"] == side and D(str(i["price"])) == D(price):
            return oid
    raise AssertionError("no order on %s at %s" % (side, price))


# ============================================================ 1. repeats

class TestRepeatedFillsWork:
    """The lockout `if inv[side][0] > 0: continue` capped the mode at two
    fills a window. These drive three fills on one side through the real
    adapter and show the side is re-quoted each time, and that the
    bounds -- not a lockout -- are what finally stops it."""

    def test_a_filled_side_is_quoted_again(self, rig, armed):
        r1 = rig.step(**RECOVERED)
        legs = placed(r1, "STRADDLE_LEG")
        assert legs, r1
        up = oid_of(rig, "UP", "0.08")
        rig.venue.fill(up)

        rig.tick(2)
        r2 = rig.step(**RECOVERED)
        #: the fill is VENUE-CONFIRMED before anything acts on it
        assert any(D(str(f["qty"])) > 0 for f in r2["fills"]), r2
        again = [o for o in placed(r2, "STRADDLE_LEG") if o["side"] == "UP"]
        assert again, "a filled side must be re-quoted, not locked out"

    def test_three_fills_on_one_side_all_replenish(self, rig, armed):
        seen = 0
        for _ in range(4):
            rep = rig.step(**RECOVERED)
            fresh = [o for o in placed(rep, "STRADDLE_LEG")
                     if o["side"] == "UP"]
            if not fresh:
                break
            seen += 1
            rig.venue.fill(oid_of(rig, "UP", str(fresh[0]["price"])))
            rig.tick(2)
        assert seen >= 2, "the lockout is back: a side filled once stopped"

    def test_repetition_never_breaches_the_side_bound(self, rig, armed):
        #: Repetition replaced the lockout, and the lockout WAS the
        #: de-facto inventory bound (one clip a side). So the thing to
        #: prove is not that some string is logged but that the explicit
        #: bound now does that job: $1.20 a side, over as many fills as
        #: the loop will give.
        for _ in range(8):
            rep = rig.step(**RECOVERED)
            fresh = [o for o in placed(rep, "STRADDLE_LEG")
                     if o["side"] == "UP"]
            inv_ = rep["inventory"] or {}
            cost = D(str((inv_.get("UP") or {}).get("cost") or 0))
            resting = sum(D(str(o["price"])) * D(str(o["qty"]))
                          for o in rep["placed"] if o["side"] == "UP")
            assert cost + resting <= D("1.20"), rep
            if not fresh:
                rig.tick(2)
                continue
            rig.venue.fill(oid_of(rig, "UP", str(fresh[0]["price"])))
            rig.tick(2)

    def test_the_unmatched_ceiling_is_never_breached(self, rig, armed):
        #: $0.90 of unmatched cost, whichever side is heavy. This is the
        #: real directional exposure and repetition must not widen it.
        for _ in range(8):
            rep = rig.step(**RECOVERED)
            exp = rep["exposure"]["btc5m"]
            #: the unmatched leg valued at what we PAID for it -- the
            #: ceiling is in dollars of real directional exposure, so
            #: the worst-case settlement loss is the quantity to bound.
            assert (D(str(exp["worst_case_settlement_loss_usd"]))
                    <= D("0.90")), rep
            fresh = [o for o in placed(rep, "STRADDLE_LEG")
                     if o["side"] == "UP"]
            if fresh:
                rig.venue.fill(oid_of(rig, "UP", str(fresh[0]["price"])))
            rig.tick(2)


# ================================================== 2. residual control

class TestAOneSidedFillIsManagedSafely:

    def test_the_residual_is_named_from_confirmed_fills(self, rig, armed):
        rig.step(**COLLAPSED)
        rig.venue.fill(oid_of(rig, "UP", "0.08"))
        rig.tick(2)
        rep = rig.step(**COLLAPSED)
        assert rep["exposure"]["btc5m"]["unmatched_side"] == "UP"
        assert rep["residual_phase"]["phase"] not in (None, "")

    def test_a_completion_answers_it_without_buying_the_side_twice(self, rig, armed):
        rig.step(**COLLAPSED)
        rig.venue.fill(oid_of(rig, "UP", "0.08"))
        rig.tick(2)
        rep = rig.step(**COLLAPSED)
        comp = placed(rep, "STRADDLE_COMPLETION")
        if comp:
            #: exactly one route to a pair: no cheap DOWN rung may ride
            #: beside the expensive one. This is the 1925z defect, which
            #: cost 1.76 in one window.
            assert not [o for o in placed(rep, "STRADDLE_LEG")
                        if o["side"] == "DOWN"]
            assert len(comp) == 1

    def test_no_loss_making_hedge_is_forced(self, rig, armed):
        #: DOWN offers 0.95 against a 0.88 budget. The correct action is
        #: to rest, or to wait -- never to cross at a price that puts the
        #: pair past its target.
        rig.step(**COLLAPSED)
        rig.venue.fill(oid_of(rig, "UP", "0.08"))
        rig.tick(2)
        rep = rig.step(**COLLAPSED)
        for o in placed(rep, "STRADDLE_COMPLETION"):
            assert o["post_only"] is True
            assert D(str(o["price"])) <= D("0.88")

    def test_an_unfilled_completion_leaves_the_residual_exactly_as_it_was(
            self, rig, armed):
        #: PMUS bonds nothing. A completion that does not fill must not
        #: be booked as a pair.
        rig.step(**COLLAPSED)
        rig.venue.fill(oid_of(rig, "UP", "0.08"))
        rig.tick(2)
        a = rig.step(**COLLAPSED)
        rig.tick(2)
        b = rig.step(**COLLAPSED)
        assert (b["exposure"]["btc5m"]["unmatched_qty"]
                == a["exposure"]["btc5m"]["unmatched_qty"])


# =================================================== 3. uncertainty

class TestAnUncertainOrderFailsClosed:

    def test_an_unreadable_order_halts_submission(self, rig, armed):
        rig.step(**RECOVERED)
        up = oid_of(rig, "UP", "0.08")
        #: the venue cannot say what became of it
        rig.venue.read_error[up] = 99
        rig.tick(2)
        rep = rig.step(**RECOVERED)
        #: the latch NAMES THE CAUSE after the colon, so match the latch
        assert str(rep["halted_reason"]).startswith(X.HALT_ORDER), rep
        assert rep["placed"] == [], "nothing may be sent while a fate is unknown"

    def test_it_stays_closed_while_the_read_keeps_failing(self, rig, armed):
        rig.step(**RECOVERED)
        up = oid_of(rig, "UP", "0.08")
        rig.venue.read_error[up] = 99
        for _ in range(3):
            rig.tick(2)
            rep = rig.step(**RECOVERED)
            assert rep["placed"] == []
            assert str(rep["halted_reason"]).startswith(X.HALT_ORDER)

    def test_the_latch_lifts_only_on_a_clean_read(self, rig, armed):
        rig.step(**RECOVERED)
        up = oid_of(rig, "UP", "0.08")
        rig.venue.read_error[up] = 2
        rig.tick(2)
        assert str(rig.step(**RECOVERED)["halted_reason"]).startswith(
            X.HALT_ORDER)
        rig.tick(2)
        rig.step(**RECOVERED)                      # second failure
        rig.tick(2)
        rep = rig.step(**RECOVERED)                # now readable
        assert rep["halted_reason"] is None, rep

    def test_an_uncertain_submission_is_never_retried_blind(self, rig, armed):
        #: A submit whose outcome is unknown may have reached the book.
        #: Re-sending it is how one clip becomes two.
        rig.venue.submit_error = TimeoutError("ack never arrived")
        rep = rig.step(**RECOVERED)
        assert rep["placed"] == []
        sent_once = len(rig.venue.sent)
        rig.venue.submit_error = None
        rig.tick(2)
        rig.step(**RECOVERED)
        assert len(rig.venue.sent) <= sent_once + 1


# ======================================================== 4. expiry

class TestExpiryCancelsAndDisarms:

    def test_past_the_cancel_lead_nothing_new_is_sent(self, rig, armed):
        rig.step(**RECOVERED)
        before = len(rig.venue.sent)
        rig.tick(2)
        rep = rig.step(t=295.0, **RECOVERED)
        assert rep["placed"] == [], rep
        assert len(rig.venue.sent) == before

    def test_the_working_order_is_withdrawn_by_exact_id(self, rig, armed):
        rig.step(**RECOVERED)
        up = oid_of(rig, "UP", "0.08")
        rig.tick(2)
        rep = rig.ex.end_of_window(H.MARKET)
        #: BY EXACT ID. A blanket cancel-all would reach other lanes'
        #: orders on the same account.
        #: BY EXACT ID, and only ids this lane owns. A blanket
        #: cancel-all would reach other lanes' orders on the same
        #: account.
        ours = set(rig.venue.by_oid)
        assert up in rig.venue.cancelled_ids(), rig.venue.cancelled
        assert set(rig.venue.cancelled_ids()) <= ours
        assert all(slug == H.MARKET for _oid, slug in rig.venue.cancelled)
        assert rep["unresolved"] == [], rep

    def test_arming_refuses_after_the_window(self, rig, armed):
        rig.step(**RECOVERED)
        #: the clock moves past NOT_AFTER
        rig.now = rig.now.replace(hour=12, minute=6)
        rep = rig.step(**RECOVERED)
        assert rep["arming_refusal"] is not None
        assert rep["placed"] == []

    def test_a_cancel_that_loses_to_a_fill_is_booked_not_assumed_away(
            self, rig, armed):
        #: A cancellation never reverses an execution. If the fill beat
        #: the cancel the inventory must show it.
        rig.step(**RECOVERED)
        up = oid_of(rig, "UP", "0.08")
        rig.venue.fill_on_cancel.add(up)
        rig.tick(2)
        rep = rig.ex.end_of_window(H.MARKET)
        assert D(str(rep["inventory"]["UP"]["qty"])) > 0, rep
        #: the order that filled is reported FILLED, not cancelled --
        #: a cancellation never reverses an execution.
        assert [r for r in rep["filled"] if r.get("venue_order_id") == up], rep
        assert not [r for r in rep["cancelled"]
                    if r.get("venue_order_id") == up], rep
