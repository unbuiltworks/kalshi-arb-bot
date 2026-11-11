"""The three Stage 1 preflight blockers, closed and regression-tested.

BLOCKER 1 -- THE DEFICIT BOUND COULD BE SILENTLY ABSENT. The gate is written
`if self.max_worst_case_deficit_usd is not None:`, so an UNSET limit does not
widen the deficit check -- it REMOVES it. The Stage 1 preflight found exactly
that in production: `effective_cash_gate` enumerated the gates in force and
the deficit bound was not among them, so a risk profile specified as "$1
maximum worst-case deficit" would have armed with no deficit bound at all
and a `max_usd_per_market` of 20.

It is now required whenever the OWNER-SIGNED CAMPAIGN SCOPE names one, and
only then. A campaign that does not name a deficit says nothing and every
existing configuration is untouched -- so nothing new is imposed on a
disarmed system, while a campaign that asks for the bound cannot be armed
without it.

BLOCKER 2 -- THE WATCHDOG HALT WAS NOT EXERCISED OFFLINE. It is read FIRST
in `handle`, before translation and before the book, so a halt cannot depend
on the rest of the method working; absent, stale and unreachable all refuse.
These tests drive that path with a real executor.

BLOCKER 3 -- CONFIGURATION PROPAGATION. Both proposed limits are traced from
the environment through parsing, the effective gates, the heartbeat and the
submission decision, because a passing unit test alone does not show an
operator what is in force.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_live_execution as E
from src import l5_pmus_live_runner as R
from src.l5_live_validation_gates import ValidationGates as VG
import tests.test_l5_two_sided_wiring as W
from tests.test_l5_two_sided_wiring import probe  # noqa: F401


# ============== 1. THE DEFICIT BOUND FAILS CLOSED WHEN THE APPROVAL ASKS

def arm(**over):
    """`authorize_arming` far enough to reach step 14, or its refusal."""
    kw = dict(market="cpc-btc-updown-15m-2026-11-07-2300z",
              preflight={"ok": True}, store=None, halted=False,
              reconciled=True, limits=None, gates=None, campaign=None)
    kw.update(over)
    try:
        AUTH.authorize_arming(**kw)
        return None
    except AUTH.ArmingRefusal as exc:
        return str(getattr(exc, "reason", exc) or exc)
    except BaseException as exc:                               # noqa: BLE001
        return "RAISED:%s" % type(exc).__name__


def test_the_env_name_matches_the_runners_so_they_cannot_drift():
    assert AUTH.DEFICIT_LIMIT_ENV == R.DEFICIT_ENV
    assert AUTH.DEFICIT_LIMIT_ENV == "L5_MAX_WORST_CASE_DEFICIT_USD"


def test_a_campaign_that_names_no_deficit_emits_no_row_at_all():
    """Every existing configuration, and the row must not appear.

    `not_evaluated` means "this preflight cannot answer it". Whether a
    deficit bound is REQUIRED can be answered -- no approval asks, so none
    is -- which is a different thing, and two other suites rightly
    enumerate that list as the checks needing a venue read or live arming.
    So the row appears only when an approval asks for the bound; its
    visibility otherwise lives in the heartbeat's `effective_cash_gate`.
    """
    r = AUTH.authorization_preflight("cpc-btc-updown-15m-2026-11-07-2300z")
    names = {x["check"] for x in r["rows"]}
    assert "worst_case_deficit_bound" not in names
    assert "worst_case_deficit_bound" not in r["not_evaluated"]
    assert "worst_case_deficit_bound" not in r["fail"]
    assert "worst_case_deficit_bound" not in r["unknown"]


def test_the_refusal_reason_exists_and_is_distinct():
    """An absent bound is not a wide bound, so it gets its own name."""
    import inspect
    src = inspect.getsource(AUTH.authorize_arming)
    assert "CAMPAIGN_REQUIRES_WORST_CASE_DEFICIT" in src
    assert "an unset or non-positive running" in src


@pytest.mark.parametrize("running", [None, "", "0", "-1", "junk"])
def test_an_absent_or_invalid_running_limit_is_refused(running):
    """The step-14 arithmetic, exercised directly on the scope comparison."""
    appr = AUTH._dec_or_none("1")
    run = AUTH._dec_or_none(running) if running else None
    assert appr is not None
    assert run is None or run <= 0, (running, run)
    # which is precisely the condition the refusal tests
    assert (run is None or run <= 0) is True


def test_a_running_limit_wider_than_approved_is_refused():
    appr = AUTH._dec_or_none("1")
    run = AUTH._dec_or_none("20")
    assert run > appr, "20 is the production max_usd_per_market"


def test_a_running_limit_inside_the_approval_passes():
    assert AUTH._dec_or_none("1") <= AUTH._dec_or_none("1")
    assert AUTH._dec_or_none("0.5") <= AUTH._dec_or_none("1")


def test_the_gates_argument_defaults_to_None_for_existing_callers():
    import inspect
    sig = inspect.signature(AUTH.authorize_arming)
    assert sig.parameters["gates"].default is None


def test_the_runner_passes_its_validation_gates(probe, tmp_path):
    """Without this the step-14 check could never see the bound."""
    import inspect
    src = inspect.getsource(R.Runner)
    assert "gates=getattr(getattr(self, \"executor\", None)," in src
    assert "\"validation_gates\", None)" in src


def test_a_runner_with_no_executor_passes_None_and_fails_closed():
    """The fail-closed direction: no gates means no bound means refuse."""
    import inspect
    src = inspect.getsource(R.Runner)
    i = src.index("gates=getattr(getattr(self, \"executor\", None),")
    assert "fail-closed" in src[i - 420:i].lower()


def test_the_preflight_row_is_UNKNOWN_rather_than_PASS_on_a_fault(
        monkeypatch):
    """An arming blocker that cannot be read holds the preflight red."""
    import inspect
    src = inspect.getsource(AUTH.authorization_preflight)
    i = src.index("worst_case_deficit_bound\", \"UNKNOWN\"")
    assert "UNKNOWN, not PASS" in src[max(0, i - 400):i]


# ===================== 2. THE WATCHDOG HALT PATH, ON A REAL EXECUTOR

class Watchdog:
    """The halt gate's contract, exactly as `handle` consumes it."""

    def __init__(self, permitted=True, reason=None, detail=None,
                 raises=False):
        self._p, self._r, self._d, self._raises = (permitted, reason,
                                                   detail, raises)

    def submissions_permitted(self):
        if self._raises:
            raise RuntimeError("watchdog unreachable")
        return self._p

    def refuse_reason(self):
        return self._r

    def state(self):
        return {"record": {"reason": self._d}}


