"""Delivering the campaign approval: the env store, and the RUNNER WIRING.

tests/test_l5_campaign_session.py covers the approval document's own rules --
clamping, staleness, expiry, the durable window ledger. None of that binds
anything if the runner never asks. The gate existed for a whole deployment
before `authorize_arming(campaign=...)` had a caller, and an approval document
set in that state would have been read by nobody and refused nothing.

So this file pins the DELIVERY:

  * `EnvApprovalStore` -- absent means absent, unreadable means unreadable,
    and the executor cannot write its own approval;
  * `campaign_decision` -- one call, so the runner cannot half-wire it;
  * `Runner.try_arm` -- the verdict is computed and PASSED IN on every arm
    attempt, a fault in the layer refuses rather than falling through, and
    the counter it publishes never carries the nonce.

Nothing here arms anything or touches a venue.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_execution as LX
from src import l5_live_authorization as AUTH
from src import l5_pmus_live_runner as R
from src import pmus_auth as Q1

SERIES = "cpc-btc-updown-15m-"
W1 = SERIES + "2026-10-20-1600z"
W2 = SERIES + "2026-10-20-1615z"
W3 = SERIES + "2026-10-20-1630z"
W4 = SERIES + "2026-10-20-1645z"
W5 = SERIES + "2026-10-20-1700z"

#: the approved bounded campaign, verbatim: TWO_SIDED on BTC15m, $5 exposure,
#: $5 daily loss, four windows, four hours.
NONCE = "delivery-test-nonce-0123456789"


def _bound(base, over):
    """Derive the opening bound from the expiry the caller ACTUALLY ends up
    with, one window inside it.

    Several tests here override `expires_utc`, and the settlement grace must
    outlast the last permitted open -- while `eff_expires` is itself clamped
    to issued + MAX_CAMPAIGN_S. A fixed bound would sit outside a shortened
    expiry and be refused as inconsistent, which is a fixture artefact and
    not the property under test. An explicit `last_open_utc` from the caller
    always wins.
    """
    if "last_open_utc" in over:
        return base
    iss = dt.datetime.fromisoformat(base["issued_utc"])
    exp = dt.datetime.fromisoformat(base["expires_utc"])
    eff = min(exp, iss + dt.timedelta(seconds=14400))
    base["last_open_utc"] = (eff - dt.timedelta(minutes=15)).isoformat()
    return base


def live_doc(**over):
    now = dt.datetime.now(dt.timezone.utc)
    base = {"approved": True, "nonce": NONCE, "series": SERIES,
            "issued_utc": (now - dt.timedelta(minutes=1)).isoformat(),
            "expires_utc": (now + dt.timedelta(hours=4)).isoformat(),
            "max_windows": 4,
            "max_total_exposure_usd": "5",
            "max_daily_loss_usd": "5"}
    base.update(over)
    return _bound(base, over)


# ====================================================== the env-backed store


def test_an_UNSET_variable_is_ABSENT_not_an_error(monkeypatch):
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    assert CA.EnvApprovalStore().get(CA.APPROVAL_KEY) is None


@pytest.mark.parametrize("raw", ["", "   ", "\n", "\t "])
def test_an_EMPTY_variable_is_ABSENT_too(monkeypatch, raw):
    """An owner who clears the variable has withdrawn the campaign, and that
    must read as "no campaign" rather than as a malformed one -- the first
    preserves the single-window handshake, the second would refuse it."""
    monkeypatch.setenv(CA.APPROVAL_ENV, raw)
    assert CA.EnvApprovalStore().get(CA.APPROVAL_KEY) is None


def test_the_store_serves_ONLY_the_approval_key(monkeypatch):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    s = CA.EnvApprovalStore()
    assert s.get(CA.APPROVAL_KEY) is not None
    assert s.get("halt.json") is None, (
        "a store that answered any key would let one object's absence be "
        "mistaken for another's")


def test_the_executor_may_NOT_WRITE_ITS_OWN_APPROVAL(monkeypatch):
    """The security property of the whole layer. If this ever returns, Claude
    can approve a campaign."""
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    with pytest.raises(CA.ApprovalReadUnavailable):
        CA.EnvApprovalStore().put(CA.APPROVAL_KEY, b"{}")


# ======================================================= campaign_decision


def test_NO_document_leaves_the_single_window_path_EXACTLY_as_it_was(
        monkeypatch, tmp_path):
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    out = CA.campaign_decision(W1, root=str(tmp_path))
    assert out["present"] is False
    assert out["ok"] is False
    # and nothing was spent
    assert not (tmp_path / CA.CONSUMED_DIR).exists()


def test_a_VALID_document_approves_and_RESERVES_the_window(
        monkeypatch, tmp_path):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    out = CA.campaign_decision(W1, root=str(tmp_path))
    assert out["ok"] is True
    assert out["present"] is True
    assert out["window"]["reserved_now"] is True
    assert out["window"]["windows_remaining"] == 3
    led = CA.WindowLedger(str(tmp_path)).read(CA.nonce_sid(NONCE))
    assert led["windows"] == [W1]


def test_the_verdict_names_the_document_by_DIGEST(monkeypatch, tmp_path):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    out = CA.campaign_decision(W1, root=str(tmp_path))
    assert out["approval_state"] == "approved"
    assert len(str(out["document_sha256"])) == 64
    assert NONCE not in str(out["document_sha256"])


def test_an_EXPIRED_document_is_PRESENT_AND_REFUSING(monkeypatch, tmp_path):
    """Not absent. An expired campaign that read as absent would quietly
    become a fresh one-window authorization."""
    now = dt.datetime.now(dt.timezone.utc)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc(
        issued_utc=(now - dt.timedelta(minutes=30)).isoformat(),
        expires_utc=(now - dt.timedelta(minutes=1)).isoformat())))
    out = CA.campaign_decision(W1, root=str(tmp_path))
    assert out["present"] is True and out["ok"] is False
    assert out["reason"] in ("APPROVAL_NOT_VALID", "CAMPAIGN_EXPIRED")


def test_a_MALFORMED_variable_refuses_rather_than_reading_as_absent(
        monkeypatch, tmp_path):
    monkeypatch.setenv(CA.APPROVAL_ENV, "{not json")
    out = CA.campaign_decision(W1, root=str(tmp_path))
    assert out["present"] is True and out["ok"] is False


def test_a_market_OUTSIDE_the_approved_series_is_refused(
        monkeypatch, tmp_path):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    out = CA.campaign_decision("cpc-eth-updown-15m-2026-10-20-1600z",
                               root=str(tmp_path))
    assert out["ok"] is False
    assert out["reason"] == "CAMPAIGN_SERIES_MISMATCH"


def test_FOUR_WINDOWS_then_exhausted_ACROSS_SEPARATE_CALLS(
        monkeypatch, tmp_path):
    """Each call is a fresh gate and a fresh ledger object -- the same shape a
    redeploy produces. The budget has to come from the volume, not memory."""
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    for i, w in enumerate((W1, W2, W3, W4), start=1):
        out = CA.campaign_decision(w, root=str(tmp_path))
        assert out["ok"] is True, w
        assert out["window"]["index"] == i
    out = CA.campaign_decision(W5, root=str(tmp_path))
    assert out["ok"] is False
    assert out["reason"] == "CAMPAIGN_WINDOWS_EXHAUSTED"
    assert out["present"] is True, (
        "an exhausted campaign must refuse, not hand the caller back the "
        "one-window path it had before the campaign existed")


def test_RE_READING_THE_SAME_WINDOW_does_not_spend_a_second(
        monkeypatch, tmp_path):
    """try_arm is retried every tick. If each retry spent a window, one
    window would exhaust a four-window campaign in four seconds."""
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    for _ in range(25):
        out = CA.campaign_decision(W1, root=str(tmp_path))
        assert out["ok"] is True
    assert out["window"]["windows_spent"] == 1
    assert out["window"]["reserved_now"] is False


# ========================================================= the RUNNER wiring


@pytest.fixture(autouse=True)
def _arming_boundary_satisfied(monkeypatch, tmp_path):
    """As tests/test_l5_arm_retry.py: satisfy the boundary's OTHER
    preconditions so that what is under test here is the campaign fact and
    nothing else. Those preconditions keep their own tests in
    tests/test_l5_arming_boundary.py, the shadow lock included."""
    sid = "campaign-delivery-session"
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, sid)
    monkeypatch.setenv(Q1.ARM_ENV, sid)
    monkeypatch.setenv("L5_SHADOW_ONLY", "0")
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    monkeypatch.delenv(AUTH.MARKET_ENV, raising=False)
    monkeypatch.delenv(AUTH.NOT_AFTER_ENV, raising=False)
    monkeypatch.delenv(AUTH.MAX_SUBMISSIONS_ENV, raising=False)
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "test-auth-holder")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "test-auth-holder")
    monkeypatch.setattr(AUTH, "durability_refusal", lambda p: None)


class _Transport:
    def __init__(self, market=W1) -> None:
        self.armed = False
        self.market_slug = market
        self.tick_size = "0.01"
        self.arms = 0

    def arm(self):
        self.arms += 1
        self.armed = True
        return {"armed": True, "market": self.market_slug}


class _Store:
    def orders_uncertain(self):
        return None


def _Limits():
    """INSIDE the approved campaign: $5 exposure, $5 daily loss. The REAL
    SafetyLimits, so the boundary reads the same fields it reads live."""
    return LX.SafetyLimits(max_total_exposure_usd=D("5"),
                           daily_realized_loss_halt_usd=D("5"))


def _WideLimits():
    """Caps raised ABOVE the approval after it was issued."""
    return LX.SafetyLimits(max_total_exposure_usd=D("50"),
                           daily_realized_loss_halt_usd=D("50"))


class _Executor:
    def __init__(self, limits=None) -> None:
        self.limits = limits or _Limits()
        self.halt_gate = object()


class _Runner:
    """Duck-typed stand-in driving the REAL `try_arm` code object."""

    try_arm = R.Runner.try_arm
    # `try_arm` asks whether the window is worth spending before reserving
    # it, so the stub carries that method too.
    campaign_window_is_usable = R.Runner.campaign_window_is_usable

    def venue_window_bounds(self):
        base = dt.datetime.fromtimestamp(getattr(self, "now_t", 0.0) or 0.0,
                                         dt.timezone.utc)
        s = base - dt.timedelta(seconds=300)
        return {"source": "VENUE", "start": s.isoformat(),
                "end": (s + dt.timedelta(seconds=900)).isoformat()}

    def __init__(self, root, *, market=W1, limits=None) -> None:
        self.root = str(root)
        self.transport = _Transport(market)
        self.store = _Store()
        self.executor = _Executor(limits)
        self.preflight_status = "PASSED"
        self.reconciled = True
        self.now_t = 0.0
        self.win = collections.Counter()
        self.stats = collections.Counter()

    def halted(self) -> bool:
        return False

    def reconcile_if_needed(self, now: float) -> None:
        pass


def test_the_runner_PASSES_THE_CAMPAIGN_FACT_on_every_arm_attempt(
        monkeypatch, tmp_path):
    """The defect this file exists for: the gate existed and nothing called
    it, so an approval document would have bounded nothing."""
    seen = []
    real = AUTH.authorize_arming

    def spy(**kw):
        seen.append(kw)
        return real(**kw)

    monkeypatch.setattr(AUTH, "authorize_arming", spy)
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    r = _Runner(tmp_path)
    r.try_arm()
    assert seen, "try_arm did not reach the authorization boundary at all"
    assert "campaign" in seen[0], (
        "the campaign verdict must be passed in as a FACT, like `reconciled` "
        "and `halted`, so a runner that cannot establish it is refused "
        "rather than silently un-bounded")
    assert seen[0]["campaign"].get("present") is False


def test_with_NO_approval_the_runner_still_arms_as_it_did_before(
        monkeypatch, tmp_path):
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    r = _Runner(tmp_path)
    r.try_arm()
    assert r.transport.armed is True
    assert r.stats == collections.Counter(), (
        "an absent campaign is silence: it must not even publish a counter")


def test_a_VALID_campaign_arms_and_spends_exactly_one_window(
        monkeypatch, tmp_path):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "test-deployment")
    r = _Runner(tmp_path)
    r.try_arm()
    assert r.transport.armed is True, dict(r.win)
    assert r.stats["campaign:OK"] == 1
    # the window is spent by `reserve_window` AFTER the boundary returns,
    # not by the verdict that asked -- see the two wasted windows in
    # tests/test_l5_campaign_is_the_authorization.py
    assert r.stats["campaign_window:RESERVED"] == 1
    led = CA.WindowLedger(str(tmp_path)).read(CA.nonce_sid(NONCE))
    assert led["windows"] == [W1]


def test_an_EXHAUSTED_campaign_REFUSES_the_arm(monkeypatch, tmp_path):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc(max_windows=1)))
    CA.campaign_decision(W2, root=str(tmp_path))         # spends the one
    r = _Runner(tmp_path, market=W1)
    r.try_arm()
    assert r.transport.armed is False, (
        "a spent campaign must not fall back to the single-window path")
    assert r.win["arm_refused_campaign_windows_exhausted"] == 1
    assert r.stats["campaign:CAMPAIGN_WINDOWS_EXHAUSTED"] == 1


def test_a_FAULT_IN_THE_CAMPAIGN_LAYER_refuses_rather_than_arming(
        monkeypatch, tmp_path):
    """The dangerous direction. A crash in the layer that BOUNDS a live run
    must never read as "no campaign, carry on"."""
    def boom(*a, **kw):
        raise RuntimeError("volume gone")

    monkeypatch.setattr(CA, "campaign_decision", boom)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    r = _Runner(tmp_path)
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_campaign_layer_failed"] == 1
    assert r.stats["campaign:CAMPAIGN_LAYER_FAILED"] == 1


def test_limits_WIDER_than_the_approved_campaign_refuse_the_arm(
        monkeypatch, tmp_path):
    """A running cap raised after approval invalidates the approval; it does
    not inherit it."""
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    r = _Runner(tmp_path, limits=_WideLimits())
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_campaign_limits_wider_than_approved"] == 1


def test_the_published_counter_NEVER_CARRIES_THE_NONCE(monkeypatch, tmp_path):
    """The owner's standing instruction: the nonce is not exposed in logs."""
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "test-deployment")
    r = _Runner(tmp_path)
    r.try_arm()
    blob = json.dumps(dict(r.stats)) + json.dumps(dict(r.win))
    assert NONCE not in blob
    assert "nonce" not in blob.lower()


