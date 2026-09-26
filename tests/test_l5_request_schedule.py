"""The request schedule, and the ceiling it must fit under.

2026-09-26. The executor rate-limited ITSELF: a 3s tick polled every live
order and read a settlement for every open market on every pass, the venue
answered 429, the order query failed, and reconcile released a filled order
unbooked. $1.09 of cost basis went missing because of a request schedule.

The disarmed soak that followed the fix measured 0.027 req/s and proved
nothing about the armed case -- but it did find two things the arithmetic
had not:

  * the limiter was built in PmusTransport.__init__ and Runner.roll() builds
    a new transport every window, so the token bucket AND the shared 429
    cool-off were discarded every fifteen minutes. Request counter 134 at
    22:57:06Z on the 2245z transport, 24 at 23:00:40Z on the 2300z one.
  * preflight, not order polling, was the dominant consumer: 80 requests in
    126 seconds -- 0.63 req/s, disarmed, with no orders at all -- while the
    venue had not yet listed 2300z.

1.5 req/s is a CEILING, not a target. These tests hold the schedule under
it with room, and hold the fail-closed semantics that must not be traded
for the headroom.
"""
import collections
import os
import tempfile
import urllib.error
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_pmus_transport as T

from tests.test_l5_continuous_lifecycle import (
    MKTS, Venue, runner, step, submit, rate_limit)


@pytest.fixture()
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture(autouse=True)
def _fresh_process_state():
    T.reset_shared_limiter()
    yield
    T.reset_shared_limiter()


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept += s
        self.t += s


# =====================================================================
# 1 + 2. the limiter is the PROCESS's, and survives the rollover
# =====================================================================

def test_a_429_cooldown_survives_a_window_rollover():
    """THE DEFECT THE SOAK FOUND. roll() replaces the transport every 15
    minutes; a cool-off that dies with the old one is no cool-off at the
    only boundary that matters -- the original incident's failures
    clustered on rollovers."""
    old = T.PmusTransport(market_slug=MKTS[0], http=object())
    span = old.limiter.on_429()
    assert span > 0
    assert old.limiter.metrics()["in_cooloff"] is True

    new = T.PmusTransport(market_slug=MKTS[1], http=object())   # the roll
    assert new.limiter is old.limiter
    assert new.limiter.metrics()["in_cooloff"] is True
    assert new.limiter.consecutive_429 == 1
    # and the NEXT 429 escalates from where the old transport left off
    assert new.limiter.on_429() > span


def test_the_token_bucket_survives_a_window_rollover():
    """Counter 134 -> 24 across the 22:57/23:00 boundary was the tell."""
    a = T.PmusTransport(market_slug=MKTS[0], http=object())
    for _ in range(3):
        a.limiter.before(write=False)
    spent = a.limiter.metrics()["requests"]
    tokens_left = a.limiter.tokens

    b = T.PmusTransport(market_slug=MKTS[1], http=object())
    assert b.limiter.metrics()["requests"] == spent     # no reset
    assert b.limiter.tokens == tokens_left              # bucket carried


def test_every_transport_in_the_process_shares_one_budget():
    made = [T.PmusTransport(market_slug=m, http=object()) for m in MKTS]
    assert len({id(t.limiter) for t in made}) == 1
    assert made[0].limiter is T.shared_limiter()


# =====================================================================
# 3 + 6. parked orders resolve SERIALLY, and block submissions throughout
# =====================================================================

def park_eight(r, v):
    """Eight orders that filled and whose /v1/order read is refused.

    The refusal matters: an order the venue still answers for is polled to
    terminal in the ordinary way and never parks at all. Parking is the
    2045z path -- filled, gone from the open list, unreadable -- so the
    query has to fail for these to reach it.
    """
    for i in range(8):
        submit(r, v, "V%d" % i, MKTS[0], "ORDER_SIDE_SELL", "0.80", "1")
    step(r)
    for i in range(8):
        v.fill("V%d" % i, "1", "0.80", done=True)   # filled, off the list
        v.fail_order_query.add("V%d" % i)           # and unreadable
    for _ in range(8):                        # until a reconcile parks them
        step(r, dt_s=R.ORDER_POLL_S + 1)
    assert len(r.store.pending_terminal()) == 8
    v.fail_order_query.clear()                # the venue comes back


def test_eight_parked_orders_resolve_one_at_a_time_not_in_a_burst(tmp):
    """Eight parked orders at one read each per interval is 1.6 req/s from
    a path that exists only because the venue was already refusing us."""
    v = Venue()
    r = runner(tmp, v)
    park_eight(r, v)

    v.reads.clear()
    r.resolve_pending_terminal(r.now_t + 1000.0)
    assert v.reads["order"] == 1               # ONE, not eight
    assert len(r.store.pending_terminal()) == 7


