"""Several pairs resting at once -- a bounded ladder -- THROUGH THE RUNNER.

OWNER DIRECTION, 2026-11-10: remove one-round-per-window. Continuous
simultaneous UP/DOWN maker quoting, multiple outstanding orders at bounded
price levels, replenishment after venue-confirmed fills, one controller
balancing everything filled and resting, and a conservative profitability
gate -- capable of 20+ fills a window, without forcing a trade whose
expected value is negative.

WHAT ONE-ROUND-PER-WINDOW ACTUALLY WAS. Not the round model: `probe_round`
already walks past a pair whose legs are both placed. It was the ORDER
COUNT -- the runner's timer path refused any tick while an order rested, and
`max_live_orders` is 2.

WHAT CHANGED, all of it off unless `L5_TS_CONCURRENT_ROUNDS` is above one:

  * the timer path may open ANOTHER pair beside resting ones when a price
    level is free and the owner's order limit has room for both legs;
  * each such pair is quoted one step further behind the touch;
  * a half-placed pair gets its other leg even with a ladder resting;
  * a requote moves the reducing-side order nearest its touch;
  * and a pair laddered beside resting ones opens only when the ledger's own
    settled record is usable and clears the floor -- unless the owner waives
    that, by name, for a bounded validation.

NOTHING HERE RAISES A LIMIT. A ladder of K pairs needs the OWNER to set
`L5_MAX_LIVE_ORDERS` to 2K; cash, exposure, residual and loss gates price
every resting order as though it fills alone, as they always did.

THE CONFIGURATIONS BELOW ARE NOT APPROVED. They use a larger order limit and
cash envelope than production because a ladder cannot be exercised inside
two orders and $0.90. They prove mechanism and limits, not economics --
see `analysis/l5/PAIR_COMPLETION_REPLAY.md` for those.

Harness: `test_l5_runner_pair_and_cancel_state.py` -- a Runner built by its
own constructor, a FaithfulExecutor wired as `Runner.roll` wires it, a
scripted venue and book. `Runner.roll` and arming are not exercised.
"""
import datetime as dt
import inspect
from decimal import Decimal as D

import pytest

import tests.test_l5_runner_pair_and_cancel_state as T
from src import l5_live_execution as E
from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS
from src import l5_unmatched_disposition as UD

SLUG = T.SLUG
#: three levels a side, six orders, a $3.00 envelope in four parts, and the
#: owner's waiver of the settled-record requirement. NOT APPROVED.
LADDER = {"L5_TS_CONCURRENT_ROUNDS": "3", "L5_MAX_LIVE_ORDERS": "6",
          "L5_TS_ROUND_ALLOCATIONS": "4", "L5_MAX_CASH_DEPLOYED_USD": "3.00",
          "L5_ACC_MAX_ROUNDS": "16",
          "L5_TS_OWN_EVIDENCE_GATE": "WAIVED_BY_OWNER"}
NEW_ENV = ("L5_TS_CONCURRENT_ROUNDS", "L5_TS_OWN_EVIDENCE_GATE",
           "L5_TS_LEVEL_STEP_TICKS")


def harness(tmp_path, monkeypatch, root="live", **env):
    for k, v in T.PRODUCTION.items():
        monkeypatch.setenv(k, v)
    for k in T.ABSENT + NEW_ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    h = T.Harness(tmp_path, monkeypatch, root=root)
    apply_limits(h)
    return h


def apply_limits(h):
    """The harness freezes production's two-order limit; the order count is
    the owner's variable, so it follows the environment here."""
    h.r.limits = E.SafetyLimits(
        max_usd_per_market=D("20"), max_abs_residual=D("10"),
        max_total_exposure_usd=D("1.00"), max_concurrent_markets=1,
        max_live_orders=R.max_live_orders(),
        daily_realized_loss_halt_usd=D("5.00"))
    h.r.executor.limits = h.r.limits


@pytest.fixture()
def lad(tmp_path, monkeypatch):
    return harness(tmp_path, monkeypatch, **LADDER)


def resting(h, side=None):
    return sorted(
        (str(h.venue.by_oid[o]["side"]), D(str(h.venue.by_oid[o]["price"])))
        for o in h.venue.by_oid
        if o not in h.venue.full and o not in h.venue.retired
        and (side is None or str(h.venue.by_oid[o]["side"]) == side))