def test_the_campaign_fact_is_wired_at_EVERY_authorize_arming_call_site():
    """A second arming path added later without `campaign=` would be a hole
    the tests above cannot see, because they only drive this one."""
    import pathlib
    src = pathlib.Path(R.__file__).read_text()
    sites = src.count("AUTH.authorize_arming(")
    wired = src.count("campaign=campaign")
    assert sites >= 1
    assert wired == sites, (
        "every authorize_arming call site in the runner must pass the "
        "campaign verdict; found %d call sites and %d wired" % (sites, wired))


def test_a_runner_that_cannot_LOCATE_ITS_LEDGER_refuses(monkeypatch, tmp_path):
    """Fail closed on our own fault, not on the owner's. A runner with no
    durable root cannot show how many windows are left, and "I could not
    tell" must not arm."""
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    r = _Runner(tmp_path)
    del r.root
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_campaign_layer_failed"] == 1


def test_a_BROKEN_COUNTER_cannot_crash_the_arming_path(monkeypatch, tmp_path):
    """Telemetry never decides arming. A counter that raises must not turn a
    valid campaign into an exception out of try_arm -- the tick would die and
    the window would be lost to a logging fault."""
    class _Exploding(dict):
        def __getitem__(self, k):
            raise RuntimeError("counter is broken")

        def __setitem__(self, k, v):
            raise RuntimeError("counter is broken")

    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "test-deployment")
    r = _Runner(tmp_path)
    r.stats = _Exploding()
    r.try_arm()
    assert r.transport.armed is True, (
        "the campaign was valid and the window reserved; only the counter "
        "failed, and that must not cost the window")


