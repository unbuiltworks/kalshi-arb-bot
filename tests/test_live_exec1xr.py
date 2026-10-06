"""LIVE-L5-EXEC-1-XR end-to-end: exactly one order is possible, and only one.

These tests drive the REAL runner with a fake venue and a fake book, because
the properties that matter are about what the runner does when the venue
misbehaves -- a timeout, an unreadable response, a rejection, a partial fill,
a restart -- and none of those can be arranged against the live venue.
"""
from __future__ import annotations

import time
from decimal import Decimal as D

import pytest

from analysis.l5.live_exec.spec import VENUE_MIN_QTY
from analysis.l5.live_exec2 import latch as L
from analysis.l5.live_exec2 import orders as O
from analysis.l5.live_exec2 import runner as RUN
from analysis.l5.live_exec2 import runner_spec as X
from analysis.l5.live_exec2 import sides as S
from analysis.l5.live_exec2 import spec as XSPEC
from analysis.l5.live_exec2 import timing as T

SLUG = "cpc-btc-updown-15m-2026-10-06-1500z"
SID = "xr-test-session"
ARM = ("PMUS_EXEC1_LIVE", "PMUS_EXEC1_SESSION_ID", "PMUS_EXEC1_ARM",
       "PMUS_EXEC1_GATE_EVIDENCE")


# --------------------------------------------------------------- the fakes

class FakeBook:
    def __init__(self, bid="0.50", ask="0.51", age=0.1, two_sided=True):
        self.bid, self.ask, self.age, self.two_sided = bid, ask, age, two_sided
        self.stopped = False

    def snapshot(self, slug):
        if not self.two_sided:
            return None
        return {"bid": self.bid, "ask": self.ask, "bid_shares": "120",
                "ask_shares": "95", "stream_age_s": self.age,
                "source": "authenticated_ws"}

    def stop(self):
        self.stopped = True


class FakeVenue(O.BoundedOrderTransport):
    """Records every mutation and lets a test choose how the POST behaves."""

    def __init__(self, *, submit="ok", order_id="ord-1", cum="0",
                 role="maker"):
        super().__init__()
        self.submit, self.order_id, self.cum = submit, order_id, cum
        self.role = role
        self.posts = []

    def request(self, method, path, body=None, base=None):
        if not self._allowed(str(method).upper(), path):
            raise O.MutationRefused("%s %s refused" % (method, path))
        if method == "GET":
            o = {"state": "ORDER_STATE_OPEN", "cumQuantity": self.cum,
                 "leavesQuantity": "0.01"}
            if D(self.cum) > 0 and self.role is not None:
                o["executions"] = [{"isAggressor": self.role == "taker",
                                    "commissionNotionalCollected": "-0.0001"}]
            elif D(self.cum) > 0:
                o["executions"] = [{"aggressor": False}]   # the WRONG field
            return {"order": o}
        self.posts.append((method, path, body))
        if path == O.PREVIEW_PATH:
            o = dict(body)
            o.update({"state": "ORDER_STATE_NEW", "cumQuantity": "0",
                      "quantity": body["quantity"]})
            return {"order": o}
        if path == O.SUBMIT_PATH:
            self._submits += 1
            if self._submits > 1:
                raise O.MutationRefused("second POST /v1/orders refused")
            if self.submit == "timeout":
                raise TimeoutError("venue did not answer")
            if self.submit == "reject":
                return {"error": "REJECTED", "id": ""}
            if self.submit == "unknown":
                return {"weird": True}
            return {"id": self.order_id}
        return {"ok": True}


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(XSPEC, "LATCH_DIR", str(tmp_path / "x"))
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "x"))
    for k in ARM + (X.FINGERPRINT_PIN_ENV,):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(RUN, "read_venue",
                        lambda http, slug: {"minimumTradeQty": "0.01",
                                            "tick": "0.01", "slug": slug})
    monkeypatch.setattr(RUN.AB, "slug_start", lambda s: time.time() - 60.0)
    monkeypatch.setattr(RUN.AB, "window_start", lambda *a, **k: 0)
    monkeypatch.setattr(RUN.AB, "slug_for", lambda ws: SLUG)
    yield


