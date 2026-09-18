"""The invoker-level decision observer: evidence only, and provably so.

WHAT IT CLOSES. The research question CONTINUOUS-5 exists to answer is whether
a real fill changes the L5 ACTION -- OPEN becomes COMPLETE. `run_window`
recorded `_plan_summary(out["plan"])`, four integers, and discarded
`out["turn"]`, which is where the classification lives; `WindowGuard.record`
has no caller in the live path, so `guard.log` holds nothing either. The one
field the experiment turns on was written down nowhere.

WHY THE TESTS LOOK LIKE THIS. A recorder that changes what it records is
worse than no recorder, so the load-bearing test is not "does it capture the
label" but "does the window behave identically with and without it" -- byte
for byte, across the driver's report, the frames that reached the session and
the durable mutation ledger.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from src.gemini_execution import bundle as BUNDLE
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import ws_continuous as WC

SYMBOL = "GEMI-BTC05M2609181730-UP"

PLACE_ACK = {
    "id": "l5cp-place", "status": 200,
    "result": {"symbol": SYMBOL, "orderId": "145828836246613472",
               "price": "0.9000", "status": "OPEN", "type": "LIMIT"}}
CANCEL_ACK = {
    "id": "l5cp-cancel", "status": 200,
    "result": {"orderId": "145828836246611835", "symbol": SYMBOL,
               "status": "CANCELLED"}}

BOOK = {"readable": True, "identity_consistent": True,
        "yes": {"bid": "0.90", "ask": "0.93"},
        "no": {"bid": "0.07", "ask": "0.10"}}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop(
        ).run_until_complete(coro)


class ScriptedSession:
    def __init__(self, acks):
        self.acks = list(acks)
        self.sent = []
        self.events = None

    async def request(self, method, params, **kw):
        self.sent.append((method, params))
        return self.acks.pop(0)


def _canon(obj) -> str:
    """Canonical form: arbitrary identity and wall-clock removed, nothing else.

    clientOrderIds carry a uuid4 and the ledger stamps `time.time()`, so two
    runs of the same window can never be literally equal. Both are renamed in
    FIRST-APPEARANCE ORDER, which preserves exactly what must match -- how
    many orders, in what order, at what prices -- while discarding only the
    randomness. Anything else that differs survives into the comparison.
    """
    s = json.dumps(obj, sort_keys=True, default=str)
    seen = []
    for m in re.finditer(r"l5cp-[A-Za-z0-9\-]+", s):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    for i, cid in enumerate(seen):
        s = s.replace(cid, "<COID-%d>" % i)
    s = re.sub(r'"(t|started|resting_since)": [0-9.]+',
               r'"\1": "<clock>"', s)
    return s


def _driver(tmp_path, tag):
    led = tmp_path / ("led-%s.jsonl" % tag)
    led.write_text("", encoding="utf-8")
    d = WC.Driver("AUTH-OBS", SYMBOL, ledger_path=str(led),
                  now_fn=lambda: 1000.0, max_actions=6)
    d.inv = CP.Inventory()
    d.state = WC.AUTHORIZED
    d.preflight = lambda **k: {"ok": True}
    d.authorize = lambda: {"authorized": True}
    d.recover = lambda **k: {"recovered": True}
    d.bind = lambda: {"bound": True}
    return d, led


def _scripted_decide(d):
    """A fixed plan: cancel the resting rung, place YES 0.90.

    Deterministic ON PURPOSE. The controller's own decision is exercised by
    the real-decide tests below; here the plan is held constant so that any
    difference between the observed and unobserved runs can only be the
    observer.
    """
    def decide(tte_s, **kw):
        resting = d.working.rungs()
        return {
            "state": WC.RUNNING, "window_phase": "NORMAL",
            "turn": {"actions": [{"outcome": "Up", "leg": "YES",
                                  "price": "0.90", "qty": "0.01",
                                  "rung_index": 0, "ticks_behind_bid": 0,
                                  "action": CP.COMPLETE, "stage": "DECIDED"}],
                     "holds": [], "actions_remaining": 5,
                     "probe_state": "RUNNING"},
            "plan": {
                "keep": [],
                "cancel": [{"action": WC.CANCEL, "order": o, "why": "stale"}
                           for o in resting],
                "place": [{"action": WC.PLACE,
                           "rung": {"leg": "YES", "price": "0.90",
                                    "qty": "0.01"}}],
                "mutations": len(resting) + 1},
            "budget_left": d.budget_left()}
    return decide


def _window(tmp_path, tag, *, observed):
    d, led = _driver(tmp_path, tag)
    d.working.add("l5cp-pre-no-aaaaaaaa", leg="NO", price="0.14", qty="0.01",
                  now=1000.0, venue_order_id=145828836246611835)
    d.decide = _scripted_decide(d)
    session = ScriptedSession([CANCEL_ACK, PLACE_ACK])

    calls = {"n": 0}

    async def refresh():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"fills": [], "open_orders": [
                {"clientOrderId": "l5cp-pre-no-aaaaaaaa", "outcome": "no",
                 "price": "0.14", "quantity": "0.01",
                 "orderId": 145828836246611835, "resting_since": 1000.0}]}
        return {"fills": [], "open_orders": []}

    obs = RC.DecisionObserver() if observed else None
    fn = obs.attach(d, refresh) if observed else refresh
    rep = _run(d.run_window(session, tte_of=lambda: 150.0, refresh_fn=fn,
                            interval_s=0.0, max_cycles=1))
    return rep, session.sent, led.read_text(encoding="utf-8"), obs


# ================================================= THE LOAD-BEARING INVARIANT

def test_the_window_is_byte_equivalent_with_and_without_the_observer(
        tmp_path, monkeypatch):
    """Same state in, same decisions and same mutations out.

    Compared across all three durable surfaces: the driver's own report, the
    frames that actually reached the session, and the append-only ledger.
    """
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)

    plain_rep, plain_sent, plain_led, none_obs = _window(
        tmp_path, "plain", observed=False)
    obs_rep, obs_sent, obs_led, obs = _window(
        tmp_path, "observed", observed=True)

    assert none_obs is None
    assert _canon(plain_rep) == _canon(obs_rep)
    assert _canon(plain_sent) == _canon(obs_sent)
    assert _canon([json.loads(l) for l in plain_led.splitlines() if l]) == \
        _canon([json.loads(l) for l in obs_led.splitlines() if l])

    # and the run was a real one: it cancelled, placed, and then the
    # TERMINAL WITHDRAWAL cancelled what was still resting
    assert [m for m, _ in obs_sent] == ["order.cancel", "order.place",
                                        "order.cancel"]
    assert obs_rep["budget_consumed"] == 2


def test_the_observer_adds_no_decision_and_no_frame(tmp_path, monkeypatch):
    """One decide per cycle, and not one extra frame."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    plain_rep, plain_sent, _, _ = _window(tmp_path, "a", observed=False)
    obs_rep, obs_sent, _, obs = _window(tmp_path, "b", observed=True)

    assert obs.decide_calls == obs_rep["cycles"] == plain_rep["cycles"] == 1
    assert len(obs_sent) == len(plain_sent)
    # 2 strategy mutations plus the terminal withdrawal's safety cancel
    assert obs.summary()["mutations_observed"] == 2      # observed, not caused


