"""Shared signed-request budget: cancels and order-status first, research
only from spare budget, a 429 starts a cool-down."""
import pytest

from src import pmus_request_budget as RB


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def budget(**kw):
    c = Clock()
    b = RB.RequestBudget(clock=c, sleep=c.sleep, **kw)
    return b, c


def test_classification():
    assert RB.classify("POST", "/v1/order/ABC/cancel") == "critical"
    assert RB.classify("GET", "/v1/order/ABC") == "critical"
    assert RB.classify("POST", "/v1/orders") == "normal"
    assert RB.classify("GET", "/v1/orders/open") == "normal"
    assert RB.classify("GET", "/v1/portfolio/positions") == "normal"
    assert RB.classify("GET", "/v1/rfqs?status=RFQ_STATUS_OPEN") == "research"
    assert RB.classify("GET", "/v1/incentives/earnings") == "research"


def test_critical_is_never_refused_even_empty_and_cooling():
    b, c = budget(rps=1, burst=2)
    b.note_429()
    for _ in range(20):
        b.acquire("critical")
    assert b.stats["sent"]["critical"] == 20
    assert b.stats["critical_in_cooldown"] == 20


def test_research_only_from_spare_and_never_in_cooldown():
    b, c = budget(rps=1, burst=4, reserve=0.5)
    b.acquire("research")                       # 4 -> 3 (>= 2 reserve)
    b.acquire("research")                       # 3 -> 2
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("research")                   # would go below reserve
    c.t += 10
    b.note_429()
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("research")                   # cooling
    assert b.stats["deferred"]["research"] == 2


def test_normal_waits_for_budget_then_defers():
    b, c = budget(rps=1, burst=1, normal_wait_s=3)
    b.acquire("normal")                         # uses the only token
    b.acquire("normal")                         # waits ~1 s for a refill
    assert b.stats["sent"]["normal"] == 2
    b.note_429()                                # 15 s cool-down > 3 s wait
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("normal")


def test_critical_takes_precedence_over_normal():
    b, c = budget(rps=1, burst=2, normal_wait_s=0.1)
    for _ in range(4):
        b.acquire("critical")                   # bucket into deficit
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("normal")


class Inner:
    def __init__(self, fail_429=False):
        self.calls, self.fail_429 = [], fail_429

    def request(self, method, path, body=None, base="api"):
        self.calls.append((method, path, base))
        if self.fail_429:
            raise Exception("HTTP Error 429: Too Many Requests")
        return {}


def test_governed_transport_counts_429_and_cools():
    b, c = budget()
    t = RB.GovernedTransport(Inner(fail_429=True), b)
    with pytest.raises(Exception):
        t.request("GET", "/v1/orders/open")
    assert b.stats["http_429"] == 1 and b.cooling()


def test_public_gateway_reads_bypass_the_budget():
    b, c = budget(rps=0, burst=0, normal_wait_s=0)
    inner = Inner()
    t = RB.GovernedTransport(inner, b)
    t.request("GET", "/v1/markets/x/bbo", base="https://gateway.polymarket.us")
    assert len(inner.calls) == 1 and sum(b.stats["sent"].values()) == 0


def test_report_shape():
    b, c = budget()
    r = b.report()
    assert {"rps_budget", "sent_rps", "cooling", "sent", "deferred",
            "http_429"} <= set(r)


def test_critical_burst_cannot_starve_reconciliation_for_long():
    """The deficit is floored at -burst, so after ANY cancel storm an
    account read gets a token within (burst + 1) / rps seconds."""
    b, c = budget(rps=1, burst=4, normal_wait_s=3)
    for _ in range(100):
        b.acquire("critical")
    assert b._tokens >= -4
    c.t += (4 + 1) / 1
    b.acquire("normal")                          # account read goes
    assert b.stats["sent"]["normal"] == 1


def test_no_queue_waits_are_bounded():
    """Nothing is queued: research is refused at once, normal waits at most
    normal_wait_s on the caller's own thread, then is refused."""
    b, c = budget(rps=1, burst=1, normal_wait_s=3)
    b.acquire("normal")
    b.note_429()
    t0 = c.t
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("normal")
    assert c.t - t0 <= 3.5
    t0 = c.t
    with pytest.raises(RB.BudgetDeferred):
        b.acquire("research")
    assert c.t == t0
