"""Cancelling and replacing a quote does not spend an opening round.

OWNER, 2026-11-11: "Fix round accounting so cancelling and replacing an
existing order does not consume a new opening round. A genuine new opening
must still consume a round."

WHAT IT WAS. With rounds resting side by side, a leg cancelled unfilled
cannot be re-placed inside its round (the attempt axis refuses while any
order is live in the market), so each re-quote arrived as a whole new
round. At a 15-second quote, 8 rounds were gone in under two minutes and
16 in about four, and the engine stopped on ACCUMULATION_EXHAUSTED with
nothing ever filled.

`L5_TS_REQUOTE_KEEPS_ROUND=ON` (default off): a round whose every order is
on record, finished at the venue and booked nothing is walked past without
being charged. A round that filled anything, that still has an order
working, or whose orders are not on record is charged exactly as before.
Submission budgets, exact-id cancellation and every other gate are
untouched; the walk is bounded.

`L5_INVENTORY_RECOVERY=ON` is covered at the end: the missing leg is
re-priced as a completion as soon as its other leg fills.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

import tests.test_l5_continuous_config as CC
import tests.test_l5_ladder_runner as LAD
from src import l5_live_execution as E
from src import l5_two_sided_probe as TS

KEEP = E.REQUOTE_KEEPS_ROUND_ENV
TWO = dict(CC.PROPOSED, L5_MAX_CASH_DEPLOYED_USD="0.90",
           L5_TS_ROUND_ALLOCATIONS="3", L5_TS_CONCURRENT_ROUNDS="2",
           L5_MAX_LIVE_ORDERS="4", L5_ACC_MAX_ROUNDS="8",
           PMUS_RESIDUAL_CAP_K="2", L5_TS_ORDER_TTL_S="15",
           L5_ACC_MAX_ATTEMPTS="16", **CC.STEER)


def drifting(tmp_path, monkeypatch, env, ticks=150):
    """Nothing fills; the bid rises a cent every fifteen seconds."""
    h = CC.steered(CC.harness(tmp_path, monkeypatch, env))
    h.at(6.5).l5_action()
    off, bid = 6.8, D("0.50")
    for i in range(ticks):
        if i % 5 == 4:
            bid += D("0.01")
            h.book.set(str(bid), str(bid + D("0.01")))
        off += 3.0
        h.tick(off)
    return h


def exhausted(h):
    return sum(v for k, v in h.r.stats.items()
               if "ACCUMULATION_EXHAUSTED" in k)


class TestTheSwitch:

    @pytest.mark.parametrize("raw", [None, "", "on", "1", "ON ", "YES"])
    def test_only_the_exact_token(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv(KEEP, raising=False)
        else:
            monkeypatch.setenv(KEEP, raw)
        assert E.requote_keeps_round() is False

    def test_the_exact_token(self, monkeypatch):
        monkeypatch.setenv(KEEP, "ON")
        assert E.requote_keeps_round() is True


class TestAReQuoteIsNotAnOpening:

    def test_off_reproduces_it_eight_rounds_gone_and_nothing_filled(
            self, tmp_path, monkeypatch):
        h = drifting(tmp_path, monkeypatch, TWO)
        rl = h.r.executor.round_lifecycle(CC.SLUG)
        assert h.pos("UP") == h.pos("DOWN") == 0
        assert rl["rounds_spent"] == 8 and rl["rounds_left"] == 0
        assert exhausted(h) > 20
        assert "rounds_requoted_without_a_fill" not in rl   # record unchanged

    def test_on_the_same_window_spends_no_round(self, tmp_path, monkeypatch):
        h = drifting(tmp_path, monkeypatch, dict(TWO, **{KEEP: "ON"}))
        rl = h.r.executor.round_lifecycle(CC.SLUG)
        assert h.pos("UP") == h.pos("DOWN") == 0
        assert rl["rounds_spent"] == 0 and rl["rounds_left"] == 8
        assert rl["rounds_requoted_without_a_fill"] >= 20
        assert exhausted(h) == 0

    def test_on_every_re_quote_is_a_fresh_id_cancelled_by_that_id(
            self, tmp_path, monkeypatch):
        h = drifting(tmp_path, monkeypatch, dict(TWO, **{KEEP: "ON"}))
        coids = [s["client_order_id"] for s in h.venue.sent]
        assert len(coids) == len(set(coids)) > 40
        assert len(h.venue.sent) - len(h.venue.cancelled) == len(
            LAD.resting_ids(h))
        assert h.r.store.orders_uncertain() is None
        assert len(h.r.store.live_orders()) <= 4            # the order cap
        assert CC.worst_loss(h) <= CC.DEFICIT + CC.CENT

    def test_on_the_quotes_follow_the_book(self, tmp_path, monkeypatch):
        h = drifting(tmp_path, monkeypatch, dict(TWO, **{KEEP: "ON"}))
        ups = [p for s, p, _q in h.sent() if s == "UP"]
        # up to the first-leg price ceiling (0.60), where opening stops
        assert ups[0] <= D("0.48") and max(ups) >= D("0.58")
        assert ups == sorted(ups) or max(ups) > ups[0]

    def test_the_walk_is_bounded(self, tmp_path, monkeypatch):
        h = CC.harness(tmp_path, monkeypatch, dict(TWO, **{KEEP: "ON"}))
        ex = h.r.executor
        assert ex.probe_round_index_cap() == 8 * (1 + E.ACC_REQUOTES_PER_ROUND)
        monkeypatch.setenv("L5_ACC_MAX_ROUNDS", "64")
        assert ex.probe_round_index_cap() == E.ACC_ROUND_INDEX_HARD_CAP


class TestAGenuineOpeningIsStillCharged:

    ONE = dict(TWO, L5_TS_CONCURRENT_ROUNDS="1", PMUS_RESIDUAL_CAP_K="1",
               L5_ACC_MAX_ROUNDS="2", L5_TS_ORDER_TTL_S="120")

    def pairs(self, tmp_path, monkeypatch, keep):
        """Both legs of every round fill as soon as they rest."""
        env = dict(self.ONE)
        if keep:
            env[KEEP] = "ON"
        h = CC.steered(CC.harness(tmp_path, monkeypatch, env))
        h.at(6.5).l5_action()
        off = 6.8
        for _ in range(60):
            for oid in LAD.resting_ids(h):
                h.venue.fill(oid)
            off += 3.0
            h.tick(off)
        return h

    @pytest.mark.parametrize("keep", [False, True])
    def test_two_filled_rounds_spend_the_budget_of_two(
            self, tmp_path, monkeypatch, keep):
        h = self.pairs(tmp_path, monkeypatch, keep)
        rl = h.r.executor.round_lifecycle(CC.SLUG)
        assert h.pos("UP") == h.pos("DOWN") > 0
        assert rl["rounds_spent"] == 2 and rl["rounds_left"] == 0
        assert rl["next_round"] is None
        assert exhausted(h) > 0
        # two rounds, two legs each, and not one order more
        assert len(h.venue.sent) == 4

    def test_a_round_that_filled_one_leg_is_charged(self, tmp_path,
                                                    monkeypatch):
        h = CC.steered(CC.harness(tmp_path, monkeypatch,
                                  dict(self.ONE, **{KEEP: "ON"})))
        h.at(6.5).l5_action()
        off = 6.8
        for _ in range(3):
            off += 1.0
            h.tick(off)
        for oid in LAD.resting_ids(h, "DOWN"):
            h.venue.fill(oid)
        for _ in range(4):
            off += 3.0
            h.tick(off)
        ex = h.r.executor
        assert h.pos("DOWN") > 0
        assert ex.probe_round_never_filled(CC.SLUG, 0) is False


class TestWhatCannotBeShownToBeUnfilledIsCharged:

    def test_an_order_still_at_the_venue(self, tmp_path, monkeypatch):
        h = CC.steered(CC.harness(tmp_path, monkeypatch,
                                  dict(TWO, **{KEEP: "ON"})))
        h.at(6.5).l5_action()
        h.tick(8.0)
        assert LAD.resting_ids(h)
        assert h.r.executor.probe_round_never_filled(CC.SLUG, 0) is False

    def test_a_round_with_no_orders_on_record(self, tmp_path, monkeypatch):
        """The record is what makes a re-quote free. Without it the count
        is the old one."""
        h = drifting(tmp_path, monkeypatch, dict(TWO, **{KEEP: "ON"}),
                     ticks=40)
        ex = h.r.executor
        free = ex.round_lifecycle(CC.SLUG)["rounds_requoted_without_a_fill"]
        assert free >= 4
        h.r.store.state["probe_round_orders"] = {}
        rl = ex.round_lifecycle(CC.SLUG)
        assert rl["rounds_requoted_without_a_fill"] == 0
        assert rl["rounds_spent"] >= free

    def test_an_unreadable_record(self, tmp_path, monkeypatch):
        h = drifting(tmp_path, monkeypatch, dict(TWO, **{KEEP: "ON"}),
                     ticks=20)
        h.r.store.state["probe_round_orders"] = "corrupt"
        assert h.r.executor.probe_round_never_filled(CC.SLUG, 0) is False

    def test_the_record_is_on_disk(self, tmp_path, monkeypatch):
        h = drifting(tmp_path, monkeypatch, dict(TWO, **{KEEP: "ON"}),
                     ticks=20)
        import json
        raw = json.load(open(h.r.store.path))
        rows = raw.get("probe_round_orders") or {}
        assert rows and all(k.startswith(CC.SLUG + "|") for k in rows)
        sent = {s["client_order_id"] for s in h.venue.sent}
        assert {c for v in rows.values() for c in v} <= sent


# ======================================================================
# THE RECOVERY SWITCH: the missing leg is re-priced when its other leg fills
# ======================================================================
REC = dict(CC.FOUR, L5_MAX_CASH_DEPLOYED_USD="0.90",
           L5_TS_ROUND_ALLOCATIONS="3", L5_TS_CONCURRENT_ROUNDS="1",
           PMUS_RESIDUAL_CAP_K="1", **CC.STEER)
REC[TS.COMPLETION_MAX_PAIR_ENV] = "0.98"


def one_leg_fills(tmp_path, monkeypatch, on, away=("0.53", "0.54")):
    env = dict(REC)
    if on:
        env[TS.INVENTORY_RECOVERY_ENV] = "ON"
    h = CC.steered(CC.harness(tmp_path, monkeypatch, env))
    h.at(6.5).l5_action()
    off = 6.8
    for _ in range(3):
        off += 1.0
        h.tick(off)
    for oid in LAD.resting_ids(h, "DOWN"):
        h.venue.fill(oid)
    h.book.set(*away)
    trail = []
    for _ in range(6):
        off += 2.0
        h.tick(off)
        trail.append([p for _s, p in LAD.resting(h, "UP")])
    return h, off, trail


class TestTheMissingLegIsRePricedOnTheFill:

    @pytest.mark.parametrize("raw", [None, "", "on", "1", "YES"])
    def test_only_the_exact_token(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv(TS.INVENTORY_RECOVERY_ENV, raising=False)
        else:
            monkeypatch.setenv(TS.INVENTORY_RECOVERY_ENV, raw)
        assert TS.inventory_recovery() is False
        assert TS.reprice_missing_leg_on_fill() is False

    def test_it_names_all_three_parts(self, monkeypatch):
        monkeypatch.setenv(TS.INVENTORY_RECOVERY_ENV, "ON")
        assert TS.partial_completion_per_contract() is True
        assert TS.completion_rests_at_ceiling() is True
        assert TS.reprice_missing_leg_on_fill() is True

    def test_off_the_opening_quote_stays_where_it_was(self, tmp_path,
                                                      monkeypatch):
        h, _off, trail = one_leg_fills(tmp_path, monkeypatch, on=False)
        assert h.pos("DOWN") > 0 and h.pos("UP") == 0
        # unmoved while it serves the 14 s minimum rest of a completion
        assert all(t == [D("0.48")] for t in trail[:4])

    def test_on_it_is_re_placed_at_the_target_within_ten_seconds(
            self, tmp_path, monkeypatch):
        h, _off, trail = one_leg_fills(tmp_path, monkeypatch, on=True)
        avg = h.r.store.leg_cost(CC.SLUG)["DOWN"] / h.pos("DOWN")
        target = D("0.98") - avg
        moved = [i for i, t in enumerate(trail) if t and t[0] > D("0.48")]
        assert moved and (moved[0] + 1) * 2 <= 8           # seconds
        px = trail[moved[0]][0]
        assert target - D("0.01") < px <= target           # the 0.98 target
        assert px <= D("0.53")                             # not through
        assert h.r.stats["requote_opening_leg_repriced_on_fill"] == 1
        assert len(LAD.resting_ids(h, "UP")) == 1
        assert h.r.store.orders_uncertain() is None

    def test_on_the_replacement_serves_the_minimum_rest(self, tmp_path,
                                                        monkeypatch):
        """Re-priced once on the fill; after that it is a completion quote
        and is not moved again for a book that walks a tick."""
        h, off, trail = one_leg_fills(tmp_path, monkeypatch, on=True)
        n = len(h.venue.sent)
        h.book.set("0.54", "0.55")
        for _ in range(3):
            off += 2.0
            h.tick(off)
        assert len(h.venue.sent) == n
        assert h.r.stats["requote_opening_leg_repriced_on_fill"] == 1

    def test_on_the_pair_completes_inside_the_target(self, tmp_path,
                                                     monkeypatch):
        h, off, _trail = one_leg_fills(tmp_path, monkeypatch, on=True)
        h.book.set("0.49", "0.50")
        for oid in LAD.resting_ids(h, "UP"):
            h.venue.fill(oid)
        h.tick(off + 3.0)
        up, dn = h.pos("UP"), h.pos("DOWN")
        lc = h.r.store.leg_cost(CC.SLUG)
        assert up == dn > 0
        assert (lc["UP"] + lc["DOWN"]) / up <= D("0.98")

    def test_on_never_above_the_hard_ceiling_when_the_market_runs(
            self, tmp_path, monkeypatch):
        h, _off, _trail = one_leg_fills(tmp_path, monkeypatch, on=True,
                                        away=("0.88", "0.89"))
        avg = h.r.store.leg_cost(CC.SLUG)["DOWN"] / h.pos("DOWN")
        for s, p, _q in h.sent():
            if s == "UP":
                assert avg + p <= D("1.00")
        assert all(avg + p <= D("0.98") for _s, p in LAD.resting(h, "UP"))