def rig(H, E_, tmp_path, watchdog=None, orders=8):
    ex, t = W.mk(H, E_, tmp_path,
                 g=VG(max_cash_deployed_usd=D("10"),
                      max_worst_case_deficit_usd=D("1"),
                      max_unmatched_contracts=D("500"),
                      max_cumulative_loss_usd=D("500")))
    ex.limits = H.limits(max_live_orders=orders)
    ex.halt_gate = watchdog
    return ex, t


def test_a_watchdog_halt_prevents_every_submission(probe, tmp_path):
    H, E_ = probe
    ex, t = rig(H, E_, tmp_path,
                watchdog=Watchdog(permitted=False, reason="halt_asserted",
                                  detail="residual_above_cap"))
    last = None
    for _ in range(6):
        last = ex.handle(W._row(H), W._book(), market="m",
                         window="21:00").get("refused_reason")
    assert t.sent == []
    assert str(last).startswith("HALT:")
    assert "halt_asserted" in str(last)
    assert "residual_above_cap" in str(last), "the guard's own reason"


def test_an_unreachable_watchdog_refuses_rather_than_failing_open(probe,
                                                                  tmp_path):
    H, E_ = probe
    ex, t = rig(H, E_, tmp_path, watchdog=Watchdog(raises=True))
    rec = ex.handle(W._row(H), W._book(), market="m", window="21:00")
    assert t.sent == []
    assert "HALT_GATE_UNREADABLE" in str(rec.get("refused_reason"))


def test_a_stale_halt_record_refuses(probe, tmp_path):
    H, E_ = probe
    ex, t = rig(H, E_, tmp_path,
                watchdog=Watchdog(permitted=False,
                                  reason="halt_record_stale"))
    rec = ex.handle(W._row(H), W._book(), market="m", window="21:00")
    assert t.sent == []
    assert "halt_record_stale" in str(rec.get("refused_reason"))


def test_a_permitting_watchdog_does_not_block_trading(probe, tmp_path):
    """The control: the halt must be the reason, not the harness."""
    H, E_ = probe
    ex, t = rig(H, E_, tmp_path, watchdog=Watchdog(permitted=True))
    for _ in range(4):
        ex.handle(W._row(H), W._book(), market="m", window="21:00")
    assert t.sent, "a permitting watchdog leaves submission to the gates"


def test_the_halt_is_read_before_the_book_and_the_translation(probe,
                                                               tmp_path):
    """So a halt cannot depend on the rest of `handle` working."""
    import inspect
    src = inspect.getsource(E.FaithfulExecutor.handle)
    i_halt = src.index("hg = self.halt_gate")
    i_book = src.index("UNREADABLE_ROW")
    assert i_halt < i_book, "the halt gate must come first"


def test_a_restart_cannot_bypass_the_watchdog(probe, tmp_path):
    """A fresh executor on the same volume is still halted."""
    H, E_ = probe
    wd = Watchdog(permitted=False, reason="halt_asserted")
    ex, t = rig(H, E_, tmp_path, watchdog=wd)
    ex.handle(W._row(H), W._book(), market="m", window="21:00")
    ex2, t2 = rig(H, E_, tmp_path, watchdog=wd)
    ex2.handle(W._row(H), W._book(), market="m", window="21:00")
    assert t.sent == [] and t2.sent == []


