"""A round is spent by an ORDER, not by a proposal that was turned down.

OWNER DIRECTION, 2026-11-10: "Rejected proposals must not consume a trading
round. Stale plans should be recalculated against the current order book. A
round should only be charged when a qualifying order is actually submitted."

THE DEFECT, MEASURED ON 2026-11-10 2145z with eight rounds permitted. Round
0 was retired before any order existed. Rounds 1 and 2 traded and matched.
Rounds 3 to 7 were then spent in about 100 seconds without one order: each
reopening was refused on its economics, its plan stayed frozen, the book
moved, the plan stopped being placeable, and the round was retired for it.
The window then answered ACCUMULATION_EXHAUSTED 223 times.

Reproduced below THROUGH THE RUNNER, on the harness of
`test_l5_runner_pair_and_cancel_state.py`: a real Runner, a FaithfulExecutor,
a scripted venue and book. Off unless `L5_TS_ROUND_CHARGED_ON_SUBMISSION` is
exactly ON.
"""
from decimal import Decimal as D

import pytest

import tests.test_l5_ladder_runner as LAD
from src import l5_live_authorization as AUTH
from src import l5_live_execution as E

SLUG = LAD.SLUG
ENV = {"L5_TS_OWN_EVIDENCE_GATE": "WAIVED_BY_OWNER",
       "L5_MAX_CASH_DEPLOYED_USD": "3.00"}
#: a book that keeps moving more than a frozen plan can follow
BOOKS = [("0.52", "0.54"), ("0.62", "0.64"), ("0.44", "0.46"),
         ("0.58", "0.60"), ("0.40", "0.42"), ("0.55", "0.57"),
         ("0.66", "0.68"), ("0.47", "0.49"), ("0.60", "0.62"),
         ("0.50", "0.52")]
REFUSED = {"reopen": False, "reason": "INCREMENTAL_EXPECTANCY_BELOW_FLOOR"}


def start(tmp_path, monkeypatch, flag="", **env):
    monkeypatch.delenv(E.ROUND_ON_SUBMISSION_ENV, raising=False)
    kw = dict(ENV, **env)
    if flag:
        kw[E.ROUND_ON_SUBMISSION_ENV] = flag
    h = LAD.harness(tmp_path, monkeypatch, **kw)
    h.at(6.5).l5_action()
    for off in (6.8, 7.8, 8.8):
        h.tick(off)
    assert len(h.venue.sent) == 2                  # one pair, round 0
    return h


def one_pair_then_refused_reopenings(h, monkeypatch, books=BOOKS):
    """Round 0 matches; every later reopening is refused while the book
    walks. Returns the runner's clock offset."""
    ex = h.r.executor
    monkeypatch.setattr(ex, "probe_reopen_decision",
                        lambda *a, **k: dict(REFUSED))
    for oid in LAD.resting_ids(h):
        h.venue.fill(oid)
    LAD.book_fills(h, 12)
    assert h.pos("UP") == h.pos("DOWN") == D("1")
    off = 30.0
    for bid, ask in books:
        h.book.set(bid, ask)
        for _ in range(3):
            off += 3.5
            h.tick(off)
    return off


def retired(h):
    return sorted((h.r.store.state.get("probe_rounds_retired") or {}))


def refusals(h, needle):
    return sum(v for k, v in h.r.stats.items()
               if k.startswith("controller_open:") and needle in k)


# ======================================================================
# 1. THE DEFECT, AS DEPLOYED
# ======================================================================

