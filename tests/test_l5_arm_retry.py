"""1545z: arming must be retried after a PASSED preflight, not attempted once.

THE LIVE FAILURE. On 1545z the executor was arm-eligible -- owner token matched,
preflight PASSED -- and the whole window ran `armed: false` with zero orders. The
single attempt at 15:45:23 hit a terminal order not yet booked, `orders_uncertain()`
was set, arming refused CORRECTLY, and by 15:45:35 the uncertainty had cleared with
nothing left in the process to ask again. A refusal that clears on its own must not
cost a window.

WHERE IT NOW LIVES. `Runner.try_arm` was removed by 3a: exactly one component may own
the arming transition, and it is `scripts/l5_pmus_live_oneshot.py`. This suite
therefore tests the requirement WHERE IT BELONGS -- against the one-shot's
`arm_with_bounded_retry` and the `src/pmus_arm_retry` predicates -- and the file's
temporary xfail is removed because the requirement is now implemented, not deferred.

THE DISTINCTION EVERY TEST HERE DEFENDS:

    retrying a TRANSIENT PRE-ARM SAFETY CHECK   -- permitted, bounded
    reacquiring or renewing AUTHORIZATION       -- never

so the negative cases matter more than the positive one.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_arm_retry as AR                            # noqa: E402
from src import l5_live_authorization as AUTH                   # noqa: E402

MKT = "cpc-btc-updown-15m-2026-09-27-1545z"
OTHER = "cpc-btc-updown-15m-2026-09-27-1600z"


class _Auth:
    def __init__(self, market=MKT, claimed=True, consumed=False):
        self.market, self.claimed, self.consumed = market, claimed, consumed
        self.claims = 0
        self.consumes = 0

    def claim(self, market):
        self.claims += 1
        self.market, self.claimed = market, True

    def consume(self, outcome):
        self.consumes += 1
        self.consumed = True


class _Journal:
    def __init__(self):
        self.rows = []

    def write(self, kind, payload):
        self.rows.append((kind, dict(payload)))


class _Probe:
    """Only what arm_with_bounded_retry touches. The real method is bound in."""
    from scripts.l5_pmus_live_oneshot import Probe as _Real
    arm_with_bounded_retry = _Real.arm_with_bounded_retry

    def __init__(self, refusals, *, market=MKT):
        self.market = market
        self.armed = False
        self.journal = _Journal()
        self.refusals = list(refusals)      # popped per attempt; [] means arm
        self.arm_calls = 0

    def arm(self):
        self.arm_calls += 1
        if self.refusals:
            raise AUTH.Refused(self.refusals.pop(0), {})
        self.armed = True
        return {"armed": True, "market": self.market}


def _gate(auth=None, *, halted=False, fp="MANDATE-1", submitted=0,
          target=None, deadline=45.0, market=MKT, clock=None):
    now = {"t": 0.0}
    tick = clock or (lambda: now["t"])
    g = AR.ArmRetryGate(
        market=market, authorization=auth or _Auth(),
        halted=lambda: halted,
        mandate_fingerprint=(fp if callable(fp) else (lambda: fp)),
        submitted_count=(submitted if callable(submitted)
                         else (lambda: submitted)),
        window_target=(target if callable(target) else (lambda: target)),
        deadline_s=deadline, interval_s=0.0, clock=tick)
    g._now = now
    return g


# ------------------------------------------------- the 1545z case, positive

def test_a_transient_orders_uncertain_clears_and_then_arms():
    """THE POINT. The refusal that cost 1545z must no longer cost a window."""
    p = _Probe(["orders_uncertain", "orders_uncertain"])
    g = _gate()
    out = p.arm_with_bounded_retry(g, sleep=lambda s: None)
    assert out["armed"] is True and p.armed is True
    assert p.arm_calls == 3 and g.attempts == 3
    assert [k for k, _ in p.journal.rows].count("arm_refused") == 2


def test_the_same_authorization_is_reused_and_never_reclaimed():
    a = _Auth()
    p = _Probe(["orders_uncertain"])
    p.arm_with_bounded_retry(_gate(a), sleep=lambda s: None)
    assert a.claims == 0, "a retry must never re-claim an authorization"
    assert a.consumes == 0, "arming does not consume; the probe close-out does"


def test_success_stays_one_shot_and_cannot_become_renewal():
    p = _Probe([])
    g = _gate()
    first = p.arm_with_bounded_retry(g, sleep=lambda s: None)
    assert first["armed"] is True and p.arm_calls == 1
    again = p.arm_with_bounded_retry(_gate(), sleep=lambda s: None)
    assert again["armed"] is True
    assert "renewal" in again["note"]
    assert p.arm_calls == 1, "a second call must not re-arm"


# ------------------------------------------------------- the negative cases

def test_a_non_transient_refusal_aborts_immediately():
    p = _Probe(["PREFLIGHT_NOT_PASSED"])
    with pytest.raises(AR.ArmAbort) as e:
        p.arm_with_bounded_retry(_gate(), sleep=lambda s: None)
    assert e.value.reason == "NON_TRANSIENT_REFUSAL"
    assert p.arm_calls == 1 and p.armed is False


def test_an_unknown_refusal_is_treated_as_non_transient():
    """Whitelist, not blacklist: a refusal we do not understand is not retried."""
    assert AR.is_transient("orders_uncertain") is True
    assert AR.is_transient("something_new_from_the_venue") is False
    p = _Probe(["something_new_from_the_venue"])
    with pytest.raises(AR.ArmAbort):
        p.arm_with_bounded_retry(_gate(), sleep=lambda s: None)


def test_halt_aborts_before_any_attempt():
    p = _Probe([])
    with pytest.raises(AR.ArmAbort) as e:
        p.arm_with_bounded_retry(_gate(halted=True), sleep=lambda s: None)
    assert e.value.reason == "HALT_PRESENT" and p.arm_calls == 0


def test_halt_appearing_mid_retry_aborts():
    state = {"halted": False}
    g = AR.ArmRetryGate(market=MKT, authorization=_Auth(),
                        halted=lambda: state["halted"],
                        mandate_fingerprint=lambda: "M",
                        submitted_count=lambda: 0,
                        window_target=lambda: None,
                        deadline_s=45.0, interval_s=0.0, clock=lambda: 0.0)
    p = _Probe(["orders_uncertain", "orders_uncertain"])

    def sleep(_):
        state["halted"] = True
    with pytest.raises(AR.ArmAbort) as e:
        p.arm_with_bounded_retry(g, sleep=sleep)
    assert e.value.reason == "HALT_PRESENT"
    assert p.armed is False


def test_a_changed_mandate_aborts():
    seq = iter(["MANDATE-1", "MANDATE-2", "MANDATE-2", "MANDATE-2"])
    fps = [next(seq)]
    g = _gate(fp=lambda: fps[0])
    fps[0] = "MANDATE-2"
    p = _Probe(["orders_uncertain"])
    with pytest.raises(AR.ArmAbort) as e:
        p.arm_with_bounded_retry(g, sleep=lambda s: None)
    assert e.value.reason == "MANDATE_CHANGED" and p.arm_calls == 0


def test_an_absent_mandate_aborts():
    with pytest.raises(AR.ArmAbort) as e:
        _Probe([]).arm_with_bounded_retry(_gate(fp=None),
                                         sleep=lambda s: None)
    assert e.value.reason == "NO_MANDATE"


def test_a_window_change_aborts():
    p = _Probe([])
    with pytest.raises(AR.ArmAbort) as e:
        p.arm_with_bounded_retry(_gate(target=OTHER), sleep=lambda s: None)
    assert e.value.reason == "WINDOW_CHANGED" and p.arm_calls == 0


def test_an_authorization_bound_to_another_market_aborts():
    p = _Probe([])
    with pytest.raises(AR.ArmAbort) as e:
        p.arm_with_bounded_retry(_gate(_Auth(market=OTHER)),
                                 sleep=lambda s: None)
    assert e.value.reason == "AUTHORIZATION_BOUND_TO_MARKET"


def test_a_consumed_authorization_aborts():
    with pytest.raises(AR.ArmAbort) as e:
        _Probe([]).arm_with_bounded_retry(_gate(_Auth(consumed=True)),
                                          sleep=lambda s: None)
    assert e.value.reason == "AUTHORIZATION_CONSUMED"


def test_an_unclaimed_authorization_aborts():
    with pytest.raises(AR.ArmAbort) as e:
        _Probe([]).arm_with_bounded_retry(_gate(_Auth(claimed=False)),
                                          sleep=lambda s: None)
    assert e.value.reason == "AUTHORIZATION_NOT_CLAIMED"


def test_any_submitted_order_aborts_retry():
    """After partial execution there is no such thing as a pre-arm retry."""
    p = _Probe(["orders_uncertain"])
    with pytest.raises(AR.ArmAbort) as e:
        p.arm_with_bounded_retry(_gate(submitted=1), sleep=lambda s: None)
    assert e.value.reason == "ORDER_ALREADY_SUBMITTED" and p.arm_calls == 0


def test_an_order_submitted_mid_retry_aborts():
    n = {"v": 0}
    g = AR.ArmRetryGate(market=MKT, authorization=_Auth(),
                        halted=lambda: False,
                        mandate_fingerprint=lambda: "M",
                        submitted_count=lambda: n["v"],
                        window_target=lambda: None,
                        deadline_s=45.0, interval_s=0.0, clock=lambda: 0.0)
    p = _Probe(["orders_uncertain", "orders_uncertain"])

    def sleep(_):
        n["v"] = 1
    with pytest.raises(AR.ArmAbort) as e:
        p.arm_with_bounded_retry(g, sleep=sleep)
    assert e.value.reason == "ORDER_ALREADY_SUBMITTED"


def test_the_retry_deadline_is_bounded():
    now = {"t": 0.0}
    g = AR.ArmRetryGate(market=MKT, authorization=_Auth(),
                        halted=lambda: False,
                        mandate_fingerprint=lambda: "M",
                        submitted_count=lambda: 0,
                        window_target=lambda: None,
                        deadline_s=10.0, interval_s=0.0,
                        clock=lambda: now["t"])
    p = _Probe(["orders_uncertain"] * 50)

    def sleep(_):
        now["t"] += 4.0
    with pytest.raises(AR.ArmAbort) as e:
        p.arm_with_bounded_retry(g, sleep=sleep)
    assert e.value.reason == "RETRY_DEADLINE_EXCEEDED"
    # t=0 refuse (elapsed 0 < 10) -> t=4 refuse (4 < 10) -> t=8 refuse (8 < 10)
    # -> t=12 refuse, 12 >= 10, abort. Four looks, then bounded out.
    assert p.arm_calls == 4, p.arm_calls
    assert p.armed is False


def test_every_gate_is_re_read_on_every_attempt_not_cached():
    calls = {"halt": 0, "fp": 0, "sub": 0, "win": 0}

    def c(k, v):
        def f():
            calls[k] += 1
            return v
        return f
    g = AR.ArmRetryGate(market=MKT, authorization=_Auth(),
                        halted=c("halt", False), mandate_fingerprint=c("fp", "M"),
                        submitted_count=c("sub", 0), window_target=c("win", None),
                        deadline_s=45.0, interval_s=0.0, clock=lambda: 0.0)
    p = _Probe(["orders_uncertain", "orders_uncertain"])
    p.arm_with_bounded_retry(g, sleep=lambda s: None)
    # 1 preconditions() before the loop + 1 per may_retry() = 3
    for k in ("halt", "sub", "win"):
        assert calls[k] >= 3, (k, calls)
    assert calls["fp"] >= 4, calls        # pinned once at construction, then re-read


def test_the_policy_module_holds_no_arming_capability():
    """3a: only the one-shot may own the transition, so this file has no arm()."""
    src = open(AR.__file__, encoding="utf-8").read()
    assert ".arm()" not in src