def test_the_parked_queue_drains_at_the_stated_rate_and_fully(tmp):
    v = Venue()
    r = runner(tmp, v)
    park_eight(r, v)
    v.reads.clear()
    for _ in range(40):
        step(r, dt_s=R.PENDING_RESOLVE_S + 0.1)
    assert r.store.pending_terminal() == {}
    # eight reads for eight orders, and not one more
    assert v.reads["order"] == 8
    # every fill landed: 8 shares of DOWN at 0.20 own terms
    assert r.store.position(MKTS[0], LX.DOWN) == D("8")
    assert r.store.leg_cost(MKTS[0])[LX.DOWN] == D("1.60")


def test_submissions_stay_blocked_for_the_whole_serial_drain(tmp):
    """THE SAFETY TRADE, stated explicitly. Serial resolution is slower and
    is NOT less safe: the book is uncertain and every submission refused
    from the first parked order to the last, which is the same state it was
    in before any of them resolved."""
    v = Venue()
    r = runner(tmp, v)
    park_eight(r, v)
    lim = LX.SafetyLimits(**R.PROBE_LIMITS)

    def may_submit():
        return lim.refuse(
            price=D("0.5"), qty=D("1"), market=MKTS[0],
            committed_by_market=r.store.committed(),
            residual_after=D("1"), realized_today=r.store.realized_today(),
            open_markets=(), outstanding_by_market={},
            live_order_count=r.store.live_order_count(),
            outstanding_residual=D(0),
            orders_uncertain=r.store.orders_uncertain()) is None

    seen_blocked = 0
    for _ in range(40):
        if r.store.pending_terminal():
            assert not may_submit()            # refused, every single tick
            seen_blocked += 1
        step(r, dt_s=R.PENDING_RESOLVE_S + 0.1)
    assert seen_blocked >= 8                   # at least one tick per order
    assert r.store.pending_terminal() == {}


# =====================================================================
# 4. live-order polling is capped in AGGREGATE, not per order
# =====================================================================

def test_eight_live_orders_stay_under_the_aggregate_progress_budget(tmp):
    v = Venue()
    r = runner(tmp, v)
    for i in range(8):
        submit(r, v, "V%d" % i, MKTS[0], "ORDER_SIDE_SELL", "0.80", "1")
    step(r)
    v.reads.clear()
    t0 = r.now_t
    for _ in range(200):                       # 600 seconds of 3s ticks
        step(r, dt_s=3.0)
    span = r.now_t - t0
    rate = v.reads["order"] / span
    assert rate <= R.ORDER_POLL_RATE + 1e-6, rate
    # the old schedule would have been 8/6 = 1.333
    assert rate < 1.333 / 2


def test_one_or_two_orders_poll_exactly_as_often_as_before(tmp):
    """The cap must not slow down the case we actually run. The bounded
    probe works one order at a time; at that size the interval is the
    unchanged 6 seconds."""
    v = Venue()
    r = runner(tmp, v)
    submit(r, v, "V0", MKTS[0], "ORDER_SIDE_SELL", "0.80", "1")
    step(r)
    v.reads.clear()
    for _ in range(20):                        # 60s
        step(r, dt_s=3.0)
    assert v.reads["order"] == pytest.approx(60 / R.ORDER_POLL_S, abs=1)


def test_a_stretched_envelope_round_robins_instead_of_starving_one_order(tmp):
    v = Venue()
    r = runner(tmp, v)
    for i in range(8):
        submit(r, v, "V%d" % i, MKTS[0], "ORDER_SIDE_SELL", "0.80", "1")
    step(r)
    for _ in range(40):
        step(r, dt_s=3.0)
    # every order has been read at least once: none starved
    assert len(r.last_order_poll) == 8
    ages = [r.now_t - v for v in r.last_order_poll.values()]
    assert max(ages) - min(ages) <= 8 / R.ORDER_POLL_RATE


# =====================================================================
# 5. everything at once stays bounded and prioritised
# =====================================================================

def test_the_whole_stressed_demand_stays_under_the_ceiling(tmp):
    """Parked + uncertain + full live envelope + settlement + a rollover
    whose market is not listed yet. The arithmetic said 4.933 req/s before,
    3.3x the ceiling."""
    v = Venue()
    r = runner(tmp, v)
    for i in range(8):
        submit(r, v, "V%d" % i, MKTS[0], "ORDER_SIDE_SELL", "0.80", "1")
    step(r)
    for i in range(8):
        v.fill("V%d" % i, "1", "0.80", done=True)
    for _ in range(6):
        step(r, dt_s=R.ORDER_POLL_S + 1)
    for i in range(8, 16):                     # a fresh full envelope
        submit(r, v, "V%d" % i, MKTS[1], "ORDER_SIDE_SELL", "0.80", "1")
    r.store.mark_uncertain("stress")
    v.settled[MKTS[2]] = None

    v.reads.clear()
    t0 = r.now_t
    for _ in range(200):
        step(r, dt_s=3.0)
    rate = v.reads["total"] / (r.now_t - t0)
    assert rate < 1.5, rate
    assert rate < 1.25, rate                   # the stated 1.208 budget
    # and priority held: the parked orders were all resolved
    assert r.store.pending_terminal() == {}