def test_decide_returns_the_drivers_own_object(tmp_path):
    """Transparency is identity, not equality: a copy could drift."""
    d, _ = _driver(tmp_path, "identity")
    sentinel = {"state": WC.RUNNING, "plan": {}, "turn": {}}
    d.decide = lambda *a, **k: sentinel
    obs = RC.DecisionObserver()
    obs.attach(d, lambda: None)
    assert d.decide(150.0) is sentinel


def test_an_exception_from_the_driver_propagates_untouched(tmp_path):
    """The driver's RECONCILE path depends on seeing its own exceptions."""
    d, _ = _driver(tmp_path, "raise")

    async def boom(*a, **k):
        raise WC.ReconcileRequired("cancel unknown")

    d.cancel = boom
    obs = RC.DecisionObserver()
    obs.attach(d, lambda: None)
    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(None, {"client_order_id": "x"}))
    assert obs.mutations[0]["error"].endswith("cancel unknown")


def test_a_recorder_failure_is_written_down_rather_than_raised(tmp_path):
    """Evidence capture must never be able to stop a live window."""
    d, _ = _driver(tmp_path, "badrec")

    class Exploding:
        def report(self):
            raise RuntimeError("inventory report exploded")

    d.inv = Exploding()
    sentinel = {"state": WC.RUNNING, "plan": {}, "turn": {}}
    d.decide = lambda *a, **k: sentinel
    obs = RC.DecisionObserver()
    obs.attach(d, lambda: None)
    assert d.decide(150.0) is sentinel
    assert obs.cycles[0]["inventory_before_decision"] is None
    assert any("exploded" in e for e in obs.errors)


