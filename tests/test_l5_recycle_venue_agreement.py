"""Recycled cash is credited only where the venue's buying power agrees.

OWNER, 2026-11-11: "prove that recycled capital agrees with venue-confirmed
buying power and that partial fills, stale order states, restarts, and
reconciliation uncertainty cannot release unearned cash."

THE RULE (`Runner.confirm_recycled_cash`). A completed pair's cost leaves
the cash figure only for the matched quantity that a balance read taken
AFTERWARDS has confirmed, by comparing buying power with an anchor taken
while the account was flat and empty:

    buying power >= anchor - cost of unmatched inventory - one cent

The venue here is scripted from ITS OWN fills (`VenueCash`), so the two
sides of the comparison are independent: `nets=True` is the venue as it was
observed on 2026-11-11, `nets=False` a venue that keeps every fill's cash.

The owner's configuration throughout: $0.90 cash, three allocations, one
round at a time, sixteen rounds, the own-record gate waived.
"""
from decimal import Decimal as D

import pytest

import tests.test_l5_cash_recycle_on_pair as RC
import tests.test_l5_ladder_runner as LAD
import tests.test_l5_runner_pair_and_cancel_state as T
from src import l5_live_execution as E
from src import l5_pmus_live_runner as R

SLUG = LAD.SLUG
OWNER = dict(RC.RECYCLE, L5_ACC_MAX_ROUNDS="16", L5_TS_CONCURRENT_ROUNDS="1")
PAIR = D("0.20") * D("0.48") + D("0.20") * D("0.47")       # 0.190


def harness(tmp_path, monkeypatch, nets=True, **env):
    return RC.harness(tmp_path, monkeypatch, nets=nets, **dict(OWNER, **env))


def held(h):
    return RC.held(h)


def verdicts(h):
    return [e[1]["verdict"] for e in h.emitted if e[0] == "L5_RECYCLE_CHECK"]


def one_pair(h, read=True):
    """Open one pair and fill both legs at the venue; book the fills.
    Further openings are held back so the account is quiet afterwards --
    the moment a balance read can be compared with the anchor."""
    h.at(6.5).l5_action()
    for off in (6.8, 7.8):
        T.Harness.tick(h, off)                 # the base tick: no balance read
    h.r.executor.probe_reopen_decision = (
        lambda *a, **k: {"reopen": False, "reason": "TEST_HOLD"})
    for oid in LAD.resting_ids(h):
        h.venue.fill(oid)
    for i in range(4):
        T.Harness.tick(h, 9.0 + 3.5 * i)
    assert h.pos("UP") == h.pos("DOWN") == D("0.20")
    if read:
        RC.read_cash(h)
    return h


def run(h, until=470.0):
    h.at(6.5).l5_action()
    off, peak = 6.8, D(0)
    while off < until:
        for oid in LAD.resting_ids(h):
            h.venue.fill(oid)
        off += 3.5
        h.tick(off)
        peak = max(peak, held(h))
    return peak


# ======================================================================
# 1. THE VENUE AGREES: THE PAIR'S CASH IS RELEASED, AND SAYS WHY
# ======================================================================

class TestAVenueThatNets:

    def test_no_credit_before_a_read_taken_after_the_pair(self, tmp_path,
                                                          monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch), read=False)
        assert held(h) == PAIR                      # the old reading is stale
        assert h.r.executor.recycle_confirmed_share(SLUG) == 0

    def test_the_next_read_confirms_and_releases_it(self, tmp_path,
                                                    monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch))
        assert held(h) == 0
        assert verdicts(h)[-1] == "CONFIRMED"
        rec = [e[1] for e in h.emitted if e[0] == "L5_RECYCLE_CHECK"][-1]
        assert rec["confirmed"] == {SLUG: "0.20"}
        # buying power is back above where it started, by the pair's profit
        assert D(rec["buying_power_usd"]) == D(rec["anchor_usd"]) + D("0.010")
        assert D(rec["expected_at_least_usd"]) == D(rec["anchor_usd"]) - D(
            "0.01")

    def test_sixteen_rounds_are_sixteen_pairs_inside_ninety_cents(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        peak = run(h)
        rl = h.r.executor.round_lifecycle(SLUG)
        assert rl["rounds_with_a_submitted_order"] == 16, rl
        assert h.pos("UP") == h.pos("DOWN") == D("3.20")
        assert peak <= D("0.90")
        assert sum(D(str(o["price"])) * D(str(o["qty"]))
                   for o in h.venue.sent) == D("16") * PAIR      # $3.04 turned
        assert h.r.stats["recycle_check:CONFIRMED"] >= 3
        # the venue's own balance: every pair's cash back, plus its profit
        assert h.cash.value() == h.cash.start + D("16") * D("0.20") * D("0.05")


# ======================================================================
# 2. THE VENUE DISAGREES: NOTHING IS RELEASED
# ======================================================================

class TestAVenueThatKeepsTheCash:

    def test_the_read_is_below_the_line_and_confirms_nothing(self, tmp_path,
                                                             monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch, nets=False))
        assert held(h) == PAIR
        assert verdicts(h)[-1] == "BUYING_POWER_BELOW_EXPECTED_NOTHING_" \
                                  "CONFIRMED"
        rec = [e[1] for e in h.emitted if e[0] == "L5_RECYCLE_CHECK"][-1]
        assert D(rec["buying_power_usd"]) == D(rec["anchor_usd"]) - PAIR

    def test_the_window_stops_at_the_cash_cap_exactly_as_without_recycling(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch, nets=False)
        peak = run(h)
        assert h.r.executor.round_lifecycle(SLUG)[
            "rounds_with_a_submitted_order"] == 4
        assert held(h) == D("4") * PAIR and peak <= D("0.90")
        assert LAD.reasons(h, "CASH_COMMITMENT")
        assert h.r.stats["recycle_check:CONFIRMED"] == 0

    def test_a_disagreeing_read_withdraws_what_was_confirmed(self, tmp_path,
                                                             monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch))
        assert held(h) == 0
        h.cash.nets = False                        # the venue now says less
        RC.read_cash(h)
        assert held(h) == PAIR
        assert h.r.executor.__dict__["recycle_confirmed"] == {}


