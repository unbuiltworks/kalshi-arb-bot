"""The executor's authenticated pricing boundary: reachable, and still
fail-closed when nothing is supplied.

`pmus_q1_executor.attach_ws_feed` existed since the module was written and
was called from NOWHERE, so `fresh_bbo` always saw `WS_FEED is None`. An
armed `run_campaign` therefore halted with `WS_PRICING_REQUIRED` before it
could place anything: safe, but the authenticated path was unreachable.
These tests pin both halves of the repair -- the path now works when a feed
or state is passed in, and the old refusal is byte-for-byte unchanged when
one is not.
"""
from __future__ import annotations

import base64
from decimal import Decimal as D

import pytest

from src import pmus_q1_executor as ex
from src import pmus_ws_live_state as WS


SLUG = "aec-mlb-bal-col-2026-09-02"
SECRET = base64.b64encode(b"\x07" * 32).decode()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for var in (ex.LIVE_ENV, ex.SID_ENV, ex.ARM_ENV, ex.MARKET_ENV,
                ex.GATE_ENV, ex.KEY_ENV, ex.SECRET_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(ex.STATE_ENV, str(tmp_path / "q1"))
    monkeypatch.setattr(ex, "WS_FEED", None, raising=False)
    return monkeypatch


def arm(monkeypatch, sid="boundary-01"):
    monkeypatch.setenv(ex.LIVE_ENV, "YES")
    monkeypatch.setenv(ex.SID_ENV, sid)
    monkeypatch.setenv(ex.ARM_ENV, sid)
    monkeypatch.setenv(ex.KEY_ENV, "kid-test-abc")
    monkeypatch.setenv(ex.SECRET_ENV, SECRET)
    monkeypatch.setenv(ex.MARKET_ENV, SLUG)
    monkeypatch.setenv(ex.GATE_ENV, SLUG)


class _T:
    """Minimal transport. Serves nothing but a flat, funded account, so the
    only thing under test is where the PRICE came from."""

    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None, base=ex.API):
        self.calls.append((method, path, body))
        if path == "/v1/account/balances":
            return {"buyingPower": {"value": "39.50"}}
        if path == "/v1/portfolio/positions":
            return {"positions": []}
        if path == "/v1/orders/open":
            return {"orders": []}
        if path.endswith("/bbo"):
            # DELIBERATELY out of band. If a report prices from here the
            # gateway was read, which an armed run must never do.
            return {"marketData": {"bestBid": {"value": "0.9900"},
                                   "bestAsk": {"value": "0.9950"}}}
        return {}


class _FakeState:
    """A state-shaped object that is NOT a `WS.LiveState`, to pin that the
    readiness wait is skipped for it while `snapshot` is still the only
    source of a price."""

    def __init__(self, bid, ask, age_s=0.0):
        self._b, self._a, self._age = bid, ask, age_s
        self.asked = []

    def snapshot(self, slug, now=None, max_age_s=None):
        self.asked.append((slug, max_age_s))
        if self._age and max_age_s is not None and self._age > max_age_s:
            raise WS.StaleFeed("BOOK_TOO_OLD: %ss" % self._age)
        return {"bid": self._b, "ask": self._a,
                "book_age_s": self._age, "book_updates": 7}


# ---------- the old refusal is unchanged when nothing is supplied ----------

def test_armed_run_with_no_feed_still_refuses_in_fresh_bbo(env):
    """No new early return. The refusal keeps its old name and its old
    position -- AFTER preflight, so preflight's own findings still win."""
    arm(env)
    with pytest.raises(ex.Halt) as e:
        ex.fresh_bbo(_T(), SLUG)
    assert "WS_PRICING_REQUIRED" in str(e.value)


def test_unarmed_diagnostic_may_still_read_the_gateway(env):
    """Unchanged: only an unarmed run gets the keyless quote."""
    bbo = ex.fresh_bbo(_T(), SLUG)
    assert bbo["source"] == "public_gateway"


