"""The owner's seven pre-activation checks, as executable assertions.

Each of these was asked for before live execution is authorized. Prose in a
markdown file is not a verification; a test that fails when the property
stops holding is. One section per check.

Nothing here arms anything or touches a venue.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH
from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERIES = "cpc-btc-updown-15m-"
W1 = SERIES + "2026-10-20-1600z"
W2 = SERIES + "2026-10-20-1615z"
NONCE = "preactivation-check-nonce-01"


def live_doc(**over):
    now = dt.datetime.now(dt.timezone.utc)
    base = {"approved": True, "nonce": NONCE, "series": SERIES,
            "issued_utc": (now - dt.timedelta(minutes=1)).isoformat(),
            "expires_utc": (now + dt.timedelta(hours=4)).isoformat(),
            "max_windows": 4, "max_total_exposure_usd": "5",
            "max_daily_loss_usd": "5"}
    base.update(over)
    return base


# ===== CHECK 1: the campaign gate is invoked on EVERY live authorization


def test_1_every_authorize_arming_call_site_in_src_passes_the_campaign():
    """Not just the one the runner tests drive. A second arming path added
    later without `campaign=` would be a hole no behavioural test can see."""
    offenders = []
    for py in sorted((ROOT / "src").glob("*.py")):
        if py.name == "l5_live_authorization.py":
            continue                      # the definition, not a call site
        text = py.read_text()
        for m in re.finditer(r"authorize_arming\(", text):
            # the call's argument list, up to the matching close
            tail = text[m.end():m.end() + 4000]
            if "campaign=" not in tail.split(")\n")[0] + ")":
                # fall back to a generous window: the runner's call spans
                # many lines of comments
                if "campaign=campaign" not in tail[:4000]:
                    offenders.append("%s:%d" % (py.name,
                                                text[:m.start()].count("\n") + 1))
    assert offenders == [], (
        "authorize_arming call sites that do not pass the campaign verdict: "
        "%s" % offenders)


def test_1_the_boundary_REFUSES_a_present_but_invalid_campaign():
    """And never falls back to the single-window path, which would turn an
    expired or exhausted campaign into a fresh one-window authorization."""
    import inspect
    src = inspect.getsource(AUTH.authorize_arming)
    assert 'campaign.get("present")' in src
    assert 'campaign.get("ok")' in src
    assert "CAMPAIGN_LIMITS_WIDER_THAN_APPROVED" in src


def test_1_an_absent_campaign_is_SILENCE_and_not_permission(tmp_path,
                                                            monkeypatch):
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    out = CA.campaign_decision(W1, root=str(tmp_path))
    assert out["present"] is False and out["ok"] is False


# ===== CHECK 2: the approval module is present in the deployed image


def test_2_the_dockerfile_copies_the_approval_module():
    df = (ROOT / "deploy" / "l5_pmus_live" / "Dockerfile").read_text()
    assert "COPY src/l5_campaign_approval.py" in df, (
        "the runner imports it on every arm attempt; without the COPY the "
        "container fails to import")


def test_2_the_runner_imports_it_so_the_closure_check_covers_it():
    src = (ROOT / "src" / "l5_pmus_live_runner.py").read_text()
    assert "from src import l5_campaign_approval as CAMP" in src


# ===== CHECK 3: authorization and submission budgets survive restarts


def test_3_class_budgets_are_reconstructed_from_the_FILESYSTEM(tmp_path,
                                                               monkeypatch):
    """A redeploy keeps nothing in memory. Everything that bounds the run
    has to be readable off the volume, or a restart hands budget back."""
    led = str(tmp_path / "auth.jsonl")
    monkeypatch.setenv(AUTH.LEDGER_ENV, led)
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "restart-exp")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "2")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "12")
    AUTH.claim_class_ticket(led, "restart-exp", "OPEN")
    # ---- restart: drop every module-level cache by re-importing
    import importlib
    import src.l5_live_authorization as A2
    A2 = importlib.reload(A2)
    assert len(A2.class_spent(led, "restart-exp", "OPEN")) == 1
    A2.claim_class_ticket(led, "restart-exp", "OPEN")
    with pytest.raises(A2.Refused):
        A2.claim_class_ticket(led, "restart-exp", "OPEN")


def test_3_campaign_windows_are_reconstructed_from_the_VOLUME(tmp_path,
                                                              monkeypatch):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc(max_windows=1)))
    assert CA.campaign_decision(W1, root=str(tmp_path))["ok"] is True
    # a fresh gate, a fresh ledger object: the shape a redeploy produces
    out = CA.campaign_decision(W2, root=str(tmp_path))
    assert out["ok"] is False
    assert out["reason"] == "CAMPAIGN_WINDOWS_EXHAUSTED"


def test_3_the_ledger_directory_is_UNDER_THE_DURABLE_ROOT(monkeypatch):
    """A budget written to the container filesystem is not durable. Both
    counters have to land on the mounted volume."""
    monkeypatch.setenv("L5_LIVE_ROOT", "/data/l5-live-x")
    assert CA.consumed_root().startswith("/data/l5-live-x")
    monkeypatch.setenv(AUTH.LEDGER_ENV, "/data/l5-live-x/auth.jsonl")
    assert AUTH.ledger_path().startswith("/data/")


# ===== CHECK 4: the daily loss halt uses VERIFIED realized P&L


def test_4_the_gate_reads_the_UTC_DAY_and_not_the_all_time_scalar():
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    assert "realized_current_utc_day()" in src
    assert "realized_today=day_realized" in src


def test_4_an_UNDATED_settlement_row_makes_the_day_UNKNOWN():
    """Fail closed, never zero. An undated settlement cannot be ruled out
    as today's, and a ceiling fed an unknown figure is not a ceiling."""
    st = LX.InventoryStore.__new__(LX.InventoryStore)
    st.state = {"realized_unattributed": "0",
                "retired_markets": {"m": {"settlement_pnl": "-9", "t": ""}}}
    assert st.realized_current_utc_day() is None


