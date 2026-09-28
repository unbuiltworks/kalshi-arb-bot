"""The REAL run() money path, executed. Not a composition test.

WHY THIS FILE EXISTS. Every previous guard on this path was satisfiable without
the path working:

* the 39 translation tests constructed `Lock5Session` directly, so the entrypoint
  never needing it went unnoticed;
* the composition tests called `probe.build_lock5(...)` themselves, so `run()`
  never calling it went unnoticed -- BLOCKER 1 one level up;
* every one of them faked `admit` as `{"admitted": True}`, so a translation that
  refused EVERY order went unnoticed.

Three guards, three false negatives, one cause: the test supplied the thing whose
absence was the defect. So this file invokes `run()` itself, with controlled
adapters at the edges only -- venue, book feed, activity, clock -- and asserts the
traversal from the artifacts the run leaves behind. The mandate is real, parsed by
`pmus_live_mandate`, and admission is the real `admit()` against a real state dir.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

import scripts.l5_pmus_live_oneshot as EP
from src import l5_order_body as OB
from src import pmus_live_mandate as MANDATE

SLUG = "cpc-btc-updown-15m-2026-09-28-0600z"
SEED = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

MANDATE_RAW = {
    "authorization_id": "OWNER-EXEC-TEST",
    "authorized_at": "2026-09-28T00:00:00Z",
    "service": "l5-live-probe-oneshot",
    "strategy": "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
    "strategy_version": "exec-test",
    "max_markets": 1, "max_total_markets": 1,
    "max_spend_per_market": "1.00", "max_total_spend": "1.00",
    "max_residual": "1.00", "max_daily_realized_loss": "1.00",
    "expiry": "2099-01-01T00:00:00Z",
}


# --------------------------------------------------------------- edge adapters

class Feed:
    """The authenticated book feed, at the edge. No network."""

    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def wait_ready(self, sleep=None):
        return True

    def books(self):
        return []

    def health(self):
        return {"connected": True, "frames": 1, "fresh": True,
                "source": "TEST_ADAPTER", "errors": [], "stop_reason": "test"}

    def stop(self):
        self.started = False


def ticking(start_at, step=1.0):
    """A clock that ADVANCES. A constant clock makes run()'s pre-window and
    preflight loops spin forever, which is a property of the real control flow
    and not something to paper over with a shorter timeout."""
    box = {"t": float(start_at)}

    def now():
        box["t"] += step
        return box["t"]
    return now


class Venue:
    """The wire. Records POSTs; answers order queries with a scripted fill."""

    def __init__(self, *, fill_qty="1", avg_px="0.40"):
        self.posts = []
        self.gets = []
        self.fill_qty, self.avg_px = fill_qty, avg_px
        self.order_id = "OID-1"

    def request(self, method, path, body=None, **kw):
        if str(method).upper() == "POST" and path == MANDATE.ORDER_PATH:
            self.posts.append(dict(body or {}))
            # ACK deliberately reports ZERO filled, as probe 04's did two
            # milliseconds before it actually filled.
            return {"order": {"orderId": self.order_id, "filledQty": 0}}
        self.gets.append((method, path))
        return {}

    def order(self, vid):
        return {"orderId": vid, "state": "ORDER_STATE_FILLED",
                "cumQty": self.fill_qty, "avgPx": self.avg_px,
                "commission": "0", "leavesQty": "0", "terminal": True}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A complete, real Rung-A environment: mandate, arm triple, fresh paths."""
    monkeypatch.setenv(MANDATE.MANDATE_ENV, json.dumps(MANDATE_RAW))
    monkeypatch.setenv("PMUS_API_KEY_ID", "key-aaaaaaaaaaaa")
    monkeypatch.setenv("PMUS_API_SECRET_KEY", SEED)
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "exec-test-session")
    monkeypatch.setenv("PMUS_Q1_ARM", "exec-test-session")
    monkeypatch.setenv(EP.AUTH.MARKET_ENV, SLUG)
    monkeypatch.setenv("L5_MAKER_ONLY", "1")
    monkeypatch.setenv("L5_OBSERVE_ONLY", "")
    monkeypatch.setenv("L5_RECOVER_ONLY", "")
    return tmp_path


def paired_doc(tmp_path):
    p = tmp_path / "paired_shadow_live.json"
    assert p.exists(), "no paired record was written by run()"
    return json.loads(p.read_text())


