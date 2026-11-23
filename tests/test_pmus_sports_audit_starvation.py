"""LIVE 2026-11-23, 17:15-18:56Z: both hourly account audits of a campaign
were refused by the request budget ("normal request deferred") because
quote traffic had spent the bucket, the reward read likewise, and nothing
retried. The bot quoted for 1 h 45 min with no account audit.

Owner 2026-11-23: "Safety-critical account audits must never compete on
equal terms with optional quote traffic." "Reserve request capacity for
risk checks, retry deferred audits, and fail closed on stale account
state."
"""
import threading
import time
from decimal import Decimal as D

import pytest

from src import pmus_request_budget as RB
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def budget(**kw):
    c = Clock()
    return RB.RequestBudget(clock=c, sleep=c.sleep, **kw), c


class Inner:
    def __init__(self):
        self.calls = []

    def request(self, method, path, *a, **k):
        self.calls.append((method, path))
        return {}


# ---------------------------------------------------------------- budget
def test_an_empty_bucket_refuses_a_normal_read_but_not_a_safety_read():
    """The failure as it happened: the bucket is spent by quoting; the
    audit's read waits 3 s as a normal request and is refused."""
    b, c = budget(rps=0.2, burst=1)
    b.acquire("normal")                       # quote traffic took the token
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("normal")                   # the audit, as it was classed
    b.acquire("safety")                       # waits for the refill instead
    assert b.stats["sent"]["safety"] == 1
    assert b.stats["deferred"]["safety"] == 0


def test_a_safety_read_is_bounded_and_counted_when_it_cannot_be_served():
    b, c = budget(rps=0.01, burst=1, safety_wait_s=20)
    b.acquire("normal")
    t0 = c.t
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("safety")
    assert c.t - t0 <= 21
    assert b.stats["deferred"]["safety"] == 1
    assert b._safety_waiting == 0             # the waiter is always released


def test_quote_traffic_stands_aside_while_a_safety_read_waits():
    """Real clock, two threads: orders arrive continuously; the safety
    read still gets a token, and gets the next one."""
    b = RB.RequestBudget(rps=20, burst=1, normal_wait_s=0.05,
                         safety_wait_s=3)
    stop = threading.Event()
    sent = {"normal": 0}

    def quoting():
        while not stop.is_set():
            try:
                b.acquire("normal")
                sent["normal"] += 1
            except RB.BudgetDeferred:
                pass

    th = [threading.Thread(target=quoting) for _ in range(3)]
    for t in th:
        t.start()
    try:
        time.sleep(0.2)
        for _ in range(10):
            b.acquire("safety")               # raises if starved
    finally:
        stop.set()
        for t in th:
            t.join()
    assert b.stats["sent"]["safety"] == 10
    assert b.stats["deferred"]["safety"] == 0
    assert sent["normal"] > 0                 # quoting was not shut out


def test_normal_and_research_yield_only_while_a_safety_read_waits():
    b, c = budget(rps=1, burst=4)
    b._safety_waiting = 1
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("research")
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("normal")
    b._safety_waiting = 0
    b.acquire("normal")
    b.acquire("research")


def test_a_cancel_is_never_delayed_by_a_waiting_safety_read():
    b, c = budget(rps=1, burst=1)
    b._safety_waiting = 1
    for _ in range(5):
        b.acquire("critical")
    assert b.stats["sent"]["critical"] == 5


def test_safety_transport_marks_only_its_own_request():
    b, c = budget(rps=1, burst=4)
    inner = Inner()
    g = RB.GovernedTransport(inner, b)
    seen = []
    real = b.acquire
    b.acquire = lambda cls: (seen.append(cls), real(cls))[1]
    RB.SafetyTransport(g).request("GET", "/v1/portfolio/activities")
    RB.SafetyTransport(g).request("GET", "/v1/incentives/earnings")
    g.request("POST", "/v1/orders")           # the pass between requests
    RB.SafetyTransport(g).request("POST", "/v1/order/A/cancel")
    assert seen == ["safety", "safety", "normal", "critical"]
    assert not b.in_safety()


def test_the_safety_context_is_per_thread():
    b, c = budget()
    out = {}
    with b.safety():
        t = threading.Thread(
            target=lambda: out.setdefault("other", b.in_safety()))
        t.start()
        t.join()
        assert b.in_safety()
    assert out["other"] is False and not b.in_safety()


# ---------------------------------------------------------------- engine
@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    v = FakeVenue()
    v.bid, v.ask = D("0.48"), D("0.50")
    global _ENG
    _ENG = M.Engine(v, M.Book(tmp_path / "s.json"))
    return _ENG