class TestTheDefectAsDeployed:

    def test_refused_proposals_spend_every_round(self, tmp_path, monkeypatch):
        h = start(tmp_path, monkeypatch)
        one_pair_then_refused_reopenings(h, monkeypatch)
        assert len(retired(h)) == 7                 # rounds 1..7, no orders
        assert len(h.venue.sent) == 2
        assert h.r.executor.probe_round(SLUG) is None
        assert refusals(h, "ACCUMULATION_EXHAUSTED") > 0

    def test_and_the_window_cannot_trade_when_the_economics_return(
            self, tmp_path, monkeypatch):
        h = start(tmp_path, monkeypatch)
        ex = h.r.executor
        real = ex.probe_reopen_decision
        off = one_pair_then_refused_reopenings(h, monkeypatch)
        monkeypatch.setattr(ex, "probe_reopen_decision", real)
        for _ in range(4):
            off += 3.5
            h.tick(off)
        assert len(h.venue.sent) == 2

    @pytest.mark.parametrize("value", ["", "on", "1", "true", "OFF"])
    def test_only_the_exact_token_changes_it(self, tmp_path, monkeypatch,
                                             value):
        h = start(tmp_path, monkeypatch)
        monkeypatch.setenv(E.ROUND_ON_SUBMISSION_ENV, value)
        one_pair_then_refused_reopenings(h, monkeypatch)
        assert len(retired(h)) == 7


# ======================================================================
# 2. WITH THE SWITCH: NO ORDER, NO ROUND SPENT
# ======================================================================

class TestARoundIsSpentByAnOrder:

    def test_refused_proposals_spend_nothing(self, tmp_path, monkeypatch):
        h = start(tmp_path, monkeypatch, "ON")
        one_pair_then_refused_reopenings(h, monkeypatch)
        assert retired(h) == []
        assert h.r.executor.probe_round(SLUG) == 1
        assert refusals(h, "ACCUMULATION_EXHAUSTED") == 0
        assert refusals(h, "REOPENING_NOT_JUSTIFIED") >= 3 * len(BOOKS)
        assert len(h.venue.sent) == 2               # and nothing was sent

    def test_the_pair_is_priced_from_the_book_as_it_is_now(
            self, tmp_path, monkeypatch):
        """Economics return with the book at 0.50/0.52: the next pair is two
        ticks behind THAT touch, not behind the one a refused plan saw."""
        h = start(tmp_path, monkeypatch, "ON")
        ex = h.r.executor
        real = ex.probe_reopen_decision
        off = one_pair_then_refused_reopenings(h, monkeypatch)
        monkeypatch.setattr(ex, "probe_reopen_decision", real)
        for _ in range(4):
            off += 3.5
            h.tick(off)
        assert sorted(h.sent()[2:]) == [("DOWN", D("0.46"), D("1")),
                                        ("UP", D("0.48"), D("1"))]
        assert ex.probe_round_never_placed(SLUG, 1) is False

    def test_every_proposal_was_judged_on_a_fresh_plan(
            self, tmp_path, monkeypatch):
        """The reopening decision is handed the plan for the CURRENT book
        each time, so a refusal is always a refusal of today's prices."""
        h = start(tmp_path, monkeypatch, "ON")
        seen = []

        def judge(market, rnd, plan, **kw):
            seen.append((kw["bid"], tuple(sorted(
                (l["side"], str(l["price"])) for l in plan["legs"]))))
            return dict(REFUSED)
        ex = h.r.executor
        for oid in LAD.resting_ids(h):
            h.venue.fill(oid)
        monkeypatch.setattr(ex, "probe_reopen_decision", judge)
        LAD.book_fills(h, 12)
        off = 30.0
        for bid, ask in BOOKS:
            h.book.set(bid, ask)
            off += 3.5
            h.tick(off)
        by_bid = {}
        for bid, legs in seen:
            by_bid.setdefault(str(bid), set()).add(legs)
        assert len(by_bid) >= len({b for b, _a in BOOKS}) - 1
        for bid, plans in by_bid.items():
            for legs in plans:
                up = D(dict(legs)["UP"])
                assert up == D(bid) - D("0.02"), (bid, legs)

    def test_a_round_that_submitted_is_still_spent(self, tmp_path,
                                                   monkeypatch):
        """THE BOUND IS NOT LOOSENED. With two rounds permitted, two pairs
        placed is the end of the window, switch or no switch."""
        h = start(tmp_path, monkeypatch, "ON", L5_ACC_MAX_ROUNDS="2")
        off = 12.0
        for _ in range(12):
            for oid in LAD.resting_ids(h):
                h.venue.fill(oid)
            off += 3.5
            h.tick(off)
        assert len(h.venue.sent) == 4
        assert h.r.executor.probe_round(SLUG) is None
        assert refusals(h, "ACCUMULATION_EXHAUSTED") > 0

    def test_submissions_are_still_one_claim_per_leg_per_round(
            self, tmp_path, monkeypatch):
        h = start(tmp_path, monkeypatch, "ON")
        ex = h.r.executor
        real = ex.probe_reopen_decision
        off = one_pair_then_refused_reopenings(h, monkeypatch)
        monkeypatch.setattr(ex, "probe_reopen_decision", real)
        for _ in range(4):
            off += 3.5
            h.tick(off)
        coids = [s["client_order_id"] for s in h.venue.sent]
        assert len(coids) == len(set(coids)) == 4
        for rnd in (0, 1):
            for leg in AUTH.PROBE_LEGS:
                assert AUTH.claim_held(AUTH.accumulation_sid(
                    AUTH.experiment_id(), SLUG, rnd, leg))