# ============================================== WHAT IT ACTUALLY WRITES DOWN

def test_the_real_classification_is_recorded_not_reconstructed(tmp_path):
    """The CONTINUOUS-4 state, through the REAL controller.

    Holding NO 0.01 @ 0.14 it labels YES 0.90 COMPLETE and NO 0.07 EXTEND.
    Those labels are what the experiment is for, and they now appear in the
    artifact rather than having to be recomputed from the residual afterwards.
    """
    d, _ = _driver(tmp_path, "real")
    d.inv.apply_fill("NO", "0.01", "0.14", source="venue_restart")
    d.guard = CP.WindowGuard(SYMBOL, auth_id="AUTH-OBS")
    d.state = WC.BOUND
    obs = RC.DecisionObserver()
    obs.attach(d, lambda: None)

    out = d.decide(150.0, book=BOOK)
    rec = obs.cycles[0]

    assert rec["classification"] == [CP.COMPLETE, CP.EXTEND]
    by_leg = {a["leg"]: a for a in rec["actions"]}
    assert by_leg["YES"]["price"] == "0.90" and by_leg["YES"]["qty"] == "0.01"
    assert by_leg["YES"]["action"] == CP.COMPLETE
    assert by_leg["NO"]["action"] == CP.EXTEND
    # the inventory the decision SAW, not the one it left behind
    inv = rec["inventory_before_decision"]
    assert inv["no_qty"] == "0.01" and inv["directional_residual"] == "-0.01"
    assert inv["avg_no_cost"] == "0.14"
    assert rec["tte_s"] == 150.0 and rec["cycle"] == 1
    assert rec["budget_before_decision"]["remaining"] == 6
    assert [p["leg"] for p in rec["plan"]["place"]] == ["YES", "NO"]
    assert out["turn"]["actions"][0]["action"] == CP.COMPLETE