def resting_ids(h, side=None):
    return [o for o in h.venue.by_oid
            if o not in h.venue.full and o not in h.venue.retired
            and (side is None or str(h.venue.by_oid[o]["side"]) == side)]


def build(h, ticks=(6.8, 7.8, 8.8, 9.8, 10.8)):
    h.at(6.5).l5_action()
    for off in ticks:
        h.tick(off)
    return h


def book_fills(h, start):
    """Ticks until the order poll comes round: the runner reads each order
    every `poll_interval_s`, not every tick, and a fill is booked only from
    that read."""
    for i in range(4):
        h.tick(float(start) + 3.5 * i)
    return h


def seed(h, n, pnl):
    rows = h.r.store.state.setdefault("retired_markets", {})
    t0 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    for i in range(n):
        rows["cpc-btc-updown-15m-2026-11-%02d-%02d00z"
             % (1 + i // 24, i % 24)] = {
            "t": (t0 + dt.timedelta(hours=i)).isoformat(),
            "yes_value": "1.0000", "settlement_pnl": str(pnl),
            "resolution_source": "VENUE_SETTLEMENT_PX", "up_qty": "0.6",
            "dn_qty": "0.6", "cost_total": "0.594",
            "redemption_total": "0.6", "unverified_cost_basis_keys": []}


def reasons(h, needle):
    return {k: v for k, v in h.r.stats.items() if needle in k and v}


# ======================================================================
# 0. OFF BY DEFAULT
# ======================================================================
class TestOffUnlessTheOwnerSetIt:

    def test_unset_is_one_pair_and_no_extra_depth(self, monkeypatch):
        for k in NEW_ENV:
            monkeypatch.delenv(k, raising=False)
        assert TS.concurrent_rounds() == 1
        assert [TS.level_offset_ticks(n) for n in (0, 2, 4, 9)] == [0] * 4
        assert UD.own_evidence_gate_waived() is False

    @pytest.mark.parametrize("raw,want", [
        ("", 1), ("0", 1), ("-3", 1), ("three", 1), ("1", 1), ("3", 3),
        ("6", 6), ("7", 6), ("999", 6)])
    def test_the_level_count_is_bounded_and_a_typo_is_one(self, monkeypatch,
                                                         raw, want):
        monkeypatch.setenv(TS.CONCURRENT_ROUNDS_ENV, raw)
        assert TS.concurrent_rounds() == want

    @pytest.mark.parametrize("raw", ["", "waived", "WAIVED", "1", "yes",
                                     "WAIVED_BY_OWNER ", " waived_by_owner"])
    def test_only_the_exact_token_waives_the_record(self, monkeypatch, raw):
        monkeypatch.setenv(UD.OWN_EVIDENCE_GATE_ENV, raw)
        assert UD.own_evidence_gate_waived() is (
            raw.strip() == "WAIVED_BY_OWNER")

    def test_production_still_places_one_pair(self, tmp_path, monkeypatch):
        h = build(harness(tmp_path, monkeypatch))
        assert resting(h) == [("DOWN", D("0.44")), ("UP", D("0.50"))]
        assert not reasons(h, "CONCURRENT_ROUND_SLOT")

    def test_a_raised_order_limit_alone_does_not_ladder(self, tmp_path,
                                                        monkeypatch):
        """Six order slots and no ladder is still one pair. FOUND BY THIS
        TEST: with the limit above two, the pass loop's second call opened
        the first leg of a new round beside the resting pair, and nothing
        could place its complement. A pair that can only have one leg is
        now refused by name."""
        h = build(harness(tmp_path, monkeypatch, L5_MAX_LIVE_ORDERS="6",
                          L5_MAX_CASH_DEPLOYED_USD="3.00",
                          L5_TS_ROUND_ALLOCATIONS="4"))
        assert len(h.venue.sent) == 2
        assert reasons(h, "CONCURRENT_ROUND_REFUSED:CONCURRENT_ROUNDS_DISABLED")


# ======================================================================
# 1. THE LADDER
# ======================================================================
class TestSeveralPairsRestAtBoundedPriceLevels:

    def test_three_pairs_rest_together_at_three_levels_a_side(self, lad):
        build(lad)
        assert resting(lad, "UP") == [("UP", D("0.48")), ("UP", D("0.49")),
                                      ("UP", D("0.50"))]
        assert resting(lad, "DOWN") == [("DOWN", D("0.42")),
                                        ("DOWN", D("0.43")),
                                        ("DOWN", D("0.44"))]
        for oid in resting_ids(lad):
            assert lad.venue.order(oid)["state"] == "ORDER_STATE_NEW"

    def test_each_level_is_one_step_further_from_the_touch(self, lad,
                                                           monkeypatch):
        monkeypatch.setenv(TS.LEVEL_STEP_ENV, "2")
        build(lad)
        assert [p for _s, p in resting(lad, "UP")] == [D("0.46"), D("0.48"),
                                                       D("0.50")]

    def test_every_order_is_a_post_only_maker_and_never_crosses(self, lad):
        build(lad)
        assert {str(s["mode"]) for s in lad.venue.sent} == {"MAKER"}
        for side, px in resting(lad):
            if side == "UP":
                assert px < D("0.54")
            else:
                assert D(1) - px > D("0.52")

    def test_every_pair_is_inside_the_pair_ceiling(self, lad):
        build(lad)
        sent = lad.sent()
        for (s1, p1, _), (s2, p2, _) in zip(sent[0::2], sent[1::2]):
            assert {s1, s2} == {"UP", "DOWN"}
            assert p1 + p2 <= D("0.96"), (p1, p2)

    def test_no_pair_is_left_with_one_leg(self, lad):
        """FOUND BY DRIVING IT. The third pair's complement was refused by
        the one-resting-order rule and its first leg stood alone."""
        build(lad)
        assert len(resting(lad, "UP")) == len(resting(lad, "DOWN")) == 3
        for r in range(3):
            assert lad.r.executor.probe_legs_claimed(SLUG, r) == {
                "UP": True, "DOWN": True}

    def test_the_level_count_is_the_bound(self, tmp_path, monkeypatch):
        h = build(harness(tmp_path, monkeypatch,
                          **dict(LADDER, L5_TS_CONCURRENT_ROUNDS="2")))
        assert len(resting(h, "UP")) == len(resting(h, "DOWN")) == 2
        assert h.r.concurrent_round_slot_refusal(SLUG) == "PRICE_LEVELS_FULL"

    def test_the_owners_order_limit_is_the_bound(self, tmp_path,
                                                 monkeypatch):
        """Three levels asked for, four orders allowed: two pairs."""
        h = build(harness(tmp_path, monkeypatch,
                          **dict(LADDER, L5_MAX_LIVE_ORDERS="4")))
        assert len(h.venue.sent) == 4
        assert h.r.concurrent_round_slot_refusal(SLUG) == \
            "NO_ORDER_SLOTS_FOR_A_PAIR"

    def test_with_two_order_slots_a_ladder_cannot_form(self, tmp_path,
                                                       monkeypatch):
        """THE PRODUCTION LIMIT. Asking for three levels buys nothing while
        the owner allows two orders."""
        h = build(harness(tmp_path, monkeypatch,
                          **dict(LADDER, L5_MAX_LIVE_ORDERS="2")))
        assert len(h.venue.sent) == 2


class TestThePortfolioLimitsStillBind:

    def test_resting_orders_never_exceed_the_order_limit(self, lad):
        lad.at(6.5).l5_action()
        most = 0
        for off in range(7, 200, 3):
            lad.tick(float(off))
            most = max(most, len(resting_ids(lad)))
            for oid in resting_ids(lad):
                lad.venue.fill(oid)
        assert 2 < most <= 6

    def test_the_cash_limit_ends_the_sequence(self, lad):
        lad.at(6.5).l5_action()
        for off in range(7, 200, 3):
            lad.tick(float(off))
            for oid in resting_ids(lad):
                lad.venue.fill(oid)
        assert sum(p * q for _s, p, q in lad.sent()) <= D("3.00")
        assert reasons(lad, "CASH_COMMITMENT")

    def test_one_side_filling_alone_stays_inside_the_exposure_cap(self, lad):
        """THE WORST CASE OF A LADDER: the market runs through every level
        on one side. Each resting order was priced as if it filled alone."""
        build(lad)
        lad.book.set("0.40", "0.41")
        for oid in resting_ids(lad, "UP"):
            lad.venue.fill(oid)
        book_fills(lad, 13.0)
        legs = lad.r.store.leg_cost(SLUG)
        assert lad.pos("UP") == D("1.50") and lad.pos("DOWN") == 0
        assert D(str(legs["UP"])) <= D("1.00"), legs

    def test_a_ladder_too_large_for_the_exposure_cap_is_not_built(
            self, tmp_path, monkeypatch):
        """One contract a level: three UP legs would cost 1.47 if they all
        filled alone. The cap is $1.00, so the third pair is refused."""
        h = build(harness(tmp_path, monkeypatch, **dict(
            LADDER, L5_MAX_CASH_DEPLOYED_USD="12.00")))
        up_cost = sum(p * q for s, p, q in h.sent() if s == "UP")
        dn_cost = sum(p * q for s, p, q in h.sent() if s == "DOWN")
        assert max(up_cost, dn_cost) <= D("1.00"), h.sent()
        assert len(h.venue.sent) < 6

    def test_while_one_side_is_held_nothing_new_is_opened_on_it(self, lad):
        build(lad)
        lad.book.set("0.40", "0.41")
        for oid in resting_ids(lad, "UP"):
            lad.venue.fill(oid)
        n = len(lad.venue.sent)
        for off in range(13, 110, 3):
            lad.tick(float(off))
        assert all(s == "DOWN" for s, _p, _q in lad.sent()[n:]), lad.sent()[n:]

    def test_the_stale_hedges_are_cancelled_by_exact_id_at_their_ttl(
            self, lad):
        build(lad)
        down = set(resting_ids(lad, "DOWN"))
        lad.book.set("0.40", "0.41")
        for oid in resting_ids(lad, "UP"):
            lad.venue.fill(oid)
        for off in list(range(13, 60, 3)) + list(range(60, 200, 10)):
            lad.tick(float(off))
        assert set(lad.venue.cancelled) == down
        assert lad.r.store.orders_uncertain() is None

    def test_no_completion_is_ever_priced_above_redemption(self, lad):
        build(lad)
        lad.book.set("0.40", "0.41")
        for oid in resting_ids(lad, "UP"):
            lad.venue.fill(oid)
        n = len(lad.venue.sent)
        for off in list(range(13, 60, 3)) + list(range(60, 400, 10)):
            lad.tick(float(off))
        avg_up = D(str(lad.r.store.leg_cost(SLUG)["UP"])) / lad.pos("UP")
        for _s, px, _q in lad.sent()[n:]:
            assert px + avg_up <= D("1.00")
        assert {str(s["mode"]) for s in lad.venue.sent} == {"MAKER"}


# ======================================================================
# 2. REPLENISHMENT AND THROUGHPUT
# ======================================================================
class TestReplenishmentAfterVenueConfirmedFills:

    def test_a_filled_level_is_requoted(self, lad):
        build(lad)
        for oid in resting_ids(lad):
            lad.venue.fill(oid)
        n = len(lad.venue.sent)
        book_fills(lad, 13.0)
        assert len(lad.venue.sent) > n, "nothing was re-quoted after fills"
        assert lad.pos("UP") == lad.pos("DOWN") == D("1.50")

    def test_nothing_is_requoted_on_a_fill_the_venue_has_not_confirmed(
            self, lad):
        """A touched quote is not a fill. With the venue reporting every
        order still working, the ladder is full and stays as it is."""
        build(lad)
        lad.book.set("0.49", "0.51")            # touches the top UP level
        for off in (13.0, 16.0, 19.0):
            lad.tick(off)
        assert len(lad.venue.sent) == 6
        assert lad.pos("UP") == lad.pos("DOWN") == 0

    def test_the_capability_reaches_twenty_fills_in_one_window(
            self, tmp_path, monkeypatch):
        """TWENTY-FOUR venue-confirmed fills, twelve matched pairs, never
        more than six orders resting. Under a $6.00 envelope in eight parts,
        WHICH THE OWNER HAS NOT APPROVED -- see the next two tests for what
        the approved capital allows."""
        h = harness(tmp_path, monkeypatch, **dict(
            LADDER, L5_FIDELITY_MODE="ON", L5_TS_ROUND_ALLOCATIONS="8",
            L5_MAX_CASH_DEPLOYED_USD="6.00"))
        h.at(6.5).l5_action()
        fills, most = 0, 0
        for off in range(7, 300, 3):
            h.tick(float(off))
            live = resting_ids(h)
            most = max(most, len(live))
            for oid in live:
                h.venue.fill(oid)
                fills += 1
        assert fills >= 20, (fills, h.sent())
        assert most <= 6
        assert h.pos("UP") == h.pos("DOWN")
        assert sum(p * q for _s, p, q in h.sent()) <= D("6.00")
        assert h.r.store.orders_uncertain() is None
        coids = [s["client_order_id"] for s in h.venue.sent]
        assert len(coids) == len(set(coids))

    def test_inside_the_approved_cash_two_levels_fit_and_no_more(
            self, tmp_path, monkeypatch):
        """WHAT THE APPROVED $0.90 AND $1.00 ACTUALLY ALLOW, with only the
        order limit raised to four: two pairs of 0.3 contracts resting
        together, four fills, and then the cash is committed until
        settlement. One side filling alone is $0.297 of exposure."""
        h = harness(tmp_path, monkeypatch, **dict(
            LADDER, L5_TS_CONCURRENT_ROUNDS="2", L5_MAX_LIVE_ORDERS="4",
            L5_TS_ROUND_ALLOCATIONS="2", L5_MAX_CASH_DEPLOYED_USD="0.90"))
        build(h)
        assert resting(h) == [("DOWN", D("0.43")), ("DOWN", D("0.44")),
                              ("UP", D("0.49")), ("UP", D("0.50"))]
        assert {q for _s, _p, q in h.sent()} == {D("0.3")}
        one_side = sum(p * q for s, p, q in h.sent() if s == "UP")
        assert one_side == D("0.297") and one_side <= D("1.00")
        for off in range(13, 200, 3):
            for oid in resting_ids(h):
                h.venue.fill(oid)
            h.tick(float(off))
        assert len(h.venue.sent) == 4, h.sent()
        assert h.pos("UP") == h.pos("DOWN") == D("0.6")
        assert sum(p * q for _s, p, q in h.sent()) <= D("0.90")
        assert reasons(h, "CASH_COMMITMENT")

    @pytest.mark.parametrize("book,k,orders", [
        (("0.52", "0.53"), 2, 4),   # the usual one-tick book: two levels
        (("0.52", "0.53"), 3, 2),   # ...and a third does not clear the floor
        (("0.52", "0.54"), 3, 6),   # a two-tick book pays for a third
        (("0.52", "0.54"), 4, 2),   # nothing pays for a fourth
        (("0.52", "0.53"), 4, 2),
    ])
    def test_what_the_approved_cash_allows_at_each_depth(
            self, tmp_path, monkeypatch, book, k, orders):
        """MEASURED, NOT ARGUED. $0.90 split across k levels, k pairs of
        order slots, every order filling. Past two levels the rounds are too
        small to clear the one-cent floor, and a matched pair holds its cash
        until settlement -- so twenty fills a window is not available at the
        approved capital, and no setting here pretends otherwise."""
        h = harness(tmp_path, monkeypatch, **dict(
            LADDER, L5_TS_CONCURRENT_ROUNDS=str(k),
            L5_MAX_LIVE_ORDERS=str(2 * k), L5_TS_ROUND_ALLOCATIONS=str(k),
            L5_MAX_CASH_DEPLOYED_USD="0.90"))
        h.book.set(*book)
        h.at(6.5).l5_action()
        for off in range(7, 200, 3):
            h.tick(float(off))
            if off > 14:
                for oid in resting_ids(h):
                    h.venue.fill(oid)
        assert len(h.venue.sent) == orders, h.sent()
        assert sum(p * q for _s, p, q in h.sent()) <= D("0.90")


# ======================================================================
# 3. THE PROFITABILITY GATE
# ======================================================================
class TestALadderNeedsTheLedgersOwnRecord:

    def gated(self, tmp_path, monkeypatch, **env):
        cfg = dict(LADDER, L5_TS_OWN_EVIDENCE_GATE="")
        cfg.update(env)
        return harness(tmp_path, monkeypatch, **cfg)

    def test_with_no_record_no_second_pair_is_laddered(self, tmp_path,
                                                       monkeypatch):
        h = build(self.gated(tmp_path, monkeypatch))
        assert len(h.venue.sent) == 2
        assert reasons(h, "CONCURRENT_ROUND_NEEDS_OWN_SETTLED_EVIDENCE")

    def test_a_thin_record_is_not_permission_to_ladder(self, tmp_path,
                                                       monkeypatch):
        h = self.gated(tmp_path, monkeypatch)
        seed(h, UD.OWN_EVIDENCE_MIN_WINDOWS - 1, "0.0500")
        build(h)
        assert len(h.venue.sent) == 2

    def test_a_losing_record_refuses_it(self, tmp_path, monkeypatch):
        h = self.gated(tmp_path, monkeypatch)
        seed(h, 30, "-0.0400")
        build(h)
        assert len(h.venue.sent) == 2
        assert reasons(h, "OWN_SETTLED_EXPECTANCY_BELOW_FLOOR")

    def test_a_record_that_clears_the_floor_opens_the_ladder(self, tmp_path,
                                                             monkeypatch):
        h = self.gated(tmp_path, monkeypatch)
        seed(h, 30, "0.0300")
        build(h)
        assert len(h.venue.sent) == 6

    def test_a_mistyped_waiver_leaves_the_gate_enforced(self, tmp_path,
                                                        monkeypatch):
        h = build(self.gated(tmp_path, monkeypatch,
                             L5_TS_OWN_EVIDENCE_GATE="waived"))
        assert len(h.venue.sent) == 2

    def test_the_owners_waiver_is_recorded_on_the_decision(self, lad):
        seed(lad, 30, "-0.0400")
        out = UD.reopen_decision(
            market="m", rounds_used=1, worst_leg_own_px="0.50", bid="0.52",
            ask="0.53", tick="0.01", qty="0.66", now_s=100.0,
            window_end_s=900.0, exposure_headroom_usd="1.00", depth_ticks=2,
            own_evidence=lad.r.store.own_settled_round_evidence(),
            concurrent=True)
        assert out["reopen"] is True
        assert out["own_evidence_gate"] == "WAIVED_BY_OWNER"
        assert out["own_settled_evidence"]["clears_floor"] is False
        assert out["concurrent_round"] is True

    def test_the_waiver_does_not_waive_the_arithmetic_or_the_capacity(
            self, lad):
        base = dict(market="m", rounds_used=1, worst_leg_own_px="0.50",
                    bid="0.52", ask="0.53", tick="0.01", now_s=100.0,
                    window_end_s=900.0, depth_ticks=2, concurrent=True)
        thin = UD.reopen_decision(qty="0.10", exposure_headroom_usd="1.00",
                                  **base)
        assert thin["reason"] == "INCREMENTAL_EXPECTANCY_BELOW_FLOOR"
        full = UD.reopen_decision(qty="0.66", exposure_headroom_usd="0.10",
                                  **base)
        assert full["reason"] == "EXPOSURE_HEADROOM_BELOW_WORST_CASE"

    def test_the_first_pair_of_a_window_is_still_the_owners_decision(
            self, tmp_path, monkeypatch):
        h = self.gated(tmp_path, monkeypatch)
        seed(h, 30, "-0.5000")
        build(h)
        assert resting(h) == [("DOWN", D("0.44")), ("UP", D("0.50"))]


# ======================================================================
# 4. REPRICING WITH A LADDER RESTING
# ======================================================================
class TestTheRequoteMovesTheNearestHedge:

    def held_up(self, lad):
        build(lad)
        top = [o for o in resting_ids(lad, "UP")
               if D(str(lad.venue.by_oid[o]["price"])) == D("0.50")][0]
        lad.venue.fill(top)
        book_fills(lad, 13.0)
        assert lad.pos("UP") == D("0.5")
        return lad

    def live_map(self, h):
        return {c: o for c, o in h.r.store.live_orders().items()
                if o.get("market") == SLUG}

    def test_it_picks_the_reducing_order_nearest_its_touch(self, lad):
        self.held_up(lad)
        live = self.live_map(lad)
        coid = lad.r._requote_ladder_pick(SLUG, live)
        assert live[coid]["side"] == "DOWN"
        assert D(str(live[coid]["price"])) == D("0.44")

    def test_it_never_picks_an_order_on_the_held_side(self, lad):
        self.held_up(lad)
        live = {c: o for c, o in self.live_map(lad).items()
                if o["side"] == "UP"}
        assert lad.r._requote_ladder_pick(SLUG, live) is None

    def test_flat_inventory_declines_as_it_always_did(self, lad):
        build(lad)
        assert lad.r._requote_ladder_pick(SLUG, self.live_map(lad)) is None
        lad.tick(13.0)
        lad.tick(16.0)
        assert reasons(lad, "MULTIPLE_RESTING_ORDERS_IN_MARKET")

    def test_with_the_ladder_off_several_orders_still_decline(
            self, lad, monkeypatch):
        self.held_up(lad)
        monkeypatch.setenv(TS.CONCURRENT_ROUNDS_ENV, "1")
        assert lad.r._requote_ladder_pick(SLUG, self.live_map(lad)) is None

    def test_an_order_the_venue_cannot_name_is_never_picked(self, lad):
        self.held_up(lad)
        live = self.live_map(lad)
        for o in live.values():
            o["venue_order_id"] = ""
        assert lad.r._requote_ladder_pick(SLUG, live) is None

    def test_the_requote_reaches_its_own_decision_for_that_order(self, lad):
        """The pick only chooses WHICH order; the requote's own economics --
        ceiling, redemption cap, queue evidence -- still decide WHETHER."""
        self.held_up(lad)
        lad.book.set("0.47", "0.48")
        lad.tick(30.0)
        assert lad.r.stats["requote_ladder_pick"] >= 1
        assert reasons(lad, "requote_refused:") or \
            lad.r.stats["requote_cancelled"] >= 1


# ======================================================================
# 5. RESTART AND RECONCILIATION
# ======================================================================
class TestRestartRecovery:

    def restarted(self, lad, tmp_path, monkeypatch):
        again = T.Harness.__new__(T.Harness)
        again.emitted, again.t = [], lad.t
        again.book, again.venue, again._seq = lad.book, lad.venue, lad._seq
        monkeypatch.setattr(R, "emit", lambda *a, **k: None)
        again.r = R.Runner(root=str(tmp_path / "live"), book=lad.book,
                           feed=lambda: [])
        again.attach()
        apply_limits(again)
        return again

    def test_a_restart_finds_every_resting_order_by_exact_id(
            self, lad, tmp_path, monkeypatch):
        build(lad)
        before = {str(o["venue_order_id"])
                  for o in lad.r.store.live_orders().values()}
        again = self.restarted(lad, tmp_path, monkeypatch)
        after = {str(o["venue_order_id"])
                 for o in again.r.store.live_orders().values()}
        assert after == before and len(after) == 6

    def test_a_restart_places_no_duplicate_and_no_extra_order(
            self, lad, tmp_path, monkeypatch):
        build(lad)
        again = self.restarted(lad, tmp_path, monkeypatch)
        for off in (11.8, 12.8, 13.8, 14.8):
            again.tick(off)
        assert len(lad.venue.sent) == 6
        coids = [s["client_order_id"] for s in lad.venue.sent]
        assert len(coids) == len(set(coids))

    def test_reconciliation_agrees_with_the_venue_on_a_full_ladder(
            self, lad):
        build(lad)
        out = lad.r.store.reconcile_from_venue(lad.venue.positions(),
                                               lad.venue.open_orders())
        assert lad.r.store.orders_uncertain() is None, out
        assert len(lad.r.store.live_orders()) == 6

    def test_fills_while_down_are_booked_from_the_venue_after_restart(
            self, lad, tmp_path, monkeypatch):
        build(lad)
        for oid in resting_ids(lad, "DOWN")[:2]:
            lad.venue.fill(oid)
        again = self.restarted(lad, tmp_path, monkeypatch)
        book_fills(again, 13.0)
        assert again.pos("DOWN") == D("1.00")
        assert again.r.store.orders_uncertain() is None


# ======================================================================
# 6. THE RULES THEMSELVES
# ======================================================================
def _rows(*sides, market=SLUG):
    return {"c%d" % i: {"market": market, "side": s}
            for i, s in enumerate(sides)}


class TestTheSlotRules:

    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        monkeypatch.setenv(TS.CONCURRENT_ROUNDS_ENV, "3")
        monkeypatch.setenv(TS.TWO_ORDER_PAIR_ENV, "1")

    @pytest.mark.parametrize("rows,limit,want", [
        (_rows("UP", "DOWN"), 6, None),
        (_rows("UP", "DOWN", "UP", "DOWN"), 6, None),
        (_rows("UP", "DOWN", "UP", "DOWN", "UP", "DOWN"), 8,
         "PRICE_LEVELS_FULL"),
        (_rows("UP", "UP", "UP"), 8, "PRICE_LEVELS_FULL"),
        (_rows("UP", "DOWN", "UP", "DOWN"), 5, "NO_ORDER_SLOTS_FOR_A_PAIR"),
        (_rows("UP", "DOWN"), 3, "NO_ORDER_SLOTS_FOR_A_PAIR"),
        (_rows("UP", "DOWN", market="other"), 6,
         "RESTING_ORDER_IS_ANOTHER_MARKET"),
        (_rows("UP", "SIDEWAYS"), 6, "RESTING_ORDER_SIDE_UNREADABLE"),
        (None, 6, "LIVE_ORDERS_UNREADABLE"),
        (_rows("UP", "DOWN"), "many", "LIVE_ORDERS_UNREADABLE"),
    ])
    def test_a_new_pair_needs_a_free_level_and_two_slots(self, rows, limit,
                                                         want):
        assert TS.concurrent_round_slot_refusal(
            market=SLUG, live_orders=rows, max_live_orders=limit) == want

    @pytest.mark.parametrize("rows,limit,want", [
        (_rows("UP", "DOWN", "DOWN"), 6, None),
        (_rows("UP", "DOWN", "UP", "DOWN", "DOWN"), 6, None),
        (_rows("UP", "DOWN", "UP", "DOWN", "UP", "DOWN"), 6,
         "NO_ORDER_SLOT_FOR_THE_COMPLEMENT"),
        (_rows("UP", "DOWN", market="other"), 6,
         "RESTING_ORDER_IS_ANOTHER_MARKET"),
        (None, 6, "LIVE_ORDERS_UNREADABLE"),
    ])
    def test_a_half_placed_pair_needs_one_slot(self, rows, limit, want):
        assert TS.ladder_complement_refusal(
            market=SLUG, live_orders=rows, max_live_orders=limit) == want

    def test_both_refuse_with_the_ladder_or_the_pair_slot_off(
            self, monkeypatch):
        rows = _rows("UP", "DOWN")
        monkeypatch.setenv(TS.TWO_ORDER_PAIR_ENV, "0")
        assert TS.concurrent_round_slot_refusal(
            market=SLUG, live_orders=rows, max_live_orders=6) == \
            "TWO_ORDER_PAIR_DISABLED"
        monkeypatch.setenv(TS.TWO_ORDER_PAIR_ENV, "1")
        monkeypatch.setenv(TS.CONCURRENT_ROUNDS_ENV, "1")
        for fn in (TS.concurrent_round_slot_refusal,
                   TS.ladder_complement_refusal):
            assert fn(market=SLUG, live_orders=rows, max_live_orders=6) == \
                "CONCURRENT_ROUNDS_DISABLED"

    @pytest.mark.parametrize("n,want", [(0, 0), (1, 0), (2, 1), (3, 1),
                                        (4, 2), (5, 2), (6, 2), (40, 2)])
    def test_both_legs_of_a_pair_read_the_same_level(self, n, want):
        assert TS.level_offset_ticks(n) == want

    def test_a_parked_order_refuses_the_runners_slot(self, tmp_path,
                                                     monkeypatch):
        h = build(harness(tmp_path, monkeypatch,
                          **dict(LADDER, L5_TS_CONCURRENT_ROUNDS="2")),
                  ticks=(6.8,))
        h.r.store.state.setdefault("pending_terminal", {})["x"] = {
            "coid": "x"}
        assert h.r.concurrent_round_slot_refusal(SLUG) == \
            "ORDER_STATE_UNRESOLVED"


def test_the_executor_asks_the_same_question_the_runner_does():
    """An L5 action reaches the executor without passing the runner's timer
    gate, so the level bound is enforced there too."""
    src = inspect.getsource(E.FaithfulExecutor.probe_reopen_decision)
    assert "TS.concurrent_round_slot_refusal(" in src
    assert "TSP.concurrent_round_slot_refusal(" in inspect.getsource(
        R.Runner.concurrent_round_slot_refusal)


def test_at_the_order_cap_the_cap_still_names_the_refusal():
    """A third order with no slot left is stopped by MAX_LIVE_ORDERS, under
    that name. The ladder's test speaks only when the ladder is on or a
    slot is free."""
    src = inspect.getsource(E.FaithfulExecutor.probe_reopen_decision)
    gate = src.index("if resting_here > 0 and (ladder_on or slot_free):")
    assert gate < src.index("TS.concurrent_round_slot_refusal(")
