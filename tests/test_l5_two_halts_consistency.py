"""TWO halts, read consistently, neither clearable by anything automated.

THE TWO MECHANISMS ARE NOT INTERCHANGEABLE and that is why both exist:

  * `L5_LIVE_HALT` -- the OPERATOR halt. An environment variable the owner
    sets on the service. It is local to the executor, takes effect on the
    next process read, and cannot be set by anything without deploy
    credentials. Read in exactly one expression,
    `l5_pmus_live_runner.operator_halted()`.

  * the DURABLE LATCH -- the INDEPENDENT halt. An object a separate
    watchdog PUTs, which outlives our process, our crash and our redeploy.
    Read through `l5_halt_gate.HaltGate`, whose default answer is NO.

A halt that one reader honours and another reports as OPEN is the failure
that cost the 2026-11-09 08:30Z window: the halt was in the heartbeat the
whole time and the blocker list read off `submission_gate()` did not name
it, because that reader did not consult the env halt AT ALL. These tests
pin both properties -- agreement, and no automatic clear -- so neither can
regress quietly.

WHAT IS DELIBERATELY NOT CLAIMED HERE. These prove the gates refuse. They
do not prove an order never reached a venue in any past window; that is a
settlement-audit question answered from venue evidence, not from tests.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json

import pytest

from src import l5_halt_gate as HG
from src import l5_launch as L
from src import l5_pmus_live_runner as R

UTC = dt.timezone.utc


# =====================================================================
# ONE EXPRESSION FOR THE OPERATOR HALT
# =====================================================================

@pytest.mark.parametrize("value,expect", [
    (None, False), ("", False), ("   ", False),
    ("1", True), ("0", True), ("false", True), ("no", True),
    ("stop", True), (" 1 ", True),
])
def test_every_reader_of_the_operator_halt_agrees(monkeypatch, value, expect):
    """ANY non-empty value halts, including "0" and "false".

    That is intentional and it is why the readers must share one
    expression: an operator who types L5_LIVE_HALT=0 meaning "off" has
    engaged the halt, and a second reader that parsed it as a boolean
    would disagree with the first and run.
    """
    if value is None:
        monkeypatch.delenv(R.LIVE_HALT_ENV, raising=False)
    else:
        monkeypatch.setenv(R.LIVE_HALT_ENV, value)
    assert R.operator_halted() is expect


def test_the_runner_method_is_the_module_expression_and_not_a_second_one():
    src = inspect.getsource(R.Runner.halted)
    assert "operator_halted()" in src
    assert "os.getenv" not in src, (
        "a second read of the variable is a second opinion")


def test_the_authorization_preflight_reads_the_same_expression():
    from src import l5_live_authorization as LA
    src = inspect.getsource(LA)
    assert "operator_halted()" in src
    assert 'os.getenv("L5_LIVE_HALT"' not in src


def test_the_submission_gate_reader_reads_the_same_expression():
    src = inspect.getsource(R.Runner.submission_gate)
    assert "operator_halted()" in src, (
        "the EFFECTIVE gate reader must consult the operator halt; its "
        "absence here is the 08:30Z failure")


# =====================================================================
# EITHER HALT ALONE BLOCKS, AND NEITHER MASKS THE OTHER
# =====================================================================
class Store:
    """An ObjectStore that can be absent, present, or unreadable."""

    def __init__(self, body=None, *, raise_on_get=False):
        self.body = body
        self.raise_on_get = raise_on_get
        self.puts = []

    def get(self, key):
        if self.raise_on_get:
            raise HG.HaltReadUnavailable("boom")
        return self.body

    def put(self, key, body):
        self.puts.append((key, body))
        self.body = body
        return {"ok": True}


def rec(halt=False, *, age_s=0.0, who="watchdog", **kw):
    t = dt.datetime.now(UTC) - dt.timedelta(seconds=age_s)
    d = {"halt": halt, "t": t.isoformat(), "who": who}
    d.update(kw)
    return json.dumps(d).encode()


class FakeGate:
    """Stands in for the runner's `executor.halt_gate`."""

    def __init__(self, why=None, *, raises=False):
        self.why = why
        self.raises = raises

    def refuse_reason(self):
        if self.raises:
            raise RuntimeError("unreachable")
        return self.why

    def telemetry(self):
        return {"sha256": "d" * 64, "kind": "PRESENT", "halt": None}