@pytest.mark.parametrize("setup,leg,held_by,tte", [
    # both legs at the 0.10 per-leg cap: the envelope refuses the rung and
    # the hold carries `would_breach`'s own sentence
    ((("YES", 10, "0.05"), ("NO", 10, "0.05")), "YES", "ENVELOPE", 150.0),
    # residual already at 0.04: the oscillator suppresses the rung that
    # would widen it, while the completing rung is still acted on
    ((("NO", 4, "0.50"),), "NO", "OSCILLATOR", 150.0),
    # under the 60s floor a new leg could not be complemented before expiry
    ((), "YES", "END_OF_WINDOW", 45.0),
])
def test_every_hold_carries_its_price_and_its_literal_reason(
        tmp_path, setup, leg, held_by, tte):
    """A cycle that does nothing must say WHY it did nothing.

    This is the half of the capture that no amount of after-the-fact
    reconstruction can recover: `classify` is pure in the residual, so an
    ACTED rung's label can always be recomputed -- but a rung that was
    decided and then suppressed leaves no trace in inventory at all. All
    three suppression reasons are exercised against the real controller.
    """
    d, _ = _driver(tmp_path, "holds-%s" % held_by)
    for lg, n, price in setup:
        for _ in range(n):
            d.inv.apply_fill(lg, "0.01", price, source="venue_restart")
    d.guard = CP.WindowGuard(SYMBOL, auth_id="AUTH-OBS")
    d.state = WC.BOUND
    obs = RC.DecisionObserver()
    obs.attach(d, lambda: None)
    d.decide(tte, book=BOOK)

    hold = next(h for h in obs.cycles[0]["holds"] if h["leg"] == leg)
    assert hold["action"] == CP.HOLD
    assert hold["held_by"] == held_by
    assert hold["price"] == ("0.90" if leg == "YES" else "0.07")
    assert hold["qty"] == "0.01"
    assert hold["why"]                       # the literal reason, not a code
    assert held_by in obs.cycles[0]["held_by"]
    assert obs.summary()["action_labels"]["HOLD:" + held_by] >= 1
    if held_by == "ENVELOPE":
        # verbatim `would_breach`, not a paraphrase of it
        assert hold["why"] == CP.would_breach(leg, hold["price"], d.inv)


def test_a_second_identical_fill_is_reported_as_new(tmp_path):
    """A multiset difference, not a set one.

    Two 0.01 @ 0.14 fills are two fills. Collapsing them would hide exactly
    the second fill this experiment is watching for.
    """
    obs = RC.DecisionObserver()
    one = {"leg": "NO", "qty": "0.01", "price": "0.14"}
    obs._record_read(None, {"fills": [dict(one)], "open_orders": []}, None)
    obs._record_read(None, {"fills": [dict(one), dict(one)],
                            "open_orders": []}, None)
    assert obs.reads[0]["new_fills"] == [one]
    assert obs.reads[1]["new_fills"] == [one]
    assert obs.reads[1]["n_fills"] == 2


def test_an_unreadable_read_is_recorded_as_unreadable_not_empty(tmp_path):
    obs = RC.DecisionObserver()
    obs._record_read(None, {"fills": None, "open_orders": None}, None)
    r = obs.reads[0]
    assert r["fills_readable"] is False and r["open_orders_readable"] is False
    assert r["fills"] is None and r["new_fills"] is None


def test_the_place_records_the_inventory_in_force_at_the_call(tmp_path,
                                                              monkeypatch):
    """Not the inventory the DECISION saw: a post-cancel refresh sits between
    them, and CONTINUOUS-4's $1.06 pair is what a stale answer looks like."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d, _ = _driver(tmp_path, "atcall")
    d.state = WC.AUTHORIZED
    obs = RC.DecisionObserver()
    obs.attach(d, lambda: None)
    d.inv.apply_fill("NO", "0.01", "0.14", source="venue_restart")
    session = ScriptedSession([PLACE_ACK])
    _run(d.place(session, {"leg": "YES", "price": "0.90", "qty": "0.01"}))

    m = obs.mutations[0]
    assert m["kind"] == "PLACE"
    assert m["inventory_at_call"]["no_qty"] == "0.01"
    assert m["target"] == {"leg": "YES", "price": "0.90", "qty": "0.01"}
    assert m["result"]["placed"] is True
    assert m["budget_at_call"]["remaining"] == 6       # before the spend
    assert m["budget_after_call"]["remaining"] == 5    # and after it


def test_reads_take_the_drivers_own_phase_label(tmp_path):
    """The phase is JOINED from the driver's report, never inferred."""
    obs = RC.DecisionObserver()
    obs._record_read(None, {"fills": [], "open_orders": []}, None)
    obs._record_read(None, {"fills": [], "open_orders": []}, None)
    link = obs.link_refreshes([{"when": "pre_decide", "cycle": 1},
                               {"when": "post_cancel", "cycle": 1}])
    assert link["linked"] is True
    assert [r["when"] for r in obs.reads] == ["pre_decide", "post_cancel"]


