"""The reopening floor is one cent PER CONTRACT, as its own note says.

`REOPEN_MIN_EDGE_USD` is documented as "one tick on a one-contract pair",
and the note beside it works the deployed case per contract: 0.727 * 0.05 +
0.273 * (-0.0236) = +0.0299, "above the floor". The code compared that
floor with the expectancy of the WHOLE ROUND, so at production's 0.21 to
0.31-contract clips the same economics read $0.0063 to $0.0093 and every
reopening was refused -- 2026-11-10 2145z, round 7, verbatim below.

Off unless `L5_REOPEN_FLOOR_PER_CONTRACT` is exactly ON. Nothing about the
probabilities, the adverse-selection figure or the capacity bounds changes,
and an expectancy at or below zero is refused at any size.
"""
from decimal import Decimal as D

import pytest

import tests.test_l5_ladder_runner as LAD
from src import l5_live_execution as E
from src import l5_unmatched_disposition as UD

ENV = UD.REOPEN_FLOOR_PER_CONTRACT_ENV


def decide(qty="0.21", **over):
    kw = dict(market="m", rounds_used=2, worst_leg_own_px=D("0.60"),
              qty=D(str(qty)), bid=D("0.62"), ask=D("0.63"), tick=D("0.01"),
              depth_ticks=2, now_s=150.0, window_end_s=900.0,
              exposure_headroom_usd=D("5"))
    kw.update(over)
    return UD.reopen_decision(**kw)


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setenv(ENV, "ON")