def journal_kinds(tmp_path):
    p = tmp_path / "journal.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line).get("kind") or json.loads(line).get("k"))
        except Exception:                                      # noqa: BLE001
            pass
    return [k for k in out if k]


# ------------------------------------------------------------- $0 WITH NO MANDATE

def _arm_owner(monkeypatch):
    """Set the owner arm triple. The claim is gated on it and happens BEFORE the
    mandate is read, so without this the mandate gate is never reached and a
    no-mandate test would pass for the wrong reason."""
    monkeypatch.setenv("PMUS_API_KEY_ID", "key-aaaaaaaaaaaa")
    monkeypatch.setenv("PMUS_API_SECRET_KEY", SEED)
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "exec-test-session")
    monkeypatch.setenv("PMUS_Q1_ARM", "exec-test-session")


def test_without_a_mandate_run_refuses_with_zero_authority(tmp_path, monkeypatch):
    _arm_owner(monkeypatch)
    monkeypatch.delenv(MANDATE.MANDATE_ENV, raising=False)
    monkeypatch.setenv(MANDATE.MANDATE_FILE_ENV, str(tmp_path / "absent.json"))
    monkeypatch.setenv(EP.AUTH.MARKET_ENV, SLUG)
    venue = Venue()
    rep = EP.run(clock=ticking(EP.window_start(SLUG) + EP.PREFLIGHT_AT_S),
                 sleep=lambda *_a, **_k: None,
                 transport_factory=lambda slug: venue,
                 state_path=str(tmp_path / "inv.json"),
                 journal_path=str(tmp_path / "journal.jsonl"),
                 ledger_path=str(tmp_path / "ledger.jsonl"),
                 book_feed=Feed())
    assert rep["refused"] == "NO_MONETARY_AUTHORITY"
    assert rep["armed"] is False
    assert rep["effective_limits_usd"] == "0"
    assert rep["monetary_authority"] == "NONE_NO_MANDATE"
    assert venue.posts == [], "an order went out with no monetary authority"


def test_a_malformed_mandate_is_refused_not_defaulted(tmp_path, monkeypatch):
    _arm_owner(monkeypatch)
    bad = dict(MANDATE_RAW)
    bad.pop("max_spend_per_market")
    monkeypatch.setenv(MANDATE.MANDATE_ENV, json.dumps(bad))
    monkeypatch.setenv(EP.AUTH.MARKET_ENV, SLUG)
    venue = Venue()
    rep = EP.run(clock=ticking(EP.window_start(SLUG) + EP.PREFLIGHT_AT_S),
                 sleep=lambda *_a, **_k: None,
                 transport_factory=lambda slug: venue,
                 state_path=str(tmp_path / "inv.json"),
                 journal_path=str(tmp_path / "journal.jsonl"),
                 ledger_path=str(tmp_path / "ledger.jsonl"),
                 book_feed=Feed())
    assert rep["refused"] == "NO_MONETARY_AUTHORITY"
    assert "max_spend_per_market" in str(rep["detail"])
    assert venue.posts == []


# ------------------------------------------------- the money path, dynamically

def _run(tmp_path, venue, **kw):
    """run() with real mandate + real admit, edges controlled."""
    start = EP.window_start(SLUG)
    assert start is not None, "the test slug must name a readable window"
    return EP.run(clock=kw.pop("clock", ticking(start + EP.PREFLIGHT_AT_S)),
                  sleep=lambda *_a, **_k: None,
                  transport_factory=lambda slug: venue,
                  state_path=str(tmp_path / "inv.json"),
                  journal_path=str(tmp_path / "journal.jsonl"),
                  ledger_path=str(tmp_path / "ledger.jsonl"),
                  book_feed=kw.pop("book_feed", Feed()), **kw)


def test_the_executors_transport_IS_the_paired_lock_adapter(env):
    """The one assertion that catches a disconnected adapter.

    `work_window` passes `self.tp` to the frozen executor, so `self.tp` is the
    object every order actually travels through. Asserting on `probe.adapter`
    instead proves only that the adapter works in isolation -- and removing
    `probe.tp = paired_tp` left all thirteen of these tests green until this test
    existed. That is the same vacuity as BLOCKER 1, for the fourth time: the test
    reached for the component directly instead of the seam production uses.
    """
    venue = Venue()
    _run(env, venue)
    probe = EP._LAST_PROBE
    assert isinstance(probe.tp, EP.ADAPT.PairedLockTransport), type(probe.tp)
    assert probe.tp is probe.adapter
    # and underneath it, the mandate guard
    assert MANDATE.is_guarded(probe.tp._inner), type(probe.tp._inner)