def test_a_mismatched_join_is_reported_rather_than_guessed(tmp_path):
    obs = RC.DecisionObserver()
    obs._record_read(None, {"fills": [], "open_orders": []}, None)
    link = obs.link_refreshes([])
    assert link["linked"] is False and "guessed" in link["why"]
    assert "when" not in obs.reads[0]


# ================================================================= THE HASHES

def test_the_capture_did_not_move_the_envelope():
    """The capture lives in the invoker, so it never touched a BOUND.

    This pinned the executable hash too, which was right while the capture
    was the only change in flight. The CONTINUOUS-5 cancel fix has since
    moved the driver deliberately, so pinning that hash here would assert
    that no authorized fix may ever land. What the capture must never move
    is the envelope, and that is what this now says.
    """
    assert WC.AUTHORIZED_ENVELOPE == (
        "b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17")
    assert str(CP.MAX_ACTIONS) == "12" and str(CP.MAX_RESIDUAL) == "0.04"
    assert str(CP.MAX_IMBALANCE) == "0.04" and str(CP.ACTION_SIZE) == "0.01"


def test_the_observer_is_covered_by_the_bundle():
    """It lives in a bundle member, so it cannot be changed unnoticed."""
    assert "run_continuous.py" in BUNDLE.MEMBERS
    assert len(BUNDLE.bundle_sha()) == 64


def test_the_observer_owns_no_transport_and_no_bound():
    """Structural: it relays, it does not compose. No frame, no credential,
    no envelope number restated in the recorder."""
    src = open("src/gemini_execution/run_continuous.py",
               encoding="utf-8").read()
    body = src.split("class DecisionObserver", 1)[1].split(
        "\ndef _expiry_epoch", 1)[0]
    # comments explain the recorder; only EXECUTABLE lines are scanned, or
    # the test would forbid describing what the code deliberately avoids.
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    for forbidden in ("build_ws_order", "GEMINI_API", "os.environ",
                      "CP.MAX_", "CP.would_breach(", ".apply_fill(",
                      "CP.classify(", ".request(", "session.", "T.",
                      "MD."):
        assert forbidden not in code, forbidden


# ============================================ THE WIRING, NOT JUST THE CLASS

