"""Who asked for the window-boundary burst, and what may be deferred.

THE BURST IS MEASURED AND UNATTRIBUTED. Across one window roll the shared
limiter served +121 requests, slept +61.14s inside the tick and skipped a
60-second heartbeat, with http_429 at 0 throughout; the egress trace sits
at exactly 1.5 flows/second for the whole 80-second stall. Three
independent measurements of the SAME pacing, and not one of them says
which caller asked. There was no per-path counter in the process.

So this file pins two things:

  * `by_purpose` accounting, which is pure observation and always on;
  * the deferral budget, which is OFF by default in two independent ways
    (a 0-second budget and an empty allowlist) and, even when on, may
    only refuse purposes whose callers treat a failed read as "not yet".

The allowlist discipline is the load-bearing safety property here.
`sweep_settlements` counts `settlement_read_error` and leaves the market
unretired -- its own docstring calls that the safe direction. `poll_orders`
and `reconcile_if_needed` both call `mark_uncertain` on ANY exception,
which refuses submissions until an authoritative read succeeds. Deferring
either would trade latency for a stopped engine, so the tests below pin
that they are not deferrable by default and that the budget is inert
unless an operator sets both knobs.
"""
from __future__ import annotations

import pytest

from src import l5_pmus_transport as T


def lim(**kw):
    """A limiter on a fake clock, so no test ever really sleeps."""
    clock = {"t": 0.0}
    slept = []

    def now():
        return clock["t"]

    def sleep(s):
        slept.append(s)
        clock["t"] += s

    base = dict(rps=1.5, burst=4.0, now_fn=now, sleep_fn=sleep)
    base.update(kw)
    lm = T.RateLimiter(**base)
    lm._clock, lm._slept = clock, slept
    return lm


# ------------------------------------------------------- the default is inert

def test_default_budget_and_allowlist_are_both_off():
    lm = lim()
    assert lm.tick_wait_budget_s == 0.0
    assert lm.deferrable_purposes == frozenset()


def test_default_never_defers_however_long_the_tick_sleeps():
    """100 queued reads on one tick: every one is served, as today."""
    lm = lim()
    lm.tick_begin()
    for _ in range(100):
        lm.before(write=False, purpose="settlement")
    assert lm.counts["requests"] == 100
    assert lm.counts["deferred"] == 0
    # 4 free from the burst, the rest paced at 1.5/s
    assert lm.tick_wait_s == pytest.approx(96 / 1.5, abs=0.5)


def test_an_empty_allowlist_makes_a_budget_a_no_op():
    lm = lim(tick_wait_budget_s=5.0)
    lm.tick_begin()
    for _ in range(50):
        lm.before(write=False, purpose="settlement")
    assert lm.counts["deferred"] == 0
    assert lm.counts["requests"] == 50


def test_a_zero_budget_makes_an_allowlist_a_no_op():
    lm = lim(deferrable_purposes=["settlement"])
    lm.tick_begin()
    for _ in range(50):
        lm.before(write=False, purpose="settlement")
    assert lm.counts["deferred"] == 0
    assert lm.counts["requests"] == 50


# ------------------------------------------------------------- the accounting

def test_every_purpose_is_counted_separately():
    lm = lim()
    lm.tick_begin()
    for p in ("account", "reconcile", "reconcile", "settlement"):
        lm.before(write=False, purpose=p)
    lm.before(write=True, purpose="order_submit")
    bp = lm.metrics()["by_purpose"]
    assert bp["reconcile"]["demanded"] == 2
    assert bp["account"]["requests"] == 1
    assert bp["settlement"]["requests"] == 1
    assert bp["order_submit"]["requests"] == 1
    assert sum(v["requests"] for v in bp.values()) == lm.counts["requests"]


def test_wait_seconds_are_attributed_to_the_purpose_that_waited():
    lm = lim()
    lm.tick_begin()
    for _ in range(4):                     # drains the burst, no waiting
        lm.before(write=False, purpose="account")
    for _ in range(3):
        lm.before(write=False, purpose="settlement")
    bp = lm.metrics()["by_purpose"]
    assert bp["account"]["waits"] == 0
    assert bp["account"]["wait_s"] == 0.0
    assert bp["settlement"]["waits"] == 3
    assert bp["settlement"]["wait_s"] > 0.0


def test_a_write_still_never_waits():
    """Unchanged: a bounded maker order must not queue behind a poll."""
    lm = lim()
    lm.tick_begin()
    for _ in range(10):
        lm.before(write=False, purpose="settlement")
    before = len(lm._slept)
    lm.before(write=True, purpose="order_submit")
    assert len(lm._slept) == before
    assert lm.metrics()["by_purpose"]["order_submit"]["waits"] == 0


# --------------------------------------------------------- the budget, when on

def armed():
    return lim(tick_wait_budget_s=4.0, deferrable_purposes=["settlement"])


def test_the_budget_defers_an_allowlisted_purpose_once_it_is_spent():
    lm = armed()
    lm.tick_begin()
    served = 0
    deferred = 0
    for _ in range(30):
        try:
            lm.before(write=False, purpose="settlement")
            served += 1
        except T.VenueThrottled:
            deferred += 1
    assert deferred > 0
    assert served + deferred == 30
    assert lm.tick_wait_s <= 4.0 + 1e-9
    assert lm.metrics()["by_purpose"]["settlement"]["deferred"] == deferred