def gate_view(monkeypatch, *, env_halt, latch_why, shadow=False,
              armed=True, token=True, raises=False):
    """Drive the REAL `submission_gate` with both halts controllable."""
    r = R.Runner.__new__(R.Runner)
    r.executor = type("E", (), {"halt_gate": FakeGate(latch_why,
                                                      raises=raises)})()
    r.transport = type("T", (), {"armed": armed})()
    if env_halt is None:
        monkeypatch.delenv(R.LIVE_HALT_ENV, raising=False)
    else:
        monkeypatch.setenv(R.LIVE_HALT_ENV, env_halt)
    monkeypatch.setattr(R, "shadow_only", lambda: shadow)
    monkeypatch.setattr(R.Q1, "armed", lambda: token)
    return r.submission_gate()


def test_the_operator_halt_alone_blocks_with_every_other_gate_open(monkeypatch):
    out = gate_view(monkeypatch, env_halt="1", latch_why=None)
    assert out["operator_halt"] is True
    assert out["halt_latch"] == "CLEAR", "the latch is genuinely clear here"
    assert out["shadow_lock"] is False and out["armed"] is True
    assert out["blocked"] is True
    assert out["reason"] == "OPERATOR_HALT"


def test_the_durable_latch_alone_blocks_with_the_env_halt_clear(monkeypatch):
    out = gate_view(monkeypatch, env_halt=None, latch_why="halt_asserted")
    assert out["operator_halt"] is False
    assert out["halt_latch"] == "HALT"
    assert out["blocked"] is True
    assert "HALT_GATE" in out["reason"]


def test_both_engaged_still_blocks_and_names_the_operator_halt(monkeypatch):
    out = gate_view(monkeypatch, env_halt="1", latch_why="halt_asserted")
    assert out["operator_halt"] is True and out["halt_latch"] == "HALT"
    assert out["blocked"] is True


def test_neither_engaged_is_the_only_way_through(monkeypatch):
    out = gate_view(monkeypatch, env_halt=None, latch_why=None)
    assert out["blocked"] is False and out["reason"] == "OPEN"


def test_an_unreadable_latch_blocks_even_with_the_env_halt_clear(monkeypatch):
    out = gate_view(monkeypatch, env_halt=None, latch_why=None, raises=True)
    assert out["halt_latch"] == "UNREADABLE"
    assert out["blocked"] is True


def test_a_missing_latch_gate_is_not_permission(monkeypatch):
    r = R.Runner.__new__(R.Runner)
    r.executor = type("E", (), {"halt_gate": None})()
    r.transport = type("T", (), {"armed": True})()
    monkeypatch.delenv(R.LIVE_HALT_ENV, raising=False)
    monkeypatch.setattr(R, "shadow_only", lambda: False)
    monkeypatch.setattr(R.Q1, "armed", lambda: True)
    out = r.submission_gate()
    assert out["halt_latch"] == "NO_GATE" and out["blocked"] is True


def test_the_two_are_separate_keys_so_a_reader_cannot_conflate_them(
        monkeypatch):
    out = gate_view(monkeypatch, env_halt="1", latch_why=None)
    assert "operator_halt" in out and "halt_latch" in out
    assert out["operator_halt"] != (out["halt_latch"] != "CLEAR"), (
        "this case has the env halt engaged and the latch clear; if one "
        "key implied the other they could not differ")


def test_the_label_states_that_they_are_independent(monkeypatch):
    out = gate_view(monkeypatch, env_halt=None, latch_why=None)
    assert "operator_halt" in out["labels"]
    assert "INDEPENDENT" in out["labels"]


def test_the_intent_chain_evaluates_BOTH_halts_before_arming(monkeypatch):
    """Order matters: a chain that checked `armed` first would report
    NOT_ARMED for a halted process and bury the halt."""
    from src import l5_intent_gate as IG
    o = list(IG.GATE_ORDER)
    for g in ("OPERATOR_HALT", "DURABLE_HALT_LATCH"):
        assert g in o, g
        assert o.index(g) < o.index("TRANSPORT_ARMED"), g
        assert o.index(g) < o.index("OWNER_ARM_TOKEN"), g
    assert o.index("OPERATOR_HALT") < o.index("DURABLE_HALT_LATCH"), (
        "the owner's own switch is reported first")
    assert IG.LAYER["OPERATOR_HALT"] == "SAFETY_OVERLAY"
    assert "if r.halted():" in inspect.getsource(IG)


# =====================================================================
# THE DURABLE GATE FAILS CLOSED, EVERY WAY IN
# =====================================================================

