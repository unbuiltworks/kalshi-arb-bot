"""The proposed continuous configuration, at its real sizes, THROUGH THE RUNNER.

OWNER TARGETS, 2026-11-11 (proposed, not active): $10 cash, $10 aggregate
exposure, four concurrent rounds, 32 rounds a window, eight resting orders,
12 round allocations, a $1 campaign loss budget, a $5 daily halt, 0.96
opening and $1.00 completion ceilings, maker-only.

WHAT THESE PIN, in the order the owner asked for them:

  1. four opening rounds active at once, when the loss bound permits it;
  2. eight resting orders tracked by exact id and cancellable;
  3. repeated pair completion and replenishment;
  4. thirty-two submission-charged rounds without early exhaustion;
  5. partial fills and stranded inventory;
  6. cash and worst-case loss under EVERY combination of resting fills;
  7. campaign-loss and daily-loss shutdown;
  8. restart and durable reconciliation.

THE NUMBER THAT ACTUALLY SIZES THIS IS NOT THE $10. The worst-case deficit
bound (`L5_MAX_WORST_CASE_DEFICIT_USD`, $1.00 in production) counts every
resting order as though its side fills alone. At $10 in 12 parts a clip is
0.56 contracts and the FOURTH concurrent round is refused by that bound
(three rounds, six orders); at $7.00 the clip is 0.40 and all four rest.
Both are tested. The bound includes two cents of fee reservation an order.

Harness: `test_l5_runner_pair_and_cancel_state.py` -- a real Runner, a
FaithfulExecutor, a scripted venue and book. No network, no live order.
"""
import itertools
import os
import tempfile
from decimal import Decimal as D

import pytest

import tests.test_l5_cash_recycle_on_pair as RC
import tests.test_l5_ladder_runner as LAD
import tests.test_l5_runner_pair_and_cancel_state as T
import tests.test_l5_ten_dollar_campaign_bound as CB
from src import l5_live_execution as E
from src import l5_live_validation_gates as VG
from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS
from src import l5_unmatched_disposition as UD

SLUG = LAD.SLUG
PROPOSED = {
    "L5_MAX_CASH_DEPLOYED_USD": "10", "L5_TS_ROUND_ALLOCATIONS": "12",
    "L5_TS_CONCURRENT_ROUNDS": "4", "L5_MAX_LIVE_ORDERS": "8",
    "L5_ACC_MAX_ROUNDS": "32",
    "L5_TS_ROUND_CHARGED_ON_SUBMISSION": "ON",
    "L5_REOPEN_FLOOR_PER_CONTRACT": "ON", "L5_HEDGE_CASH_AT_LIMIT": "ON",
    "L5_CASH_POLICY": "RECYCLE_ON_COMPLETE",
    UD.OWN_EVIDENCE_GATE_ENV: "WAIVED_BY_OWNER"}
#: the same, with the envelope that lets four rounds fit the $1.00 loss bound
FOUR = dict(PROPOSED, L5_MAX_CASH_DEPLOYED_USD="7.00")
DEFICIT = D("1.00")                       # production's worst-case bound
CENT = D("0.01")


def limits(h):
    h.r.limits = E.SafetyLimits(
        max_usd_per_market=D("20"), max_abs_residual=D("10"),
        max_total_exposure_usd=D("10"), max_concurrent_markets=1,
        max_live_orders=R.max_live_orders(),
        daily_realized_loss_halt_usd=D("5.00"))
    h.r.executor.limits = h.r.limits
    return h


def harness(tmp_path, monkeypatch, env=PROPOSED, **over):
    h = limits(LAD.harness(tmp_path, monkeypatch, **dict(env, **over)))
    LAD.seed(h, 30, D("-0.02"))           # a losing record; the gate waived
    h.book.set("0.50", "0.51")
    # a venue balance that nets completed pairs, read after every tick --
    # recycled cash is credited only once such a read confirms it
    return RC.with_venue_cash(h)


def rest(h, ticks=14):
    """Open and let the rounds rest, filling nothing."""
    h.at(6.5).l5_action()
    off = 6.8
    for _ in range(ticks):
        off += 1.0
        h.tick(off)
    return h


def refusals(h, needle):
    return sum(v for k, v in h.r.stats.items()
               if k.startswith("controller_open:") and needle in k)