def test_the_observer_has_a_caller_and_its_output_reaches_the_report(
        tmp_path, monkeypatch):
    """A recorder nothing calls is the CONTINUOUS-4 defect wearing a new hat.

    `apply_fill` existed, was correct, and had no caller in the live loop for
    a whole window. The unarmed production proof cannot catch the same thing
    here, because it refuses at the authorization gate BEFORE the driver is
    built -- so `rep["decisions"]` is legitimately absent from every unarmed
    artifact and its absence proves nothing either way.

    This drives the REAL `_build` past every gate with fakes, runs the REAL
    Driver through a REAL window against the real controller, and asserts the
    capture reaches the report.
    """
    from src.gemini_execution import latch as LATCH
    from src.gemini_execution import metadata as MD
    from src.gemini_execution import run_canary as RUNCAN
    from src.gemini_execution import ws_session as WS

    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    monkeypatch.setattr(RC, "eligible_symbol", lambda *a, **k: {
        "eligible": True, "chosen": {"symbol": SYMBOL, "tte_s": 150.0}})
    # 150s for the first decision, then under the 30s withdraw floor so the
    # window winds itself down instead of burning every slot at 5s a cycle.
    ttes = iter([150.0])

    def _tte(sym):
        return lambda: next(ttes, 20.0)
    monkeypatch.setattr(RC, "tte_of", _tte)

    async def _terms_status():
        return {"readable": True, "verdict": "ACCEPTED", "blocks_orders": False}
    monkeypatch.setattr(MD, "terms_status", _terms_status)
    monkeypatch.setattr(MD, "terms_for", lambda *a, **k: {
        "priceIncrement": "0.01"})
    monkeypatch.setattr(MD, "outcome_book", lambda *a, **k: BOOK)
    monkeypatch.setattr(MD, "resolution_for", lambda *a, **k: {})

    monkeypatch.setattr(RC.PF, "preflight_now", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(RC.PF, "check_frozen", lambda pf: (True, []))

    # THE VENUE ALREADY HOLDS NO 0.01 @ 0.14 -- the CONTINUOUS-4 state, so the
    # controller must classify the YES rung COMPLETE rather than OPEN.
    async def _auth_state(sym):
        return {"symbol": sym,
                "fills": [{"leg": "NO", "qty": "0.01", "price": "0.14"}],
                "open_orders": [], "orders_seen": [],
                "order_history_raw": {"readable": True},
                "active_raw": {"ok": True}}
    monkeypatch.setattr(RC, "authoritative_state", _auth_state)

    monkeypatch.setattr(WC, "authorization_state", lambda *a, **k: {
        "authorized": True, "why_not": [], "required_auth_id": "AUTH-WIRING"})
    monkeypatch.setattr(RC.BUNDLE, "state", lambda: {
        "authorized": True, "why_not": []})
    monkeypatch.setattr(LATCH, "consume", lambda *a, **k: {
        "intent_id": "test", "state": "CONSUMED"})
    monkeypatch.setattr(RUNCAN, "make_durable", lambda *a, **k: None)
    monkeypatch.setattr(RUNCAN, "durable_check", lambda *a, **k: True)

    class FakeSession:
        events = None

        def __init__(self, **kw):
            self.sent = []

        async def connect(self):
            return None

        async def close(self):
            return None

        async def request(self, method, params, **kw):
            self.sent.append((method, params))
            return PLACE_ACK
    monkeypatch.setattr(WS, "GeminiMakerSession", FakeSession)

    led = tmp_path / "hist.jsonl"
    led.write_text("", encoding="utf-8")
    rep = _run(RC._build("AUTH-WIRING", state_dir=str(tmp_path),
                         ledger_path=str(led), wire_path=None,
                         cohort_id=None))

    # the window really ran
    assert rep.get("session") == "connected", rep.get("why")
    assert "window" in rep, rep.get("window_error") or rep.get("why")
    assert rep["window"]["cycles"] >= 1

    # ---- and the capture is IN the report
    for key in ("decisions", "authoritative_reads", "mutations_observed",
                "decision_capture", "refresh_link"):
        assert key in rep, key

    first = rep["decisions"][0]
    assert first["cycle"] == 1 and first["tte_s"] == 150.0
    # the inventory the decision SAW came from the authoritative re-read
    assert first["inventory_before_decision"]["no_qty"] == "0.01"
    assert first["inventory_before_decision"]["directional_residual"] == "-0.01"
    # THE FIELD THE EXPERIMENT TURNS ON, in the artifact, not reconstructed
    assert CP.COMPLETE in first["classification"]
    yes = next(a for a in first["actions"] if a["leg"] == "YES")
    assert yes["action"] == CP.COMPLETE and yes["price"] == "0.90"
    assert first["budget_before_decision"]["remaining"] >= 1

    # the reads carry the DRIVER's own phase label, joined not inferred
    assert rep["refresh_link"]["linked"] is True
    assert rep["authoritative_reads"][0]["when"] == "pre_decide"
    assert rep["authoritative_reads"][0]["n_fills"] == 1

    # and the mutations were observed with the inventory in force at the call
    assert rep["mutations_observed"]
    assert rep["mutations_observed"][0]["inventory_at_call"]["no_qty"] == "0.01"
    # ONE DECIDE PER COHERENT CYCLE. It is no longer one per cycle: the
    # coherence gate can end a cycle before `decide` is reached, which is
    # precisely the C6 fix, so the invariant is that a decision never
    # outnumbers the cycles.
    assert 1 <= rep["decision_capture"]["decide_calls"] <= rep["window"]["cycles"]
    assert rep["decision_capture"]["action_labels"].get(CP.COMPLETE, 0) >= 1