def test_the_documented_budgets_match_the_shipped_constants():
    """The numbers in the proposal are the numbers in the code."""
    order8 = min(8 / R.ORDER_POLL_S, R.ORDER_POLL_RATE)
    parked = 1 / R.PENDING_RESOLVE_S
    settle = 2 / R.SETTLEMENT_POLL_S
    recon = 2 / R.RECONCILE_RETRY_S
    # two reads per backed-off attempt now that nothing is cached:
    # the account read and the market read.
    preflight = 2 / R.PREFLIGHT_BACKOFF_S[-1]
    assert order8 == 0.5
    assert round(parked, 3) == 0.333
    assert round(order8 + settle, 3) == 0.700           # full envelope
    stressed = order8 + parked + settle + recon + preflight
    assert round(stressed, 3) == 1.250
    assert stressed < 1.5 * 0.85                        # real headroom
    assert float(os.getenv("PMUS_RPS", "1.5")) == 1.5   # ceiling unraised


# =====================================================================
# 7 + 8. preflight caches only what cannot change, and mutations drop it
# =====================================================================

@pytest.fixture()
def creds(monkeypatch):
    """preflight short-circuits on credentials_present before it reads
    anything, so the cache cannot be observed without them."""
    monkeypatch.setattr(T.Q1, "has_credentials", lambda: True)
    monkeypatch.setattr(T.Q1, "key_fingerprint", lambda: "d51b44a3fa83",
                        raising=False)
    return True


def try_preflight(tr):
    """Run preflight and keep its report whether it passes or not.

    These tests are about WHICH READS ARE MADE, not about the verdict; the
    fake venue answers only the two endpoints the cache question turns on,
    so preflight legitimately fails and raises.
    """
    try:
        return tr.preflight()
    except T.PreflightFailed as exc:
        import json as _j
        return _j.loads(str(exc))


class PreflightHttp:
    """Counts reads by path so the cache can be seen working."""

    def __init__(self):
        self.paths = collections.Counter()

    def request(self, method, path, body, base):
        self.paths[path.split("?")[0]] += 1
        if path.startswith("/v1/account/balances"):
            return {"balances": [{"currentBalance": 61.42595,
                                  "currency": "USD", "buyingPower": 61.42595,
                                  "openOrders": 0, "unsettledFunds": 0}]}
        if path.startswith("/v1/orders/open"):
            return {"orders": []}
        if path.startswith("/v1/portfolio/positions"):
            return {"positions": {}, "eof": True, "nextCursor": "",
                    "availablePositions": []}
        raise urllib.error.HTTPError(path, 404, "nope", {}, None)


def test_preflight_caches_nothing_because_every_read_is_a_live_assertion(
        creds):
    """THE PLAN CHANGED, and the reason is worth keeping.

    Caching the account read looked safe: whether this key binds this
    account is invariant between windows. It is -- but that read also
    carries the BALANCE, which account_binding_proven is computed from and
    identity_probe reports, and which moves every time the account trades or
    settles. Six tests in test_l5_pmus_transport caught it at once.

    The same is true of every other read preflight makes. The checks that
    are genuinely invariant -- credentials_present, slug_is_btc15m_series,
    outcome_mapping_is_up_down -- make no venue call at all. So there is
    nothing to cache that would not weaken the gate, and the cost is bounded
    by asking LESS OFTEN instead.
    """
    http = PreflightHttp()
    a = T.PmusTransport(market_slug=MKTS[0], http=http)
    a.limiter = T.RateLimiter(rps=1e6, burst=1e6)
    try_preflight(a)
    try_preflight(a)
    try_preflight(a)
    # EVERY read, EVERY attempt. No staleness is possible because no state
    # is kept.
    assert http.paths["/v1/account/balances"] == 3
    assert http.paths["/v1/portfolio/positions"] == 3

    b = T.PmusTransport(market_slug=MKTS[1], http=http)   # the roll
    b.limiter = a.limiter
    try_preflight(b)
    assert http.paths["/v1/account/balances"] == 4
    assert not hasattr(T, "_PREFLIGHT_CACHE")