def live(h):
    return h.r.store.live_orders()


def worst_loss(h):
    """The most the book can lose over EVERY subset of resting orders
    filling, from the ledger: cost - min(up, dn), exact, fees aside."""
    st = h.r.store
    lc = st.leg_cost(SLUG)
    up0, dn0 = st.position(SLUG, "UP"), st.position(SLUG, "DOWN")
    c0 = D(str(lc.get("UP", 0))) + D(str(lc.get("DOWN", 0)))
    orders = [(o["side"], D(str(o["remaining"])), D(str(o["price"])))
              for o in st.live_orders().values()]
    worst = D(0)
    for r in range(len(orders) + 1):
        for sub in itertools.combinations(orders, r):
            up = up0 + sum(q for s, q, _p in sub if s == "UP")
            dn = dn0 + sum(q for s, q, _p in sub if s == "DOWN")
            worst = max(worst, c0 + sum(q * p for _s, q, p in sub)
                        - min(up, dn))
    return worst


def held(h):
    ex = h.r.executor
    return ex.validation_gates.cash_committed(ex.cash_legs_for_gates())


# ======================================================================
# 0. THE PROPOSED VALUES ARE READ, NOT CLAMPED
# ======================================================================

class TestTheValuesAreAccepted:

    def test_twelve_allocations_is_twelve(self, monkeypatch):
        monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "12")
        assert TS.round_allocations() == 12

    def test_four_rounds_thirty_two_and_eight_orders(self, tmp_path,
                                                     monkeypatch):
        h = harness(tmp_path, monkeypatch)
        assert TS.concurrent_rounds() == 4
        assert h.r.executor.acc_max_rounds() == 32
        assert R.max_live_orders() == 8 == h.r.limits.max_live_orders

    def test_the_clip_is_the_envelope_in_twelve_parts(self, tmp_path,
                                                      monkeypatch):
        h = rest(harness(tmp_path, monkeypatch))
        assert {q for _s, _p, q in h.sent()} <= {D("0.56"), D("0.57")}


# ======================================================================
# 1. FOUR OPENING ROUNDS AT ONCE, WHEN THE LOSS BOUND PERMITS
# ======================================================================