# ======================================================================
# 3. PARTIAL FILLS
# ======================================================================

class TestPartialFills:

    def half(self, h):
        h.at(6.5).l5_action()
        for off in (6.8, 7.8):
            T.Harness.tick(h, off)
        h.venue.fill(LAD.resting_ids(h, "UP")[0])
        h.venue.fill(LAD.resting_ids(h, "DOWN")[0], qty=D("0.08"))
        for i in range(4):
            T.Harness.tick(h, 9.0 + 3.5 * i)
        assert h.pos("UP") == D("0.20") and h.pos("DOWN") == D("0.08")
        return h

    def test_only_the_matched_part_of_a_partial_is_released(self, tmp_path,
                                                            monkeypatch):
        h = self.half(harness(tmp_path, monkeypatch))
        RC.read_cash(h)
        # an order is still at the venue: the reading is not comparable
        assert verdicts(h)[-1] == "ORDERS_AT_THE_VENUE_NOTHING_NEW"
        assert held(h) == D("0.20") * D("0.48") + D("0.08") * D("0.47")

    def test_quantity_matched_after_the_read_waits_for_the_next_one(
            self, tmp_path, monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch))
        assert held(h) == 0
        st = h.r.store                         # a further 0.20 a side fills
        st.apply_fill(SLUG, "UP", D("0.20"), D("0.48"), basis="OWN")
        st.apply_fill(SLUG, "DOWN", D("0.20"), D("0.47"), basis="OWN")
        assert h.r.executor.recycle_confirmed_share(SLUG) == D("0.5")
        assert held(h) == PAIR                  # half released, half not

    def test_an_unmatched_remainder_is_charged_and_expected_of_the_venue(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        h.at(6.5).l5_action()
        for off in (6.8, 7.8):
            T.Harness.tick(h, off)
        h.venue.fill(LAD.resting_ids(h, "UP")[0])
        for i in range(4):
            T.Harness.tick(h, 9.0 + 3.5 * i)
        E.deadman(h.r.store, h.r.transport, reason="test_quiet",
                  only=sorted(h.r.store.live_orders()))
        RC.read_cash(h)
        assert h.pos("UP") == D("0.20") and h.pos("DOWN") == 0
        assert held(h) == D("0.20") * D("0.48")
        assert h.r.executor.__dict__.get("recycle_confirmed") in ({}, None)


# ======================================================================
# 4. STALE ORDER STATES
# ======================================================================

class TestStaleOrderStates:

    def test_a_fill_the_ledger_has_not_booked_confirms_nothing(
            self, tmp_path, monkeypatch):
        """The venue has filled a further UP order; our ledger still shows
        only the completed pair and thinks nothing rests. Buying power is
        short of what the ledger expects, so every confirmation is dropped
        rather than the missing cash being explained away."""
        h = one_pair(harness(tmp_path, monkeypatch))
        assert held(h) == 0
        h.venue.submit({"client_order_id": "ghost", "side": "UP",
                        "price": "0.48", "qty": "0.20", "market": SLUG})
        h.venue.fill(h.venue.oid("UP"))              # filled at the venue
        RC.read_cash(h)
        assert verdicts(h)[-1].startswith("BUYING_POWER_BELOW_EXPECTED")
        assert held(h) == PAIR

    def test_an_order_parked_or_unverified_blocks_new_confirmations(
            self, tmp_path, monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch), read=False)
        monkeypatch.setattr(type(h.r.store), "still_fillable_order_count",
                            lambda self: 1)
        RC.read_cash(h)
        assert verdicts(h)[-1] == "ORDERS_AT_THE_VENUE_NOTHING_NEW"
        assert held(h) == PAIR