@pytest.mark.parametrize("store,why", [
    (Store(None), "halt_state_unavailable"),
    (Store(b"{not json"), "halt_state_unavailable"),
    (Store(None, raise_on_get=True), "halt_state_unavailable"),
    (Store(rec(True)), "halt_asserted"),
    (Store(json.dumps({"t": dt.datetime.now(UTC).isoformat()}).encode()),
     "halt_asserted"),
    (Store(json.dumps({"halt": "false",
                       "t": dt.datetime.now(UTC).isoformat()}).encode()),
     "halt_asserted"),
    (Store(rec(False, age_s=99999.0)), "halt_record_stale"),
    (Store(json.dumps({"halt": False, "t": "not-a-time"}).encode()),
     "halt_record_unparseable_time"),
    (Store(json.dumps({"halt": False,
                       "t": "2026-01-01T00:00:00"}).encode()),
     "halt_record_naive_time"),
])
def test_the_only_path_to_permitted_is_a_fresh_positive_no_halt(store, why):
    g = HG.HaltGate(store)
    assert g.submissions_permitted() is False
    assert g.refuse_reason() == why


def test_a_future_dated_record_is_refused():
    t = dt.datetime.now(UTC) + dt.timedelta(hours=1)
    g = HG.HaltGate(Store(json.dumps(
        {"halt": False, "t": t.isoformat()}).encode()))
    assert g.refuse_reason() == "halt_record_in_the_future"


def test_a_fresh_explicit_no_halt_is_the_one_permitted_case():
    g = HG.HaltGate(Store(rec(False)))
    assert g.submissions_permitted() is True
    assert g.refuse_reason() is None


def test_the_env_halt_ORs_into_the_gate_when_it_is_wired():
    """`HaltGate` accepts `env_halt` so the kill switch keeps working even
    where the gate is the only thing consulted."""
    g = HG.HaltGate(Store(rec(False)), env_halt="1")
    assert g.submissions_permitted() is False
    assert g.state()["why"] == "env_halt"


# =====================================================================
# NEITHER HALT CAN BE CLEARED AUTOMATICALLY
# =====================================================================

def test_the_asserter_has_no_delete_and_no_clear_by_construction():
    names = [n for n in dir(HG.HaltAsserter) if not n.startswith("_")]
    assert sorted(names) == ["assert_halt", "heartbeat_no_halt"], names
    for bad in ("delete", "clear", "release", "reset", "unlatch", "remove"):
        assert not hasattr(HG.HaltAsserter, bad), bad


def test_the_object_store_protocol_offers_no_delete_verb():
    """The asserter cannot delete what its interface cannot express."""
    names = [n for n in dir(HG.ObjectStore) if not n.startswith("_")]
    assert sorted(names) == ["get", "put"], names


def test_the_watchdog_cannot_attest_no_halt_over_its_own_latch():
    st = Store(rec(True, reason="deficit"))
    a = HG.HaltAsserter(st, who="watchdog")
    with pytest.raises(PermissionError) as ei:
        a.heartbeat_no_halt()
    assert "owner action" in str(ei.value)
    assert st.puts == [], "it wrote anyway"


def test_it_cannot_attest_no_halt_over_a_state_it_could_not_establish():
    """An unreadable record MAY be a latch, and writing over it clears it."""
    st = Store(None, raise_on_get=True)
    a = HG.HaltAsserter(st, who="watchdog")
    with pytest.raises(PermissionError) as ei:
        a.heartbeat_no_halt()
    assert "could not be established" in str(ei.value)
    assert st.puts == []


def test_it_may_attest_no_halt_when_the_object_is_POSITIVELY_absent():
    """The distinction that makes the test above meaningful: absence is not
    a latch, so the watchdog's ordinary heartbeat still works."""
    st = Store(None)
    rec_ = HG.HaltAsserter(st, who="watchdog").heartbeat_no_halt()
    assert rec_["halt"] is False and len(st.puts) == 1


def test_asserting_a_halt_requires_a_reason_and_named_evidence():
    st = Store(None)
    a = HG.HaltAsserter(st, who="watchdog")
    with pytest.raises(ValueError):
        a.assert_halt(reason="", evidence={"x": 1})
    with pytest.raises(ValueError):
        a.assert_halt(reason="why", evidence={})
    assert st.puts == []


def test_an_unidentified_asserter_is_refused():
    for who in (None, "", "   ", 7):
        with pytest.raises(ValueError):
            HG.HaltAsserter(Store(None), who=who)