def test_the_money_path_is_composed_by_run_itself(env):
    """run() -> mandate load -> guarded transport -> Lock5Session -> paired."""
    venue = Venue()
    rep = _run(env, venue)
    kinds = journal_kinds(env)
    assert "mandate_loaded" in kinds, kinds
    assert "money_path_composed" in kinds, kinds
    assert "lock5_composed" in kinds, kinds
    # and the composed limits are the MANDATE's dollars, not a static default
    assert rep.get("monetary_authority") == "PMUS_LIVE_MANDATE" or \
        rep.get("refused"), rep
    doc = paired_doc(env)
    assert doc["summary"]["shadow_fill_treatment"] == "queue_aware"


def test_an_order_traverses_pair_then_lock_then_mandate_then_wire(env):
    """The full traversal, asserted from the artifacts, with a REAL admit()."""
    venue = Venue()
    _run(env, venue)
    probe = EP._LAST_PROBE
    assert probe is not None and probe.adapter is not None
    # the EXECUTOR's transport, not a side reference to the adapter
    assert probe.tp is probe.adapter
    tp = probe.tp
    body = OB.build(slug=SLUG, side="up", price=D("0.40"), qty=D(1),
                    post_only=True)
    ack = tp.request("POST", MANDATE.ORDER_PATH, body)
    # wire reached, through the real mandate
    assert venue.posts, "the real mandate admitted but nothing reached the wire"
    row = tp.rows["__last__"]
    assert row.action == "OPEN"
    assert row.submission is not None          # pair_intent happened first
    assert row.ack is not None
    # ACK IS NOT FILL
    assert row.live_filled is False and row.live_state == "ACKED"
    assert probe.lock5.book.q["up"] == 0, "an ACK moved inventory"

    # authoritative fill only, and it moves inventory + evaluates LOCK
    out = tp.note_venue_progress(
        {"coid": "", "delta": "1", "avg_px": "0.40", "terminal": True,
         "state": "ORDER_STATE_FILLED"}, side="up")
    assert out is not None
    assert row.live_filled is True and row.fill_source == "VENUE_FILL"
    assert probe.lock5.book.q["up"] == D(1)
    assert out["lock"]["threshold"] == "5.00"


def test_after_lock_no_new_risk_but_completion_still_allowed(env):
    venue = Venue()
    _run(env, venue)
    tp, lock5 = EP._LAST_PROBE.tp, EP._LAST_PROBE.lock5
    assert tp is EP._LAST_PROBE.adapter
    # latch LOCK by fiat on the frozen session; the adapter must obey it
    lock5.locked = True
    up = OB.build(slug=SLUG, side="up", price=D("0.10"), qty=D(1),
                  post_only=True)
    with pytest.raises(EP.ADAPT.Lock5Refusal):
        tp.request("POST", MANDATE.ORDER_PATH, up)
    assert venue.posts == [], "a new-risk order went out after LOCK"
    # and the refusal is recorded as a resolved paired row, not dropped
    assert tp.rows["__last__"].live_state == "CANCELLED"


def test_the_mandate_refuses_over_one_dollar_on_the_real_path(env):
    venue = Venue()
    _run(env, venue)
    tp = EP._LAST_PROBE.tp
    assert tp is EP._LAST_PROBE.adapter
    big = OB.build(slug=SLUG, side="up", price=D("0.99"), qty=D(5),
                   post_only=True)
    with pytest.raises(MANDATE.MandateRefusal):
        tp.request("POST", MANDATE.ORDER_PATH, big)
    assert venue.posts == [], "$4.95 went out under a $1.00 mandate"
    assert tp.rows["__last__"].live_state == "REFUSED"


def test_a_no_side_order_is_priced_in_outcome_terms(env):
    venue = Venue()
    _run(env, venue)
    tp = EP._LAST_PROBE.tp
    assert tp is EP._LAST_PROBE.adapter
    body = OB.build(slug=SLUG, side="DOWN", price=D("0.40"), qty=D(1),
                    post_only=True)
    assert body["price"]["value"] == "0.60"          # the wire inverts it
    tp.request("POST", MANDATE.ORDER_PATH, body)
    row = tp.rows["__last__"]
    assert row.shadow_price == D("0.40"), (
        "a NO order was recorded at its wire price, overstating the outcome")