# ---------------- the boundary is now reachable ----------------

def test_supplied_state_is_attached_and_is_the_only_price_source(env):
    arm(env)
    t = _T()
    st = _FakeState("0.4500", "0.4550")
    rep = ex.run_campaign(t, clock=lambda: 0.0, sleep=lambda s: None,
                          state=st)
    # the WS book was asked for, and the out-of-band gateway quote never
    # reached `entry_ok` -- 0.99 would have been ENTRY_OUT_OF_BAND.
    assert st.asked and st.asked[0][0] == SLUG
    assert rep["status"] != "ENTRY_OUT_OF_BAND"
    assert ex.WS_FEED is not None
    assert ex.WS_FEED.bbo(SLUG)["source"] == "authenticated_ws"


def test_a_feed_object_is_accepted_by_its_state_attribute(env):
    arm(env)

    class _Feed:
        state = _FakeState("0.4500", "0.4550")

    ex.run_campaign(_T(), clock=lambda: 0.0, sleep=lambda s: None,
                    feed=_Feed())
    assert ex.WS_FEED.bbo(SLUG)["source"] == "authenticated_ws"


def test_one_sided_ws_book_is_not_a_price(env):
    arm(env)
    ex.attach_ws_feed(ex.StateBackedFeed(_FakeState("0.4500", None), 5.0))
    with pytest.raises(ex.Halt) as e:
        ex.fresh_bbo(_T(), SLUG)
    assert "ONE_SIDED_WS_BOOK" in str(e.value)


def test_stale_ws_book_is_not_a_price(env):
    arm(env)
    ex.attach_ws_feed(ex.StateBackedFeed(_FakeState("0.45", "0.46",
                                                    age_s=99.0), 5.0))
    with pytest.raises(ex.Halt) as e:
        ex.fresh_bbo(_T(), SLUG)
    assert "BOOK_TOO_OLD" in str(e.value)


def test_readiness_wait_runs_only_for_a_real_livestate(env, monkeypatch):
    arm(env)
    seen = []
    monkeypatch.setattr(WS, "wait_ready",
                        lambda *a, **k: seen.append(a) or {})
    ex.run_campaign(_T(), clock=lambda: 0.0, sleep=lambda s: None,
                    state=_FakeState("0.45", "0.46"))
    assert seen == []                     # not a LiveState -> no wait
    ex.run_campaign(_T(), clock=lambda: 0.0, sleep=lambda s: None,
                    state=WS.LiveState())
    assert len(seen) == 1                 # a LiveState -> waited


def test_ws_staleness_refusal_is_its_own_status_not_the_race_guard(env,
                                                                   monkeypatch
                                                                   ):
    """`STALE_PRICE_REFUSED` is the post-only race guard and keeps its
    meaning; a stale authenticated book is `WS_STALE_REFUSED`."""
    arm(env)

    def _raise(*a, **k):
        raise WS.StaleFeed("NO_BOOK_YET")

    monkeypatch.setattr(WS, "wait_ready", _raise)
    rep = ex.run_campaign(_T(), clock=lambda: 0.0, sleep=lambda s: None,
                          state=WS.LiveState())
    assert rep["status"] == "WS_STALE_REFUSED"
    assert "NO_BOOK_YET" in rep["reason"]
    assert not any(m == "POST" and p == "/v1/orders"
                   for m, p, _ in _T().calls)


# ---------------- one implementation, not two ----------------

def test_the_canary_shares_the_executors_boundary_class():
    from src import pmus_q1_canary as C
    assert C._StateBackedFeed is ex.StateBackedFeed


def test_run_campaign_never_opens_a_socket_itself(env, monkeypatch):
    arm(env)

    def _boom(*a, **k):                                        # pragma: no cover
        raise AssertionError("run_campaign must not create a WsFeed")

    monkeypatch.setattr(WS, "WsFeed", _boom)
    ex.run_campaign(_T(), clock=lambda: 0.0, sleep=lambda s: None)