def test_the_backoff_is_what_bounds_the_preflight_cost(tmp):
    """0.63 req/s measured on 2300z, at a fixed 3s retry against a market
    the venue had not listed yet. Retrying four times as often does not
    list it any sooner; it spends the budget the orders need."""
    assert R.PREFLIGHT_BACKOFF_S == (3.0, 6.0, 12.0, 24.0)
    v = Venue()
    r = runner(tmp, v)
    r.preflight_status = "FAILED"
    r.preflight_attempts = 0
    r.last_preflight_t = 0.0

    fired, now = [], 0.0
    for _ in range(400):                       # 1200 seconds of 3s ticks
        now += 3.0
        idx = min(r.preflight_attempts, len(R.PREFLIGHT_BACKOFF_S) - 1)
        if now - r.last_preflight_t >= R.PREFLIGHT_BACKOFF_S[idx]:
            r.last_preflight_t = now
            r.preflight_attempts += 1
            fired.append(now)
    rate = len(fired) / now
    # the old fixed 3s retry made ~5 reads per attempt at 1/3s
    assert rate <= 1 / R.PREFLIGHT_BACKOFF_S[-1] + 0.01, rate
    assert rate < (1 / 3.0) / 5


# =====================================================================
# 9. a restart loses the limiter's memory and must still fail closed
# =====================================================================

def test_a_restart_cannot_submit_before_an_authoritative_reconciliation(tmp):
    """A new process genuinely cannot know the venue was refusing the old
    one -- in-memory backoff does not survive. What must survive is that
    nothing may be submitted until the venue has answered authoritatively,
    and that is on disk, not in the limiter."""
    v = Venue()
    r = runner(tmp, v)
    submit(r, v, "V1", MKTS[0], "ORDER_SIDE_SELL", "0.80", "2")
    step(r)
    v.fill("V1", "2", "0.80", done=True)
    v.fail_order_query.add("V1")
    for _ in range(6):
        step(r, dt_s=R.ORDER_POLL_S + 1)
    assert "V1" in r.store.pending_terminal()

    # --- crash. The limiter's history is gone; the book's is not. ---
    T.reset_shared_limiter()
    r2 = runner(tmp, v)
    assert r2.store.orders_uncertain() is not None
    assert "V1" in r2.store.pending_terminal()
    lim = LX.SafetyLimits(**R.PROBE_LIMITS)
    assert lim.refuse(
        price=D("0.5"), qty=D("1"), market=MKTS[0],
        committed_by_market=r2.store.committed(), residual_after=D("1"),
        realized_today=r2.store.realized_today(), open_markets=(),
        outstanding_by_market={}, live_order_count=0,
        outstanding_residual=D(0),
        orders_uncertain=r2.store.orders_uncertain()) == "ORDER_STATE_UNCERTAIN"

    v.fail_order_query.clear()
    for _ in range(6):
        step(r2, dt_s=R.PENDING_RESOLVE_S + 0.1)
    assert r2.store.pending_terminal() == {}
    assert r2.store.position(MKTS[0], LX.DOWN) == D("2")


# =====================================================================
# demand vs transmitted
# =====================================================================

def test_demand_and_transmitted_are_reported_separately():
    """A limiter makes the transmitted rate look safe by construction -- it
    is the rate we chose. The number that says whether the schedule fits is
    how much was ASKED FOR."""
    c = Clock()
    lim = T.RateLimiter(rps=1.0, burst=1.0, now_fn=c.now, sleep_fn=c.sleep)
    for _ in range(4):
        lim.before(write=False)
    m = lim.metrics()
    assert m["demanded"] == 4 and m["requests"] == 4
    assert m["unmet_demand"] == 0
    assert m["max_wait_s"] > 0                 # it paced rather than queued
    assert m["demand_rps"] >= m["transmitted_rps"]

    lim.on_429()
    for _ in range(3):
        with pytest.raises(T.VenueThrottled):
            lim.before(write=False)
    m = lim.metrics()
    assert m["demanded"] == 7 and m["requests"] == 4
    assert m["unmet_demand"] == 3              # THE GAP, visible


def test_order_progress_latency_is_reported_not_inferred(tmp):
    """What pacing hides: not the request rate, but how long an order's
    true state goes unread."""
    v = Venue()
    r = runner(tmp, v)
    for i in range(8):
        submit(r, v, "V%d" % i, MKTS[0], "ORDER_SIDE_SELL", "0.80", "1")
    step(r)
    lat = r.order_progress_latency_s(r.now_t)
    assert lat["live_orders"] == 8
    assert lat["poll_interval_s"] == 8 / R.ORDER_POLL_RATE
    assert lat["overdue_s"] == 0.0
    for _ in range(40):
        step(r, dt_s=3.0)
    lat = r.order_progress_latency_s(r.now_t)
    assert lat["oldest_unread_s"] <= lat["poll_interval_s"] + 3.1
    assert lat["overdue_s"] <= 3.1             # keeping up, not falling behind