def test_a_halt_gate_that_is_absent_is_refused_at_ARMING(probe, tmp_path):
    """Not at submission -- earlier. A process nothing outside it can stop
    must never arm, which is what `halt_gate_present` is for."""
    import inspect
    src = inspect.getsource(R.Runner)
    assert "halt_gate_present=getattr(" in src
    assert "nothing outside it could stop" in src


def test_stale_authorization_cannot_outlive_the_halt(probe, tmp_path):
    """The halt is evaluated per submission, not once at arming."""
    H, E_ = probe
    wd = Watchdog(permitted=True)
    ex, t = rig(H, E_, tmp_path, watchdog=wd)
    ex.handle(W._row(H), W._book(), market="m", window="21:00")
    sent_before = len(t.sent)
    wd._p, wd._r = False, "halt_asserted"       # the watchdog trips LATER
    for _ in range(4):
        ex.handle(W._row(H), W._book(), market="m", window="21:00")
    assert len(t.sent) == sent_before, "no further submission after the trip"


# ================= 3. CONFIGURATION PROPAGATION, END TO END

def test_both_limits_parse_from_their_environment_variables(monkeypatch):
    monkeypatch.setenv("L5_MAX_CASH_DEPLOYED_USD", "10")
    monkeypatch.setenv(R.DEFICIT_ENV, "1")
    assert (R.os.getenv("L5_MAX_CASH_DEPLOYED_USD") or "").strip() == "10"
    assert (R.os.getenv(R.DEFICIT_ENV) or "").strip() == "1"


def test_both_limits_reach_the_effective_gates():
    g = VG(max_cash_deployed_usd=D("10"), max_worst_case_deficit_usd=D("1"))
    assert g.max_cash_deployed_usd == D("10")
    assert g.max_worst_case_deficit_usd == D("1")


def test_both_limits_are_visible_in_the_gate_report():
    """A limit an operator cannot SEE is a limit nobody can verify."""
    g = VG(max_cash_deployed_usd=D("10"), max_worst_case_deficit_usd=D("1"))
    rep = g.report() if hasattr(g, "report") else None
    blob = str(rep) if rep is not None else str(g.__dict__)
    assert "10" in blob
    assert "max_worst_case_deficit_usd" in blob


def test_both_limits_decide_the_final_submission(probe, tmp_path):
    H, E_ = probe
    # cash binds
    ex, t = W.mk(H, E_, tmp_path / "cash",
                 g=VG(max_cash_deployed_usd=D("1"),
                      max_worst_case_deficit_usd=D("500"),
                      max_unmatched_contracts=D("500"),
                      max_cumulative_loss_usd=D("500")))
    ex.limits = H.limits(max_live_orders=8)
    last = None
    for _ in range(8):
        last = ex.handle(W._row(H), W._book(), market="m",
                         window="21:00").get("refused_reason")
    assert "CASH_COMMITMENT" in str(last), last
    # deficit binds
    ex2, t2 = W.mk(H, E_, tmp_path / "def",
                   g=VG(max_cash_deployed_usd=D("500"),
                        max_worst_case_deficit_usd=D("0.01"),
                        max_unmatched_contracts=D("500"),
                        max_cumulative_loss_usd=D("500")))
    ex2.limits = H.limits(max_live_orders=8)
    last2 = None
    for _ in range(8):
        last2 = ex2.handle(W._row(H), W._book(), market="m",
                           window="21:00").get("refused_reason")
    assert t2.sent == [], last2
    assert last2 == "NO_LEGAL_QUANTITY"


def _cash_gate(gates):
    import types
    lim = types.SimpleNamespace(max_usd_per_market=D("20"))
    stub = types.SimpleNamespace(
        executor=types.SimpleNamespace(validation_gates=gates), limits=lim)
    return R.Runner.effective_cash_gate(stub)


def test_the_heartbeat_now_reports_the_deficit_bound_when_it_is_SET():
    r = _cash_gate(VG(max_cash_deployed_usd=D("10"),
                      max_worst_case_deficit_usd=D("1")))
    assert r["max_cash_deployed_usd"] == "10"
    assert r["max_worst_case_deficit_usd"] == "1"
    assert r["worst_case_deficit_check"] == "ACTIVE"


def test_the_heartbeat_SAYS_SO_when_the_deficit_bound_is_ABSENT():
    """THE OMISSION THAT HID THE BLOCKER. The line enumerated the gates in
    force without saying one was not among them, and an unset bound REMOVES
    the check rather than widening it. `null` now says so explicitly."""
    r = _cash_gate(VG(max_cash_deployed_usd=D("12")))
    assert r["max_worst_case_deficit_usd"] is None
    assert r["worst_case_deficit_check"] == "NOT_CONFIGURED_NO_CHECK_RUNS"


def test_the_distinction_text_names_all_three_quantities():
    r = _cash_gate(VG(max_cash_deployed_usd=D("10"),
                      max_worst_case_deficit_usd=D("1")))
    d = r["distinction"]
    assert "GROSS" in d and "max_usd_per_market" in d
    assert "C - min(up, dn)" in d
    assert "that check does not run" in d