# ======================================================================
# 3. A ROUND WITH AN ORDER IN IT KEEPS ITS FROZEN PLAN
# ======================================================================

class TestAPlacedRoundIsUntouched:

    def test_the_plan_of_a_working_round_is_not_dropped(self, tmp_path,
                                                        monkeypatch):
        h = start(tmp_path, monkeypatch, "ON")
        ex = h.r.executor
        frozen = ex.probe_stored_plan(SLUG, 0)
        assert frozen is not None
        assert ex.probe_round_never_placed(SLUG, 0) is False
        h.book.set("0.60", "0.62")
        for off in (12.0, 15.5, 19.0):
            h.tick(off)
        assert ex.probe_stored_plan(SLUG, 0) == frozen

    def test_one_claimed_leg_is_enough(self, tmp_path, monkeypatch):
        h = start(tmp_path, monkeypatch, "ON")
        ex = h.r.executor
        held = {AUTH.accumulation_sid(AUTH.experiment_id(), SLUG, 5, "DOWN")}
        real = AUTH.claim_held
        monkeypatch.setattr(AUTH, "claim_held",
                            lambda sid, path=None: sid in held or real(sid))
        assert ex.probe_round_never_placed(SLUG, 5) is False
        assert ex.probe_round_never_placed(SLUG, 6) is True

    def test_unreadable_claims_are_not_an_empty_round(self, tmp_path,
                                                      monkeypatch):
        h = start(tmp_path, monkeypatch, "ON")
        ex = h.r.executor

        def boom(*a, **k):
            raise OSError("claim ledger unreadable")
        monkeypatch.setattr(AUTH, "claim_held", boom)
        assert ex.probe_round_never_placed(SLUG, 3) is False
        assert ex.probe_round_never_placed("", 3) is False

    def test_dropping_a_plan_deletes_nothing_else(self, tmp_path,
                                                  monkeypatch):
        h = start(tmp_path, monkeypatch, "ON")
        ex = h.r.executor
        st = h.r.store.state
        before = {k: v for k, v in st.items() if k != "probe_plans"}
        ex.probe_store_plan(SLUG, {"ok": True, "legs": []}, 4)
        assert ex.probe_discard_plan(SLUG, 4) is True
        assert ex.probe_discard_plan(SLUG, 4) is False
        assert ex.probe_stored_plan(SLUG, 0) is not None
        assert {k: v for k, v in st.items() if k != "probe_plans"} == before


# ======================================================================
# 4. WHAT THE PROCESS REPORTS: ROUNDS SPENT AGAINST ORDERS SUBMITTED
# ======================================================================

