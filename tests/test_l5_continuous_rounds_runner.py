"""Repeated paired rounds in one window, driven THROUGH THE RUNNER.

WHAT THE OWNER ASKED FOR (2026-11-10): stop treating every pair as an
isolated transaction -- simultaneous UP/DOWN maker orders, repeated
replenishment, several fills, inventory balanced across the window -- and
optimize for SETTLED NET, not trade count.

WHAT WAS ALREADY THERE. The controller repeats rounds on its own once the
cash envelope is split (`L5_TS_ROUND_ALLOCATIONS`): both legs of each round
in one pass, a one-sided round finished before another is opened, and every
round after the first priced by `reopen_decision`. Until `7d9581999` the two
legs could not rest together; that was the only missing piece of mechanism,
and section 1 shows the rest working.

WHAT WAS WRONG WITH THE PRICING. `reopen_decision` valued a further round at
the PLANNED pair edge and L5's residual figure, which made it look worth
+0.03 a contract. Our own settled windows, a 526-window replay and the PMUS
shadow record all say a round loses several cents
(`analysis/l5/PAIR_COMPLETION_REPLAY.md`). Section 2 is the correction: a
further round must also clear what THIS LEDGER says rounds have paid.

SAME HARNESS AS `test_l5_runner_pair_and_cancel_state.py`: a Runner built by
its own constructor, a FaithfulExecutor wired as `Runner.roll` wires it,
production's switches, a scripted venue and book. `Runner.roll` and arming
are not exercised.

TWO CONFIGURATIONS, and the difference matters:

  * PRODUCTION -- $0.90 cash, one round. One pair fits. Unchanged.
  * WIDER -- $3.00 cash split over three rounds. THE OWNER HAS NOT APPROVED
    THIS. It exists here only because repeated rounds cannot be exercised
    inside $0.90, and it proves mechanism, not economics.
"""
import collections
import datetime as dt
from decimal import Decimal as D

import pytest

import tests.test_l5_runner_pair_and_cancel_state as T
from src import l5_live_execution as E
from src import l5_pmus_live_runner as R
from src import l5_unmatched_disposition as UD

SLUG = T.SLUG
WIDER = {"L5_TS_ROUND_ALLOCATIONS": "3", "L5_MAX_CASH_DEPLOYED_USD": "3.00"}


def harness(tmp_path, monkeypatch, **env):
    for k, v in T.PRODUCTION.items():
        monkeypatch.setenv(k, v)
    for k in T.ABSENT:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    return T.Harness(tmp_path, monkeypatch)


@pytest.fixture()
def wide(tmp_path, monkeypatch):
    return harness(tmp_path, monkeypatch, **WIDER)


@pytest.fixture()
def prod(tmp_path, monkeypatch):
    return harness(tmp_path, monkeypatch)


def resting(h):
    return [o for o in h.venue.by_oid
            if o not in h.venue.full and o not in h.venue.retired]


def drive(h, ticks=range(7, 121, 3), fill=lambda h, oid: True):
    """An L5 action, then ticks; the venue fills what `fill` says, one tick
    after it rests. Records the most orders ever resting at once."""
    h.at(6.5).l5_action()
    seen = {"max_resting": 0, "both_sides_resting": 0}
    for off in ticks:
        h.tick(float(off))
        live = resting(h)
        seen["max_resting"] = max(seen["max_resting"], len(live))
        if {str(h.venue.by_oid[o]["side"]) for o in live} == {"UP", "DOWN"}:
            seen["both_sides_resting"] += 1
        for oid in live:
            if fill(h, oid):
                h.venue.fill(oid)
    return seen