# ============================================================ the TELEMETRY
#
# The heartbeat is how the owner sees whether a budget is binding. Two of the
# numbers that matter were not on it: the DAILY realized figure the loss halt
# actually reads (the heartbeat carried only the all-time total, which is a
# different ceiling), and the campaign's own window/submission consumption.


class _TelStore:
    def __init__(self, day=None, all_time=D("-8.2688")) -> None:
        self._day = day
        self._all = all_time
        # the day line also reports WHY the day might be unknown, which it
        # reads off the durable state the real InventoryStore carries
        self.state = {"realized_unattributed": "0", "retired_markets": {}}

    def realized_current_utc_day(self, now=None):
        if isinstance(self._day, BaseException):
            raise self._day
        return self._day

    def realized_today(self):
        return self._all


class _TelRunner:
    campaign_telemetry = R.Runner.campaign_telemetry
    day_realized_line = R.Runner.day_realized_line

    def __init__(self, root, *, day=None, market=W1) -> None:
        self.root = str(root)
        self.store = _TelStore(day)
        self.limits = _Limits()
        self.transport = _Transport(market)


def test_the_heartbeat_reports_THE_DAY_THE_HALT_READS_not_the_all_time_total(
        tmp_path):
    r = _TelRunner(tmp_path, day=D("-1.25"))
    line = r.day_realized_line()
    assert line["utc_day_realized_usd"] == "-1.25"
    assert line["halt_usd"] == "5"
    assert line["would_halt"] is False
    assert line["room_usd"] == "3.75"
    # and the all-time total is still there, under its own name
    assert line["all_time_realized_usd"] == "-8.2688"


