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


# ------------------------------------------------- schedule and restart
def test_first_audit_is_due_about_two_minutes_after_boot(eng):
    due_in = eng._last_reward + M.REWARD_CHECK_S - time.monotonic()
    assert 100 <= due_in <= 120


def test_schedule_one_missed_hourly_audit_is_retried_before_the_limit(
        eng, monkeypatch):
    """The 23 Nov timeline with the fix on: audit completes at boot, the
    next hourly one is refused, retries run every 60 s, and the 75-minute
    limit blocks new exposure only if every retry up to it fails."""
    monkeypatch.setattr(M, "AUDIT_MAX_AGE_S", 4500.0)
    monkeypatch.setattr(M, "AUDIT_RETRY_S", 60.0)
    clock = {"t": 50_000.0}
    monkeypatch.setattr(M.time, "monotonic", lambda: clock["t"])
    outcome = {"ok": True}
    attempts = []

    def audit():
        attempts.append(clock["t"])
        if outcome["ok"]:
            eng._audit_ok_at = clock["t"]
        return outcome["ok"]
    monkeypatch.setattr(eng, "reward_check", lambda: True)
    monkeypatch.setattr(eng, "account_audit", audit)
    eng._last_reward = clock["t"] - M.REWARD_CHECK_S + 120   # boot

    def run(seconds):
        blocked_at = None
        for _ in range(int(seconds)):
            clock["t"] += 1
            if clock["t"] - eng._last_reward > M.REWARD_CHECK_S:
                eng._last_reward = clock["t"]
                eng.audit_round()
            if blocked_at is None and eng.audit_blocks():
                blocked_at = clock["t"]
        return blocked_at

    t0 = clock["t"]
    assert eng.audit_blocks() == "audit_stale"       # boot: none yet
    run(125)
    assert eng.audit_blocks() is None                # first audit, ~2 min
    first_ok = eng._audit_ok_at
    outcome["ok"] = False
    blocked_at = run(2 * 3600)
    assert blocked_at is not None
    assert 4500 <= blocked_at - first_ok <= 4502     # 75 min, not 2 h 10
    failed = [t for t in attempts if t > first_ok]
    gaps = [b - a for a, b in zip(failed, failed[1:])]
    assert len(failed) >= 10 and max(gaps) <= 61     # every minute
    outcome["ok"] = True
    run(62)
    assert eng.audit_blocks() is None                # one clean audit lifts
    assert clock["t"] > t0


def test_a_restart_blocks_until_its_own_first_audit(tmp_path, monkeypatch):
    """Nothing about the audit is carried across a restart: a new process
    has no completed audit and is blocked until it has one."""
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.setattr(M, "AUDIT_MAX_AGE_S", 4500.0)
    v = FakeVenue()
    e1 = M.Engine(v, M.Book(tmp_path / "s.json"))
    e1._audit_ok_at = time.monotonic()
    assert e1.audit_blocks() is None
    e1.b.save()
    e2 = M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))
    assert e2.audit_blocks() == "audit_stale"


# ------------------------------------- settlement releases, pairing does not
def _campaign_market(eng, slug, yes, no, done=True):
    mk = eng.b.m(slug)
    mk["pos"]["YES"], mk["cost"]["YES"] = D(yes[0]), D(yes[1])
    mk["pos"]["NO"], mk["cost"]["NO"] = D(no[0]), D(no[1])
    mk["orders"]["x" + slug] = {"side": "YES", "px": "0.5", "qty": "2",
                                "filled": "2", "open": False,
                                "stage": "rn1_open",
                                "placed_at": time.time()}
    mk["done"] = done
    if not hasattr(eng.v, "t"):
        eng.v.t = object()            # the lookup is faked; it needs a handle
    return mk


def _settles(monkeypatch, table):
    from src import pmus_settlement_public_px as SP
    seen = []

    def fake(t, slug):
        seen.append(slug)
        return table.get(slug, {"final": False, "px": None})
    monkeypatch.setattr(SP, "public_settlement", fake)
    return seen


def test_a_balanced_position_still_counts_until_settlement_is_recorded(
        eng, monkeypatch):
    eng.since = None
    _campaign_market(eng, "aec-a", ("2", "1.32"), ("2", "0.58"))
    assert eng.rn1_campaign_exposure()["spend"] == D("1.90")
    cap = eng.capital_breakdown()
    assert D(cap["paired_awaiting_settlement_usd"]) == D("1.90")
    assert D(cap["settled_released_usd"]) == 0
    _settles(monkeypatch, {"aec-a": {"final": False, "px": "0.5"}})
    assert eng.settle_lookups(5) == {"looked": 1, "found": 0}
    assert eng.rn1_campaign_exposure()["spend"] == D("1.90")
    _settles(monkeypatch, {"aec-a": {"final": True, "px": "1"}})
    assert eng.settle_lookups(5) == {"looked": 1, "found": 1}
    assert eng.rn1_campaign_exposure()["spend"] == 0
    cap = eng.capital_breakdown()
    assert D(cap["paired_awaiting_settlement_usd"]) == 0
    assert D(cap["settled_released_usd"]) == D("1.90")


