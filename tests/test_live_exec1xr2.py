"""LIVE-L5-EXEC-1-XR2: the TTE invariant, and the wait that cannot wander.

The invariant under test, once:

    NO REAL ORDER SUBMISSION MAY OCCUR WHEN TTE <= 120.0 SECONDS.

The interesting cases are races: eligible at the start, ineligible by the
POST. They are driven with a controllable clock, because that gap is a few
hundred milliseconds in production and cannot be hit by waiting.
"""
from __future__ import annotations

import time
from decimal import Decimal as D

import pytest

from analysis.l5.live_exec2 import latch as L
from analysis.l5.live_exec2 import orders as O
from analysis.l5.live_exec2 import sides as S
from analysis.l5.live_exec2 import spec as XSPEC
from analysis.l5.live_exec2 import timing as T
from analysis.l5.live_exec2.runner_spec import FINGERPRINT_PIN_ENV
from analysis.l5.live_exec3 import runner as RUN
from analysis.l5.live_exec3 import spec as X

SLUG = "cpc-btc-updown-15m-2026-10-06-1700z"
SID = "xr2-test-session"
ARM = ("PMUS_EXEC1_LIVE", "PMUS_EXEC1_SESSION_ID", "PMUS_EXEC1_ARM",
       "PMUS_EXEC1_GATE_EVIDENCE")


class FakeBook:
    def __init__(self, bid="0.50", ask="0.51", age=0.1, two_sided=True):
        self.bid, self.ask, self.age, self.two_sided = bid, ask, age, two_sided

    def snapshot(self, slug):
        if not self.two_sided:
            return None
        return {"bid": self.bid, "ask": self.ask, "bid_shares": "120",
                "ask_shares": "95", "stream_age_s": self.age}

    def stop(self):
        pass


class FakeVenue(O.BoundedOrderTransport):
    def __init__(self, *, order_id="ord-1", cum="0", role="maker"):
        super().__init__()
        self.order_id, self.cum, self.role = order_id, cum, role
        self.posts = []

    def request(self, method, path, body=None, base=None):
        if not self._allowed(str(method).upper(), path):
            raise O.MutationRefused("%s %s refused" % (method, path))
        if method == "GET":
            return {"order": {"state": "ORDER_STATE_OPEN",
                              "cumQuantity": self.cum,
                              "leavesQuantity": "0.01"}}
        self.posts.append((method, path, body))
        if path == O.PREVIEW_PATH:
            o = dict(body)
            o.update({"state": "ORDER_STATE_NEW", "cumQuantity": "0"})
            return {"order": o}
        if path == O.SUBMIT_PATH:
            self._submits += 1
            if self._submits > 1:
                raise O.MutationRefused("second POST refused")
            return {"id": self.order_id}
        return {"ok": True}

    def submits(self):
        return [p for _, p, _ in self.posts].count(O.SUBMIT_PATH)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(XSPEC, "LATCH_DIR", str(tmp_path / "x"))
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "x"))
    for k in ARM + (FINGERPRINT_PIN_ENV,):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(RUN, "read_venue",
                        lambda http, slug: {"minimumTradeQty": "0.01",
                                            "tick": "0.01", "slug": slug})
    yield


def _arm(monkeypatch, *, pin=True, slug=SLUG, sid=SID):
    monkeypatch.setenv("PMUS_EXEC1_LIVE", "YES")
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", sid)
    monkeypatch.setenv("PMUS_EXEC1_ARM", sid)
    monkeypatch.setenv("PMUS_EXEC1_GATE_EVIDENCE", slug)
    if pin:
        monkeypatch.setenv(FINGERPRINT_PIN_ENV, X.XR2_FINGERPRINT)


class Clock:
    """A clock whose TTE follows a scripted sequence of readings."""

    def __init__(self, ttes):
        self.ttes = list(ttes)
        self.window_start = 1_000_000.0
        self.i = 0

    def now(self):
        tte = self.ttes[min(self.i, len(self.ttes) - 1)]
        self.i += 1
        return self.window_start + T.WINDOW_LEN_S - tte


def _run_with(monkeypatch, ttes, **kw):
    """Run the experiment with the Nth clock reading giving ttes[N]."""
    c = Clock(ttes)
    monkeypatch.setattr(RUN.AB, "slug_start", lambda s: c.window_start)
    monkeypatch.setattr(RUN, "_now", c.now)
    monkeypatch.setattr(RUN.T, "tte_s",
                        lambda ws, now: None if ws is None
                        else (float(ws) + T.WINDOW_LEN_S) - now)
    kw.setdefault("transport", FakeVenue())
    kw.setdefault("book", FakeBook())
    kw.setdefault("observe_s", 0.0)   # the scripted clock never advances
    kw.setdefault("book_wait_s", 3.0)
    return c, RUN.run_experiment(**kw)