def test_a_day_that_CANNOT_BE_COMPUTED_is_reported_as_UNKNOWN_not_zero(
        tmp_path):
    """None is what REFUSES at the gate. A heartbeat that printed 0 for it
    would read as $5 of room at the exact moment the gate has none."""
    r = _TelRunner(tmp_path, day=None)
    line = r.day_realized_line()
    assert line["utc_day_realized_usd"] is None
    assert line["would_halt"] is None
    assert line["room_usd"] is None


def test_a_day_READ_THAT_RAISES_is_also_unknown(tmp_path):
    r = _TelRunner(tmp_path, day=RuntimeError("store broken"))
    line = r.day_realized_line()
    assert line["utc_day_realized_usd"] is None
    assert str(line["reason"]).startswith("DAY_READ_FAILED")


def test_the_day_line_names_a_breach_when_the_day_IS_through_the_ceiling(
        tmp_path):
    r = _TelRunner(tmp_path, day=D("-5.01"))
    assert r.day_realized_line()["would_halt"] is True


def test_campaign_telemetry_publishes_the_budget_without_the_NONCE(
        monkeypatch, tmp_path):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "8")
    CA.campaign_decision(W1, root=str(tmp_path))         # one window spent
    r = _TelRunner(tmp_path)
    tel = r.campaign_telemetry()
    assert tel["approval_present"] is True
    assert tel["approval_state"] == "approved"
    assert len(str(tel["document_sha256"])) == 64
    assert tel["windows_spent"] == 1
    assert tel["windows_remaining"] == 3
    assert tel["max_windows"] == 4
    assert tel["max_submissions"] == 8
    assert tel["permits_this_window"] is True
    blob = json.dumps(tel)
    assert NONCE not in blob, "the nonce must never reach a log line"
    assert CA.nonce_sid(NONCE) not in blob, (
        "nor the claim id derived from it")
    # no FIELD carries it either -- the only mention of the word is the
    # label saying it is not emitted
    assert not [k for k in tel if "nonce" in k.lower()]