# ======================================================================
# 5. RESTART
# ======================================================================

class TestRestart:

    def restarted(self, h, tmp_path, monkeypatch):
        again = T.Harness.__new__(T.Harness)
        again.emitted, again.t = [], h.t
        again.book, again.venue, again._seq = h.book, h.venue, h._seq
        monkeypatch.setattr(R, "emit",
                            lambda *a, **k: again.emitted.append(a))
        again.r = R.Runner(root=str(tmp_path / "live"), book=h.book,
                           feed=lambda: [])
        again.attach()
        LAD.apply_limits(again)
        again.cash = h.cash
        return again

    def test_a_restart_remembers_no_confirmation(self, tmp_path,
                                                 monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch))
        assert held(h) == 0
        again = self.restarted(h, tmp_path, monkeypatch)
        assert again.pos("UP") == again.pos("DOWN") == D("0.20")
        assert again.r.executor.cash_recycles_completed_pairs() is True
        assert RC.held(again) == PAIR               # nothing carried over

    def test_and_cannot_re_confirm_without_having_seen_the_account_flat(
            self, tmp_path, monkeypatch):
        """The anchor is memory only. A process that starts with inventory
        has none, so even a venue that nets earns no credit until the
        market settles and the account is seen flat again."""
        h = one_pair(harness(tmp_path, monkeypatch))
        again = self.restarted(h, tmp_path, monkeypatch)
        again.r.available_cash_t = None
        again.r.refresh_available_cash(again.r.transport, again.t)
        assert again.r.stats["recycle_check:NO_ANCHOR_NOTHING_CONFIRMED"] == 1
        assert RC.held(again) == PAIR


# ======================================================================
# 6. RECONCILIATION UNCERTAINTY, AND AN UNREADABLE BALANCE
# ======================================================================

class TestUncertainty:

    def test_an_uncertain_ledger_drops_every_confirmation(self, tmp_path,
                                                          monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch))
        assert held(h) == 0
        monkeypatch.setattr(type(h.r.store), "orders_uncertain",
                            lambda self: "CANCEL_UNVERIFIED:v9")
        RC.read_cash(h)
        assert verdicts(h)[-1] == "LEDGER_UNCERTAIN_NOTHING_CONFIRMED"
        assert held(h) == PAIR
        # and the gate refuses every order on an uncertain book regardless
        ex = h.r.executor
        assert ex.validation_gates.refuse(
            order_qty=D("0.01"), order_price=D("0.5"),
            order_intent="ORDER_INTENT_BUY_LONG",
            book_uncertain=h.r.store.orders_uncertain()
        ).startswith("BOOK_UNCERTAIN:")

    def test_an_unreadable_balance_confirms_nothing_new(self, tmp_path,
                                                        monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch), read=False)
        h.cash.unreadable = True
        RC.read_cash(h)
        assert held(h) == PAIR
        assert h.r.available_cash_usd is None

    def test_an_exception_inside_the_check_confirms_nothing(self, tmp_path,
                                                            monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch))
        assert held(h) == 0

        def boom(self):
            raise OSError("ledger unreadable")
        monkeypatch.setattr(type(h.r), "_recycle_book", boom)
        RC.read_cash(h)
        assert held(h) == PAIR


# ======================================================================
# 7. THE ANCHOR
# ======================================================================

class TestTheAnchor:

    def test_a_settlement_moves_it_by_what_it_paid(self, tmp_path,
                                                   monkeypatch):
        h = one_pair(harness(tmp_path, monkeypatch))
        before = h.r._bp_anchor
        st = h.r.store
        st.state.setdefault("retired_markets", {})[SLUG] = {
            "t": "x", "settlement_pnl": "0.0100"}
        st.state["committed"].pop(SLUG, None)
        st.state["positions"] = {}
        h.cash.start = h.cash.value()              # the venue settles flat
        h.venue.by_oid.clear(); h.venue.cum.clear()
        RC.read_cash(h)
        # flat and empty again: re-anchored on the reading itself, which is
        # the old anchor plus exactly the settled profit
        assert h.r._bp_anchor == before + D("0.0100")

    def test_no_anchor_means_no_credit_ever(self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        h.r._bp_anchor = None
        one_pair(h)
        assert verdicts(h)[-1] == "NO_ANCHOR_NOTHING_CONFIRMED"
        assert held(h) == PAIR

    def test_the_anchor_is_only_taken_flat_and_empty(self, tmp_path,
                                                     monkeypatch):
        h = harness(tmp_path, monkeypatch)
        first = h.r._bp_anchor
        assert first == h.cash.start
        one_pair(h)
        assert h.r._bp_anchor == first             # not moved by the pair