def test_the_budget_never_touches_a_purpose_off_the_allowlist():
    """reconcile and order_progress mark the book UNCERTAIN on failure."""
    lm = armed()
    lm.tick_begin()
    for p in ("reconcile", "order_progress"):
        for _ in range(20):
            lm.before(write=False, purpose=p)          # must not raise
    assert lm.counts["deferred"] == 0
    assert lm.counts["requests"] == 40


def test_a_deferral_raises_the_same_exception_the_cooloff_already_raises():
    """So every caller's existing handling applies unchanged."""
    lm = armed()
    lm.tick_begin()
    with pytest.raises(T.VenueThrottled):
        for _ in range(40):
            lm.before(write=False, purpose="settlement")


def test_tick_begin_resets_the_budget_so_a_deferral_is_not_a_skip():
    lm = armed()
    lm.tick_begin()
    with pytest.raises(T.VenueThrottled):
        for _ in range(40):
            lm.before(write=False, purpose="settlement")
    lm.tick_begin()                         # next pass
    assert lm.tick_wait_s == 0.0
    lm.before(write=False, purpose="settlement")   # served again


def test_a_write_is_never_deferred_even_when_allowlisted():
    lm = lim(tick_wait_budget_s=0.01,
             deferrable_purposes=["order_submit", "settlement"])
    lm.tick_begin()
    for _ in range(10):
        try:
            lm.before(write=False, purpose="settlement")
        except T.VenueThrottled:
            pass                      # the reads may defer; the write may not
    lm.before(write=True, purpose="order_submit")   # must not raise
    assert lm.metrics()["by_purpose"]["order_submit"]["requests"] == 1
    assert lm.metrics()["by_purpose"]["order_submit"]["deferred"] == 0


def test_cooloff_still_wins_over_the_budget_and_is_counted_apart():
    lm = armed()
    lm.on_429()
    lm.tick_begin()
    with pytest.raises(T.VenueThrottled):
        lm.before(write=False, purpose="reconcile")
    assert lm.metrics()["by_purpose"]["reconcile"]["refused_in_cooloff"] == 1
    assert lm.counts["deferred"] == 0


# ----------------------------------------------- the route classifier itself

@pytest.mark.parametrize("method,path,want", [
    ("GET", "/v1/account/balances", "account"),
    ("GET", "/v1/markets/cpc-btc-updown-15m-2026-10-27-1515z/bbo",
     "market_data"),
    ("GET", "/v1/markets/cpc-btc-updown-15m-2026-10-27-1515z/book",
     "market_data"),
    ("GET", "/v1/portfolio/positions", "reconcile_positions"),
    # longest prefix wins, so this is NOT order_progress
    ("GET", "/v1/orders/open", "reconcile_orders"),
    ("GET", "/v1/order/CQ5NPBADMYC3", "order_progress"),
    ("GET", "/v1/orders?limit=50&cursor=abc", "other"),
    ("POST", "/v1/order/preview", "order_preview"),
    ("POST", "/v1/orders", "order_submit"),
    ("POST", "/v1/order/CQ5NPBADMYC3/cancel", "order_cancel"),
    ("GET", "/v1/something/unmapped", "other"),
])
def test_the_route_classifier(method, path, want):
    assert T.purpose_for(method, path) == want


def test_the_classifier_never_raises_on_junk():
    for m, p in ((None, None), ("", ""), ("get", "/v1/orders"), (1, 2)):
        assert isinstance(T.purpose_for(m, p), str)


def test_the_call_sites_carry_no_accounting_argument():
    """The reason the classifier exists.

    Tagging each caller broke five tests that assert on the source text of
    those call sites, including one that requires the literal
    `_get("/v1/account/balances")` inside `account()`. Accounting must not
    appear in the money path's call sites, so this pins that it does not.
    """
    import inspect
    import re
    src = inspect.getsource(T.PmusTransport)
    # the literal that test_the_read_is_a_plain_get_on_the_balances_route
    # reads out of account()'s body, unchanged
    assert '_get("/v1/account/balances")' in src
    # the purpose is computed in exactly ONE place: the single door
    assert src.count("purpose=") == 1
    assert "purpose=purpose_for(method, path)" in src
    # and no _get/_request CALL passes one
    for call in re.findall(r"_(?:get|request)\((?:[^()]|\([^()]*\))*\)", src):
        assert "purpose" not in call, call


# ------------------------------------------------------- the measured effect

def test_budget_bounds_the_tick_while_the_default_does_not():
    """The one number this change is for, on a fake clock.

    120 settlement reads queued on a single tick -- the order of magnitude
    measured at a real window roll. Without a budget the tick sleeps for
    all of them; with one it returns after the budget and the rest come
    back on the next pass.
    """
    plain = lim()
    plain.tick_begin()
    for _ in range(120):
        plain.before(write=False, purpose="settlement")

    bounded = lim(tick_wait_budget_s=4.0, deferrable_purposes=["settlement"])
    bounded.tick_begin()
    for _ in range(120):
        try:
            bounded.before(write=False, purpose="settlement")
        except T.VenueThrottled:
            pass

    assert plain.tick_wait_s > 75.0            # ~77s, the measured shape
    assert bounded.tick_wait_s <= 4.0 + 1e-9
    # nothing was lost: what was not served was counted as deferred
    assert (bounded.counts["requests"] + bounded.counts["deferred"]) == 120