def test_campaign_telemetry_DOES_NOT_SPEND_A_WINDOW(monkeypatch, tmp_path):
    """A heartbeat runs every sixty seconds. If reading the budget spent
    from it, four windows would be gone in four minutes."""
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    r = _TelRunner(tmp_path)
    for _ in range(10):
        tel = r.campaign_telemetry()
    assert tel["windows_spent"] == 0
    assert not (tmp_path / CA.CONSUMED_DIR).exists()


def test_campaign_telemetry_with_NO_approval_says_so_plainly(
        monkeypatch, tmp_path):
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    r = _TelRunner(tmp_path)
    tel = r.campaign_telemetry()
    assert tel["approval_present"] is False
    assert tel["reason"] == "approval_absent"


def test_an_UNSET_submission_budget_reports_None_not_a_number(
        monkeypatch, tmp_path):
    monkeypatch.delenv(AUTH.MAX_SUBMISSIONS_ENV, raising=False)
    r = _TelRunner(tmp_path)
    assert r.campaign_telemetry()["max_submissions"] is None


def test_the_FOUR_HOUR_campaign_IS_FOUR_HOURS_not_one(monkeypatch, tmp_path):
    """`DEFAULT_MAX_AGE_S` is one hour and exists to stop a forgotten PULL
    endpoint authorizing forever. Applied to an owner-set variable it would
    instead truncate the approved four-hour campaign to one, and send the
    owner back to re-issuing by hand every hour."""
    now = dt.datetime.now(dt.timezone.utc)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc(
        issued_utc=(now - dt.timedelta(hours=3)).isoformat(),
        expires_utc=(now + dt.timedelta(hours=1)).isoformat())))
    out = CA.campaign_decision(W1, root=str(tmp_path))
    assert out["ok"] is True, out.get("reason")


