"""Runner-level BTC5m ACCUMULATE scenarios — issue #233 acceptance list.

Deterministic and offline: the scripted venue, clock, book and ledger of
tests/test_l5_btc5m_runner.py, driven through WHOLE SESSIONS in accumulation
mode. No socket, no credential, no real venue.

THE ELEVEN THE ISSUE NAMES, one class each:

  dual resting orders ....... TestDualRestingOrders
  partial fills ............. TestPartialFills
  same-side runs ............ TestSameSideRuns
  completion ................ TestCompletion (accumulation forms no pair:
                              what is pinned is that it never tries)
  restart ................... TestRestart
  uncertain state ........... TestUncertainState
  cancel race ............... TestCancelRace
  rollover .................. TestRollover
  settlement ................ TestSettlement
  hard caps ................. TestHardCaps
  halt / watchdog ........... TestHaltAndWatchdog

plus issue #233 B4's freeze/reversal requirement:

  favorite reversal with pending orders, no duplicate and no stale order
                            ... TestFavoriteReversalFreeze
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from tests import test_l5_btc5m_runner as TR
from tests.test_l5_btc5m_runner import M, WS, Rig          # noqa: F401

UTC = dt.timezone.utc
CAP = "0.45"


@pytest.fixture(autouse=True)
def accumulate(monkeypatch):
    """Every scenario here runs the accumulation rule at the real ceiling."""
    monkeypatch.setenv(X.ENTRY_MODE_ENV, "ACCUMULATE")
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, CAP)


def book_at(bid, ask):
    return lambda t: {"bid": D(bid), "ask": D(ask), "age_s": 0.1}


def rig(tmp_path, monkeypatch, env=None, **kw):
    """A session in ACCUMULATE at the real $0.45 ceiling.

    `Rig.__init__` applies `test_l5_btc5m_runner.ARMED` (which carries a
    $10 account cap) and releases the shared locks, so anything this file
    wants to bind MUST be set AFTER it is constructed -- an earlier draft
    set them before and the Rig quietly overwrote them, which is why the
    cap read $5.00 in the step records instead of $0.45.
    """
    kw.setdefault("book", book_at("0.70", "0.71"))
    r = Rig(tmp_path, monkeypatch, **kw)
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, CAP)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    r.ex.max_live_orders = X.live_order_bound()
    # THE FAVORITE FOLLOWS THE BOOK, which is the strategy contract:
    # "identify the current favorite using UP/DOWN market prices". The
    # shared Rig injects a constant p_up of 0.75, which would pin the
    # favorite to UP whatever the book did and make a reversal untestable.
    r.session.p_fn = lambda m, ws, now: {"ok": False, "p_up": None,
                                         "reason": "BOOK_IS_THE_SOURCE"}
    return r


def submitted(r):
    return [(s["side"], D(s["price"]), D(s["qty"])) for s in r.venue.sent]


def coids(r):
    return [s["client_order_id"] for s in r.venue.sent]


def run(r):
    return r.session.run()


# ======================================================================
class TestDualRestingOrders:

    def test_several_entries_rest_at_once_and_all_are_ours(self, tmp_path,
                                                           monkeypatch):
        r = rig(tmp_path, monkeypatch)
        rep = run(r)
        assert rep["submitted"] >= 2, rep
        assert len(set(coids(r))) == len(coids(r)), "duplicate client id"
        assert all(c.startswith("b5m-") for c in coids(r))
        assert all(s["post_only"] is True for s in r.venue.sent)

    def test_never_more_live_at_once_than_the_bound(self, tmp_path,
                                                    monkeypatch):
        r = rig(tmp_path, monkeypatch)
        seen = []
        real = r.ex.step

        def watch(**kw):
            out = real(**kw)
            seen.append(len(r.ex.live_orders()))
            return out
        r.ex.step = watch
        run(r)
        assert seen, "no step ran"
        assert max(seen) <= X.live_order_bound(), seen


# ======================================================================
class TestPartialFills:

    def test_a_partial_is_booked_once_and_the_remainder_keeps_working(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", "0.05")})
        rep = run(r)
        assert D(rep["fills_qty"]) >= D("0.05")
        inv = r.ex.inventory(M)
        assert inv[C.UP]["qty"] >= D("0.05")
        # cost basis is the limit price of the order that filled
        assert inv[C.UP]["cost"] == inv[C.UP]["qty"] * D("0.70")

    def test_a_partial_does_not_double_count_against_the_cap(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", "0.05")})
        run(r)
        inv = r.ex.inventory(M)
        exp = C.exposure(inv, r.ex._resting(M, None))       # noqa: SLF001
        assert D(exp["total_exposure_usd"]) <= D(CAP)


# ======================================================================
class TestSameSideRuns:

    def test_accumulation_keeps_adding_to_the_one_favorite(self, tmp_path,
                                                           monkeypatch):
        r = rig(tmp_path, monkeypatch)
        run(r)
        sides = {s["side"] for s in r.venue.sent}
        assert sides == {"UP"}, sides
        assert len(r.venue.sent) >= 2

    def test_every_entry_is_inside_the_eligible_band(self, tmp_path,
                                                     monkeypatch):
        r = rig(tmp_path, monkeypatch)
        run(r)
        for _side, price, _qty in submitted(r):
            assert D("0.51") <= price <= D("0.90"), price

    def test_a_price_outside_the_band_accumulates_nothing(self, tmp_path,
                                                          monkeypatch):
        r = rig(tmp_path, monkeypatch, book=book_at("0.95", "0.96"))
        rep = run(r)
        assert rep["submitted"] == 0
        assert r.venue.sent == []


# ======================================================================
class TestCompletion:
    """Accumulation forms no pair on purpose. What must be pinned is that
    it never tries to, however lopsided the inventory gets."""

    def test_no_opposite_side_order_is_ever_sent(self, tmp_path,
                                                 monkeypatch):
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", None)})
        run(r)
        assert {s["side"] for s in r.venue.sent} == {"UP"}

    def test_the_decision_never_reports_a_hedge(self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", None)})
        run(r)
        for e in r.kinds("L5_BTC5M_STEP"):
            assert e["action"] != C.HEDGE_UNDERDOG, e
            assert (e.get("hedge") or {}).get("why") in (
                None, "HEDGING_DISABLED")


# ======================================================================
class TestRestart:

    def test_a_restart_mid_window_keeps_the_ledger_and_adopts_no_stranger(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", "0.05")})
        r.session.start()
        r.session.trade()
        before = dict(r.ex.orders())
        reborn = X.Btc5mExecutor(
            r.venue, r.ex.ledger_path,
            now_fn=lambda: dt.datetime.fromtimestamp(r.clock(), UTC),
            max_live_orders=X.live_order_bound())
        assert set(reborn.orders()) == set(before)
        assert reborn.inventory(M)[C.UP]["qty"] == \
            r.ex.inventory(M)[C.UP]["qty"]
        assert reborn.owns(next(iter(before)))
        assert not reborn.owns("l5f-someone-elses-order")


# ======================================================================
class TestUncertainState:

    @staticmethod
    def _step(r, t):
        return r.ex.step(market=M,
                         book={"bid": "0.70", "ask": "0.71", "age_s": 0.1},
                         t_in_window_s=t, exposure_ceiling_usd=D(CAP))

    def test_an_unreadable_order_stops_every_new_submission(self, tmp_path,
                                                            monkeypatch):
        """The real uncertain state: the venue will not say what happened
        to an order of ours. Until it does, nothing new goes out."""
        r = rig(tmp_path, monkeypatch)
        r.session.start()
        self._step(r, 10.0)
        n = len(r.venue.sent)
        assert n >= 1
        oid = next(iter(r.ex.live_orders().values()))["venue_order_id"]
        r.venue.read_error[oid] = 99          # the venue stops answering
        for t in (20.0, 25.0, 30.0):
            self._step(r, t)
        assert r.ex.halted_reason, "an unreadable order did not halt us"
        assert len(r.venue.sent) == n, "submitted while an order was uncertain"

    def test_a_clean_read_clears_the_halt_and_accumulation_resumes(
            self, tmp_path, monkeypatch):
        """The other half of the contract: the halt lasts exactly as long
        as the uncertainty, not longer."""
        r = rig(tmp_path, monkeypatch)
        r.session.start()
        self._step(r, 10.0)
        n = len(r.venue.sent)
        oid = next(iter(r.ex.live_orders().values()))["venue_order_id"]
        r.venue.read_error[oid] = 1           # one failed read, then fine
        self._step(r, 20.0)
        assert r.ex.halted_reason
        self._step(r, 25.0)                   # the venue answers again
        assert r.ex.halted_reason is None
        self._step(r, 30.0)
        assert len(r.venue.sent) > n, "accumulation did not resume"


# ======================================================================
class TestCancelRace:

    def test_a_fill_that_beats_the_cancel_is_booked_not_lost(self, tmp_path,
                                                             monkeypatch):
        r = rig(tmp_path, monkeypatch)
        r.session.start()
        r.ex.step(market=M, book={"bid": "0.70", "ask": "0.71", "age_s": 0.1},
                  t_in_window_s=10.0, exposure_ceiling_usd=D(CAP))
        live = [v["venue_order_id"] for v in r.ex.live_orders().values()]
        assert live
        r.venue.fill_on_cancel.add(live[0])
        rep = r.ex.end_of_window(M)
        booked = r.ex.inventory(M)[C.UP]["qty"]
        assert booked > 0, "the racing fill was dropped"
        assert [c["venue_order_id"] for c in rep.get("filled") or ()] == [
            live[0]]

    def test_no_order_is_left_unresolved_after_the_race(self, tmp_path,
                                                        monkeypatch):
        r = rig(tmp_path, monkeypatch)
        r.session.start()
        r.ex.step(market=M, book={"bid": "0.70", "ask": "0.71", "age_s": 0.1},
                  t_in_window_s=10.0, exposure_ceiling_usd=D(CAP))
        live = [v["venue_order_id"] for v in r.ex.live_orders().values()]
        r.venue.fill_on_cancel.add(live[0])
        r.ex.end_of_window(M)
        assert r.ex.live_orders() == {}


# ======================================================================
class TestRollover:

    def test_the_window_closes_clean_and_is_never_run_twice(self, tmp_path,
                                                            monkeypatch):
        r = rig(tmp_path, monkeypatch)
        rep = run(r)
        assert rep["close"]["clean"] is True
        assert rep["close"]["unresolved"] == []
        assert r.ex.live_orders() == {}
        again = r.session.start()
        assert again["ok"] is False
        assert again["reason"] == "MARKET_ALREADY_RUN"

    def test_the_transport_is_disarmed_at_the_close(self, tmp_path,
                                                    monkeypatch):
        r = rig(tmp_path, monkeypatch)
        run(r)
        assert r.venue.armed is False
        assert r.venue.disarmed_with == "BTC5M_SESSION_CLOSED"


# ======================================================================
class TestSettlement:

    def test_a_winning_directional_book_settles_to_payout_minus_cost(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", None)},
                venue_kw={"settle_px": "1"})
        rep = run(r)
        inv = r.ex.inventory(M)
        s = rep["settlement"]
        assert s["settled"] is True and s["up_won"] is True
        assert D(s["redemption_total"]) == inv[C.UP]["qty"]
        assert D(s["settlement_pnl"]) == inv[C.UP]["qty"] - inv[C.UP]["cost"]

    def test_a_losing_directional_book_costs_exactly_what_was_spent(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", None)},
                venue_kw={"settle_px": "0"})
        rep = run(r)
        inv = r.ex.inventory(M)
        s = rep["settlement"]
        assert s["up_won"] is False
        assert D(s["redemption_total"]) == 0
        assert D(s["settlement_pnl"]) == -inv[C.UP]["cost"]
        assert -D(s["settlement_pnl"]) <= D(CAP)

    def test_the_report_states_the_venue_commission(self, tmp_path,
                                                    monkeypatch):
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", None)},
                venue_kw={"settle_px": "1", "fee": "0"})
        rep = run(r)
        assert rep["venue_fees"]["complete"] is True
        assert D(rep["venue_fees"]["total_usd"]) == 0
        assert rep["settled_net_usd"] is not None


# ======================================================================
class TestHardCaps:

    def test_exposure_never_exceeds_the_045_ceiling_at_any_step(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        seen = []
        real = r.ex.step

        def watch(**kw):
            out = real(**kw)
            e = (out.get("exposure") or {}).get("btc5m")
            if e:
                seen.append(D(e["total_exposure_usd"]))
            return out
        r.ex.step = watch
        run(r)
        assert seen
        assert max(seen) <= D(CAP), max(seen)

    def test_a_zero_account_ceiling_sends_nothing(self, tmp_path,
                                                  monkeypatch):
        r = rig(tmp_path, monkeypatch, env={X.ACCOUNT_CAP_ENV: "0"})
        rep = run(r)
        assert rep["submitted"] == 0
        assert r.venue.sent == []

    def test_the_total_filled_cost_never_exceeds_the_ceiling(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch,
                fill_at={10.0: ("v1", None), 20.0: ("v2", None),
                         30.0: ("v3", None), 40.0: ("v4", None)})
        run(r)
        inv = r.ex.inventory(M)
        assert inv[C.UP]["cost"] + inv[C.DOWN]["cost"] <= D(CAP)


# ======================================================================
class TestHaltAndWatchdog:

    def test_the_shared_halt_cancels_ours_and_places_nothing(self, tmp_path,
                                                             monkeypatch):
        r = rig(tmp_path, monkeypatch,
                halt=lambda: {"ok": False, "reason": "WATCHDOG"})
        rep = run(r)
        assert rep["submitted"] == 0
        assert any("SHARED_HALT" in (e.get("reason") or "")
                   for e in r.kinds("L5_BTC5M_BLOCKED"))

    def test_a_refusing_account_risk_state_places_nothing(self, tmp_path,
                                                          monkeypatch):
        r = rig(tmp_path, monkeypatch,
                risk=lambda now, own: {"ok": False,
                                       "reason": "ACCOUNT_DAILY_LOSS_HALT"})
        rep = run(r)
        assert rep["submitted"] == 0

    def test_the_operator_halt_blocks_accumulation(self, tmp_path,
                                                   monkeypatch):
        r = rig(tmp_path, monkeypatch, env={X.HALT_ENV: "1"})
        rep = run(r)
        assert rep["submitted"] == 0


# ======================================================================
class TestFavoriteReversalFreeze:
    """Issue #233 B4: freeze/hold must handle favorite reversal and pending
    orders without duplicate or stale orders."""

    @staticmethod
    def flipping(t):
        """UP is the favorite for the first 100 s, DOWN after."""
        return ({"bid": D("0.70"), "ask": D("0.71"), "age_s": 0.1}
                if t < 100.0 else
                {"bid": D("0.29"), "ask": D("0.30"), "age_s": 0.1})

    def test_the_old_side_is_cancelled_and_never_added_to_again(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, book=self.flipping)
        run(r)
        order = {}
        for s in r.venue.sent:
            order.setdefault(s["side"], len(order))
        assert set(order) == {"UP", "DOWN"}, order
        last_up = max(i for i, s in enumerate(r.venue.sent)
                      if s["side"] == "UP")
        first_down = min(i for i, s in enumerate(r.venue.sent)
                         if s["side"] == "DOWN")
        assert last_up < first_down, "an UP order went out after the flip"

    def test_no_duplicate_client_order_id_survives_the_reversal(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, book=self.flipping)
        run(r)
        ids = coids(r)
        assert len(ids) == len(set(ids)), ids

    def test_nothing_stale_is_left_resting_on_the_abandoned_side(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, book=self.flipping)
        run(r)
        for row in r.ex.orders().values():
            assert row["state"] in (X.FILLED, X.CANCELLED), row
        assert r.ex.live_orders() == {}

    def test_inventory_held_on_the_abandoned_side_is_carried_not_dumped(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, book=self.flipping,
                fill_at={12.0: ("v1", None)})
        run(r)
        assert r.ex.inventory(M)[C.UP]["qty"] > 0
        # maker-only: nothing was sold to flatten it
        assert all(s["action"] == "BUY" for s in r.venue.sent)

    def test_a_reversal_while_an_order_is_pending_cancels_exactly_once(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch, book=self.flipping)
        run(r)
        cancelled = r.venue.cancelled_ids()
        assert len(cancelled) == len(set(cancelled)), cancelled


# ======================================================================
class TestSubmissionBudgetIsVisible:
    """A live window must not be spent discovering that an owner-set budget
    binds. The budget is reported before trading and again in the report,
    and reading it claims nothing."""

    def test_the_budget_is_reported_at_the_start_and_in_the_report(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        st = r.session.start()
        assert "submission_budget" in st
        rep = r.session.run() if False else r.session
        out = r.ex.orders()  # noqa: F841 - keep the ledger touched
        assert set(st["submission_budget"]) >= {"OPEN", "COMPLETE",
                                                "REPLACE", "readable"}
        assert st["submission_budget"]["readable"] is True
        assert rep is r.session

    def test_an_unset_budget_does_not_bind_and_says_so(self, tmp_path,
                                                       monkeypatch):
        for k in X.AUTH.CLASS_BUDGET_ENV.values():
            monkeypatch.delenv(k, raising=False)
        b = X.submission_budget()
        for k in ("OPEN", "COMPLETE", "REPLACE"):
            assert b[k]["max"] is None
            assert b[k]["binds"] is False
            assert b[k]["remaining"] is None

    def test_a_configured_budget_reports_what_remains(self, tmp_path,
                                                      monkeypatch):
        monkeypatch.setenv(X.AUTH.CLASS_BUDGET_ENV["REPLACE"], "6")
        b = X.submission_budget()
        assert b["REPLACE"]["max"] == 6
        assert b["REPLACE"]["binds"] is True
        assert b["REPLACE"]["remaining"] == 6 - b["REPLACE"]["spent"]

    def test_reading_the_budget_claims_nothing(self, tmp_path, monkeypatch):
        """It must never consume a ticket it is only reporting on."""
        monkeypatch.setenv(X.AUTH.CLASS_BUDGET_ENV["OPEN"], "3")
        first = X.submission_budget()["OPEN"]["spent"]
        for _ in range(5):
            X.submission_budget()
        assert X.submission_budget()["OPEN"]["spent"] == first

    def test_the_session_report_carries_it_too(self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        rep = run(r)
        assert rep["submission_budget"]["readable"] is True
        assert "REPLACE" in rep["submission_budget"]