# ------------------------------------------------------------ frozen identity

def test_fingerprint_literal():
    assert X.fingerprint() == X.XR2_FINGERPRINT
    assert X.XR2_FINGERPRINT == (
        "0f3340195d68a5faf42dca97450e88eef3e7964ce4ca794c6e00baed54c9042f")


def test_assert_frozen_not_vacuous(monkeypatch):
    monkeypatch.setitem(X.SPEC, "probe", "TAMPERED")
    with pytest.raises(SystemExit):
        X.assert_frozen()


def test_exactly_one_correction():
    assert X.SPEC["correction_count"] == 1
    r = X.diff_report()
    assert r["keys_removed"] == []
    assert r["max_possible_loss_usd_unchanged"] == "0.0099"


def test_max_loss_still_0_0099():
    assert X.SPEC["size"]["MAX_POSSIBLE_LOSS_USD"] == "0.0099"
    assert (D(X.SPEC["size"]["quantity_shares"])
            * D(X.SPEC["size"]["max_entry_price"])) == D("0.0099")


@pytest.mark.parametrize("dotted,want", X.XR2_INVARIANTS)
def test_every_invariant(dotted, want):
    assert XSPEC._get(X.SPEC, dotted) == want


@pytest.mark.parametrize("dotted", [
    "size.quantity_shares", "orders.maker_intended_post_only",
    "orders.taker_permitted", "book.staleness_bound_s", "book.bbo_source",
    "book.tick_source", "behaviour.pair_cost_reservation",
    "placement_rule.offset_ticks_from_touch", "side_rule.rule",
    "accounting.matched_pair_economics", "markouts.interpolation",
    "cancellation.may_submit_a_replacement", "deadman", "withdrawal",
    "evidence", "one_submission",
])
def test_untouched_behaviour_is_identical_to_xr(dotted):
    from analysis.l5.live_exec2.runner_spec import SPEC as XR_SPEC
    assert XSPEC._get(X.SPEC, dotted) == XSPEC._get(XR_SPEC, dotted)


# ============================ THE TTE BOUNDARY CASES =======================
# Clock readings consumed per run: 0 _await_window, 1 run-start TTE,
# 2 decision_t, 3 decision tte, 4 preflight now, ... the guard reads last.

def test_initial_121_then_pre_post_119_9_refuses(monkeypatch):
    """Eligible at the start, ineligible at the guard: no latch, no POST."""
    _arm(monkeypatch)
    c, rec = _run_with(monkeypatch, [121.0] * 8 + [119.9] * 5)
    assert rec["orders_submitted"] == 0
    assert rec["stopped_at"] == "TTE_RECHECK_REFUSED"
    assert any("TTE_RECHECK_REFUSED" in r for r in rec["refusals"])
    assert rec["latch_after_refusal"]["spent"] is False   # latch protected
    assert rec["deadman"]["may_create_an_order"] is False


def test_initial_121_then_pre_post_exactly_120_refuses(monkeypatch):
    """At exactly the boundary. `<=` means refuse, not allow."""
    _arm(monkeypatch)
    c, rec = _run_with(monkeypatch, [121.0] * 8 + [120.0] * 5)
    assert rec["orders_submitted"] == 0
    assert rec["stopped_at"] == "TTE_RECHECK_REFUSED"
    assert rec["latch_after_refusal"]["spent"] is False


def test_initial_121_then_pre_post_above_120_is_eligible(monkeypatch):
    _arm(monkeypatch)
    v = FakeVenue()
    c, rec = _run_with(monkeypatch, [121.0] * 8 + [120.5] * 5, transport=v)
    assert rec["orders_submitted"] == 1
    assert v.submits() == 1
    assert L.state()["spent"] is True
    assert {ch["when"] for ch in rec["tte_checks"]} == {
        "RUN_START", "PRE_LATCH_AND_PRE_POST"}


def test_no_post_when_the_guard_refuses(monkeypatch):
    """The preview may have gone out; the SUBMIT must not have."""
    _arm(monkeypatch)
    v = FakeVenue()
    _run_with(monkeypatch, [121.0] * 8 + [100.0] * 5, transport=v)
    assert v.submits() == 0
    assert O.SUBMIT_PATH not in [p for _, p, _ in v.posts]