@pytest.fixture()
def off(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


class TestTheRecordedRefusal:

    def test_as_deployed_a_fifth_of_a_contract_can_never_reopen(self, off):
        r = decide()
        assert r["reopen"] is False
        assert r["reason"] == "INCREMENTAL_EXPECTANCY_BELOW_FLOOR"
        assert r["expectancy_usd"] == "0.0063"          # the 2145z record
        assert r["pair_edge_usd"] == "0.0105"
        assert r["min_edge_usd"] == "0.01"
        assert r["min_edge_basis"] == "FIXED_DOLLARS_WHATEVER_THE_SIZE"

    @pytest.mark.parametrize("qty", ["0.21", "0.27", "0.31", "0.33"])
    def test_no_production_clip_clears_it(self, off, qty):
        assert decide(qty)["reason"] == "INCREMENTAL_EXPECTANCY_BELOW_FLOOR"

    @pytest.mark.parametrize("value", ["", "on", "1", "true", "YES"])
    def test_only_the_exact_token_changes_it(self, monkeypatch, value):
        monkeypatch.setenv(ENV, value)
        assert decide()["reopen"] is False


class TestPerContract:

    def test_the_same_round_is_judged_on_three_cents_a_contract(self, on):
        r = decide()
        assert r["reopen"] is True, r
        assert r["expectancy_usd"] == "0.0063"          # unchanged
        assert D(r["min_edge_usd"]) == D("0.0021")      # 0.01 * 0.21
        assert r["min_edge_one_contract_usd"] == "0.01"
        assert r["min_edge_basis"] == "PER_CONTRACT_OF_THIS_ROUND"

    def test_at_one_contract_nothing_changes(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        a = decide("1")
        monkeypatch.setenv(ENV, "ON")
        b = decide("1")
        for k in ("reopen", "reason", "expectancy_usd", "min_edge_usd",
                  "breakeven_single_outcome_usd"):
            assert a[k] == b[k], k

    @pytest.mark.parametrize("qty", ["0.05", "0.21", "0.5", "1", "3"])
    @pytest.mark.parametrize("single", ["-0.0236", "-0.10", "-0.12", "-0.20"])
    def test_the_verdict_no_longer_depends_on_the_size(self, on, qty, single):
        """What one contract earns decides it. Size is capacity's business."""
        one = decide("1", single_outcome_usd=D(single))
        this = decide(qty, single_outcome_usd=D(single))
        assert this["reopen"] == one["reopen"], (qty, single, this)

    def test_a_larger_round_needs_proportionally_more(self, on):
        assert D(decide("3")["min_edge_usd"]) == D("0.03")

    @pytest.mark.parametrize("qty", ["0.05", "0.21", "1", "3"])
    def test_no_expectancy_is_refused_at_any_size(self, on, qty):
        r = decide(qty, single_outcome_usd=D("-0.20"))
        assert D(r["expectancy_usd"]) < 0
        assert r["reopen"] is False

    def test_a_zero_tick_edge_is_refused(self, on):
        """Best bid on a one-tick spread: a 0.99 pair. One cent a contract
        of gross edge does not cover the unmatched-leg cost."""
        r = decide(depth_ticks=0)
        assert r["reopen"] is False

    def test_capacity_still_refuses_first(self, on):
        r = decide(exposure_headroom_usd=D("0.05"))
        assert r["reopen"] is False
        assert r["reason"] != "INCREMENTAL_EXPECTANCY_BELOW_FLOOR"


class TestThroughTheRunner:
    """Production's $0.90 and one pair at a time: with rounds charged on
    submission the window is no longer cut short by retirements -- and with
    the floor compared per contract it reopens after a completed pair."""
    ENVS = {"L5_MAX_CASH_DEPLOYED_USD": "0.90",
            "L5_TS_ROUND_ALLOCATIONS": "4",
            "L5_TS_ROUND_CHARGED_ON_SUBMISSION": "ON"}

    def cycle(self, tmp_path, monkeypatch, **env):
        monkeypatch.delenv(ENV, raising=False)
        h = LAD.harness(tmp_path, monkeypatch, **dict(self.ENVS, **env))
        h.at(6.5).l5_action()
        off = 6.8
        for _ in range(40):
            for oid in LAD.resting_ids(h):
                h.venue.fill(oid)
            off += 3.5
            h.tick(off)
        return h

    def test_as_deployed_one_pair_and_then_only_refusals(self, tmp_path,
                                                         monkeypatch):
        h = self.cycle(tmp_path, monkeypatch)
        assert len(h.venue.sent) == 2, h.sent()
        assert LAD.reasons(h, "INCREMENTAL_EXPECTANCY_BELOW_FLOOR")
        rl = h.r.executor.round_lifecycle(LAD.SLUG)
        assert rl["rounds_with_a_submitted_order"] == 1
        assert rl["rounds_left"] == 7                 # none wasted, none used

    def test_per_contract_it_reopens_until_the_cash_is_committed(
            self, tmp_path, monkeypatch):
        h = self.cycle(tmp_path, monkeypatch, **{ENV: "ON"})
        rl = h.r.executor.round_lifecycle(LAD.SLUG)
        assert rl["rounds_with_a_submitted_order"] == 5, (rl, h.sent())
        assert rl["legs_submitted"] == 10 == len(h.venue.sent)
        assert {q for _s, _p, q in h.sent()} == {D("0.15")}
        assert rl["rounds_retired_without_an_order"] == 0
        assert rl["rounds_left"] == 3                 # cash ended it, not rounds
        assert h.pos("UP") == h.pos("DOWN")
        assert sum(p * q for _s, p, q in h.sent()) <= D("0.90")
        assert LAD.reasons(h, "CASH_COMMITMENT")


# ======================================================================
# THE OWNER'S WAIVER OF THE OWN-RECORD GATE SKIPS THAT GATE AND NOTHING ELSE
# ======================================================================

BAD_RECORD = {"windows": 50, "net_per_contract": "-0.0287",
              "windows_with_a_pair": 33, "windows_with_a_stranded_leg": 44}


class TestWhatTheWaiverDoesAndDoesNotSkip:
    """2026-11-11 0030z and 0045z: one pair each, then every reopening
    refused OWN_SETTLED_EXPECTANCY_BELOW_FLOOR on a 50-window record of
    -2.87c a contract. The waiver is read in exactly one place, after the
    current-book expectancy has already passed."""

    def test_the_record_refuses_when_enforced(self, on, monkeypatch):
        monkeypatch.delenv(UD.OWN_EVIDENCE_GATE_ENV, raising=False)
        r = decide("0.20", own_evidence=BAD_RECORD)
        assert r["reason"] == "OWN_SETTLED_EXPECTANCY_BELOW_FLOOR"
        assert r["own_evidence_gate"] == "ENFORCED"
        assert r["own_settled_evidence"]["net_per_contract"] == "-0.0287"

    def test_waived_the_same_round_reopens_and_says_so(self, on, monkeypatch):
        monkeypatch.setenv(UD.OWN_EVIDENCE_GATE_ENV, "WAIVED_BY_OWNER")
        r = decide("0.20", own_evidence=BAD_RECORD)
        assert r["reopen"] is True
        assert r["own_evidence_gate"] == "WAIVED_BY_OWNER"
        assert r["own_settled_evidence"]["clears_floor"] is False   # recorded

    @pytest.mark.parametrize("value", ["", "waived", "1", "ON", "YES"])
    def test_only_the_exact_token_waives(self, on, monkeypatch, value):
        monkeypatch.setenv(UD.OWN_EVIDENCE_GATE_ENV, value)
        assert decide("0.20", own_evidence=BAD_RECORD)["reopen"] is False

    def test_the_current_book_still_has_to_pay(self, on, monkeypatch):
        """Waived, a best-bid pair on a one-tick spread is still refused on
        today's book, before the record is ever consulted."""
        monkeypatch.setenv(UD.OWN_EVIDENCE_GATE_ENV, "WAIVED_BY_OWNER")
        r = decide("0.20", depth_ticks=0, own_evidence=BAD_RECORD)
        assert r["reason"] == "INCREMENTAL_EXPECTANCY_BELOW_FLOOR"
        r = decide("0.20", single_outcome_usd=D("-0.20"),
                   own_evidence=BAD_RECORD)
        assert r["reason"] == "INCREMENTAL_EXPECTANCY_BELOW_FLOOR"

    def test_capacity_still_refuses(self, on, monkeypatch):
        monkeypatch.setenv(UD.OWN_EVIDENCE_GATE_ENV, "WAIVED_BY_OWNER")
        r = decide("0.20", exposure_headroom_usd=D("0.05"),
                   own_evidence=BAD_RECORD)
        assert r["reopen"] is False
        assert "EXPECTANCY" not in str(r["reason"])

    def test_the_waiver_is_read_in_one_place_only(self):
        import pathlib
        hits = []
        for f in sorted(pathlib.Path("src").glob("*.py")):
            n = f.read_text().count("own_evidence_gate_waived()")
            if n:
                hits.append((f.name, n))
        assert hits == [("l5_unmatched_disposition.py", 2)], hits   # def + use

    def test_through_the_runner_cash_and_pair_ceiling_still_end_it(
            self, tmp_path, monkeypatch):
        """Production's $0.90, three allocations, one pair at a time, the
        record seeded NEGATIVE and the gate waived: four pairs of 0.20,
        every one inside the 0.96 opening ceiling, then the cash cap."""
        monkeypatch.delenv(ENV, raising=False)
        h = LAD.harness(tmp_path, monkeypatch, **{
            "L5_MAX_CASH_DEPLOYED_USD": "0.90", "L5_TS_ROUND_ALLOCATIONS": "3",
            "L5_TS_ROUND_CHARGED_ON_SUBMISSION": "ON", ENV: "ON",
            "L5_HEDGE_CASH_AT_LIMIT": "ON",
            UD.OWN_EVIDENCE_GATE_ENV: "WAIVED_BY_OWNER"})
        LAD.seed(h, 30, D("-0.02"))                 # a losing settled record
        h.book.set("0.50", "0.51")
        h.at(6.5).l5_action()
        off = 6.8
        for _ in range(50):
            for oid in LAD.resting_ids(h):
                h.venue.fill(oid)
            off += 3.5
            h.tick(off)
        rl = h.r.executor.round_lifecycle(LAD.SLUG)
        assert rl["rounds_with_a_submitted_order"] == 4, (rl, h.sent())
        assert {q for _s, _p, q in h.sent()} == {D("0.20")}
        ups = [p for s, p, _q in h.sent() if s == "UP"]
        dns = [p for s, p, _q in h.sent() if s == "DOWN"]
        assert all(u + d <= D("0.96") for u, d in zip(ups, dns))
        assert h.pos("UP") == h.pos("DOWN") == D("0.80")
        assert sum(p * q for _s, p, q in h.sent()) <= D("0.90")
        assert LAD.reasons(h, "CASH_COMMITMENT")

    def test_and_without_the_waiver_that_same_record_stops_it_at_one(
            self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        monkeypatch.delenv(UD.OWN_EVIDENCE_GATE_ENV, raising=False)
        h = LAD.harness(tmp_path, monkeypatch, **{
            "L5_MAX_CASH_DEPLOYED_USD": "0.90", "L5_TS_ROUND_ALLOCATIONS": "3",
            "L5_TS_ROUND_CHARGED_ON_SUBMISSION": "ON", ENV: "ON"})
        LAD.seed(h, 30, D("-0.02"))
        h.book.set("0.50", "0.51")
        h.at(6.5).l5_action()
        off = 6.8
        for _ in range(30):
            for oid in LAD.resting_ids(h):
                h.venue.fill(oid)
            off += 3.5
            h.tick(off)
        assert h.r.executor.round_lifecycle(LAD.SLUG)[
            "rounds_with_a_submitted_order"] == 1
        assert LAD.reasons(h, "OWN_SETTLED_EXPECTANCY_BELOW_FLOOR")