def test_4_an_UNATTRIBUTED_residual_makes_the_day_UNKNOWN():
    st = LX.InventoryStore.__new__(LX.InventoryStore)
    st.state = {"realized_unattributed": "-3.5", "retired_markets": {}}
    assert st.realized_current_utc_day() is None


def test_4_only_rows_SETTLED_TODAY_count_toward_the_daily_halt():
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    st = LX.InventoryStore.__new__(LX.InventoryStore)
    st.state = {"realized_unattributed": "0", "retired_markets": {
        "a": {"settlement_pnl": "-1.25", "t": today + "T04:15:00+00:00"},
        "b": {"settlement_pnl": "-7.00", "t": "2026-10-19T18:30:00+00:00"},
    }}
    assert st.realized_current_utc_day() == D("-1.25")


def test_4_an_UNKNOWN_day_REFUSES_at_the_gate():
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    assert "DAY_REALIZED_UNAVAILABLE" in src
    i = src.index("day_realized = self.store.realized_current_utc_day()")
    assert "if day_realized is None" in src[i:i + 400]


# ===== CHECK 5: a failed balancing order never creates new exposure


class _Store:
    def __init__(self, residual=D(0), outstanding=D(0)) -> None:
        self._r, self._o = residual, outstanding

    def residual(self, market):
        return self._r

    def outstanding_residual(self, market):
        return self._o


class _Ex:
    prospective_residual = LX.FaithfulExecutor.prospective_residual
    submission_class = LX.FaithfulExecutor.submission_class
    balancing_refusal = LX.FaithfulExecutor.balancing_refusal

    def __init__(self, residual=D(0), outstanding=D(0)) -> None:
        self.store = _Store(residual, outstanding)


def test_5_an_overshooting_balance_is_refused_BEFORE_any_claim(tmp_path,
                                                              monkeypatch):
    """The refusal is evaluated at the boundary ahead of the claim and the
    ticket, so a rejected balancing order leaves nothing spent and nothing
    on the wire -- it cannot create exposure of any kind."""
    led = str(tmp_path / "auth.jsonl")
    monkeypatch.setenv(AUTH.LEDGER_ENV, led)
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "bal-exp")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "12")
    ex = _Ex(residual=D("2.46"))
    bad = ex.balancing_refusal("m", {"side": "DOWN"},
                               {"qty": D("5.00"), "price": D("0.90")},
                               "COMPLETE")
    assert bad["reason"] == "BALANCING_WOULD_OVERSHOOT"
    # nothing was spent, because the refusal comes first
    assert AUTH.class_spent(led, "bal-exp", "COMPLETE") == []
    assert not pathlib.Path(led).exists()


