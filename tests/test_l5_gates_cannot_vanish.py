"""The four risk limits cannot disappear when configuration parsing fails.

THE FAIL-OPEN THIS CLOSES, found while verifying the $20 cumulative halt on
2026-10-24. `l5_pmus_live_runner.validation_gates()` returned None when
construction raised, and the submission path reads `if vg is not None:` --
so ONE malformed value among ten env reads silently dropped the whole
additive layer:

    max_cumulative_loss_usd   $20   account-wide lifetime halt
    max_cash_deployed_usd     $1.65 cash envelope
    max_unmatched_contracts   5     unmatched cap
    campaign_budget           $29.30 campaign worst-case budget

The old alarm label said "Pre-existing limits are unaffected", which is true
of SafetyLimits and beside the point: `max_cumulative_loss_usd` DEFAULTS to
20, so it is always requested and can never correctly be absent.

None now means exactly one thing: L5_VALIDATION_GATES=0, an explicit operator
choice taken above the try block and unreachable from an error. A construction
failure yields `RefuseAllGates`, which refuses every submission by name.

WHAT STAYS OPEN, deliberately: cancellation, reconciliation, the watchdog and
the deadman never consult these gates, so an unparseable limit stops NEW risk
without stranding exposure already taken.

Nothing here submits an order, cancels one, uses a credential or opens a
socket.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_live_validation_gates as VG
from src import l5_pmus_live_runner as R


#: every env read inside the guarded construction, with a value that cannot
#: parse. Each one must produce a refusing gate set, not an absent one.
POISON = [
    "L5_MAX_CASH_DEPLOYED_USD",
    "L5_MAX_UNMATCHED_CONTRACTS",
    "L5_MAX_CUMULATIVE_LOSS_USD",
    "L5_STRATEGY_LOSS_LIMIT_USD",
    "L5_MAX_L5_DAILY_LOSS_USD",
    "L5_MAX_L5_CUMULATIVE_LOSS_USD",
    "L5_MAX_UNRESOLVED_UNKNOWN_USD",
    "L5_MAX_ACCOUNT_WIDE_DELTA_LOSS_USD",
]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """A known-good baseline: gates enabled, nothing poisoned."""
    monkeypatch.delenv("L5_VALIDATION_GATES", raising=False)
    for k in POISON:
        monkeypatch.delenv(k, raising=False)


class TestAFailedConstructionRefusesRatherThanVanishes:

    @pytest.mark.parametrize("env", POISON)
    def test_every_poisoned_value_yields_a_refusing_gate_set(self, monkeypatch,
                                                             env):
        monkeypatch.setenv(env, "not-a-number")
        g = R.validation_gates()
        assert g is not None, env
        assert isinstance(g, VG.RefuseAllGates), env
        assert g.refuse(price=D("0.5"), qty=D("1")) == \
            "REQUIRED_RISK_CONFIG_UNREADABLE"

    @pytest.mark.parametrize("env", POISON)
    def test_it_carries_no_limits_so_it_cannot_pass_for_one(self, monkeypatch,
                                                            env):
        monkeypatch.setenv(env, "}{")
        g = R.validation_gates()
        assert g.configured is False
        assert g.max_cumulative_loss_usd is None
        assert g.max_cash_deployed_usd is None
        assert g.max_worst_case_deficit_usd is None
        assert g.campaign_budget is None

    def test_the_refusal_is_the_same_whatever_it_is_asked(self):
        g = VG.RefuseAllGates("boom")
        for kw in ({}, {"price": D(1)}, {"anything": object()}):
            assert g.refuse(**kw) == "REQUIRED_RISK_CONFIG_UNREADABLE"

    def test_its_commitment_figures_cannot_find_headroom(self):
        """If arithmetic does run on these, it must not conclude there is
        room. Finite, so it can never produce a NaN comparison."""
        g = VG.RefuseAllGates("boom")
        assert g.cash_committed([]) == VG.UNREADABLE_COMMITMENT_USD
        assert g.pending_obligation([]) == VG.UNREADABLE_COMMITMENT_USD
        assert VG.UNREADABLE_COMMITMENT_USD > D("1000")
        assert VG.UNREADABLE_COMMITMENT_USD.is_finite()

    def test_it_says_what_is_wrong(self):
        g = VG.RefuseAllGates("InvalidOperation: bad decimal")
        d = g.as_dict()
        assert d["configured"] is False
        assert d["refusing"] == "REQUIRED_RISK_CONFIG_UNREADABLE"
        assert "InvalidOperation" in d["error"]


class TestNoneStillMeansExactlyOneThing:

    def test_an_explicit_zero_disables_the_layer_as_before(self, monkeypatch):
        monkeypatch.setenv("L5_VALIDATION_GATES", "0")
        assert R.validation_gates() is None

    def test_and_a_poisoned_value_cannot_reach_that_path(self, monkeypatch):
        """The disable check is above the try block, so an error can never
        be mistaken for the operator having switched the gates off."""
        monkeypatch.setenv("L5_VALIDATION_GATES", "1")
        monkeypatch.setenv("L5_MAX_CUMULATIVE_LOSS_USD", "x")
        assert R.validation_gates() is not None

    def test_the_disable_check_precedes_the_try_in_the_source(self):
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(R.validation_gates)))
        tries = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Try)]
        returns_none = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Return)
            and isinstance(getattr(n, "value", None), ast.Constant)
            and n.value.value is None]
        assert tries and returns_none
        assert min(returns_none) < min(tries)


class TestTheFourLimitsAreEachIndependentlyUnLoseable:
    """Requirement 2, limit by limit. Each is reached by a DIFFERENT path, so
    all four are asserted separately rather than through one proxy."""

    def poison(self, monkeypatch):
        monkeypatch.setenv("L5_MAX_CUMULATIVE_LOSS_USD", "nonsense")
        return R.validation_gates()

    def test_the_twenty_dollar_cumulative_halt_cannot_disappear(self,
                                                                monkeypatch):
        g = self.poison(monkeypatch)
        # it is not silently 20 again, and it is not absent: it refuses
        assert g.max_cumulative_loss_usd is None
        assert g.refuse() == "REQUIRED_RISK_CONFIG_UNREADABLE"

    def test_the_cash_envelope_cannot_disappear(self, monkeypatch):
        """`probe_cash_envelope` returns the gate's figure, and None makes
        `build_orders` refuse CASH_ENVELOPE_UNKNOWN before pricing."""
        g = self.poison(monkeypatch)
        assert g.max_cash_deployed_usd is None
        from src import l5_two_sided_probe as TS
        monkeypatch.setenv(TS.MARKET_ENV,
                           "cpc-btc-updown-15m-2026-10-24-0100z")
        monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "gates-vanish-test")
        out = TS.build_orders(
            market="cpc-btc-updown-15m-2026-10-24-0100z",
            bid="0.62", ask="0.63", bid_depth="900", ask_depth="900",
            tick="0.01", min_qty="0.01",
            depth_ticks=2, cash_envelope=None,
            max_worst_case_loss=D("20"), book_age_s=0.1)
        assert out["ok"] is False
        assert out["reason"] == "CASH_ENVELOPE_UNKNOWN"

    def test_the_loss_ceiling_cannot_disappear(self, monkeypatch):
        """`probe_loss_ceiling` is min(deficit, cumulative); with both None
        it is None, and the planner refuses LOSS_CEILING_UNKNOWN."""
        from src import l5_two_sided_probe as TS
        monkeypatch.setenv(TS.MARKET_ENV,
                           "cpc-btc-updown-15m-2026-10-24-0100z")
        monkeypatch.setenv("L5_TS_EXPERIMENT_ID", "gates-vanish-test")
        out = TS.build_orders(
            market="cpc-btc-updown-15m-2026-10-24-0100z",
            bid="0.62", ask="0.63", bid_depth="900", ask_depth="900",
            tick="0.01", min_qty="0.01",
            depth_ticks=2, cash_envelope=D("1.65"),
            max_worst_case_loss=None, book_age_s=0.1)
        assert out["ok"] is False
        assert out["reason"] == "LOSS_CEILING_UNKNOWN"

    def test_the_two_dollar_account_wide_ceiling_is_not_in_this_layer(self):
        """It lives in SafetyLimits, which is built separately and is NOT
        affected by a gates failure -- and it ALSO fails closed on its own
        unreadable value. Both halves asserted, because 'the other object
        has it' is only reassuring if that object fails closed too."""
        lim = LX.SafetyLimits(max_total_exposure_usd=D("2"),
                              max_live_orders=1)
        assert lim.max_total_exposure_usd == D("2")
        lim.max_total_exposure_usd = None
        assert lim.refuse(
            price=D("0.35"), qty=D("1"), market="m",
            committed_by_market={}, residual_after=D(0),
            realized_today=D(0), open_markets=["m"],
            outstanding_by_market={}, live_order_count=0
        ) == "MAX_TOTAL_EXPOSURE_CEILING_UNREADABLE"

    def test_the_five_dollar_daily_halt_is_in_safetylimits_and_binds(self):
        lim = LX.SafetyLimits(max_total_exposure_usd=D("2"),
                              daily_realized_loss_halt_usd=D("5"),
                              max_live_orders=1)
        assert lim.refuse(
            price=D("0.35"), qty=D("1"), market="m",
            committed_by_market={}, residual_after=D(0),
            realized_today=D("-5"), open_markets=["m"],
            outstanding_by_market={}, live_order_count=0
        ) == "DAILY_REALIZED_LOSS_HALT"

    def test_and_the_runner_default_for_the_cumulative_halt_is_twenty(self):
        """So it is always requested: an absent env is 20, never absent."""
        src = inspect.getsource(R.validation_gates)
        assert '"L5_MAX_CUMULATIVE_LOSS_USD", "20"' in src


class TestWhatMustStayOpen:
    """An unparseable limit stops NEW risk; it must not strand exposure."""

    def test_cancellation_does_not_consult_these_gates(self):
        src = inspect.getsource(LX.deadman)
        assert "validation_gates" not in src
        assert "RefuseAllGates" not in src

    def test_the_sentinel_holds_no_transport_and_cannot_act(self):
        g = VG.RefuseAllGates("boom")
        for attr in ("submit", "cancel", "arm", "http", "transport"):
            assert not hasattr(g, attr), attr

    def test_the_alarm_names_the_failure_rather_than_going_quiet(self):
        src = inspect.getsource(R.validation_gates)
        assert "validation_gates_unreadable" in src
        assert "L5_LIVE_ALARM" in src