def test_the_audit_walk_uses_the_safety_class(eng, monkeypatch):
    b, c = budget()
    inner = Inner()
    eng.v.t = RB.GovernedTransport(inner, b)
    seen = []
    real = b.acquire
    b.acquire = lambda cls: (seen.append(cls), real(cls))[1]
    monkeypatch.setattr(M, "AUDIT_INTERLEAVE", False)
    eng.audit_transport().request("GET", "/v1/portfolio/activities")
    with eng.safety_reads():
        eng.v.t.request("GET", "/v1/portfolio/positions")
    eng.v.t.request("GET", "/v1/portfolio/positions")
    assert seen == ["safety", "safety", "normal"]


def test_the_interleaved_order_pass_is_not_a_safety_read(eng, monkeypatch):
    b, c = budget()
    eng.v.t = RB.GovernedTransport(Inner(), b)
    seen = []
    monkeypatch.setattr(M, "AUDIT_INTERLEAVE", True)
    monkeypatch.setattr(eng, "service_pass",
                        lambda: seen.append(b.in_safety()))
    eng.audit_transport().request("GET", "/v1/portfolio/activities")
    assert seen == [False]


def test_a_deferred_audit_is_retried_within_the_retry_interval(
        eng, monkeypatch):
    monkeypatch.setattr(M, "AUDIT_RETRY_S", 60.0)
    monkeypatch.setattr(eng, "reward_check", lambda: True)
    monkeypatch.setattr(eng, "account_audit", lambda: False)
    eng._last_reward = time.monotonic()           # as the loop sets it
    assert eng.audit_round() is False
    due_in = eng._last_reward + M.REWARD_CHECK_S - time.monotonic()
    assert 55 <= due_in <= 60.5                   # not another hour
    monkeypatch.setattr(eng, "account_audit", lambda: True)
    eng._last_reward = time.monotonic()
    assert eng.audit_round() is True
    assert eng._last_reward + M.REWARD_CHECK_S - time.monotonic() > 3500


def test_a_deferred_reward_read_is_retried_too(eng, monkeypatch):
    monkeypatch.setattr(eng, "reward_check", lambda: False)
    monkeypatch.setattr(eng, "account_audit", lambda: True)
    eng._last_reward = time.monotonic()
    assert eng.audit_round() is False
    assert eng._last_reward + M.REWARD_CHECK_S - time.monotonic() <= 60.5


def test_a_retry_never_postpones_a_sooner_recheck(eng, monkeypatch):
    monkeypatch.setattr(eng, "reward_check", lambda: True)
    monkeypatch.setattr(eng, "account_audit", lambda: False)
    sooner = time.monotonic() - M.REWARD_CHECK_S + 5
    eng._last_reward = sooner
    eng.audit_round()
    assert eng._last_reward == sooner


def test_the_real_audit_reports_a_budget_refusal_as_not_complete(
        eng, monkeypatch):
    class Refuses:
        def request(self, *a, **k):
            raise RB.BudgetDeferred("safety request deferred")
    monkeypatch.setattr(eng, "audit_transport", lambda: Refuses())
    assert eng.account_audit() is False
    assert eng.audit_age_s() is None


def test_stale_audit_blocks_new_exposure_only_when_the_setting_is_on(
        eng, monkeypatch):
    assert M.AUDIT_MAX_AGE_S == 0                 # default: today's behaviour
    assert eng.audit_blocks() is None
    monkeypatch.setattr(M, "AUDIT_MAX_AGE_S", 4500.0)
    assert eng.audit_blocks() == "audit_stale"    # none since boot
    eng._audit_ok_at = time.monotonic()
    assert eng.audit_blocks() is None
    eng._audit_ok_at = time.monotonic() - 4501
    assert eng.audit_blocks() == "audit_stale"


def test_a_failed_reconciliation_outranks_staleness(eng, monkeypatch):
    monkeypatch.setattr(M, "AUDIT_MAX_AGE_S", 4500.0)
    eng._audit_strikes = {"positions": 1}
    assert eng.audit_blocks() == "audit_failed:positions"


def _open_orders(v, eng=None):
    return sorted((o["side"], o.get("stage")) for mk in
                  _ENG.b.d["markets"].values() for o in mk["orders"].values()
                  if o.get("open"))


def test_no_audit_since_boot_stops_openings_and_a_clean_one_restores_them(
        eng, monkeypatch, capsys):
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    eng.v.states = {}
    monkeypatch.setattr(M, "AUDIT_MAX_AGE_S", 4500.0)
    eng.b.m("aec-x")
    eng.step_market("aec-x")
    assert _open_orders(eng.v) == []
    assert '"block":"audit_stale"' in capsys.readouterr().out
    eng._audit_ok_at = time.monotonic()
    eng.step_market("aec-x")
    assert len(_open_orders(eng.v)) == 2


def test_a_stale_audit_pulls_openings_but_keeps_the_completion(
        eng, monkeypatch):
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    eng.v.states = {}
    monkeypatch.setattr(M, "AUDIT_MAX_AGE_S", 4500.0)
    eng._audit_ok_at = time.monotonic() - 9000
    mk = eng.b.m("aec-x")
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.88")
    eng.step_market("aec-x")
    assert _open_orders(eng.v) == [("NO", "two_sided_complete")]