def test_5_the_boundary_refuses_before_it_claims():
    """Source-level, because the ORDER of the two is the property."""
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    i_guard = src.index("_bal = self.balancing_refusal(")
    i_claim = src.index("AUTH.claim_accumulation_leg(")
    assert i_guard < i_claim, (
        "the balancing safeguard must be evaluated before any claim or "
        "ticket is spent, or a refused order still costs budget")


def test_5_a_refused_balance_cannot_reach_the_transport():
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    i_guard = src.index("_bal = self.balancing_refusal(")
    i_submit = src.index("self.transport.submit(")
    assert i_guard < i_submit


def test_5_an_order_that_does_not_reduce_can_never_be_charged_as_reducing():
    """The exemption from the flat backstop rides on `reducing`, which the
    boundary derives from the SAME safeguard that refuses -- so an order
    exempt from the total has provably passed it."""
    src = (ROOT / "src" / "l5_live_execution.py").read_text()
    assert 'reducing=(_bal_klass == "COMPLETE")' in src
    assert src.count('reducing=(_bal_klass == "COMPLETE")') == 2, (
        "both claim paths must derive the exemption from the safeguard's "
        "own verdict")


# ===== CHECK 6: no stale single-market binding blocks advancement


def test_6_a_pinned_exact_market_is_REPORTED_as_blocking_advancement(
        tmp_path, monkeypatch):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    monkeypatch.setenv(AUTH.MARKET_ENV, W1)
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "pin-exp")
    r = _TelRunner(tmp_path, market=W1)
    tel = r.campaign_telemetry()
    assert tel["exact_market_binding"] == W1
    assert tel["blocks_advancement"] is True
    assert "AUTHORIZATION_BOUND_TO_MARKET" in tel["advancement_note"]


def test_6_a_pin_on_the_WRONG_market_says_so_explicitly(tmp_path,
                                                        monkeypatch):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    monkeypatch.setenv(AUTH.MARKET_ENV, W1)
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "pin-exp")
    r = _TelRunner(tmp_path, market=W2)
    note = r.campaign_telemetry()["advancement_note"]
    assert "is not the pinned one" in note


def test_6_with_NO_pin_advancement_is_clear(tmp_path, monkeypatch):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    monkeypatch.delenv(AUTH.MARKET_ENV, raising=False)
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "pin-exp")
    r = _TelRunner(tmp_path, market=W1)
    tel = r.campaign_telemetry()
    assert tel["exact_market_binding"] is None
    assert tel["blocks_advancement"] is False


def test_6_the_series_mandate_still_refuses_a_market_OUTSIDE_it(tmp_path,
                                                               monkeypatch):
    """Clearing the exact-market pin must not clear the scope. The campaign
    series and the mandate series both still bind."""
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(live_doc()))
    out = CA.campaign_decision("cpc-eth-updown-15m-2026-10-20-1600z",
                               root=str(tmp_path))
    assert out["ok"] is False
    assert out["reason"] == "CAMPAIGN_SERIES_MISMATCH"


# --------------------------------------------------------------- fixtures


class _Transport:
    def __init__(self, market=W1) -> None:
        self.market_slug = market
        self.armed = False
        self.tick_size = "0.01"


class _TelStore:
    def realized_current_utc_day(self, now=None):
        return D("0")

    def realized_today(self):
        return D("-8.2688")


class _TelRunner:
    campaign_telemetry = R.Runner.campaign_telemetry

    def __init__(self, root, *, market=W1) -> None:
        self.root = str(root)
        self.store = _TelStore()
        self.limits = LX.SafetyLimits()
        self.transport = _Transport(market)