def seed(h, n, pnl, up="0.6", dn="0.6", series="cpc-btc-updown-15m-",
         source="VENUE_SETTLEMENT_PX", unverified=()):
    """`n` settled windows in the ledger, each paying `pnl`."""
    rows = h.r.store.state.setdefault("retired_markets", {})
    t0 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    for i in range(n):
        rows["%s2026-11-%02d-%02d00z" % (series, 1 + i // 24, i % 24)] = {
            "t": (t0 + dt.timedelta(hours=i)).isoformat(),
            "yes_value": "1.0000", "settlement_pnl": str(pnl),
            "resolution_source": source, "up_qty": up, "dn_qty": dn,
            "cost_total": "0.594", "redemption_total": "0.6",
            "unverified_cost_basis_keys": list(unverified)}


def reasons(h, needle):
    return {k: v for k, v in h.r.stats.items() if needle in k and v}


# ======================================================================
# 1. THE MECHANISM: ROUNDS REPEAT, INSIDE EVERY LIMIT
# ======================================================================
class TestRoundsRepeatInOneWindow:

    def test_a_filled_pair_is_followed_by_another_and_another(self, wide):
        drive(wide)
        sides = [s for s, _p, _q in wide.sent()]
        assert len(sides) >= 6, wide.sent()
        assert sides[:6] == ["DOWN", "UP"] * 3
        assert wide.pos("UP") == wide.pos("DOWN") > D("1.9")

    def test_both_legs_of_every_round_rest_together(self, wide):
        seen = drive(wide)
        rounds = len(wide.venue.sent) // 2
        assert seen["both_sides_resting"] >= rounds >= 3, seen

    def test_never_more_than_two_orders_rest_at_once(self, wide):
        seen = drive(wide)
        assert seen["max_resting"] == 2
        assert wide.r.limits.max_live_orders == 2

    def test_every_order_is_a_post_only_maker_inside_the_pair_ceiling(
            self, wide):
        drive(wide)
        assert {str(s["mode"]) for s in wide.venue.sent} == {"MAKER"}
        sent = wide.sent()
        for (s1, p1, _q1), (s2, p2, _q2) in zip(sent[0::2], sent[1::2]):
            assert {s1, s2} == {"UP", "DOWN"}
            assert p1 + p2 <= D("0.96")

    def test_the_cash_limit_is_what_ends_the_rounds(self, wide):
        drive(wide)
        spent = sum(p * q for _s, p, q in wide.sent())
        assert spent <= D("3.00"), spent
        assert reasons(wide, "CASH_COMMITMENT"), dict(wide.r.stats)

    def test_each_round_is_its_own_claimed_pair(self, wide):
        drive(wide)
        ex = wide.r.executor
        rounds = len(wide.venue.sent) // 2
        for r in range(rounds):
            assert ex.probe_legs_claimed(SLUG, r) == {"UP": True,
                                                      "DOWN": True}, r

    def test_unfilled_legs_are_cancelled_by_exact_id_and_replaced(self, wide):
        """Replenishment after a cancel: the TTL withdraws a leg nobody
        took, and the round is quoted again."""
        drive(wide, ticks=range(7, 300, 3), fill=lambda h, o: False)
        assert wide.pos("UP") == wide.pos("DOWN") == 0
        assert len(wide.venue.cancelled) >= 2
        assert set(wide.venue.cancelled) <= set(wide.venue.by_oid)
        assert len(wide.venue.sent) > 2, "nothing was re-quoted"
        assert len(resting(wide)) <= 2


class TestInventoryIsBalancedAcrossTheWindow:

    def test_a_stranded_leg_stops_every_new_opening(self, wide):
        """Portfolio-wide, not per-round: while one leg is unmatched the
        only order the engine may add is the one that matches it."""
        def only_down(h, oid):
            return str(h.venue.by_oid[oid]["side"]) == "DOWN"

        wide.at(6.5).l5_action()
        wide.tick(6.8)
        wide.venue.fill(wide.venue.oid("DOWN"))
        wide.tick(13.0)
        wide.book.set("0.87", "0.88")           # runs away from the UP leg
        for off in range(16, 200, 3):
            wide.tick(float(off))
        assert wide.pos("DOWN") == D("0.66") and wide.pos("UP") == 0
        after = wide.sent()[2:]
        assert all(side == "UP" for side, _p, _q in after), after
        assert [s for s, _p, _q in wide.sent()].count("DOWN") == 1

    def test_the_complement_is_never_bought_above_redemption(self, wide):
        wide.at(6.5).l5_action()
        wide.tick(6.8)
        wide.venue.fill(wide.venue.oid("DOWN"))
        wide.tick(13.0)
        wide.book.set("0.87", "0.88")
        for off in range(16, 200, 3):
            wide.tick(float(off))
        for side, px, _q in wide.sent()[2:]:
            assert px + D("0.44") <= D("1.00"), (side, px)

    def test_exposure_never_exceeds_the_cap(self, wide):
        """A matched pair has no worst case; an unmatched leg's is its
        cost. Either way inside $1.00."""
        drive(wide)
        up, dn = wide.pos("UP"), wide.pos("DOWN")
        legs = wide.r.store.leg_cost(SLUG)
        unmatched_cost = abs(D(str(legs.get("UP", 0)))
                             - D(str(legs.get("DOWN", 0)))) if up != dn \
            else D(0)
        assert unmatched_cost <= D("1.00")


class TestProductionIsUnchanged:

    def test_at_the_approved_limits_one_pair_fits_and_one_is_placed(
            self, prod):
        drive(prod)
        assert [s for s, _p, _q in prod.sent()] == ["DOWN", "UP"]
        assert reasons(prod, "CASH_COMMITMENT")

    def test_splitting_the_approved_cash_does_not_buy_more_rounds(
            self, tmp_path, monkeypatch):
        """$0.90 over three rounds is 0.2 contracts a round, and a 0.2
        round does not clear the one-cent floor. No configuration inside
        the approved cash turns one pair into several."""
        h = harness(tmp_path, monkeypatch, L5_TS_ROUND_ALLOCATIONS="3")
        drive(h)
        assert [(s, q) for s, _p, q in h.sent()] == [("DOWN", D("0.2")),
                                                     ("UP", D("0.2"))]
        assert reasons(h, "INCREMENTAL_EXPECTANCY_BELOW_FLOOR")


# ======================================================================
# 2. THE PRICING: A FURTHER ROUND MUST CLEAR OUR OWN SETTLED RECORD
# ======================================================================
class TestTheLedgersOwnRecordDecidesTheNextRound:

    def test_a_losing_record_refuses_the_second_round(self, wide):
        seed(wide, 25, "-0.0400")               # -0.0667 a contract
        drive(wide)
        assert [s for s, _p, _q in wide.sent()] == ["DOWN", "UP"]
        assert reasons(wide, "OWN_SETTLED_EXPECTANCY_BELOW_FLOOR")
        assert not reasons(wide, "CASH_COMMITMENT")

    def test_the_refusal_states_the_figure_it_acted_on(self, wide):
        seed(wide, 25, "-0.0400")
        wide.at(6.5).l5_action()
        wide.tick(6.8)
        for oid in resting(wide):
            wide.venue.fill(oid)
        wide.tick(13.0)
        out = wide.tick(19.0)
        rec = out["maybe_controller_open"]
        assert rec is not None
        assert "OWN_SETTLED_EXPECTANCY_BELOW_FLOOR" in str(
            rec.get("refused_reason"))

    def test_a_winning_record_lets_the_rounds_run(self, wide):
        seed(wide, 25, "0.0200")
        drive(wide)
        assert len(wide.venue.sent) >= 6
        assert not reasons(wide, "OWN_SETTLED")

    def test_a_thin_record_does_not_vote(self, wide):
        """Five windows is a coin flip, not evidence. Below the minimum the
        decision stands on its priors, exactly as it did before."""
        seed(wide, UD.OWN_EVIDENCE_MIN_WINDOWS - 1, "-0.0400")
        drive(wide)
        assert len(wide.venue.sent) >= 6
        assert not reasons(wide, "OWN_SETTLED")

    def test_a_winning_record_cannot_rescue_a_round_the_priors_refuse(
            self, tmp_path, monkeypatch):
        """IT CAN ONLY REFUSE. $0.90 over three rounds fails the floor on
        the priors; the best record imaginable does not change that."""
        h = harness(tmp_path, monkeypatch, L5_TS_ROUND_ALLOCATIONS="3")
        seed(h, 40, "0.5000")
        drive(h)
        assert len(h.venue.sent) == 2
        assert reasons(h, "INCREMENTAL_EXPECTANCY_BELOW_FLOOR")

    def test_the_first_round_of_a_window_is_not_gated_by_it(self, wide):
        """The first round is the owner's decision -- it is what an
        authorized window IS -- and a bad record must not silently turn an
        approved probe into a window that does nothing."""
        seed(wide, 40, "-0.5000")
        wide.at(6.5).l5_action()
        wide.tick(6.8)
        assert [s for s, _p, _q in wide.sent()] == ["DOWN", "UP"]

    def test_an_unreadable_record_does_not_open_a_round(self, wide,
                                                        monkeypatch):
        def boom(**kw):
            raise OSError("ledger unreadable")

        monkeypatch.setattr(wide.r.store, "own_settled_round_evidence", boom)
        drive(wide)
        assert len(wide.venue.sent) == 2
        assert reasons(wide, "OWN_SETTLED_EVIDENCE_UNREADABLE")

    def test_a_refusal_never_stops_inventory_being_completed(self, wide):
        """The record gates OPENING a round. A round with one leg placed is
        being completed, and its complement is never refused by it."""
        seed(wide, 40, "-0.5000")
        wide.at(6.5).l5_action()
        assert len(wide.venue.sent) == 1, "leg one, from the L5 action"
        wide.tick(6.8)
        assert [s for s, _p, _q in wide.sent()] == ["DOWN", "UP"]


# ======================================================================
# 3. WHAT THE LEDGER COUNTS
# ======================================================================
class _H:
    """Just enough of a harness for `seed`."""

    def __init__(self, tmp_path):
        self.r = collections.namedtuple("R", "store")(
            E.InventoryStore(str(tmp_path / "inv.json")))


class TestOwnSettledRoundEvidence:

    def test_it_is_net_per_contract_that_took_a_first_fill(self, tmp_path):
        h = _H(tmp_path)
        seed(h, 3, "0.0060")                    # three 0.6 pairs at 0.99
        seed(h, 1, "-0.3080", up="0", dn="0.7", series="cpc-btc-updown-15m-x")
        ev = h.r.store.own_settled_round_evidence()
        assert ev["windows"] == 4
        assert D(ev["contracts"]) == D("2.5")
        assert D(ev["net_usd"]) == D("-0.2900")
        assert D(ev["net_per_contract"]) == D("-0.1160")
        assert ev["windows_with_a_pair"] == 3
        assert ev["windows_with_a_stranded_leg"] == 1

    def test_a_pair_at_0_99_and_one_stranded_leg_in_four_is_a_loss(
            self, tmp_path):
        """The arithmetic the owner should see: three pairs earn 1.8 cents
        between them and the fourth window gives back 30.8."""
        h = _H(tmp_path)
        seed(h, 3, "0.0060")
        seed(h, 1, "-0.3080", up="0", dn="0.7", series="cpc-btc-updown-15m-x")
        v = UD.own_settled_verdict(
            dict(h.r.store.own_settled_round_evidence(), windows=20),
            qty="0.6", floor="0.01")
        assert v["usable"] and v["clears_floor"] is False
        assert D(v["expectancy_usd"]) < 0

    def test_only_windows_whose_money_is_known_are_counted(self, tmp_path):
        h = _H(tmp_path)
        seed(h, 2, "0.0060")
        seed(h, 2, "9.0000", series="cpc-btc-updown-15m-a",
             source="BOOK_PRICE")
        seed(h, 2, "9.0000", series="cpc-btc-updown-15m-b",
             unverified=["m|UP"])
        seed(h, 2, "9.0000", series="aec-nfl-")
        ev = h.r.store.own_settled_round_evidence()
        assert ev["windows"] == 2 and ev["skipped"] == 4
        assert D(ev["net_usd"]) == D("0.0120")

    def test_a_window_that_never_filled_is_not_an_observation(self, tmp_path):
        h = _H(tmp_path)
        seed(h, 3, "0", up="0", dn="0")
        ev = h.r.store.own_settled_round_evidence()
        assert ev["windows"] == 0 and ev["net_per_contract"] is None

    def test_it_listens_to_the_most_recent_windows_only(self, tmp_path):
        """So the gate adapts: a record that turns, turns the decision."""
        h = _H(tmp_path)
        seed(h, 60, "-0.0400")
        rows = h.r.store.state["retired_markets"]
        newest = sorted(rows, key=lambda k: rows[k]["t"])[-50:]
        for k in newest:
            rows[k]["settlement_pnl"] = "0.0200"
        ev = h.r.store.own_settled_round_evidence(last_n=50)
        assert ev["windows"] == 50
        assert D(ev["net_per_contract"]) == D("0.0333")

    def test_an_unreadable_row_is_skipped_not_guessed(self, tmp_path):
        h = _H(tmp_path)
        seed(h, 2, "0.0060")
        h.r.store.state["retired_markets"]["cpc-btc-updown-15m-bad"] = {
            "t": "x", "settlement_pnl": "not-a-number",
            "resolution_source": "VENUE_SETTLEMENT_PX",
            "up_qty": "0.6", "dn_qty": "0.6"}
        ev = h.r.store.own_settled_round_evidence()
        assert ev["windows"] == 2 and ev["skipped"] == 1


class TestTheVerdict:

    @pytest.mark.parametrize("ev,why", [
        (None, "NO_RECORD_SUPPLIED"),
        ({"windows": "many"}, "WINDOW_COUNT_UNREADABLE"),
        ({"windows": 3, "net_per_contract": "-0.5"},
         "TOO_FEW_SETTLED_WINDOWS"),
        ({"windows": 30, "net_per_contract": None}, "RECORD_UNREADABLE"),
    ])
    def test_a_record_that_cannot_be_used_does_not_vote(self, ev, why):
        v = UD.own_settled_verdict(ev, qty="1", floor="0.01")
        assert v["usable"] is False and v["why_not_used"] == why
        assert v["clears_floor"] is None

    def test_it_must_clear_the_floor_not_merely_be_positive(self):
        v = UD.own_settled_verdict({"windows": 30,
                                    "net_per_contract": "0.0100"},
                                   qty="0.6", floor="0.01")
        assert v["usable"] and v["clears_floor"] is False

    def test_reopen_decision_reports_the_record_it_used(self):
        base = dict(market="m", rounds_used=1, worst_leg_own_px="0.50",
                    bid="0.52", ask="0.53", tick="0.01", qty="0.66",
                    now_s=100.0, window_end_s=900.0,
                    exposure_headroom_usd="1.00", depth_ticks=2)
        ok = UD.reopen_decision(**base)
        assert ok["reopen"] is True
        assert ok["own_settled_evidence"]["why_not_used"] == \
            "NO_RECORD_SUPPLIED"
        bad = UD.reopen_decision(own_evidence={
            "windows": 30, "net_per_contract": "-0.0700"}, **base)
        assert bad["reopen"] is False
        assert bad["reason"] == "OWN_SETTLED_EXPECTANCY_BELOW_FLOOR"
        assert D(bad["own_settled_evidence"]["expectancy_usd"]) == \
            D("-0.0462")
        assert D(bad["expectancy_usd"]) > 0, "the priors alone said yes"

    def test_a_first_round_is_still_never_priced(self):
        r = UD.reopen_decision(
            market="m", rounds_used=0, worst_leg_own_px="0.50", bid="0.52",
            ask="0.53", tick="0.01", now_s=100.0, window_end_s=900.0,
            exposure_headroom_usd="1.00",
            own_evidence={"windows": 99, "net_per_contract": "-0.9"})
        assert r["reopen"] is True
        assert r["reason"] == "FIRST_ROUND_NOT_A_REOPENING"