class TestTheCycleIsReported:

    def test_as_deployed_five_rounds_show_as_spent_without_an_order(
            self, tmp_path, monkeypatch):
        h = start(tmp_path, monkeypatch)
        one_pair_then_refused_reopenings(h, monkeypatch)
        rl = h.r.executor.round_lifecycle(SLUG)
        assert rl["charged_on_submission"] is False
        assert rl["rounds_permitted"] == 8
        assert rl["rounds_with_a_submitted_order"] == 1
        assert rl["legs_submitted"] == 2
        assert rl["rounds_retired_without_an_order"] == 7
        assert rl["rounds_spent"] == 8 and rl["rounds_left"] == 0
        assert rl["next_round"] is None

    def test_with_the_switch_only_the_round_that_submitted_is_spent(
            self, tmp_path, monkeypatch):
        h = start(tmp_path, monkeypatch, "ON")
        one_pair_then_refused_reopenings(h, monkeypatch)
        rl = h.r.executor.round_lifecycle(SLUG)
        assert rl["charged_on_submission"] is True
        assert rl["rounds_with_a_submitted_order"] == 1
        assert rl["rounds_retired_without_an_order"] == 0
        assert rl["rounds_spent"] == 1 and rl["rounds_left"] == 7
        assert rl["next_round"] == 1
        assert rl["proposals_repriced_since_start"] >= len(BOOKS)

    def test_all_eight_rounds_can_each_submit_a_pair(self, tmp_path,
                                                     monkeypatch):
        """EIGHT GENUINE EXECUTION ATTEMPTS. Every pair fills, the book
        keeps moving, and the window ends only when eight rounds have each
        put their two orders on the venue."""
        h = start(tmp_path, monkeypatch, "ON", L5_MAX_CASH_DEPLOYED_USD="40",
                  L5_MAX_TOTAL_EXPOSURE_USD="40")
        ex = h.r.executor
        monkeypatch.setattr(ex, "probe_reopen_decision",
                            lambda *a, **k: {"reopen": True, "reason": "T"})
        off = 12.0
        for i in range(60):
            for oid in LAD.resting_ids(h):
                h.venue.fill(oid)
            bid, ask = BOOKS[i % len(BOOKS)]
            h.book.set(bid, ask)
            off += 3.5
            h.tick(off)
        rl = ex.round_lifecycle(SLUG)
        assert rl["rounds_with_a_submitted_order"] == 8, (rl, h.sent())
        assert rl["legs_submitted"] == 16 == len(h.venue.sent)
        assert rl["rounds_retired_without_an_order"] == 0
        assert rl["rounds_left"] == 0 and rl["next_round"] is None
        assert refusals(h, "ACCUMULATION_EXHAUSTED") > 0

    def test_the_heartbeat_and_window_record_carry_it(self, tmp_path,
                                                      monkeypatch):
        monkeypatch.setenv("L5_HEDGE_CASH_AT_LIMIT", "ON")
        h = start(tmp_path, monkeypatch, "ON")
        one_pair_then_refused_reopenings(h, monkeypatch)
        brief = h.r.cycle_report(SLUG, brief=True)
        assert set(brief) == {"market", "rounds"}
        assert brief["rounds"]["charged_on_submission"] is True
        full = h.r.cycle_report(SLUG)
        assert full["inventory"]["matched_contracts"] == "1"
        assert D(full["inventory"]["average_pair_cost"]) == D("0.94")
        assert full["inventory"]["unmatched_side"] is None
        assert full["pairs_opened_after_the_first"] == 0
        assert full["reservations"]["max_cash_deployed_usd"] == "3.00"
        assert D(full["reservations"]["cash_held_in_fills_usd"]) == D("0.94")
        assert h.r.effective_cash_gate()["hedge_cash_at_limit"] is True
        assert h.r.cycle_report(None) == {"market": None}

    def test_the_most_that_rested_together_is_remembered(self, tmp_path,
                                                         monkeypatch):
        h = start(tmp_path, monkeypatch, "ON")
        h.r._note_cycle_peaks(SLUG, {"orders": [
            {"side": "UP"}, {"side": "DOWN"}, {"side": "DOWN"}],
            "committed_usd": "0.61", "worst_case_with_resting": "-0.42"})
        h.r._note_cycle_peaks(SLUG, {"orders": [{"side": "UP"}],
                                     "committed_usd": "0.20",
                                     "worst_case_with_resting": "-0.10"})
        h.r._note_cycle_peaks(SLUG, {"orders": None})
        pk = h.r.cycle_report(SLUG)["resting_peaks"]
        assert pk["samples"] == 3
        assert pk["peak_resting_orders"] == 3
        assert pk["peak_resting_up"] == 1 and pk["peak_resting_down"] == 2
        assert pk["samples_with_both_sides_resting"] == 1
        assert pk["peak_committed_usd"] == "0.61"
        assert pk["peak_worst_case_with_resting_usd"] == "0.42"