def test_unknown_tte_at_the_guard_refuses(monkeypatch):
    _arm(monkeypatch)
    v = FakeVenue()
    monkeypatch.setattr(RUN.AB, "slug_start", lambda s: 1_000_000.0)
    c = Clock([121.0] * 20)
    monkeypatch.setattr(RUN, "_now", c.now)
    calls = {"n": 0}

    def flaky_tte(ws, now):
        calls["n"] += 1
        if calls["n"] > 3:        # the clock becomes unreadable mid-run
            return None
        return (float(ws) + T.WINDOW_LEN_S) - now

    monkeypatch.setattr(RUN.T, "tte_s", flaky_tte)
    rec = RUN.run_experiment(transport=v, book=FakeBook(), observe_s=0.0,
                             book_wait_s=3.0)
    assert rec["orders_submitted"] == 0
    assert v.submits() == 0
    assert L.state()["spent"] is False


def test_delay_between_preview_and_submission_cannot_submit(monkeypatch):
    """A slow preview round trip pushes the run over the boundary."""
    _arm(monkeypatch)
    real = O.G.require_echo
    v = FakeVenue()

    state = {"ttes": [121.0] * 8}
    c = Clock(state["ttes"] + [121.0])
    monkeypatch.setattr(RUN.AB, "slug_start", lambda s: c.window_start)
    monkeypatch.setattr(RUN, "_now", c.now)
    monkeypatch.setattr(RUN.T, "tte_s",
                        lambda ws, now: None if ws is None
                        else (float(ws) + T.WINDOW_LEN_S) - now)

    def slow_echo(sent, resp):
        out = real(sent, resp)
        c.ttes.extend([5.0] * 10)    # time jumps past the boundary
        c.i = len(c.ttes) - 10
        return out

    monkeypatch.setattr(O.G, "require_echo", slow_echo)
    rec = RUN.run_experiment(transport=v, book=FakeBook(), observe_s=0.0,
                             book_wait_s=3.0)
    assert rec["orders_submitted"] == 0
    assert rec["stopped_at"] == "TTE_RECHECK_REFUSED"
    assert v.submits() == 0
    assert O.PREVIEW_PATH in [p for _, p, _ in v.posts]   # preview did happen
    assert L.state()["spent"] is False


def test_guard_runs_twice_per_submission(monkeypatch):
    """Once before the latch, once before the POST."""
    calls = []

    def guard():
        calls.append(len(calls))
        return {"ok": True}

    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", SID)
    v = FakeVenue()
    O.submit_one(v, slug=SLUG, outcome_side=S.UP, price="0.50",
                 checks={"ok": True}, guard=guard)
    assert len(calls) == 2
    assert v.submits() == 1


def test_a_guard_refusal_after_the_latch_sends_nothing(monkeypatch):
    """The second check fires. The latch is spent and NO order goes out --
    the documented safe direction."""
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", SID)
    seq = iter([{"ok": True}, {"ok": False, "reason": "TTE 119.9s"}])
    v = FakeVenue()
    with pytest.raises(O.GuardRefused) as exc:
        O.submit_one(v, slug=SLUG, outcome_side=S.UP, price="0.50",
                     checks={"ok": True}, guard=lambda: next(seq))
    assert "after latch consumption" in str(exc.value)
    assert v.submits() == 0
    assert L.state()["spent"] is True


def test_initial_check_still_refuses_on_its_own(monkeypatch):
    """Defence in depth: check 1 was not removed."""
    _arm(monkeypatch)
    v = FakeVenue()
    c, rec = _run_with(monkeypatch, [900.0, 71.9] + [71.9] * 20, transport=v)
    assert rec["stopped_at"] == "TIMING"
    assert rec["tte_checks"][0]["when"] == "RUN_START"
    assert v.posts == []


# ====================== THE PRE-OPEN WAIT, AND ITS LIMITS ==================

def test_waits_for_a_future_window_then_proceeds():
    slept = []
    t = {"v": 1_000_000.0 - 30.0}

    def now():
        return t["v"]

    def sleep(s):
        slept.append(s)
        t["v"] += 10.0

    w = RUN._await_window(SLUG, 1_000_000.0, sleep=sleep, now=now)
    assert w["opened"] is True
    assert w["polls"] >= 1 and slept
    assert w["tte_at_open_s"] <= T.WINDOW_LEN_S
    assert w["slug_waited_for"] == SLUG


def test_already_open_window_waits_not_at_all():
    w = RUN._await_window(SLUG, time.time() - 100.0)
    assert w["opened"] is True and w["polls"] == 0 and w["waited_s"] == 0.0


def test_wait_bound_exhausts_into_a_skip():
    t = {"v": 0.0}
    w = RUN._await_window(SLUG, 10_000_000.0, max_wait_s=30.0,
                          sleep=lambda s: t.__setitem__("v", t["v"] + s),
                          now=lambda: t["v"])
    assert w["opened"] is False
    assert "PREOPEN_WAIT_EXHAUSTED" in w["why"]
    assert "never the next one" in w["why"]