def _arm(monkeypatch, *, pin=True, slug=SLUG, sid=SID):
    monkeypatch.setenv("PMUS_EXEC1_LIVE", "YES")
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", sid)
    monkeypatch.setenv("PMUS_EXEC1_ARM", sid)
    monkeypatch.setenv("PMUS_EXEC1_GATE_EVIDENCE", slug)
    if pin:
        monkeypatch.setenv(X.FINGERPRINT_PIN_ENV, X.XR_FINGERPRINT)


def _run(**kw):
    kw.setdefault("transport", FakeVenue())
    kw.setdefault("book", FakeBook())
    kw.setdefault("observe_s", 1.0)
    kw.setdefault("book_wait_s", 3.0)
    kw.setdefault("explicit_slug", SLUG)
    return RUN.run_experiment(**kw)


# ------------------------------------------------------------ frozen identity

def test_fingerprint_literal():
    assert X.fingerprint() == X.XR_FINGERPRINT
    assert X.XR_FINGERPRINT == (
        "f3e54d8225db2d9bc626ddbc285e347186f2ca9511543a8ff9b86f87359bc5e9")


def test_assert_frozen_not_vacuous(monkeypatch):
    monkeypatch.setitem(X.SPEC, "probe", "TAMPERED")
    with pytest.raises(SystemExit):
        X.assert_frozen()


def test_max_loss_is_still_0_0099():
    assert X.SPEC["size"]["MAX_POSSIBLE_LOSS_USD"] == "0.0099"
    assert X.SPEC["size"]["quantity_shares"] == "0.01"
    assert (D(X.SPEC["size"]["quantity_shares"])
            * D(X.SPEC["size"]["max_entry_price"])) == D("0.0099")


@pytest.mark.parametrize("dotted,want", X.XR_INVARIANTS)
def test_every_invariant(dotted, want):
    assert XSPEC._get(X.SPEC, dotted) == want


def test_entrypoint_is_not_preflight_only():
    assert X.SPEC["entrypoint"]["is_preflight_only"] is False
    assert X.SPEC["entrypoint"]["module"] == "scripts.l5_exec1xr_run"
    assert X.SPEC["entrypoint"]["call_graph_to_submit"][-1].endswith(
        "POST /v1/orders")


# ------------------------------------------------------------ DISARMED runner

def test_disarmed_runner_reaches_final_gate_and_stops():
    v = FakeVenue()
    rec = _run(transport=v)
    assert rec["orders_submitted"] == 0
    assert rec["stopped_at"] == "FINAL_PRE_POST_REFUSAL"
    assert rec["reached_final_gate"] is True
    assert "DISARMED" in rec["refusals"]


def test_disarmed_runner_never_previews_or_posts():
    v = FakeVenue()
    _run(transport=v)
    assert v.posts == []