class TestFourRoundsAtOnce:

    def test_four_rounds_rest_together(self, tmp_path, monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        ups, dns = LAD.resting(h, "UP"), LAD.resting(h, "DOWN")
        assert len(ups) == len(dns) == 4, (ups, dns)
        assert len({p for _s, p in ups}) == 4      # four price levels a side
        assert len({p for _s, p in dns}) == 4
        assert h.r.executor.round_lifecycle(SLUG)[
            "rounds_with_a_submitted_order"] == 4
        # each round's pair is inside the opening ceiling, level by level
        for (_u, up), (_d, dn) in zip(sorted(ups, reverse=True),
                                      sorted(dns, reverse=True)):
            assert up + dn <= D("0.96")

    def test_at_ten_dollars_the_loss_bound_stops_the_fourth(self, tmp_path,
                                                            monkeypatch):
        """NOT the order limit and NOT cash: three rounds of 0.56 already
        put $0.80 at risk if one side fills alone."""
        h = rest(harness(tmp_path, monkeypatch))
        assert len(LAD.resting_ids(h)) == 6
        assert refusals(h, "WORST_CASE_DEFICIT") > 0
        assert refusals(h, "MAX_LIVE_ORDERS") == 0
        assert refusals(h, "CASH_COMMITMENT") == 0

    def test_a_ninth_order_is_never_sent(self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch, FOUR)
        h.at(6.5).l5_action()
        off, most = 6.8, 0
        for _ in range(60):
            off += 1.0
            h.tick(off)
            most = max(most, len(LAD.resting_ids(h)))
        assert most == 8


# ======================================================================
# 2. EIGHT RESTING ORDERS, TRACKED AND CANCELLABLE BY EXACT ID
# ======================================================================

class TestEightOrders:

    def test_each_is_tracked_with_its_venue_id(self, tmp_path, monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        rows = live(h)
        assert len(rows) == 8
        vids = {str(o["venue_order_id"]) for o in rows.values()}
        assert len(vids) == 8 and vids == set(LAD.resting_ids(h))
        coids = [s["client_order_id"] for s in h.venue.sent]
        assert len(coids) == len(set(coids)) == 8
        out = h.r.executor.outstanding_for_gates()
        assert len(out) == 8 and {r["market"] for r in out} == {SLUG}

    def test_reconciliation_agrees_with_the_venue(self, tmp_path,
                                                  monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        h.r.store.reconcile_from_venue(h.venue.positions(),
                                       h.venue.open_orders())
        assert h.r.store.orders_uncertain() is None
        assert len(live(h)) == 8

    def test_all_eight_cancel_by_exact_id_and_are_proved_gone(
            self, tmp_path, monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        ids = sorted(live(h))
        out = E.deadman(h.r.store, h.r.transport, reason="test_cancel_all",
                        only=ids)
        assert len(out["cancelled"]) == 8, out
        assert not (out.get("still_open") or out.get("unverified")
                    or out.get("errors"))
        assert live(h) == {} and LAD.resting_ids(h) == []
        assert h.r.store.orders_uncertain() is None
        assert h.r.executor.outstanding_for_gates() == []

    def test_cancelling_some_leaves_the_others_exactly(self, tmp_path,
                                                       monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        ids = sorted(live(h))
        E.deadman(h.r.store, h.r.transport, reason="test_cancel_three",
                  only=ids[:3])
        assert sorted(live(h)) == ids[3:]


# ======================================================================
# 3. REPEATED COMPLETION AND REPLENISHMENT
# ======================================================================

class TestReplenishment:

    def cycle(self, h, every=3, until=470.0):
        h.at(6.5).l5_action()
        off, n, peak_cash, peak_loss = 6.8, 0, D(0), D(0)
        while off < until:
            n += 1
            if n % every == 0:
                for oid in LAD.resting_ids(h):
                    h.venue.fill(oid)
            off += 3.5
            h.tick(off)
            peak_cash = max(peak_cash, held(h))
            peak_loss = max(peak_loss, worst_loss(h))
        return peak_cash, peak_loss

    def test_pairs_keep_completing_and_reopening(self, tmp_path,
                                                 monkeypatch):
        h = harness(tmp_path, monkeypatch)
        peak_cash, peak_loss = self.cycle(h)
        rl = h.r.executor.round_lifecycle(SLUG)
        assert rl["rounds_with_a_submitted_order"] >= 10, rl
        assert h.pos("UP") == h.pos("DOWN") > D("5")
        assert len(h.venue.sent) == 2 * rl["rounds_with_a_submitted_order"]
        assert rl["rounds_retired_without_an_order"] == 0
        # completed pairs gave their cash back -- once a balance read had
        # confirmed it. Until then the cap binds, which is the point.
        assert h.r.stats["recycle_check:CONFIRMED"] > 0
        assert peak_cash <= D("10") and peak_loss <= DEFICIT + CENT

    def test_every_pair_cleared_the_opening_ceiling(self, tmp_path,
                                                    monkeypatch):
        h = harness(tmp_path, monkeypatch)
        self.cycle(h)
        fills = {}
        for o in h.venue.sent:
            fills.setdefault(str(o["side"]), []).append(D(str(o["price"])))
        assert len(fills["UP"]) == len(fills["DOWN"])
        # dearest UP with dearest DOWN is the worst pairing that can occur
        assert max(fills["UP"]) + max(fills["DOWN"]) <= D("0.96")

    def test_without_recycling_the_cap_is_what_stops_it(self, tmp_path,
                                                        monkeypatch):
        h = harness(tmp_path, monkeypatch, L5_CASH_POLICY="")
        self.cycle(h)
        assert refusals(h, "CASH_COMMITMENT") > 0
        assert sum(D(str(o["price"])) * D(str(o["qty"]))
                   for o in h.venue.sent) <= D("10")


# ======================================================================
# 4. THIRTY-TWO ROUNDS, CHARGED ON SUBMISSION
# ======================================================================

class TestThirtyTwoRounds:

    def test_refused_proposals_in_between_spend_none_of_them(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        ex = h.r.executor
        real = ex.probe_reopen_decision
        refuse = lambda *a, **k: {"reopen": False,          # noqa: E731
                                  "reason": "INCREMENTAL_EXPECTANCY_BELOW_"
                                            "FLOOR"}
        books = [("0.50", "0.51"), ("0.58", "0.59"), ("0.44", "0.45"),
                 ("0.53", "0.54")]
        h.at(6.5).l5_action()
        off, n = 6.8, 0
        while off < 470:
            n += 1
            # a third of the time the economics are absent and the book moves
            blocked = (n // 6) % 3 == 1
            monkeypatch.setattr(ex, "probe_reopen_decision",
                                refuse if blocked else real)
            if blocked:
                h.book.set(*books[n % len(books)])
            else:
                h.book.set("0.50", "0.51")
                for oid in LAD.resting_ids(h):
                    h.venue.fill(oid)
            off += 3.5
            h.tick(off)
        rl = ex.round_lifecycle(SLUG)
        assert rl["rounds_with_a_submitted_order"] == 32, rl
        assert rl["legs_submitted"] == 64
        assert rl["rounds_retired_without_an_order"] == 0
        assert rl["rounds_left"] == 0 and rl["next_round"] is None
        assert rl["proposals_repriced_since_start"] > 0
        assert refusals(h, "ACCUMULATION_EXHAUSTED") > 0

    def test_the_thirty_third_pair_is_never_sent(self, tmp_path,
                                                 monkeypatch):
        h = harness(tmp_path, monkeypatch)
        h.at(6.5).l5_action()
        off = 6.8
        while off < 470:
            for oid in LAD.resting_ids(h):
                h.venue.fill(oid)
            off += 3.5
            h.tick(off)
        assert len(h.venue.sent) == 64


# ======================================================================
# 5. PARTIAL FILLS AND STRANDED INVENTORY
# ======================================================================

class TestPartialAndStranded:

    def test_a_partial_fill_is_booked_and_its_complement_keeps_working(
            self, tmp_path, monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        oid = LAD.resting_ids(h, "UP")[0]
        h.venue.fill(oid, qty=D("0.20"))
        LAD.book_fills(h, 22)
        assert h.pos("UP") == D("0.20") and h.pos("DOWN") == 0
        assert h.r.store.orders_uncertain() is None
        # the rest of that order is still at the venue, still counted
        assert any(D(str(o["remaining"])) < D(str(o["qty"]))
                   for o in live(h).values())
        down_resting = sum(D(str(o["remaining"])) for o in live(h).values()
                           if o["side"] == "DOWN")
        assert down_resting >= D("0.20")           # the hedge is working
        assert worst_loss(h) <= DEFICIT + CENT

    def test_one_side_filling_alone_stays_inside_the_loss_bound(
            self, tmp_path, monkeypatch):
        """THE WORST REACHABLE STATE: every resting UP order fills and no
        DOWN does, tick after tick."""
        h = harness(tmp_path, monkeypatch, FOUR)
        h.at(6.5).l5_action()
        off, peak, peak_unm = 6.8, D(0), D(0)
        for _ in range(60):
            for oid in LAD.resting_ids(h, "UP"):
                h.venue.fill(oid)
            off += 3.5
            h.tick(off)
            peak = max(peak, worst_loss(h))
            peak_unm = max(peak_unm, h.pos("UP") - h.pos("DOWN"))
        assert h.pos("DOWN") == 0
        assert peak <= DEFICIT + CENT, peak
        assert peak_unm <= D("5")                  # L5_MAX_UNMATCHED_CONTRACTS
        # and it stopped adding: UP orders sent never exceed four clips
        assert sum(1 for s, _p, _q in h.sent() if s == "UP") <= 4

    def test_stranded_inventory_keeps_its_cash_charge(self, tmp_path,
                                                      monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        for oid in LAD.resting_ids(h, "UP")[:2]:
            h.venue.fill(oid)
        LAD.book_fills(h, 22)
        unmatched = h.pos("UP") - h.pos("DOWN")
        assert unmatched > 0
        assert h.r.executor.matched_pair_cost(SLUG) == 0
        assert held(h) == h.r.store.committed()[SLUG] > 0

    def test_no_second_hedge_is_stacked_on_a_working_one(self, tmp_path,
                                                         monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        h.venue.fill(LAD.resting_ids(h, "UP")[0])
        for i in range(12):
            h.tick(22.0 + 3.5 * i)
        unmatched = h.pos("UP") - h.pos("DOWN")
        down = sum(D(str(o["remaining"])) for o in live(h).values()
                   if o["side"] == "DOWN")
        # resting DOWN quantity is the four rounds' own legs, never more
        assert down <= D("4") * D("0.40") and unmatched <= D("0.40")
        assert len(LAD.resting_ids(h)) <= 8


# ======================================================================
# 6. EVERY COMBINATION OF RESTING FILLS
# ======================================================================

class TestEveryFillCombination:

    @pytest.mark.parametrize("env,count", [(FOUR, 8), (PROPOSED, 6)])
    def test_no_subset_of_fills_breaches_cash_or_the_loss_bound(
            self, tmp_path, monkeypatch, env, count):
        h = rest(harness(tmp_path, monkeypatch, env))
        orders = [(o["side"], D(str(o["remaining"])), D(str(o["price"])))
                  for o in live(h).values()]
        assert len(orders) == count
        cap = h.r.executor.validation_gates.max_cash_deployed_usd
        worst = (D(0), None)
        for r in range(count + 1):
            for sub in itertools.combinations(orders, r):
                up = sum(q for s, q, _p in sub if s == "UP")
                dn = sum(q for s, q, _p in sub if s == "DOWN")
                cost = sum(q * p for _s, q, p in sub)
                loss = cost - min(up, dn)          # what settles worst
                assert cost <= cap
                if loss > worst[0]:
                    worst = (loss, sub)
        assert worst[0] <= DEFICIT, worst
        # the worst case IS one side filling alone, and it is material
        assert {s for s, _q, _p in worst[1]} in ({"UP"}, {"DOWN"})
        assert worst[0] > D("0.70")


# ======================================================================
# 7. CAMPAIGN-LOSS AND DAILY-LOSS SHUTDOWN
# ======================================================================

def campaign_gate(budget="1.00", base="-16.0108"):
    return VG.ValidationGates(
        max_cash_deployed_usd=D("10"), max_unmatched_contracts=D("5"),
        max_cumulative_loss_usd=D("50"),
        campaign_baseline=CB._baseline(tempfile.mkdtemp(), realized=base),
        campaign_id=CB.CID, max_campaign_additional_loss_usd=D(budget),
        cash_policy=VG.RECYCLE_ON_COMPLETE)


def ask(g, side, qty, px, inv, rest=(), realized="-16.0108"):
    return g.refuse(
        order_qty=D(qty), order_price=D(px),
        order_intent=("ORDER_INTENT_BUY_SHORT" if side == "DOWN"
                      else "ORDER_INTENT_BUY_LONG"),
        inventory=dict(inv, market="m"), outstanding=list(rest),
        cumulative_realized=D(realized))


FLAT = {"up_qty": "0", "up_cost": "0", "dn_qty": "0", "dn_cost": "0"}


def leg(side, qty, px):
    return {"qty": D(qty), "price": D(px), "market": "m",
            "intent": ("ORDER_INTENT_BUY_SHORT" if side == "DOWN"
                       else "ORDER_INTENT_BUY_LONG")}


class TestLossShutdown:

    def test_a_fresh_campaign_may_open_a_clip(self):
        g = campaign_gate()
        assert ask(g, "UP", "0.56", "0.48", FLAT,
                   [leg("DOWN", "0.56", "0.47")]) is None

    @pytest.mark.parametrize("lost,allowed", [
        ("0.30", True), ("0.60", True), ("0.75", False), ("1.00", False),
        ("1.40", False)])
    def test_new_pairs_stop_as_the_budget_is_spent(self, lost, allowed):
        """Settled campaign loss plus the new pair's own worst case must
        stay under $1.00. A 0.56 clip risks about $0.29 alone."""
        g = campaign_gate()
        why = ask(g, "UP", "0.56", "0.48", FLAT,
                  [leg("DOWN", "0.56", "0.47")],
                  realized=str(D("-16.0108") - D(lost)))
        assert (why is None) == allowed, why
        if not allowed:
            assert why.startswith("CAMPAIGN_ADDITIONAL_LOSS_LIMIT:")

    def test_a_hedge_is_never_what_the_budget_refuses(self):
        g = campaign_gate()
        strand = {"up_qty": "0.56", "up_cost": "0.2688",
                  "dn_qty": "0", "dn_cost": "0"}
        for lost in ("0", "0.60", "0.95"):
            assert ask(g, "DOWN", "0.56", "0.50", strand,
                       realized=str(D("-16.0108") - D(lost))) is None

    def test_a_gain_buys_no_extra_room(self):
        g = campaign_gate()
        assert ask(g, "UP", "2.50", "0.48", FLAT,
                   realized=str(D("-16.0108") + D("5"))
                   ).startswith("CAMPAIGN_ADDITIONAL_LOSS_LIMIT:")

    def test_the_daily_halt_stops_every_new_order(self, tmp_path,
                                                  monkeypatch):
        h = harness(tmp_path, monkeypatch, FOUR)
        monkeypatch.setattr(type(h.r.store), "realized_current_utc_day",
                            lambda self: D("-5.00"))
        rest(h)
        assert h.venue.sent == []
        assert any("DAILY_REALIZED_LOSS_HALT" in k and v
                   for k, v in h.r.stats.items())

    def test_one_cent_short_of_the_daily_halt_still_trades(self, tmp_path,
                                                           monkeypatch):
        h = harness(tmp_path, monkeypatch, FOUR,
                    L5_MAX_CUMULATIVE_LOSS_USD="50")
        monkeypatch.setattr(type(h.r.store), "realized_current_utc_day",
                            lambda self: D("-4.99"))
        rest(h)
        assert len(h.venue.sent) >= 2


# ======================================================================
# 8. RESTART AND DURABLE RECONCILIATION
# ======================================================================

class TestRestart:

    def restarted(self, h, tmp_path, monkeypatch):
        again = T.Harness.__new__(T.Harness)
        again.emitted, again.t = [], h.t
        again.book, again.venue, again._seq = h.book, h.venue, h._seq
        monkeypatch.setattr(R, "emit", lambda *a, **k: None)
        again.r = R.Runner(root=str(tmp_path / "live"), book=h.book,
                           feed=lambda: [])
        again.attach()
        return limits(again)

    def test_all_eight_orders_are_found_by_exact_id(self, tmp_path,
                                                    monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        before = {str(o["venue_order_id"]) for o in live(h).values()}
        again = self.restarted(h, tmp_path, monkeypatch)
        after = {str(o["venue_order_id"]) for o in live(again).values()}
        assert after == before and len(after) == 8

    def test_no_duplicate_and_no_ninth_order_after_a_restart(
            self, tmp_path, monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        again = self.restarted(h, tmp_path, monkeypatch)
        for off in (22.0, 23.0, 24.0, 25.0, 26.0):
            again.tick(off)
        coids = [s["client_order_id"] for s in h.venue.sent]
        assert len(coids) == len(set(coids)) == 8

    def test_fills_while_down_are_booked_and_the_rounds_survive(
            self, tmp_path, monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        for oid in LAD.resting_ids(h):
            h.venue.fill(oid)
        again = self.restarted(h, tmp_path, monkeypatch)
        LAD.book_fills(again, 22.0)
        assert again.pos("UP") == again.pos("DOWN") > D("1.5")
        assert again.r.store.orders_uncertain() is None
        rl = again.r.executor.round_lifecycle(SLUG)
        assert rl["rounds_with_a_submitted_order"] >= 4     # from the claims
        assert rl["rounds_retired_without_an_order"] == 0

    def test_the_window_record_survives_a_window_close(self, tmp_path,
                                                       monkeypatch):
        h = rest(harness(tmp_path, monkeypatch, FOUR))
        for oid in LAD.resting_ids(h):
            h.venue.fill(oid)
        LAD.book_fills(h, 22.0)
        cy = h.r.cycle_report(SLUG)
        assert cy.get("error") is None
        assert cy["rounds"]["rounds_with_a_submitted_order"] >= 4
        assert D(cy["inventory"]["average_pair_cost"]) <= D("0.96")
        assert D(cy["inventory"]["unmatched_contracts"]) == 0


# ======================================================================
# 9. THE SAME, STEERED BY THE BALANCE CONTROLLER -- AS A CAMPAIGN RUNS IT
# ======================================================================

STEER = {"L5_PORTFOLIO_CONTROLLER": "STEER", "L5_PORTFOLIO_PROFILE": "BALANCE"}


def steered(h):
    """`Runner.tick` runs the controller's withdraw stage after the
    completion requote; the shared harness tick stops short of it. Add it,
    so these ticks do what production's do."""
    base = h.tick

    def tick(offset_s, poll=True):
        out = base(offset_s, poll)
        h.r.maybe_portfolio_withdraw(h.t)
        return out
    h.tick = tick
    return h


class TestUnderTheBalanceController:

    def test_four_rounds_still_rest_together(self, tmp_path, monkeypatch):
        h = rest(steered(harness(tmp_path, monkeypatch, dict(FOUR, **STEER))),
                 ticks=20)
        assert len(LAD.resting(h, "UP")) == len(LAD.resting(h, "DOWN")) == 4
        assert len(h.venue.sent) == 8              # nothing was churned

    def test_pairs_complete_and_are_replenished(self, tmp_path, monkeypatch):
        h = steered(harness(tmp_path, monkeypatch, dict(FOUR, **STEER)))
        peak_cash, peak_loss = TestReplenishment().cycle(h)
        rl = h.r.executor.round_lifecycle(SLUG)
        assert rl["rounds_with_a_submitted_order"] >= 10, rl
        assert h.pos("UP") == h.pos("DOWN") > D("4")
        assert rl["rounds_retired_without_an_order"] == 0
        assert peak_loss <= DEFICIT and peak_cash <= D("7.00")

    def test_one_unmatched_clip_is_hedged_first_and_never_compounded(
            self, tmp_path, monkeypatch):
        """One UP order fills and nothing else does. The held side's other
        openings are withdrawn by exact id; once they are, what is left on
        UP, held plus resting, never exceeds two clips. UNTIL they are, all
        four UP legs are still at the venue and can fill -- which is why the
        bound that matters is the worst case over every fill, checked on
        every tick, and not the withdrawal."""
        h = steered(harness(tmp_path, monkeypatch, dict(FOUR, **STEER)))
        h.at(6.5).l5_action()
        off, peak, most_up = 6.8, D(0), D(0)
        for i in range(40):
            if i == 4:
                h.venue.fill(LAD.resting_ids(h, "UP")[0])
            off += 3.5
            h.tick(off)
            peak = max(peak, worst_loss(h))
            if i > 8:           # the fill is booked and the withdrawals are in
                up_rest = sum(D(str(o["remaining"]))
                              for o in live(h).values() if o["side"] == "UP")
                most_up = max(most_up, h.pos("UP") + up_rest)
            assert len(LAD.resting_ids(h)) <= 8
        assert D("0.38") < h.pos("UP") <= D("0.40") and h.pos("DOWN") == 0
        assert peak <= DEFICIT
        assert most_up <= D("2") * D("0.40")
        assert any(o["side"] == "DOWN" for o in live(h).values())  # the hedge
        assert h.r.store.orders_uncertain() is None
        assert len(h.venue.cancelled) >= 3          # by exact id, verified


# ======================================================================
# 10. PRODUCTION'S RESIDUAL CAP IS THE REAL CONCURRENCY DIAL
# ======================================================================

class TestTheResidualCapDecidesHowManyRoundsRest:
    """`PMUS_RESIDUAL_CAP_K` is 1 in production and absent from the shared
    harness. It counts every resting order on a side as though it fills, so
    it -- not `L5_TS_CONCURRENT_ROUNDS` and not the order limit -- decides
    how many rounds may rest together, and with them how much one side
    filling alone can strand."""

    def run(self, tmp_path, monkeypatch, k):
        env = dict(FOUR, PMUS_RESIDUAL_CAP_K=k, **STEER)
        a = rest(steered(harness(tmp_path / "rest", monkeypatch, env)),
                 ticks=20)
        b = steered(harness(tmp_path / "one", monkeypatch, env))
        b.at(6.5).l5_action()
        off, loss, unm = 6.8, D(0), D(0)
        for _ in range(40):
            for oid in LAD.resting_ids(b, "UP"):
                b.venue.fill(oid)
            off += 3.5
            b.tick(off)
            loss = max(loss, worst_loss(b))
            unm = max(unm, b.pos("UP") - b.pos("DOWN"))
        return len(LAD.resting_ids(a)), unm, loss

    @pytest.mark.parametrize("k,orders,clips,most", [
        ("1", 2, 1, "0.20"), ("2", 4, 2, "0.40"), ("4", 8, 4, "0.75")])
    def test_k_clips_rest_and_k_clips_can_strand(self, tmp_path, monkeypatch,
                                                 k, orders, clips, most):
        resting, unmatched, loss = self.run(tmp_path, monkeypatch, k)
        assert resting == orders
        assert D(clips) * D("0.38") < unmatched <= D(clips) * D("0.40")
        assert loss <= D(most)
        assert loss <= DEFICIT

    def test_at_production_s_one_clip_pairs_still_replenish(self, tmp_path,
                                                            monkeypatch):
        """One pair at a time is still continuous: it completes, its cash
        comes back, and the next one opens -- with one clip the most that
        can ever be unmatched."""
        h = steered(harness(tmp_path, monkeypatch,
                            dict(FOUR, PMUS_RESIDUAL_CAP_K="1", **STEER)))
        peak_cash, peak_loss = TestReplenishment().cycle(h)
        rl = h.r.executor.round_lifecycle(SLUG)
        assert rl["rounds_with_a_submitted_order"] >= 10, rl
        assert h.pos("UP") == h.pos("DOWN") > D("3")
        assert peak_loss <= D("0.20")


# ======================================================================
# 11. QUOTES THAT MOVE: A 15-SECOND LIFETIME, SIXTEEN RE-PLACEMENTS A LEG
# ======================================================================

class TestQuotesFollowTheBook:
    """OWNER, 2026-11-11: "Rest orders 2 ticks below the bid. Move it
    constantly." Both knobs already exist: `L5_TS_ORDER_TTL_S` (shorter
    only) and `L5_ACC_MAX_ATTEMPTS` (1 to 16). On 2026-11-11 0400z three UP
    quotes sat at 0.45 to 0.47 for 120 seconds while the bid went to 0.71."""
    MOVING = dict(PROPOSED, L5_MAX_CASH_DEPLOYED_USD="0.90",
                  L5_TS_ROUND_ALLOCATIONS="3", L5_TS_CONCURRENT_ROUNDS="1",
                  L5_MAX_LIVE_ORDERS="4", L5_TS_ORDER_TTL_S="15",
                  L5_ACC_MAX_ATTEMPTS="16")

    def drift(self, h, ticks=110):
        """Nothing fills; the bid rises a cent every fifteen seconds."""
        h.at(6.5).l5_action()
        off, bid, trail = 6.8, D("0.50"), []
        for i in range(ticks):
            if i % 5 == 4:
                bid += D("0.01")
                h.book.set(str(bid), str(bid + D("0.01")))
            off += 3.0
            h.tick(off)
            for s, p in LAD.resting(h, "UP"):
                trail.append((bid, p))
        return bid, trail

    def test_an_unfilled_quote_is_cancelled_and_re_placed_at_the_book(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch, self.MOVING)
        bid, trail = self.drift(h)
        ups = [p for s, p, _q in h.sent() if s == "UP"]
        assert ups[0] == D("0.48") and max(ups) >= bid - D("0.06")
        assert ups == sorted(ups)                       # it only follows up
        # a resting UP quote is never more than a few ticks behind the bid
        assert max(b - p for b, p in trail) <= D("0.08")
        assert len(h.venue.cancelled) >= 20
        assert len(h.venue.sent) - len(h.venue.cancelled) == len(
            LAD.resting_ids(h))
        assert h.r.store.orders_uncertain() is None
        assert h.pos("UP") == h.pos("DOWN") == 0

    def test_at_the_default_lifetime_the_quote_is_left_behind(
            self, tmp_path, monkeypatch):
        slow = dict(self.MOVING)
        slow.pop("L5_TS_ORDER_TTL_S")
        slow.pop("L5_ACC_MAX_ATTEMPTS")
        h = harness(tmp_path, monkeypatch, slow)
        bid, trail = self.drift(h, ticks=38)            # under 120 seconds
        assert [p for s, p, _q in h.sent() if s == "UP"] == [D("0.48")]
        assert max(b - p for b, p in trail) >= D("0.09")

    def test_every_re_placement_is_a_fresh_exact_id(self, tmp_path,
                                                    monkeypatch):
        h = harness(tmp_path, monkeypatch, self.MOVING)
        self.drift(h)
        coids = [s["client_order_id"] for s in h.venue.sent]
        assert len(coids) == len(set(coids)) > 20
        rl = h.r.executor.round_lifecycle(SLUG)
        assert rl["rounds_retired_without_an_order"] == 0
        assert rl["rounds_with_a_submitted_order"] <= 32
