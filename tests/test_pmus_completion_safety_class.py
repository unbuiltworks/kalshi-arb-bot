"""PMUS_RN1_COMPLETION_SAFETY_CLASS: completion placements go through the
shared budget as SAFETY requests (wait, not deferred; normal yields);
openings and extensions stay normal. Rate and cool-downs unchanged."""
from decimal import Decimal as D

from src import pmus_request_budget as RB
from src import pmus_sports_two_sided as M


class _Inner:
    def __init__(self):
        self.calls = []

    def request(self, method, path, *a, **k):
        self.calls.append((method, path))
        return {"ok": True}


def _venue(monkeypatch, budget):
    v = M.Venue.__new__(M.Venue)
    v.t = RB.GovernedTransport(_Inner(), budget)
    seen = []

    class _Micro:
        @staticmethod
        def place_limit_buy(t, slug, side, px, qty, stage, post_only=True,
                            **k):
            seen.append((stage, budget.in_safety()))
            t.request("POST", "/v1/orders", {})
            return "oid"
    v.micro = _Micro
    monkeypatch.setattr(M, "order_good_till", lambda: None)
    return v, seen


def test_default_off_completion_is_normal(monkeypatch):
    b = RB.RequestBudget(rps=100, burst=10)
    v, seen = _venue(monkeypatch, b)
    v.place("s", "NO", D("0.5"), D("1"), "two_sided_complete")
    assert seen == [("two_sided_complete", False)]
    assert b.stats["sent"]["normal"] == 1 and b.stats["sent"]["safety"] == 0


def test_on_completion_is_safety_opening_stays_normal(monkeypatch):
    monkeypatch.setattr(M, "RN1_COMPLETION_SAFETY_CLASS", True)
    b = RB.RequestBudget(rps=100, burst=10)
    v, seen = _venue(monkeypatch, b)
    v.place("s", "NO", D("0.5"), D("1"), "two_sided_complete")
    v.place("s", "YES", D("0.4"), D("1"), "rn1_open")
    v.place("s", "YES", D("0.4"), D("1"), "rn1_extend")
    assert seen == [("two_sided_complete", True), ("rn1_open", False),
                    ("rn1_extend", False)]
    assert b.stats["sent"]["safety"] == 1 and b.stats["sent"]["normal"] == 2


def test_on_completion_waits_through_a_cooldown_instead_of_deferring(
        monkeypatch):
    monkeypatch.setattr(M, "RN1_COMPLETION_SAFETY_CLASS", True)
    now = [0.0]
    b = RB.RequestBudget(rps=10, burst=2, cooldown_s=1.0, normal_wait_s=3.0,
                         safety_wait_s=5.0, clock=lambda: now[0],
                         sleep=lambda s: now.__setitem__(0, now[0] + s))
    b.note_429()                                   # cooling for 1 s
    v, seen = _venue(monkeypatch, b)
    v.place("s", "NO", D("0.5"), D("1"), "two_sided_complete")
    assert seen and b.stats["sent"]["safety"] == 1
    try:
        v.place("s", "YES", D("0.4"), D("1"), "rn1_open")
    except RB.BudgetDeferred:
        pass                                       # an opening may still defer


def test_on_the_wait_is_capped_at_the_normal_wait(monkeypatch):
    """A cool-down longer than the normal wait defers the completion after
    the normal wait instead of holding the loop for the full safety wait."""
    monkeypatch.setattr(M, "RN1_COMPLETION_SAFETY_CLASS", True)
    now = [0.0]
    b = RB.RequestBudget(rps=10, burst=2, cooldown_s=60.0, normal_wait_s=3.0,
                         safety_wait_s=20.0, clock=lambda: now[0],
                         sleep=lambda s: now.__setitem__(0, now[0] + s))
    b.note_429()
    v, _ = _venue(monkeypatch, b)
    import pytest
    with pytest.raises(RB.BudgetDeferred):
        v.place("s", "NO", D("0.5"), D("1"), "two_sided_complete")
    assert now[0] <= 3.5
    # audits keep the full safety wait (no cap outside the completion path)
    assert getattr(b._tl, "max_wait", None) is None


def test_critical_cancel_is_never_held_by_a_waiting_completion():
    b = RB.RequestBudget(rps=1, burst=1)
    b._tokens = -1                                   # budget exhausted
    b.acquire("critical")                            # cancels always go
    assert b.stats["sent"]["critical"] == 1