def test_an_open_market_is_never_looked_up_or_released(eng, monkeypatch):
    eng.since = None
    _campaign_market(eng, "aec-a", ("2", "1.32"), ("2", "0.58"), done=False)
    seen = _settles(monkeypatch, {"aec-a": {"final": True, "px": "1"}})
    assert eng.settle_lookups(5) == {"looked": 0, "found": 0}
    assert seen == []
    assert eng.rn1_campaign_exposure()["spend"] == D("1.90")


def test_short_passes_reach_every_closed_market(eng, monkeypatch):
    eng.since = None
    monkeypatch.setattr(M, "SETTLE_LOOKUP_GAP_S", 0.0)
    for s in ("aec-a", "aec-b", "aec-c"):
        _campaign_market(eng, s, ("1", "0.6"), ("1", "0.35"))
    seen = _settles(monkeypatch, {})           # none final yet
    for _ in range(3):
        assert eng.settle_lookups(1)["looked"] == 1
    assert sorted(seen) == ["aec-a", "aec-b", "aec-c"]


def test_capital_breakdown_keeps_the_four_states_apart(eng):
    eng.since = None
    _campaign_market(eng, "aec-a", ("2", "1.32"), ("2", "0.58"))
    mk = _campaign_market(eng, "aec-b", ("4", "2.54"), ("2", "0.62"),
                          done=False)
    mk["orders"]["r"] = {"side": "NO", "px": "0.33", "qty": "2",
                         "filled": "0", "open": True,
                         "stage": "two_sided_complete",
                         "placed_at": time.time()}
    s = _campaign_market(eng, "aec-c", ("1", "0.65"), ("1", "0.31"))
    s["settle_px"] = "1"
    cap = eng.capital_breakdown()
    assert D(cap["resting_orders_usd"]) == D("0.66")
    assert D(cap["unpaired_inventory_usd"]) == D("1.27")
    assert D(cap["paired_awaiting_settlement_usd"]) == D("1.90") + D("1.89")
    assert D(cap["settled_released_usd"]) == D("0.96")


def test_loop_side_settlement_check_is_off_by_default():
    assert M.SETTLE_CHECK_S == 0


# ------------------------------------------------ strict competition scope
def test_the_allowed_list_alone_is_not_a_whitelist(eng, monkeypatch):
    """Why the setting exists: with the open gate on, an unlisted
    competition is still allowed when the gate has nothing against it."""
    monkeypatch.setattr(M, "OPEN_EV_GATE", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"nba"}))
    assert M.STRICT_SPORTS is False
    assert eng.open_gate("aec-atp-a-b-2026-11-24")["allow"] is True
    assert eng.scope_block("aec-atp-a-b-2026-11-24") is None


def test_strict_scope_refuses_an_unlisted_competition_at_every_gate(
        eng, monkeypatch, capsys):
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    eng.v.states = {}
    monkeypatch.setattr(M, "STRICT_SPORTS", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"nba"}))
    out = "aec-atp-a-b-2026-11-24"
    assert eng.scope_block(out) == "competition_not_permitted"
    assert eng.scope_block("aec-nba-a-b-2026-11-24") is None
    # the controller's own decision
    eng.b.m(out)
    eng.step_market(out)
    assert _open_orders(eng.v) == []
    assert '"block":"competition_not_permitted"' in capsys.readouterr().out
    # and the last gate, whatever asked for the order
    n = len(eng.v.orders)
    eng.place(out, "YES", D("0.47"), D("2"), "rn1_open")
    eng.place(out, "YES", D("0.47"), D("2"), "rn1_extend")
    assert len(eng.v.orders) == n
    assert "place_refused_scope" in capsys.readouterr().out


def test_strict_scope_never_blocks_a_completion(eng, monkeypatch):
    monkeypatch.setattr(M, "STRICT_SPORTS", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"nba"}))
    out = "aec-atp-a-b-2026-11-24"
    n = len(eng.v.orders)
    eng.place(out, "NO", D("0.40"), D("1"), "two_sided_complete")
    assert len(eng.v.orders) == n + 1


def test_strict_scope_with_an_empty_list_permits_nothing(eng, monkeypatch):
    monkeypatch.setattr(M, "STRICT_SPORTS", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset())
    assert eng.scope_block("aec-nba-a-b-2026-11-24") == \
        "competition_not_permitted"


def test_a_listed_competition_is_quoted_as_before(eng, monkeypatch):
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    eng.v.states = {}
    monkeypatch.setattr(M, "STRICT_SPORTS", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"nba"}))
    s = "aec-nba-a-b-2026-11-24"
    eng.b.m(s)
    eng.step_market(s)
    assert len(_open_orders(eng.v)) == 2


def test_a_flat_carried_over_market_outside_the_scope_is_dropped(
        eng, monkeypatch):
    """23 Nov left five flat markets tracked. Under a strict scope one
    that is not on the list places nothing and gives up its slot."""
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    eng.v.states = {}
    monkeypatch.setattr(M, "STRICT_SPORTS", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"nba"}))
    out = "aec-atp-a-b-2026-11-24"
    eng.b.m(out)
    assert out in eng.active()
    eng.step_market(out)
    assert _open_orders(eng.v) == []
    # the slot is given up once the market has been idle two minutes
    eng.b.d["markets"][out]["idle_since"] = 0
    eng.step_market(out)
    assert _open_orders(eng.v) == []
    assert out not in eng.active()