def test_4_the_heartbeat_says_WHY_the_day_is_unknown_not_only_that_it_is(
        tmp_path):
    """`realized_current_utc_day` returns None whenever an UNATTRIBUTED
    residual is nonzero or any settlement row is undated. Both refuse at the
    gate, and an operator seeing only `None` cannot tell which, nor what to
    reconcile."""
    class _S:
        state = {"realized_unattributed": "-3.5",
                 "retired_markets": {"m": {"settlement_pnl": "1",
                                           "t": ""}}}

        def realized_current_utc_day(self, now=None):
            return None

        def realized_today(self):
            return D("-8.2688")

    class _Rn:
        day_realized_line = R.Runner.day_realized_line

        def __init__(self):
            self.store = _S()
            self.limits = LX.SafetyLimits()

    line = _Rn().day_realized_line()
    assert line["utc_day_realized_usd"] is None
    assert line["unattributed_usd"] == "-3.5"
    assert line["undated_settlements"] == ["m"]


# ===== the self-check that runs the REAL enforcement in the container


class _SelfStore:
    def residual(self, m):
        return D("2.46")

    def outstanding_residual(self, m):
        return D(0)


class _SelfEx:
    balancing_refusal = LX.FaithfulExecutor.balancing_refusal
    prospective_residual = LX.FaithfulExecutor.prospective_residual
    submission_class = LX.FaithfulExecutor.submission_class

    def __init__(self):
        self.store = _SelfStore()


class _SelfRunner:
    budget_selfcheck = R.Runner.budget_selfcheck

    def __init__(self, root):
        self.root = str(root)
        self.executor = _SelfEx()


def test_the_selfcheck_PASSES_against_the_approved_budgets(tmp_path,
                                                           monkeypatch):
    """The approved configuration, exercised through the real functions."""
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "selfcheck-exp")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "8")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "12")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["REPLACE"], "16")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "28")
    out = _SelfRunner(tmp_path).budget_selfcheck()
    assert out["ran"] is True, out.get("error")
    ck = out["checks"]
    assert ck["open_exhausts_at_its_ceiling"] is True
    assert ck["open_tickets_taken"] == 8
    assert ck["complete_survives_exhausted_open"] is True
    assert ck["backstop_binds_a_non_reducing_order"] is True
    assert ck["backstop_exempts_a_reducing_order"] is True
    assert ck["open_refused_without_completion_capacity"] is True
    assert ck["overshoot_is_refused"] is True
    assert ck["a_balance_inside_the_need_is_allowed"] is True
    assert out["all_passed"] is True


def test_the_selfcheck_NEVER_TOUCHES_THE_REAL_LEDGER(tmp_path, monkeypatch):
    """It must be impossible for a diagnostic to spend the budget it is
    checking."""
    real = tmp_path / "real_auth.jsonl"
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(real))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "real-exp")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "8")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "12")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "28")
    before = sorted(p.name for p in tmp_path.glob("*"))
    out = _SelfRunner(tmp_path).budget_selfcheck()
    assert out["ran"] is True
    assert AUTH.class_spent(str(real), "real-exp", "OPEN") == []
    assert AUTH.class_spent(str(real), "real-exp", "COMPLETE") == []
    assert AUTH.submissions_spent(str(real), "real-exp") == []
    # and it cleans up after itself
    assert sorted(p.name for p in tmp_path.glob("*")) == before


def test_the_selfcheck_says_so_when_NO_budgets_are_configured(tmp_path,
                                                              monkeypatch):
    for env in AUTH.CLASS_BUDGET_ENV.values():
        monkeypatch.delenv(env, raising=False)
    out = _SelfRunner(tmp_path).budget_selfcheck()
    assert out["ran"] is True
    assert out["checks"]["budgets_configured"] is False
    assert "nothing to enforce" in out["note"]


def test_the_selfcheck_REPORTS_a_failure_rather_than_raising(tmp_path,
                                                            monkeypatch):
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "8")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "12")
    monkeypatch.setattr(AUTH, "claim_class_ticket",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError("volume gone")))
    out = _SelfRunner(tmp_path).budget_selfcheck()
    assert out["all_passed"] is False
    assert "OSError" in str(out["error"])


def test_the_startup_path_EMITS_the_selfcheck():
    src = (ROOT / "src" / "l5_pmus_live_runner.py").read_text()
    assert 'emit("L5_BUDGET_SELFCHECK", runner.budget_selfcheck())' in src