def test_unreadable_window_start_does_not_wait():
    w = RUN._await_window(SLUG, None)
    assert w["opened"] is False
    assert "WINDOW_START_UNREADABLE" in w["why"]


def test_empty_gate_evidence_refuses_rather_than_substituting(monkeypatch):
    monkeypatch.delenv("PMUS_EXEC1_GATE_EVIDENCE", raising=False)
    v = FakeVenue()
    rec = RUN.run_experiment(transport=v, book=FakeBook(), observe_s=0.0,
                             book_wait_s=2.0)
    assert rec["stopped_at"] == "NO_FROZEN_SLUG"
    assert "substitution" in rec["market"]["why"]
    assert v.posts == []


def test_the_runner_has_no_market_discovery():
    """There is no code path that can name a market other than the frozen
    one: no slug_for, no wanted_slugs, no next-window arithmetic."""
    import pathlib
    src = pathlib.Path(RUN.__file__).read_text()
    for forbidden in ("slug_for(", "wanted_slugs", "window_start()",
                      "+ 900", "+ WINDOW_LEN"):
        assert forbidden not in src, forbidden
    assert 'os.getenv("PMUS_EXEC1_GATE_EVIDENCE"' in src


def test_missed_window_is_a_skip_not_the_next_one(monkeypatch):
    """A slug whose window has closed refuses, and the record names only
    that slug."""
    _arm(monkeypatch)
    v = FakeVenue()
    monkeypatch.setattr(RUN.AB, "slug_start",
                        lambda s: time.time() - 10_000.0)
    rec = RUN.run_experiment(transport=v, book=FakeBook(), observe_s=0.0,
                             book_wait_s=2.0)
    assert rec["orders_submitted"] == 0
    assert rec["market"]["slug"] == SLUG
    assert v.posts == []


# ------------------------------------------------- disarmed, on the real path

def test_disarmed_reaches_the_final_gate_and_shows_the_recheck(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_GATE_EVIDENCE", SLUG)
    v = FakeVenue()
    c, rec = _run_with(monkeypatch, [500.0] * 40, transport=v)
    assert rec["orders_submitted"] == 0
    assert rec["stopped_at"] == "FINAL_PRE_POST_REFUSAL"
    assert rec["reached_final_gate"] is True
    assert "DISARMED" in rec["refusals"]
    # the disarmed run demonstrates checks 2 and 3 rather than asserting them
    assert rec["tte_recheck_would_say"]["ok"] is True
    assert v.posts == []


def test_disarmed_never_previews_or_signs(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_GATE_EVIDENCE", SLUG)
    v = FakeVenue()
    _run_with(monkeypatch, [500.0] * 40, transport=v)
    assert v.posts == []


def test_armed_but_unpinned_refuses(monkeypatch):
    _arm(monkeypatch, pin=False)
    v = FakeVenue()
    c, rec = _run_with(monkeypatch, [500.0] * 40, transport=v)
    assert "FINGERPRINT_NOT_PINNED_BY_OWNER" in rec["refusals"]
    assert v.posts == []


@pytest.mark.parametrize("ns", ["PMUS_Q1_", "PMUS_MICRO_", "PMUS_CANARY_",
                                "PMUS_CASE1_", "PMUS_SCHEMA_PROBE_"])
def test_foreign_namespaces_cannot_arm_it(monkeypatch, ns):
    monkeypatch.setenv("PMUS_EXEC1_GATE_EVIDENCE", SLUG)
    for suffix in ("LIVE", "SESSION_ID", "ARM", "GATE_EVIDENCE"):
        monkeypatch.setenv(ns + suffix, "YES" if suffix == "LIVE" else "x")
    monkeypatch.setenv(FINGERPRINT_PIN_ENV, X.XR2_FINGERPRINT)
    v = FakeVenue()
    c, rec = _run_with(monkeypatch, [500.0] * 40, transport=v)
    assert rec["orders_submitted"] == 0
    assert "DISARMED" in rec["refusals"]
    assert v.posts == []


def test_side_is_unchanged_from_xr(monkeypatch):
    assert S.side_for(SLUG, SID) == S.side_for(SLUG, SID)
    assert X.SPEC["side_rule"]["rule"] == S.RULE


def test_one_submission_still_bounded(monkeypatch):
    _arm(monkeypatch)
    c, rec = _run_with(monkeypatch, [500.0] * 40)
    assert rec["orders_submitted"] == 1
    c2, second = _run_with(monkeypatch, [500.0] * 40, transport=FakeVenue())
    assert second["orders_submitted"] == 0
    assert any("LATCH" in r for r in second["refusals"])