# ---------------------------------------------------------------------
# AND THE LAUNCHER CANNOT CLEAR THE ENV HALT AS A SIDE EFFECT
# ---------------------------------------------------------------------
def plan(now=None, **over):
    """A minimal READY plan whose document hashes to its own digest."""
    now = now or dt.datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    start = dt.datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
    doc = L.build_document(window_start=start, now=now)
    out = {"verdict": L.READY, "blocking": [], "document": doc,
           "document_line": L.document_line(doc),
           "document_digest": L.document_digest(doc),
           "variables": L.variables_for(doc),
           "market": L.slug_for_window(start)}
    out.update(over)
    return out


def apply(p, **kw):
    sent = {}
    kw.setdefault("read_back", lambda: {
        "campaign": {"document_sha256": L.document_digest(p["document"]),
                     "approval_state": "VALID"}})
    kw.setdefault("approved_digest", L.document_digest(p["document"]))
    out = L.apply_plan(prepared=p,
                       set_variables=lambda v: sent.update(v), **kw)
    return out, sent


def test_an_ordinary_apply_never_writes_the_halt_variable():
    _, sent = apply(plan())
    assert "L5_LIVE_HALT" not in sent
    assert "L5_SHADOW_ONLY" not in sent, (
        "nor the structural lock, absent an explicit unlock")
    assert sorted(sent) == ["L5_CAMPAIGN_APPROVAL", "L5_LIVE_AUTH_NOT_AFTER",
                            "L5_TS_EXPERIMENT_ID", "L5_TS_MARKET"]


def test_a_halt_reset_without_a_reason_is_refused():
    with pytest.raises(L.ApplyRefused) as ei:
        apply(plan(), reset_operator_halt="   ")
    assert ei.value.reason == "HALT_RESET_NEEDS_A_REASON"


def test_a_halt_reset_with_a_reason_but_no_evidence_is_refused():
    with pytest.raises(L.ApplyRefused) as ei:
        apply(plan(), reset_operator_halt="origin established")
    assert ei.value.reason == "HALT_RESET_PRECONDITIONS_UNMET"
    assert set(ei.value.detail["unmet"]) == {
        n for n, _ in L.HALT_RESET_PRECONDITIONS}


@pytest.mark.parametrize("missing", [n for n, _ in
                                     L.HALT_RESET_PRECONDITIONS])
def test_EVERY_precondition_is_individually_load_bearing(missing):
    ev = {n: True for n, _ in L.HALT_RESET_PRECONDITIONS}
    ev[missing] = False
    with pytest.raises(L.ApplyRefused) as ei:
        apply(plan(), reset_operator_halt="origin established",
              halt_reset_evidence=ev)
    assert ei.value.detail["unmet"] == [missing]


def test_a_reset_with_a_reason_and_every_precondition_clears_it_and_says_so():
    ev = {n: True for n, _ in L.HALT_RESET_PRECONDITIONS}
    out, sent = apply(plan(), reset_operator_halt="origin established in #214",
                      halt_reset_evidence=ev)
    assert sent["L5_LIVE_HALT"] == ""
    steps = [s["step"] for s in out["steps"]]
    assert "operator_halt_reset_authorized" in steps
    rec_ = [s for s in out["steps"]
            if s["step"] == "operator_halt_reset_authorized"][0]
    assert rec_["detail"]["reason"].startswith("origin established")


def test_a_precondition_that_is_MISSING_is_not_a_satisfied_one():
    """`None` is absent, and absent is never True."""
    ev = {n: True for n, _ in L.HALT_RESET_PRECONDITIONS}
    ev.pop("exposure_zero")
    with pytest.raises(L.ApplyRefused) as ei:
        apply(plan(), reset_operator_halt="x", halt_reset_evidence=ev)
    assert ei.value.detail["unmet"] == ["exposure_zero"]


def test_a_plan_carrying_the_halt_variable_is_REFUSED_not_published():
    """THE DEFECT THIS PINS. The approved digest covers `document` alone,
    so a `variables` mapping edited after approval used to ride along --
    and `L5_LIVE_HALT` was unconditionally exempt from both the forbidden
    and the writable check, so an injected empty value would have cleared
    the halt under a valid approval with none of the preconditions."""
    p = plan()
    p["variables"] = dict(p["variables"], L5_LIVE_HALT="")
    with pytest.raises(L.ApplyRefused) as ei:
        apply(p)
    assert ei.value.reason == "PLAN_VARIABLES_DISAGREE_WITH_DOCUMENT"
    assert ei.value.detail["extra_or_changed"] == ["L5_LIVE_HALT"]