def test_disarmed_runner_does_not_consume_the_latch(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", SID)
    _run()
    assert L.state()["spent"] is False


def test_armed_but_unpinned_fingerprint_refuses(monkeypatch):
    _arm(monkeypatch, pin=False)
    v = FakeVenue()
    rec = _run(transport=v)
    assert rec["orders_submitted"] == 0
    assert "FINGERPRINT_NOT_PINNED_BY_OWNER" in rec["refusals"]
    assert v.posts == []


# --------------------------------------------------- exactly one submission

def test_exactly_one_submission_is_possible(monkeypatch):
    _arm(monkeypatch)
    v = FakeVenue()
    rec = _run(transport=v)
    assert rec["orders_submitted"] == 1
    assert rec["submission"]["order_id"] == "ord-1"
    assert [p for _, p, _ in v.posts].count(O.SUBMIT_PATH) == 1
    assert L.state()["spent"] is True


def test_second_run_cannot_submit_again(monkeypatch):
    _arm(monkeypatch)
    assert _run()["orders_submitted"] == 1
    second = _run(transport=FakeVenue())
    assert second["orders_submitted"] == 0
    assert any("LATCH" in r for r in second["refusals"])


def test_restart_cannot_submit_again(monkeypatch):
    """A fresh process -- reloaded modules, no in-memory state."""
    _arm(monkeypatch)
    assert _run()["orders_submitted"] == 1
    import importlib
    fresh_l = importlib.reload(L)
    monkeypatch.setattr(fresh_l, "LATCH_DIR", L.LATCH_DIR)
    try:
        assert fresh_l.state()["spent"] is True
        with pytest.raises(fresh_l.LatchSpent):
            fresh_l.consume(slug=SLUG, side="UP", price="0.5", qty="0.01")
    finally:
        importlib.reload(L)


def test_timeout_is_ambiguous_and_never_retried(monkeypatch):
    _arm(monkeypatch)
    v = FakeVenue(submit="timeout")
    rec = _run(transport=v)
    assert rec["stopped_at"] == "AMBIGUOUS_SUBMISSION"
    assert rec["ambiguous_submission"]["error"] == "TimeoutError"
    assert L.state()["spent"] is True          # spent, so no retry is possible
    assert [p for _, p, _ in v.posts].count(O.SUBMIT_PATH) == 1
    assert _run(transport=FakeVenue())["orders_submitted"] == 0


def test_ambiguous_submission_still_runs_the_deadman(monkeypatch):
    _arm(monkeypatch)
    v = FakeVenue(submit="timeout")
    rec = _run(transport=v)
    assert rec["deadman"]["cancel_all"]["ok"] is True
    assert "ambiguous" in rec["deadman"]["ran_because"]


def test_unknown_response_is_ambiguous_not_a_fill(monkeypatch):
    _arm(monkeypatch)
    rec = _run(transport=FakeVenue(submit="unknown"))
    assert rec["refusals"][-1] == "AMBIGUOUS_SUBMISSION_NO_ORDER_ID"
    assert rec.get("markouts") is None
    assert _run(transport=FakeVenue())["orders_submitted"] == 0


def test_rejection_is_not_retried(monkeypatch):
    _arm(monkeypatch)
    rec = _run(transport=FakeVenue(submit="reject"))
    assert rec["orders_submitted"] == 1        # one ATTEMPT was made
    assert "AMBIGUOUS_SUBMISSION_NO_ORDER_ID" in rec["refusals"]
    assert _run(transport=FakeVenue())["orders_submitted"] == 0


def test_partial_fill_does_not_replace(monkeypatch):
    _arm(monkeypatch)
    v = FakeVenue(cum="0.005")
    rec = _run(transport=v, observe_s=2.0)
    assert [p for _, p, _ in v.posts].count(O.SUBMIT_PATH) == 1
    assert D(rec["filled_qty"]) == D("0.005")
    assert rec["order_state"] == "PARTIALLY_FILLED"
    assert rec["account"]["n_orders"] == 1
    assert rec["venue_confirmed_liquidity_role"]["role"] == "maker"


def test_fill_without_isAggressor_leaves_the_role_unresolved(monkeypatch):
    """The venue reports a fill but not its liquidity role. The quantity is
    kept; the maker claim is not invented."""
    _arm(monkeypatch)
    rec = _run(transport=FakeVenue(cum="0.005", role=None), observe_s=2.0)
    assert D(rec["filled_qty"]) == D("0.005")
    assert rec["venue_confirmed_liquidity_role"]["role"] is None
    assert rec["account"]["venue_confirmed_liquidity_role"] == "UNRESOLVED"
    assert rec["unresolved_role_fills"][0]["qty"] == "0.005"


def test_role_never_comes_from_the_boolean_aggressor_field():
    from analysis.l5.live_exec2.runner import venue_role
    assert venue_role({"executions": [{"aggressor": False}]})["role"] is None
    assert venue_role({"executions": [{"isAggressor": False}]})["role"] \
        == "maker"
    assert venue_role({"executions": [{"isAggressor": True}]})["role"] \
        == "taker"


def test_cancellation_does_not_replace(monkeypatch):
    _arm(monkeypatch)
    v = FakeVenue()
    rec = _run(transport=v, observe_s=1.0)
    assert [p for _, p, _ in v.posts].count(O.SUBMIT_PATH) == 1
    assert any("cancel" in p for _, p, _ in v.posts)
    assert rec["deadman"]["may_create_an_order"] is False


def test_deadman_cannot_create_an_order():
    v = FakeVenue()
    O.deadman_cancel_all(v, slug=SLUG)
    assert [p for _, p, _ in v.posts] == [O.DEADMAN_PATH]
    assert O.SUBMIT_PATH not in [p for _, p, _ in v.posts]


def test_cancel_does_not_need_arming(monkeypatch):
    """No arming keys set at all, and cancel still works."""
    v = FakeVenue()
    assert O.cancel_exact(v, order_id="ord-1", slug=SLUG) == {"ok": True}


# ----------------------------------------------------------- the book gates

@pytest.mark.parametrize("book,stop", [
    (FakeBook(age=5.0), "BOOK"),
    (FakeBook(two_sided=False), "BOOK"),
])
def test_stale_or_one_sided_book_prevents_submission(monkeypatch, book, stop):
    _arm(monkeypatch)
    v = FakeVenue()
    rec = _run(transport=v, book=book)
    assert rec["orders_submitted"] == 0
    assert rec["stopped_at"] == stop
    assert v.posts == []
    assert L.state()["spent"] is False


def test_crossing_price_prevents_submission(monkeypatch):
    _arm(monkeypatch)
    v = FakeVenue()
    # a zero-width book: our same-side touch equals the opposite quote
    rec = _run(transport=v, book=FakeBook(bid="0.50", ask="0.50"))
    assert rec["orders_submitted"] == 0
    assert any("WOULD_CROSS" in r for r in rec["refusals"])
    assert v.posts == []


@pytest.mark.parametrize("venue_meta,reason", [
    ({"minimumTradeQty": "1", "tick": "0.01"}, "MIN_QTY_NOT_LESS_THAN_1"),
    ({"minimumTradeQty": None, "tick": "0.01"}, "MIN_QTY_ABSENT"),
    ({"minimumTradeQty": "0.01", "tick": None}, "TICK_ABSENT"),
    ({"minimumTradeQty": "0.01", "tick": "0.003"}, "PRICE_OFF_GRID"),
])
def test_wrong_qty_or_tick_prevents_submission(monkeypatch, venue_meta,
                                               reason):
    _arm(monkeypatch)
    monkeypatch.setattr(RUN, "read_venue",
                        lambda http, slug: dict(venue_meta, slug=slug))
    v = FakeVenue()
    rec = _run(transport=v)
    assert rec["orders_submitted"] == 0
    assert any(reason in r for r in rec["refusals"]), rec["refusals"]
    assert v.posts == []


def test_wrong_slug_gate_prevents_submission(monkeypatch):
    _arm(monkeypatch, slug="cpc-btc-updown-15m-2026-10-06-9999z")
    v = FakeVenue()
    rec = RUN.run_experiment(transport=v, book=FakeBook(), observe_s=1.0,
                             book_wait_s=3.0, explicit_slug=SLUG)
    assert rec["orders_submitted"] == 0
    assert v.posts == []


# ---------------------------------------------------------------- the clock

def test_placement_refused_past_the_cutoff(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(RUN.AB, "slug_start",
                        lambda s: time.time() - (T.WINDOW_LEN_S - 60.0))
    v = FakeVenue()
    rec = _run(transport=v)
    assert rec["stopped_at"] == "TIMING"
    assert "PAST_PLACEMENT_CUTOFF" in rec["refusals"][0]
    assert v.posts == []


def test_unknown_tte_never_places(monkeypatch):
    _arm(monkeypatch)
    monkeypatch.setattr(RUN.AB, "slug_start", lambda s: None)
    rec = _run(transport=FakeVenue())
    assert rec["stopped_at"] == "TIMING"
    assert rec["timing"]["withdraw"]["withdraw"] is True


def test_timing_boundaries_are_explicit():
    assert T.placement_allowed(121.0)["allowed"] is True
    assert T.placement_allowed(120.0)["allowed"] is False   # at == refused
    assert T.placement_allowed(119.9)["allowed"] is False
    assert T.must_withdraw(120.0)["withdraw"] is True
    assert T.must_withdraw(120.1)["withdraw"] is False
    assert T.must_withdraw(None)["withdraw"] is True
    assert T.placement_allowed(T.WINDOW_LEN_S + 1)["allowed"] is False


def test_open_order_at_the_boundary_is_withdrawn(monkeypatch):
    _arm(monkeypatch)
    # 130 s left: placement is allowed, and the boundary arrives mid-observe
    monkeypatch.setattr(RUN.AB, "slug_start",
                        lambda s: time.time() - (T.WINDOW_LEN_S - 130.0))
    rec = _run(transport=FakeVenue(), observe_s=30.0)
    assert rec["orders_submitted"] == 1
    assert any("withdrawal" in e for e in rec["lifecycle_events"])
    assert rec["rest_time_s"] < 30.0
    assert rec["deadman"]["cancel_all"]["ok"] is True


# ------------------------------------------------------------- the side rule

def test_side_is_deterministic_and_price_independent():
    a = S.side_for(SLUG, SID)
    for bid in ("0.10", "0.50", "0.90"):
        rec = RUN.run_experiment(transport=FakeVenue(), book=FakeBook(bid=bid),
                                 observe_s=0.5, book_wait_s=2.0,
                                 explicit_slug=SLUG)
        assert rec["side_rule"].get("error") or \
            rec["side_rule"]["side"] == a["side"]
    assert S.side_for(SLUG, SID)["side"] == a["side"]


def test_side_cannot_change_after_observing_movement(monkeypatch):
    """The decision is a function of two frozen strings; market movement
    cannot reach it."""
    _arm(monkeypatch)
    want = S.side_for(SLUG, SID)["side"]
    for bid, ask in (("0.05", "0.06"), ("0.94", "0.95")):
        monkeypatch.setattr(L, "LATCH_DIR", L.LATCH_DIR + bid)
        rec = _run(transport=FakeVenue(), book=FakeBook(bid=bid, ask=ask))
        assert rec["decision"]["side"] == want


def test_side_differs_across_sessions_but_is_fixed_per_session():
    seen = {S.side_for(SLUG, "s%d" % i)["side"] for i in range(40)}
    assert seen == {S.UP, S.DOWN}       # the rule is not constant
    assert S.side_for(SLUG, "s1") == S.side_for(SLUG, "s1")


def test_side_rule_is_in_the_fingerprint():
    assert X.SPEC["side_rule"]["rule"] == S.RULE
    assert X.SPEC["side_rule"]["is_a_forecast"] is False


def test_price_is_the_same_side_touch():
    snap = FakeBook(bid="0.40", ask="0.41").snapshot(SLUG)
    assert RUN.our_price(snap, S.UP) == D("0.40")
    assert RUN.our_price(snap, S.DOWN) == D("0.59")      # 1 - 0.41
    assert RUN.opposite_touch(snap, S.UP) == D("0.41")
    assert RUN.opposite_touch(snap, S.DOWN) == D("0.60")  # 1 - 0.40


# ---------------------------------------------------------------- markouts

def test_markouts_do_not_fabricate_missing_horizons(monkeypatch):
    _arm(monkeypatch)
    book = FakeBook()
    v = FakeVenue()
    rec = _run(transport=v, book=book, observe_s=2.0)
    m = rec["markouts"]
    # only the 1 s horizon can have elapsed in a 2 s observation
    assert rec["markout_horizons_elapsed"] == [1]
    assert rec["markout_horizons_not_elapsed"] == [5, 10, 30, 60]
    assert m["t_plus_1"]["markout"] is not None
    for h in (5, 10, 30, 60):
        assert m["t_plus_%d" % h]["markout"] is None
        assert m["t_plus_%d" % h]["reason"] == "NO_OBSERVATION"
    assert m["settlement"]["reason"] == "NOT_SETTLED"
    assert m["LABEL"] == "MARK_TO_MARKET_NOT_REALIZED"


def test_markout_from_a_stale_book_is_missing_not_interpolated(monkeypatch):
    _arm(monkeypatch)

    class Decaying(FakeBook):
        def snapshot(self, slug):
            s = super().snapshot(slug)
            if s and self.age < 9:
                self.age = 9.0       # goes stale right after placement
            return s

    rec = _run(transport=FakeVenue(), book=Decaying(), observe_s=2.0)
    # the horizon DID elapse, and the mark is still missing rather than
    # carried forward from the placement price
    assert rec["markout_horizons_elapsed"] == [1]
    assert rec["markouts"]["t_plus_1"]["markout"] is None
    assert rec["markouts"]["t_plus_1"]["reason"] == "NO_OBSERVATION"
    assert "never interpolated" in rec["markout_note"]


# -------------------------------------------------------------- arming walls

@pytest.mark.parametrize("ns", ["PMUS_Q1_", "PMUS_MICRO_", "PMUS_CANARY_",
                                "PMUS_CASE1_", "PMUS_SCHEMA_PROBE_"])
def test_foreign_namespaces_cannot_arm_it(monkeypatch, ns):
    for suffix in ("LIVE", "SESSION_ID", "ARM", "GATE_EVIDENCE"):
        monkeypatch.setenv(ns + suffix, "YES" if suffix == "LIVE" else "x")
    monkeypatch.setenv(X.FINGERPRINT_PIN_ENV, X.XR_FINGERPRINT)
    v = FakeVenue()
    rec = _run(transport=v)
    assert rec["orders_submitted"] == 0
    assert "DISARMED" in rec["refusals"]
    assert v.posts == []


def test_spec_still_forbids_claude_setting_the_values():
    assert X.SPEC["arming_install"]["claude_may_not_set_any_of_them"] is True
    assert X.SPEC["arming"]["claude_may_not_set_these"] is True


def test_runner_and_entrypoint_never_set_an_arming_key():
    import pathlib
    for f in ("analysis/l5/live_exec2/runner.py",
              "scripts/l5_exec1xr_run.py",
              "analysis/l5/live_exec2/orders.py"):
        src = pathlib.Path(f).read_text()
        for key in ARM + (X.FINGERPRINT_PIN_ENV,):
            assert 'setenv("%s"' % key not in src
            assert "environ[%r]" % key not in src
        assert "os.environ[" not in src.replace(
            'os.environ["PMUS', 'os.getenv("PMUS')


# ------------------------------------------------------------- the deadman

def test_deadman_runs_even_when_nothing_was_submitted():
    rec = _run()
    assert rec["deadman"]["may_create_an_order"] is False
    assert "nothing that could be resting" in rec["deadman"]["skipped"]


def test_record_keeps_matched_pair_na(monkeypatch):
    _arm(monkeypatch)
    rec = _run()
    assert rec["matched_pair_economics"] == "N/A_SINGLE_LEG_EXPERIMENT"
    assert rec["account"]["matched_pair_economics"] == \
        "N/A_SINGLE_LEG_EXPERIMENT"