def test_but_a_document_PAST_THE_FOUR_HOUR_CEILING_still_refuses(
        monkeypatch, tmp_path):
    """The hard bound is unchanged: four hours measured from ISSUE, however
    long the document claims to last."""
    now = dt.datetime.now(dt.timezone.utc)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc(
        issued_utc=(now - dt.timedelta(hours=4, minutes=1)).isoformat(),
        expires_utc=(now + dt.timedelta(days=1)).isoformat())))
    out = CA.campaign_decision(W1, root=str(tmp_path))
    assert out["ok"] is False
    assert out["present"] is True


def test_the_heartbeat_publishes_THE_THREE_CLASS_BUDGETS(
        monkeypatch, tmp_path):
    """An operator reading only the flat total cannot tell whether the
    budget about to run out is the one that OPENS exposure or the one that
    UNWINDS it."""
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "tel-exp")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "10")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "14")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["REPLACE"], "20")
    AUTH.claim_class_ticket(AUTH.ledger_path(), "tel-exp", "OPEN")
    r = _TelRunner(tmp_path)
    cb = r.campaign_telemetry()["class_budgets"]
    assert cb["OPEN"] == {"max": 10, "spent": 1, "remaining": 9}
    assert cb["COMPLETE"] == {"max": 14, "spent": 0, "remaining": 14}
    assert cb["REPLACE"] == {"max": 20, "spent": 0, "remaining": 20}
