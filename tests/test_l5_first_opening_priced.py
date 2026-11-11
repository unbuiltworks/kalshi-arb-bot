"""The first opening of a window is priced like every other (default off).

THE DECISION THAT LET IT THROUGH, in two places:

  src/l5_unmatched_disposition.py  reopen_decision
      `if used_rounds <= 0: return reopen=True,
       reason FIRST_ROUND_NOT_A_REOPENING,
       economics_not_evaluated_for_the_first_round=True`
  src/l5_live_execution.py         probe_reopen_decision
      `if used <= 0: return reopen=True,
       reason FIRST_EXPOSURE_TAKING_ROUND_NOT_A_REOPENING`

So the capacity test, the expectancy floor and what our own settled windows
on this venue have paid were consulted for every round EXCEPT the one each
window always takes. With a settled record that says an opening loses, the
engine still opened it.

`L5_TS_FIRST_OPENING_PRICED=ON` sends the first round through the same
decision. It can only refuse an opening; it never touches a completion,
and the owner's `L5_TS_OWN_EVIDENCE_GATE=WAIVED_BY_OWNER` applies to the
first round exactly as it applies to the rest.

WHAT THIS IS NOT. It is not a filter that tells a good entry from a bad
one. Replayed in time order on 108 venue-settled markets
(analysis/l5/opening_economics_20261111.py) it would have refused 24
windows -- 12 that made money and 12 that lost, +$0.90 between them. It
stops the engine opening while its own trailing record is negative.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

import tests.test_l5_continuous_config as CC
import tests.test_l5_ladder_runner as LAD
from src import l5_unmatched_disposition as UD

ENV = UD.FIRST_OPENING_PRICED_ENV
BASE = dict(CC.PROPOSED, L5_MAX_CASH_DEPLOYED_USD="0.90",
            L5_TS_ROUND_ALLOCATIONS="3", L5_TS_CONCURRENT_ROUNDS="1",
            L5_MAX_LIVE_ORDERS="4", L5_ACC_MAX_ROUNDS="8",
            PMUS_RESIDUAL_CAP_K="1")
BASE.pop(UD.OWN_EVIDENCE_GATE_ENV)          # the gate is ENFORCED here
LOSING, WINNING = D("-0.02"), D("0.03")


def window(tmp_path, monkeypatch, *, on, record=LOSING, n=30, waive=False,
           ticks=20):
    env = dict(BASE)
    if on:
        env[ENV] = "ON"
    if waive:
        env[UD.OWN_EVIDENCE_GATE_ENV] = "WAIVED_BY_OWNER"
    h = CC.limits(LAD.harness(tmp_path, monkeypatch, **env))
    if n:
        LAD.seed(h, n, record)
    h.book.set("0.50", "0.51")
    h.at(6.5).l5_action()
    off = 6.8
    for _ in range(ticks):
        off += 3.0
        h.tick(off)
    return h, off


def refused(h, why):
    return sum(v for k, v in h.r.stats.items() if why in k)


class TestTheSwitch:

    @pytest.mark.parametrize("raw", [None, "", "on", "1", "ON ", "YES"])
    def test_only_the_exact_token(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv(ENV, raising=False)
        else:
            monkeypatch.setenv(ENV, raw)
        assert UD.first_opening_priced() is False

    def test_the_exact_token(self, monkeypatch):
        monkeypatch.setenv(ENV, "ON")
        assert UD.first_opening_priced() is True


class TestTheDefect:

    def test_off_a_losing_record_does_not_stop_the_first_opening(
            self, tmp_path, monkeypatch):
        """Thirty settled windows at -3.3c a contract, the gate enforced,
        and the engine opens anyway."""
        h, _off = window(tmp_path, monkeypatch, on=False)
        own = h.r.store.own_settled_round_evidence()
        assert own["windows"] == 30 and D(own["net_per_contract"]) < 0
        assert sorted(s for s, _p, _q in h.sent()) == ["DOWN", "UP"]
        assert refused(h, "OWN_SETTLED_EXPECTANCY_BELOW_FLOOR") == 0

    def test_off_the_decision_says_it_never_looked(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        r = UD.reopen_decision(
            market="m", rounds_used=0, locked_pair_pnl_usd=D(0),
            worst_leg_own_px=D("0.48"), qty=D("0.20"), bid=D("0.50"),
            ask=D("0.51"), tick=D("0.01"), depth_ticks=2, now_s=10.0,
            window_end_s=900.0, exposure_headroom_usd=D("1.00"),
            outstanding_worst_case_usd=D(0), daily_loss_room_usd=D("5"),
            own_evidence={"windows": 30, "net_per_contract": "-0.0333"})
        assert r["reopen"] is True
        assert r["economics_not_evaluated_for_the_first_round"] is True


class TestTheCorrection:

    def test_on_a_losing_record_refuses_the_first_opening(
            self, tmp_path, monkeypatch):
        h, _off = window(tmp_path, monkeypatch, on=True)
        assert list(h.sent()) == []
        assert refused(h, "OWN_SETTLED_EXPECTANCY_BELOW_FLOOR") >= 10
        assert h.pos("UP") == h.pos("DOWN") == 0
        assert h.r.store.orders_uncertain() is None

    def test_on_a_winning_record_opens_exactly_as_before(
            self, tmp_path, monkeypatch):
        on, _ = window(tmp_path / "a", monkeypatch, on=True, record=WINNING)
        off, _ = window(tmp_path / "b", monkeypatch, on=False,
                        record=WINNING)
        assert list(on.sent()) == list(off.sent()) != []

    def test_on_the_owners_waiver_covers_the_first_round_too(
            self, tmp_path, monkeypatch):
        h, _off = window(tmp_path, monkeypatch, on=True, waive=True)
        assert sorted(s for s, _p, _q in h.sent()) == ["DOWN", "UP"]

    def test_on_a_record_too_short_to_vote_does_not_refuse(
            self, tmp_path, monkeypatch):
        h, _off = window(tmp_path, monkeypatch, on=True, n=5)
        assert sorted(s for s, _p, _q in h.sent()) == ["DOWN", "UP"]

    def test_on_the_prior_is_applied_to_the_first_round(self, monkeypatch):
        """A plan with no edge in it: refused on the expectancy floor for
        round 0 exactly as for round 1."""
        monkeypatch.setenv(ENV, "ON")
        kw = dict(market="m", locked_pair_pnl_usd=D(0),
                  worst_leg_own_px=D("0.50"), qty=D("0.20"), bid=D("0.50"),
                  ask=D("0.51"), tick=D("0.01"), depth_ticks=0, now_s=10.0,
                  window_end_s=900.0, exposure_headroom_usd=D("1.00"),
                  outstanding_worst_case_usd=D(0),
                  daily_loss_room_usd=D("5"))
        first = UD.reopen_decision(rounds_used=0, **kw)
        later = UD.reopen_decision(rounds_used=1, **kw)
        assert first["reopen"] is False and later["reopen"] is False
        assert first["reason"] == later["reason"]

    def test_on_a_completion_is_never_priced_as_an_opening(
            self, tmp_path, monkeypatch):
        """Open on a winning record, fill one leg, then let the record turn
        losing: the other leg is still worked."""
        h, off = window(tmp_path, monkeypatch, on=True, record=WINNING,
                        ticks=3)
        for oid in LAD.resting_ids(h, "DOWN"):
            h.venue.fill(oid)
        h.tick(off + 3.0)
        h.r.store.state["retired_markets"].clear()
        LAD.seed(h, 30, LOSING)
        n = len(h.venue.cancelled)
        for i in range(60):
            h.tick(off + 6.0 + 3.0 * i)
        assert h.pos("DOWN") > 0
        ups = [p for s, p, _q in h.sent() if s == "UP"]
        assert len(ups) >= 2          # re-placed after its lifetime ended
        assert len(h.venue.cancelled) > n
        assert h.r.store.orders_uncertain() is None

    def test_on_no_second_window_opens_either(self, tmp_path, monkeypatch):
        h, off = window(tmp_path, monkeypatch, on=True, ticks=150)
        assert list(h.sent()) == []