# ------------------------------------------------------------ NEGATIVE CONTROLS
# Each disconnects ONE critical component and the traversal assertions must fail.
# Without these, everything above could be passing vacuously -- which is exactly
# how three previous guards passed.

def test_negative_control_no_guard_means_no_mandate_enforcement(env, monkeypatch):
    """Bypass GuardedTransport: the $1 cap stops binding, so the test above
    was really measuring the mandate."""
    venue = Venue()
    monkeypatch.setattr(MANDATE, "guard", lambda inner, m, d, **k: inner)
    _run(env, venue)
    tp = EP._LAST_PROBE.tp
    assert tp is EP._LAST_PROBE.adapter
    big = OB.build(slug=SLUG, side="up", price=D("0.99"), qty=D(5),
                   post_only=True)
    tp.request("POST", MANDATE.ORDER_PATH, big)
    assert venue.posts, "with the guard bypassed the order should have gone out"


def test_negative_control_no_paired_log_breaks_the_record(env, monkeypatch):
    monkeypatch.setattr(EP.PAIRED, "PairedLog", lambda **kw: None)
    venue = Venue()
    with pytest.raises(Exception):
        _run(env, venue)


def test_negative_control_no_lock5_gate_lets_new_risk_through(env, monkeypatch):
    """Disconnect may_open_risk: the LOCK test above stops proving anything."""
    venue = Venue()
    _run(env, venue)
    tp, lock5 = EP._LAST_PROBE.tp, EP._LAST_PROBE.lock5
    assert tp is EP._LAST_PROBE.adapter
    lock5.locked = True
    monkeypatch.setattr(type(lock5), "may_open_risk",
                        lambda self, action: (True, "DISCONNECTED"))
    up = OB.build(slug=SLUG, side="up", price=D("0.10"), qty=D(1),
                  post_only=True)
    tp.request("POST", MANDATE.ORDER_PATH, up)
    assert venue.posts, "with the LOCK gate disconnected the order should pass"


def test_negative_control_an_ack_can_never_be_promoted(env):
    venue = Venue()
    _run(env, venue)
    tp = EP._LAST_PROBE.tp
    assert tp is EP._LAST_PROBE.adapter
    body = OB.build(slug=SLUG, side="up", price=D("0.40"), qty=D(1),
                    post_only=True)
    tp.request("POST", MANDATE.ORDER_PATH, body)
    row = tp.rows["__last__"]
    with pytest.raises(EP.PAIRED.PromotionRefused):
        row.venue_fill(price="0.40", qty="1", source="ACK")
    assert row.live_state == "ACKED"


# ------------------------------------------- structural: called FROM run(), not
# merely defined, imported, or reachable somewhere in the package.

def test_the_critical_methods_are_called_from_the_run_execution_graph():
    """AST: reachable from run()'s own call graph within this module.

    'Present in the closure' is no longer evidence -- build_lock5 was in the
    closure while run() never called it.
    """
    import ast
    src = open(EP.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def calls_within(name, seen=None):
        seen = seen or set()
        if name in seen or name not in funcs:
            return set()
        seen.add(name)
        out = set()
        for n in ast.walk(funcs[name]):
            if isinstance(n, ast.Call):
                f = n.func
                attr = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else None)
                if attr:
                    out.add(attr)
                    out |= calls_within(attr, seen)
        return out

    reached = calls_within("run")
    for required in ("load_mandate", "guard", "build_lock5",
                     "arm_with_bounded_retry", "PairedLockTransport",
                     "record_reconciliation", "from_mandate"):
        assert required in reached, (
            "%s is not called from run()'s execution graph; reached=%s"
            % (required, sorted(reached)))
    # and the bare arm() is no longer the arming call site in run()
    assert "ArmRetryGate" in reached


def test_pair_intent_and_the_fill_path_are_called_from_the_adapter():
    import ast
    src = open("src/pmus_lock5_live_adapter.py", encoding="utf-8").read()
    tree = ast.parse(src)
    attrs = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for required in ("intend", "may_open_risk", "completion_permitted",
                     "apply_authoritative_fill", "evaluate_lock", "venue_fill",
                     "acked", "submitted"):
        assert required in attrs, required