def test_a_plan_carrying_the_shadow_lock_is_refused_too():
    p = plan()
    p["variables"] = dict(p["variables"], L5_SHADOW_ONLY="0")
    with pytest.raises(L.ApplyRefused) as ei:
        apply(p)
    assert ei.value.reason == "PLAN_VARIABLES_DISAGREE_WITH_DOCUMENT"


def test_a_plan_whose_market_was_swapped_publishes_the_DOCUMENTS_market():
    """The slug is derived from `series` + `window_starts`, both hashed, so
    editing the carried copy cannot redirect the order to another book."""
    p = plan()
    p["variables"] = dict(p["variables"], L5_TS_MARKET="some-other-market")
    with pytest.raises(L.ApplyRefused) as ei:
        apply(p)
    assert ei.value.reason == "PLAN_VARIABLES_DISAGREE_WITH_DOCUMENT"
    assert ei.value.detail["extra_or_changed"] == ["L5_TS_MARKET"]


def test_a_plan_with_NO_carried_variables_still_applies_from_the_document():
    """Deriving, not trusting: an empty carried mapping is not an error,
    because the document is the authority."""
    p = plan()
    p["variables"] = {}
    _, sent = apply(p)
    assert sent["L5_TS_MARKET"] == L.variables_for(p["document"])[
        "L5_TS_MARKET"]


def test_the_published_values_are_ALL_functions_of_the_hashed_document():
    p = plan()
    _, sent = apply(p)
    line = L.document_line(p["document"])
    assert sent["L5_CAMPAIGN_APPROVAL"] == line
    assert L.document_digest(json.loads(line)) == p["document_digest"]
    assert sent["L5_TS_EXPERIMENT_ID"] == p["document"]["nonce"]
    assert sent["L5_LIVE_AUTH_NOT_AFTER"].startswith(
        str(p["document"]["expires_utc"])[:16])


def test_the_halt_is_in_the_FORBIDDEN_set_and_not_in_the_writable_one():
    assert "L5_LIVE_HALT" in L.FORBIDDEN
    assert "L5_LIVE_HALT" not in L.WRITABLE


def test_the_railway_client_will_not_write_the_halt_under_the_launcher_set():
    from src import l5_railway as RW
    c = RW.RailwayClient(project_id="p", environment_id="e", service_id="s",
                         token="t", http=lambda *a, **k: {"data": {}},
                         allow_writes=True, writable=L.WRITABLE)
    with pytest.raises(RW.WriteRefused):
        c.set_variables({"L5_LIVE_HALT": ""})


def test_the_launch_state_machine_only_PASSES_a_reset_it_was_given():
    """It may carry an owner's reset to `apply_plan`; it may not originate
    one. So the parameter defaults to None, the only assignment is the
    pass-through, and no literal reason is ever synthesised here."""
    sig = inspect.signature(L.run_launch)
    assert sig.parameters["reset_operator_halt"].default is None
    assert sig.parameters["halt_reset_evidence"].default is None
    src = inspect.getsource(L.run_launch)
    assert "reset_operator_halt=reset_operator_halt" in src
    for line in src.splitlines():
        t = line.strip()
        if t.startswith("reset_operator_halt =") or \
                t.startswith("halt_reset_evidence ="):
            raise AssertionError("the sequence originates a reset: %s" % t)


def test_the_state_machine_passes_the_evidence_through_unaltered():
    """It must not fabricate a satisfied precondition either."""
    src = inspect.getsource(L.run_launch)
    assert "halt_reset_evidence=halt_reset_evidence" in src
    for name, _ in L.HALT_RESET_PRECONDITIONS:
        assert ('"%s": True' % name) not in src, name


def test_the_state_machine_reads_the_halt_from_the_VARIABLE_not_a_verdict():
    """A deployment predating the preflight row reports a verdict with no
    `local_operator_halt` at all, and trusting it is the 08:30Z failure."""
    src = inspect.getsource(L.run_launch)
    assert 'variables.get("L5_LIVE_HALT")' in src


def test_prepare_blocks_while_the_operator_halt_is_engaged():
    g = [x for x in L.prepare(
        heartbeat={}, deployment={}, expected_sha="a" * 40,
        window_start=dt.datetime(2026, 11, 10, 10, 0, tzinfo=UTC),
        now=dt.datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
        auth_preflight={}, operator_halted=True)["gates"]
        if x["gate"] == "local_operator_halt_clear"]
    assert len(g) == 1
    assert g[0]["ok"] is False and g[0]["engaged"] is True
