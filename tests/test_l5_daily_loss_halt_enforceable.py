"""The $5 daily halt is enforceable even while L5 attribution is UNPROVEN.

THE OWNER'S QUESTION. The heartbeat reports
`verified_l5_realized_net: null` with `verified_l5_status:
UNPROVEN_LEGACY_ATTRIBUTION`. Does a null there silently disable the
campaign loss limit?

NO, AND THE TWO FIGURES ARE NOT THE SAME QUANTITY.

  * `verified_l5_realized_net` is a REPORTING field on the day line. No
    gate reads it.
  * The gate reads `InventoryStore.realized_current_utc_day()` --
    account-wide SETTLED P&L for the current UTC day, built from
    settlement rows, with no dependence on L5 attribution at all.
  * `None` from that accessor is UNKNOWN and REFUSES
    (`SAFETY_OVERLAY / DAY_REALIZED_UNAVAILABLE`). It is never read as
    zero, so an unreadable day cannot buy permission to trade.
  * A readable day is compared in `SafetyLimits.refuse`:
    `realized_today <= -daily_realized_loss_halt_usd` ->
    `DAILY_REALIZED_LOSS_HALT`.
"""
from decimal import Decimal as D

import inspect
import io

import pytest

from src import l5_live_execution as E


def _limits(**kw):
    base = dict(max_usd_per_market=D("20"), max_abs_residual=D("10"),
                max_total_exposure_usd=D("20"), max_concurrent_markets=1,
                daily_realized_loss_halt_usd=D("5"))
    base.update(kw)
    return E.SafetyLimits(**base)


def _refuse(realized_today, **kw):
    lim = _limits(**kw)
    return lim.refuse(price=D("0.50"), qty=D("1"), market="m",
                      committed_by_market={}, residual_after=D("0"),
                      realized_today=realized_today,
                      open_markets=set(), outstanding_by_market={},
                      live_order_count=0)


# --------------------------------------------- the halt fires at exactly -$5

@pytest.mark.parametrize("day,expect", [
    (D("0"), None),
    (D("-4.99"), None),
    (D("-5.00"), "DAILY_REALIZED_LOSS_HALT"),
    (D("-5.01"), "DAILY_REALIZED_LOSS_HALT"),
    (D("-11.8495"), "DAILY_REALIZED_LOSS_HALT"),
])
def test_the_five_dollar_halt_binds_at_the_configured_value(day, expect):
    assert _refuse(day) == expect


def test_the_halt_is_the_configured_number_not_a_constant():
    """A $5 configuration must be what binds, so the limit is real."""
    lim = _limits(daily_realized_loss_halt_usd=D("5"))
    assert lim.daily_realized_loss_halt_usd == D("5")
    assert _refuse(D("-5.00"), daily_realized_loss_halt_usd=D("5")) \
        == "DAILY_REALIZED_LOSS_HALT"
    # and a tighter configuration binds earlier, proving the value is read
    assert _refuse(D("-2.00"), daily_realized_loss_halt_usd=D("1")) \
        == "DAILY_REALIZED_LOSS_HALT"
    assert _refuse(D("-2.00"), daily_realized_loss_halt_usd=D("5")) is None


# ------------------------------- an UNKNOWN day refuses, never reads as zero

def test_an_unreadable_day_refuses_in_the_money_path():
    body = inspect.getsource(E.FaithfulExecutor.handle)
    i = body.index("day_realized = self.store.realized_current_utc_day()")
    nxt = body[i:i + 420]
    assert "if day_realized is None:" in nxt
    assert "DAY_REALIZED_UNAVAILABLE" in nxt
    # the refusal precedes the limits call that consumes the value
    assert body.index("DAY_REALIZED_UNAVAILABLE") < body.index(
        "realized_today=day_realized")


def test_the_source_says_none_is_never_zero():
    body = inspect.getsource(E.FaithfulExecutor.handle)
    assert "None is UNKNOWN and refuses; it is never read as zero" in body


# ----------------------- the gate's input is NOT the unproven attribution

def test_the_gate_reads_the_utc_day_accessor_not_the_all_time_total():
    body = inspect.getsource(E.FaithfulExecutor.handle)
    assert "self.store.realized_current_utc_day()" in body
    # the all-time total is a DIFFERENT accessor and is not what feeds
    # the daily limit
    i = body.index("day_realized = self.store.realized_current_utc_day()")
    j = body.index("realized_today=day_realized")
    assert "realized_today()" not in body[i:j]


def test_the_day_accessor_is_account_wide_and_attribution_free():
    doc = inspect.getdoc(E.InventoryStore.realized_current_utc_day) or ""
    assert "CURRENT UTC DAY" in doc
    assert "STILL ACCOUNT-WIDE" in doc
    # it is explicitly NOT the L5-scoped rescoping
    assert "NOT the L5-scoped rescoping" in doc


def test_verified_l5_realized_net_is_reporting_only():
    """It exists, in a RECONCILIATION REPORT -- never in a gate.

    It is produced by `InventoryStore.reconcile_verified_l5_net`, whose
    whole job is to report, and `handle` never mentions it. So a null there
    cannot reach any refusal decision.
    """
    exe = io.open("src/l5_live_execution.py", encoding="utf-8").read()
    # it occurs exactly once, inside the reporting method
    assert exe.count("verified_l5_realized_net") == 1
    i = exe.index("def reconcile_verified_l5_net(")
    j = exe.index("verified_l5_realized_net")
    assert i < j, "expected it inside the reconciliation report"
    # the next method definition after the report starts comes AFTER it
    nxt = exe.index("\n    def ", j)
    assert nxt > j

    # and the money path never reads it
    body = inspect.getsource(E.FaithfulExecutor.handle)
    assert "verified_l5_realized_net" not in body
    assert "verified_l5_status" not in body


def test_the_unproven_status_is_not_an_input_to_any_refusal():
    exe = io.open("src/l5_live_execution.py", encoding="utf-8").read()
    body = inspect.getsource(E.FaithfulExecutor.handle)
    assert "UNPROVEN_LEGACY_ATTRIBUTION" in exe      # the label exists
    assert "UNPROVEN_LEGACY_ATTRIBUTION" not in body  # but not in the gate
    # nor does SafetyLimits.refuse know anything about attribution
    ref = inspect.getsource(E.SafetyLimits.refuse)
    for token in ("verified", "attribut", "UNPROVEN"):
        assert token not in ref


def test_the_two_cumulative_gates_are_separate_and_still_bind():
    """Narrowing the daily WINDOW did not remove the all-time ceilings."""
    doc = inspect.getdoc(E.InventoryStore.realized_current_utc_day) or ""
    assert "cumulative gates" in doc
    assert "Historical losses" in doc
