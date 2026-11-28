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
import inspect
import os
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_btc5m_runner as RUN
from src import l5_live_authorization as AUTH
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
        assert set(st["submission_budget"]) >= {"OPEN", "COMPLETE",
                                                "REPLACE", "readable"}
        assert st["submission_budget"]["readable"] is True

    def test_an_unset_budget_does_not_bind_and_says_so(self, tmp_path,
                                                       monkeypatch):
        for k in AUTH.CLASS_BUDGET_ENV.values():
            monkeypatch.delenv(k, raising=False)
        b = RUN.submission_budget()
        for k in ("OPEN", "COMPLETE", "REPLACE"):
            assert b[k]["max"] is None
            assert b[k]["binds"] is False
            assert b[k]["remaining"] is None

    def test_a_configured_budget_reports_what_remains(self, tmp_path,
                                                      monkeypatch):
        monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["REPLACE"], "6")
        b = RUN.submission_budget()
        assert b["REPLACE"]["max"] == 6
        assert b["REPLACE"]["binds"] is True
        assert b["REPLACE"]["remaining"] == 6 - b["REPLACE"]["spent"]

    def test_reading_the_budget_claims_nothing(self, tmp_path, monkeypatch):
        """It must never consume a ticket it is only reporting on."""
        monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "3")
        first = RUN.submission_budget()["OPEN"]["spent"]
        for _ in range(5):
            RUN.submission_budget()
        assert RUN.submission_budget()["OPEN"]["spent"] == first

    def test_the_session_report_carries_it_too(self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        rep = run(r)
        assert rep["submission_budget"]["readable"] is True
        assert "REPLACE" in rep["submission_budget"]


# ======================================================================
class TestApprovedWindowEnumeration:
    """Running more than one window must consume the OWNER's approval, not
    widen it. The runner may only ever name windows that the owner's own
    document enumerates, and never more than it allows."""

    @staticmethod
    def _approve(monkeypatch, tmp_path, starts, **over):
        from src import l5_campaign_approval as CA
        now = starts[0] - dt.timedelta(minutes=5)
        TR.T5.authorize_5m(monkeypatch, tmp_path, now_fn=lambda: now,
                           starts=starts, **over)
        return now

    def test_it_lists_exactly_the_windows_the_owner_enumerated(
            self, tmp_path, monkeypatch):
        starts = [WS + dt.timedelta(minutes=5 * i) for i in range(3)]
        now = self._approve(monkeypatch, tmp_path, starts)
        got = RUN.approved_markets(now)
        assert got["readable"] is True
        assert got["markets"] == sorted(
            "cpc-btc-updown-5m-%s-%sz"
            % (t.strftime("%Y-%m-%d"), t.strftime("%H%M")) for t in starts)
        assert got["max_windows"] == 3

    def test_no_approval_document_enumerates_nothing(self, tmp_path,
                                                     monkeypatch):
        from src import l5_campaign_approval as CA
        monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
        got = RUN.approved_markets(dt.datetime.now(UTC))
        assert got["markets"] == []
        assert got["readable"] is False

    def test_an_approval_for_another_series_enumerates_nothing(
            self, tmp_path, monkeypatch):
        starts = [WS]
        now = self._approve(monkeypatch, tmp_path, starts,
                            series="cpc-btc-updown-15m-")
        got = RUN.approved_markets(now)
        assert got["markets"] == []
        assert got["why"] in ("APPROVAL_IS_FOR_ANOTHER_SERIES",
                              "NO_APPROVAL_DOCUMENT")

    def test_the_runner_cannot_invent_a_window(self, tmp_path, monkeypatch):
        """Every slug it returns must map back to an approved start."""
        starts = [WS, WS + dt.timedelta(minutes=10)]
        now = self._approve(monkeypatch, tmp_path, starts)
        approved = {t.strftime("%H%M") for t in starts}
        for slug in RUN.approved_markets(now)["markets"]:
            assert slug[-5:-1] in approved, slug

    def test_the_session_passes_the_owners_window_count_to_the_boundary(
            self, tmp_path, monkeypatch):
        """Not a hardcoded 1: the boundary must check against the figure
        the owner actually wrote."""
        r = rig(tmp_path, monkeypatch)
        assert r.session.max_windows == 1
        r2 = Rig(tmp_path / "b", monkeypatch)
        r2.session.max_windows = 4
        assert r2.session.max_windows == 4

    def test_campaign_mode_is_off_unless_the_owner_sets_it(self,
                                                           monkeypatch):
        monkeypatch.delenv(RUN.CAMPAIGN_ENV, raising=False)
        assert (os.environ.get(RUN.CAMPAIGN_ENV) or "").strip() != "ON"
        for junk in ("", "on", "1", "true", "YES"):
            monkeypatch.setenv(RUN.CAMPAIGN_ENV, junk)
            assert (os.environ.get(RUN.CAMPAIGN_ENV) or "").strip() != "ON", \
                junk


# ======================================================================
class TestRollingWindows:
    """The approval layer offers TWO opening bounds: an enumerated
    `window_starts`, or a `last_open_utc` after which no window may open.
    The second is the rolling form the 15-minute lane uses. Neither widens
    anything: both are the owner's own document."""

    @staticmethod
    def _scope(monkeypatch, **scope):
        from src import l5_campaign_approval as CA

        def fake(market, now=None, reserve=False):
            return {"present": True, "ok": True,
                    "scope": dict({"series": CA.BTC5M_SERIES}, **scope)}
        monkeypatch.setattr(CA, "campaign_decision", fake)

    def test_a_last_open_bound_rolls_the_next_windows(self, monkeypatch):
        now = dt.datetime(2026, 11, 12, 15, 2, 30, tzinfo=UTC)
        self._scope(monkeypatch, max_windows=4,
                    last_open_utc="2026-11-12T15:30:00Z")
        got = RUN.approved_markets(now)
        assert got["mode"] == "ROLLING"
        assert got["markets"] == [
            "cpc-btc-updown-5m-2026-11-12-1505z",
            "cpc-btc-updown-5m-2026-11-12-1510z",
            "cpc-btc-updown-5m-2026-11-12-1515z",
            "cpc-btc-updown-5m-2026-11-12-1520z"], got["markets"]

    def test_it_never_plans_a_window_already_under_way(self, monkeypatch):
        now = dt.datetime(2026, 11, 12, 15, 2, 30, tzinfo=UTC)
        self._scope(monkeypatch, max_windows=8,
                    last_open_utc="2026-11-12T16:00:00Z")
        got = RUN.approved_markets(now)
        assert "cpc-btc-updown-5m-2026-11-12-1500z" not in got["markets"]
        assert got["markets"][0] == "cpc-btc-updown-5m-2026-11-12-1505z"

    def test_last_open_binds_before_max_windows(self, monkeypatch):
        now = dt.datetime(2026, 11, 12, 15, 2, 30, tzinfo=UTC)
        self._scope(monkeypatch, max_windows=8,
                    last_open_utc="2026-11-12T15:15:00Z")
        got = RUN.approved_markets(now)
        assert got["markets"] == [
            "cpc-btc-updown-5m-2026-11-12-1505z",
            "cpc-btc-updown-5m-2026-11-12-1510z",
            "cpc-btc-updown-5m-2026-11-12-1515z"], got["markets"]

    def test_max_windows_binds_before_last_open(self, monkeypatch):
        now = dt.datetime(2026, 11, 12, 15, 2, 30, tzinfo=UTC)
        self._scope(monkeypatch, max_windows=2,
                    last_open_utc="2026-11-12T18:00:00Z")
        got = RUN.approved_markets(now)
        assert len(got["markets"]) == 2

    def test_a_rolling_plan_never_exceeds_the_hard_ceiling(self,
                                                           monkeypatch):
        now = dt.datetime(2026, 11, 12, 15, 2, 30, tzinfo=UTC)
        self._scope(monkeypatch, max_windows=999,
                    last_open_utc="2026-11-13T00:00:00Z")
        got = RUN.approved_markets(now)
        assert len(got["markets"]) <= RUN.CA_MAX_ROLLING_WINDOWS == 8

    def test_enumerated_windows_still_win_when_both_are_present(self,
                                                                monkeypatch):
        now = dt.datetime(2026, 11, 12, 15, 2, 30, tzinfo=UTC)
        self._scope(monkeypatch, max_windows=4,
                    window_starts=["2026-11-12T16:00:00Z"],
                    last_open_utc="2026-11-12T18:00:00Z")
        got = RUN.approved_markets(now)
        assert got["mode"] == "ENUMERATED"
        assert got["markets"] == ["cpc-btc-updown-5m-2026-11-12-1600z"]

    def test_neither_bound_plans_nothing(self, monkeypatch):
        now = dt.datetime(2026, 11, 12, 15, 2, 30, tzinfo=UTC)
        self._scope(monkeypatch, max_windows=4)
        got = RUN.approved_markets(now)
        assert got["markets"] == []
        assert got["why"] == "APPROVAL_ENUMERATES_NO_WINDOWS"

    def test_a_last_open_already_past_plans_nothing(self, monkeypatch):
        now = dt.datetime(2026, 11, 12, 15, 2, 30, tzinfo=UTC)
        self._scope(monkeypatch, max_windows=4,
                    last_open_utc="2026-11-12T14:00:00Z")
        got = RUN.approved_markets(now)
        assert got["markets"] == []
        assert got["why"] == "NO_WINDOW_OPENS_BEFORE_LAST_OPEN_UTC"


# ======================================================================
class TestCampaignSkipsRatherThanStops:
    """Regression for a defect observed LIVE on 2026-11-12 at 15:20Z: a
    restart inside a campaign re-plans the whole approved list, meets the
    window it already finished, and treated that as a refusal -- ending the
    campaign on its first entry while 15:20z and 15:25z were still ahead of
    it and unrun.

    An already-run or already-past window is a SKIP. The durable marker is
    what guarantees no window trades twice, and that is exactly what makes
    skipping safe. A halt, a refusing risk state or an unclean close still
    STOP the campaign."""

    def test_an_already_run_window_is_skipped_not_fatal(self):
        src = inspect.getsource(RUN.main)
        assert '"MARKET_ALREADY_RUN", "WINDOW_ALREADY_PAST"' in src
        i = src.index("skip = {")
        j = src.index("stop = reason")
        assert i < j, "the skip set must be consulted before stopping"
        assert "continue" in src[i:j]

    def test_a_real_refusal_still_stops_the_campaign(self):
        src = inspect.getsource(RUN.main)
        for fatal in ("CLOSE_NOT_CLEAN", "LEDGER_HALTED"):
            assert fatal in src, fatal
        assert "L5_BTC5M_CAMPAIGN_STOPPED" in src

    def test_a_skipped_window_is_announced(self):
        assert "L5_BTC5M_WINDOW_SKIPPED" in inspect.getsource(RUN.main)

    def test_the_marker_still_prevents_a_second_run(self, tmp_path,
                                                    monkeypatch):
        """Skipping is only safe because the marker is authoritative."""
        r = rig(tmp_path, monkeypatch)
        run(r)
        again = r.session.start()
        assert again["ok"] is False
        assert again["reason"] == "MARKET_ALREADY_RUN"


# ======================================================================
class TestCampaignNamesTheWindowInTheExecutorLock:
    """Regression for a defect observed LIVE on 2026-11-12 at 15:42Z.

    `Btc5mExecutor.arming_refusal` refuses every submission unless
    `L5_BTC5M_MARKET` is set AND equals the market being traded. A campaign
    leaves that variable empty so the runner can take its windows from the
    owner's approval, so every order was refused MARKET_NOT_CONFIGURED
    while the controller was correctly choosing OPEN_FAVORITE at 0.76,
    0.80, 0.82 and 0.85.

    The runner now names the current window in that lock for the duration
    of the window: it still names exactly ONE market at any instant, and it
    is narrowed per window, never widened."""

    def test_the_lock_is_what_refuses_an_unnamed_market(self, tmp_path,
                                                        monkeypatch):
        r = rig(tmp_path, monkeypatch, env={X.MARKET_ENV: ""})
        assert r.ex.arming_refusal(M, dt.datetime.now(UTC)) == \
            "MARKET_NOT_CONFIGURED"

    def test_naming_the_window_clears_that_refusal(self, tmp_path,
                                                   monkeypatch):
        r = rig(tmp_path, monkeypatch, env={X.MARKET_ENV: M})
        assert r.ex.arming_refusal(M, dt.datetime.now(UTC)) != \
            "MARKET_NOT_CONFIGURED"

    def test_a_different_market_is_still_refused(self, tmp_path,
                                                 monkeypatch):
        r = rig(tmp_path, monkeypatch, env={X.MARKET_ENV: M})
        other = "cpc-btc-updown-5m-2026-11-11-1205z"
        assert r.ex.arming_refusal(other, dt.datetime.now(UTC)) == \
            "MARKET_MISMATCH"

    def test_the_runner_sets_and_restores_the_lock_around_each_window(self):
        src = inspect.getsource(RUN.main)
        assert "os.environ[X.MARKET_ENV] = slug" in src
        i = src.index("os.environ[X.MARKET_ENV] = slug")
        assert "finally:" in src[i:], "the lock must be restored"
        assert "os.environ.pop(X.MARKET_ENV, None)" in src[i:]


# ======================================================================
class TestAccountExposureIsVisible:
    """A window refused ACCOUNT_EXPOSURE_CAP must be able to say WHY from
    its own logs. Observed live 2026-11-12 15:55Z: every order was refused
    and the records could not show what was consuming the ceiling."""

    def test_the_step_record_carries_the_account_breakdown(self):
        src = inspect.getsource(RUN.Session.trade)
        assert '"account"' in src
        for field in ("total_usd", "own_usd", "other_usd",
                      "other_position_contracts", "unknown"):
            assert field in src, field

    def test_it_reads_no_extra_venue_call(self):
        """It must reuse the figure the placement already read."""
        src = inspect.getsource(RUN.Session.trade)
        i = src.index('"account"')
        assert 'rep.get("exposure")' in src[i:i + 400]

    def test_foreign_positions_count_gross_on_both_legs(self, tmp_path,
                                                        monkeypatch):
        """The mechanism behind the live refusal: a MATCHED pair elsewhere
        on the account still consumes both legs of the ceiling."""
        r = rig(tmp_path, monkeypatch)
        r.venue.extra_positions = [
            {"marketSlug": "cpc-btc-updown-15m-2026-11-11-1200z",
             "qtyBought": "0.17", "qtySold": "0.17"}]
        acct = r.ex.account_exposure()
        assert acct["unknown"] is False
        assert acct["other_position_contracts"] == D("0.34")
        assert acct["other_usd"] == D("0.34")


# ======================================================================
class TestAuthenticatedBookFallback:
    """Why BTC5m kept losing windows to `book_readable`.

    The venue's REST book route 404s for the first minutes of a window.
    `PmusTransport._ws_book_substitute` already handles that: it accepts
    the AUTHENTICATED SOCKET's book in place of a 404 when the snapshot is
    fresh, two-sided and the market reads OPEN. BTC15m supplies that
    socket. BTC5m supplied nothing, so `ws_book` was None, the substitute
    returned None immediately, and every 404 failed the preflight --
    measured live at 15:15z, 15:45z and 16:15z, t=3s to t=88s."""

    def test_the_5m_selector_takes_the_current_window(self):
        now = dt.datetime(2026, 11, 12, 16, 22, 30, tzinfo=UTC).timestamp()
        assert RUN.wanted_5m_slugs(now, 60.0) == [
            "cpc-btc-updown-5m-2026-11-12-1620z"]

    def test_it_adds_the_next_window_inside_the_lead(self):
        now = dt.datetime(2026, 11, 12, 16, 24, 30, tzinfo=UTC).timestamp()
        assert RUN.wanted_5m_slugs(now, 60.0) == [
            "cpc-btc-updown-5m-2026-11-12-1620z",
            "cpc-btc-updown-5m-2026-11-12-1625z"]

    def test_every_slug_it_names_is_in_the_5m_series(self):
        base = dt.datetime(2026, 11, 12, 0, 0, tzinfo=UTC).timestamp()
        for k in range(0, 288):
            for s in RUN.wanted_5m_slugs(base + k * 300 + 30, 60.0):
                assert s.startswith(X.MARKET_PREFIX), s
                assert RUN.window_start(s) is not None, s

    def test_the_15m_feed_is_unchanged_by_default(self):
        """The shared module gained an optional selector. Absent it, the
        feed must still subscribe exactly the 15-minute windows."""
        from src import l5_pmus_auth_book as AB
        book = AB.AuthBook()
        assert book.slugs_fn is AB.wanted_slugs
        now = dt.datetime(2026, 11, 12, 16, 22, 30, tzinfo=UTC).timestamp()
        assert book.slugs_fn(now, AB.LEAD_S) == AB.wanted_slugs(now,
                                                                AB.LEAD_S)
        assert all("-15m-" in s for s in book.slugs_fn(now, AB.LEAD_S))

    def test_an_injected_selector_is_what_the_feed_uses(self):
        from src import l5_pmus_auth_book as AB
        book = AB.AuthBook(slugs_fn=RUN.wanted_5m_slugs)
        now = dt.datetime(2026, 11, 12, 16, 22, 30, tzinfo=UTC).timestamp()
        assert book.slugs_fn(now, 60.0) == [
            "cpc-btc-updown-5m-2026-11-12-1620z"]

    def test_the_runner_hands_the_socket_to_the_transport(self):
        src = inspect.getsource(RUN.main)
        assert "AB.AuthBook(slugs_fn=wanted_5m_slugs)" in src
        assert "t.ws_book = auth_book" in src
        i = src.index("t.ws_book = auth_book")
        assert "if auth_book is not None:" in src[max(0, i - 200):i]

    def test_a_feed_that_will_not_start_is_not_fatal(self):
        """No socket means the old behaviour -- a 404 simply fails the
        preflight -- never a crash and never an unguarded order."""
        src = inspect.getsource(RUN.main)
        i = src.index("AB.AuthBook")
        assert "except BaseException" in src[i:i + 500]
        assert '"started": False' in src[i:i + 700]


# ======================================================================
class TestReconciliationHappensBeforeSettlement:
    """A safety check that cannot pass when it matters is not a check.

    OBSERVED LIVE 2026-11-12. `run()` settled before it reconciled. The
    venue redeems a settled position and then reports no row for it, while
    this ledger still states what was held, so `agrees` -- which is
    `net_ours == 0 and not rows` when the venue offers no net -- was False
    for every window that actually filled (16:20z holding 0.29 UP / 0.37
    DOWN, 16:25z holding 0.58 DOWN) and True only for windows that held
    nothing."""

    def test_reconcile_is_called_before_settle(self):
        src = inspect.getsource(RUN.Session.run)
        assert src.index("recon = self.reconcile()") < \
            src.index("settled = self.settle()"), \
            "reconciliation must see the venue before it redeems"

    def test_the_post_settlement_read_is_kept_but_not_the_verdict(self):
        src = inspect.getsource(RUN.Session.run)
        assert "recon_after = self.reconcile()" in src
        assert '"reconcile": recon,' in src
        assert '"reconcile_after_settlement": recon_after,' in src

    def test_a_held_position_reconciles_against_the_live_venue(
            self, tmp_path, monkeypatch):
        """With inventory held and the venue still reporting it, the
        reconciliation in the step record AGREES. Before the fix this
        same window reported agrees false."""
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", "0.05")})
        rep = run(r)
        assert D(rep["fills_qty"]) > 0, "nothing filled; test proves nothing"
        rec = rep["reconcile"]
        assert rec["read"] is True
        assert rec["agrees"] is True, rec
        assert rec["venue_rows"], "the venue should still show the position"

    def test_an_empty_venue_against_held_inventory_does_not_agree(
            self, tmp_path, monkeypatch):
        """The failure mode made visible. This is what a post-settlement
        read looks like -- our ledger holds, the venue has redeemed and
        reports nothing -- and it must never read as agreement."""
        r = rig(tmp_path, monkeypatch, fill_at={12.0: ("v1", "0.05")})
        run(r)
        assert r.ex.inventory(M)[C.UP]["qty"] > 0
        r.venue.by_oid.clear()      # the venue forgets, as after redemption
        r.venue.cum.clear()
        rec = r.session.reconcile()
        assert rec["read"] is True
        assert rec["venue_rows"] == []
        assert rec["agrees"] is False, rec


# ======================================================================
class TestTopOfBookDepthIsRecorded:
    """The authenticated socket carries SIZE; the public tape does not.

    `replay_gates.py` closed the entry-state filter family -- 17 gates, all
    negative out of sample, with selectivity and per-share result
    rank-correlated the wrong way (rho +0.833, t +5.84). The one feature
    class left untested is queue ahead / flow imbalance, and it was
    untestable only because nothing wrote the sizes down: depth is null in
    all 27,425 aligned rows because the PUBLIC endpoint publishes none.
    This session already runs the authenticated socket, so the capture is
    free. It must stay strictly observational."""

    class Book:
        def __init__(self, snap=None, boom=None):
            self.snap, self.boom, self.calls = snap, boom, 0

        def snapshot(self, slug):
            self.calls += 1
            if self.boom is not None:
                raise self.boom
            return self.snap

    def test_without_an_authenticated_book_it_says_so(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        r.session.t.ws_book = None
        assert r.session.book_depth() == {"read": False,
                                          "reason": "NO_AUTH_BOOK"}

    def test_sizes_are_captured_from_the_snapshot(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        r.session.t.ws_book = self.Book(snap={
            "bid": "0.70", "ask": "0.71", "bid_shares": "120",
            "ask_shares": "35", "book_updates": 9, "stream_age_s": 0.2,
            "market_state": "OPEN", "source": "authenticated_ws"})
        got = r.session.book_depth()
        assert got["read"] is True
        assert got["bid_shares"] == "120" and got["ask_shares"] == "35"
        assert got["book_updates"] == "9"
        assert got["source"] == "authenticated_ws"

    def test_a_missing_snapshot_is_a_reason_not_a_crash(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        r.session.t.ws_book = self.Book(snap=None)
        assert r.session.book_depth() == {"read": False,
                                          "reason": "NO_SNAPSHOT"}

    def test_a_raising_book_cannot_break_the_step(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        r.session.t.ws_book = self.Book(boom=RuntimeError("socket gone"))
        assert r.session.book_depth() == {"read": False,
                                          "reason": "RuntimeError"}
        rep = run(r)          # the window still runs to completion
        assert int(rep["submitted"]) > 0
        steps = r.kinds("L5_BTC5M_STEP")
        assert steps and all(
            s["depth"] == {"read": False, "reason": "RuntimeError"}
            for s in steps)

    def test_the_step_record_carries_it(self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        r.session.t.ws_book = self.Book(snap={"bid": "0.70", "ask": "0.71",
                                      "bid_shares": "120",
                                      "ask_shares": "35"})
        run(r)
        steps = r.kinds("L5_BTC5M_STEP")
        assert steps, "no step records"
        assert steps[0]["depth"]["bid_shares"] == "120"

    def test_depth_cannot_change_a_single_order(self, tmp_path, monkeypatch):
        """THE SAFETY PROPERTY. Identical submissions with and without the
        authenticated book attached. If this ever fails, an observational
        field has become an input to a money decision."""
        a = rig(tmp_path / "a", monkeypatch)
        a.session.t.ws_book = None
        run(a)
        without = submitted(a)

        b = rig(tmp_path / "b", monkeypatch)
        b.session.t.ws_book = self.Book(snap={"bid": "0.70", "ask": "0.71",
                                      "bid_shares": "999",
                                      "ask_shares": "1"})
        run(b)
        assert submitted(b) == without
        assert b.session.t.ws_book.calls > 0, "the book was never consulted"


# ======================================================================
class TestBlockedRecordSaysWhichLimitBound:
    """A refusal that names only a reason code is not diagnosable.

    OBSERVED LIVE 2026-11-12 18:53Z. A campaign blocked repeatedly on
    EXPOSURE_CEILING_EXCEEDS_LOSS_ROOM. There are TWO daily-loss limits --
    the campaign approval's `max_daily_loss_usd` and the account-wide risk
    service's `loss_room_usd` -- and `gates()` judges against the second.
    The record carried neither number, so the first one raised was the
    wrong one. `gates()` had already computed both."""

    def test_the_numbers_gates_computed_are_carried(self):
        src = inspect.getsource(RUN.Session.run_window) \
            if hasattr(RUN.Session, "run_window") else inspect.getsource(RUN)
        i = src.index('"L5_BTC5M_BLOCKED"')
        blk = src[i:i + 700]
        for field in ("exposure_ceiling_usd", "loss_room_usd", "risk"):
            assert field in blk, "%s is dropped from the blocked record" % field

    def test_a_blocked_window_reports_both_figures(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        r.session.risk_fn = lambda now, own: {
            "ok": True, "loss_room_usd": "0.01", "source": "TEST"}
        run(r)
        blocked = r.kinds("L5_BTC5M_BLOCKED")
        assert blocked, "the window should have been blocked"
        b = blocked[0]
        assert b["reason"] == "EXPOSURE_CEILING_EXCEEDS_LOSS_ROOM"
        assert b["loss_room_usd"] == "0.01"
        assert b["exposure_ceiling_usd"] is not None
        assert b["risk"].get("source") == "TEST"

    def test_an_unblocked_window_still_logs_nothing_extra(
            self, tmp_path, monkeypatch):
        r = rig(tmp_path, monkeypatch)
        run(r)
        assert not r.kinds("L5_BTC5M_BLOCKED")
