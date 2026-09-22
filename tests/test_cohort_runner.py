"""The persistent BTC15m cohort runner: one process, N consecutive contracts.

WHAT THIS REPLACES. Every live window so far was a GitHub Actions step that
waited for the 840:880 band, ran one contract and exited. CAP4-LIVE-2 met one
crossed tick at tte 332, withdrew, and the process ENDED -- with another BTC15m
contract fifteen minutes later and nothing alive to take it.

WHAT MUST NOT CHANGE, and every test here re-asserts some part of it: q=1, the
CAP-4 envelope, $4 per market, two working orders, one desired rung per
outcome, maker/post-only, the 30s retention floor, the frozen L5 controller,
authoritative reconciliation, and the bounded UNREADABLE hold. The runner is
orchestration. It holds no bound of its own.
"""
import asyncio
import json
import os

import pytest

from src.gemini_execution import cohort as CO
from src.gemini_execution import cohort_runner as CR
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import live_probe_spec as SPEC
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import ws_continuous as WC

BTC15M = "GEMI-BTC15M2609212300-UP"
NEXT15M = "GEMI-BTC15M2609212315-UP"
THIRD15M = "GEMI-BTC15M2609212330-UP"

BOOK = {"readable": True, "identity_consistent": True,
        "yes": {"bid": "0.35", "ask": "0.42"},
        "no": {"bid": "0.58", "ask": "0.65"}}


async def _READY(spec, **k):
    """A PASSING static readiness, injected. The readiness check itself has
    its own tests above; these ones are about the orchestration around it."""
    return {"ok": True, "verdict": "PASS", "why_not": [], "injected": True}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop(
    ).run_until_complete(coro)


def _cohort(tmp_path, cid="COHORT-TEST-1", budget=12):
    root = str(tmp_path / "cohorts")
    pred = tmp_path / "pred.jsonl"
    pred.write_text("", encoding="utf-8")
    CO.create(cid, budget, root=root, predecessor_ledger=str(pred),
              note="test")
    return root, cid


def _spec(cid, *, quote_band=None):
    import dataclasses
    return dataclasses.replace(SPEC.BTC15M_Q1_CAP4, live_cohort=cid,
                               quote_band=quote_band)


def _plan(tmp_path, root, cid, *, windows=3, loss="10.00", quote_band=None,
          chain=None):
    ids = tuple(chain) if chain is not None else (cid,) * windows
    return CR.CohortPlan(auth_id="OWNER-TEST",
                         spec=_spec(cid, quote_band=quote_band),
                         max_windows=windows, max_aggregate_loss_usd=loss,
                         cohort_ids=ids,
                         state_dir=str(tmp_path / "latch"),
                         out_dir=str(tmp_path / "out"), root=root)


def _chain(tmp_path, n, prefix="COHORT-TEST-W"):
    """n 12-slot ledgers over ONE common ancestor, as the real cohort is built.

    WHY A COMMON ANCESTOR RATHER THAN A LINKED LIST. `verify_predecessor`
    means "the ledger I recorded has not changed since I was created". If
    window 2 hashed window 1's ledger, then window 1 SPENDING ITS BUDGET --
    the entirely normal thing -- would flip window 2's provenance to
    tampered and stop the cohort. Every ledger therefore records the state of
    the world BEFORE the cohort began, which is a claim that stays true for
    the whole run and still detects a rewritten history.
    """
    root = str(tmp_path / "cohorts")
    pred = tmp_path / "pred.jsonl"
    pred.write_text("", encoding="utf-8")
    ids = []
    for i in range(1, n + 1):
        cid = "%s%02d" % (prefix, i)
        CO.create(cid, 12, root=root, predecessor_ledger=str(pred),
                  note="test")
        ids.append(cid)
    return root, tuple(ids)


@pytest.fixture(autouse=True)
def _outdir(tmp_path):
    os.makedirs(tmp_path / "out", exist_ok=True)
    os.makedirs(tmp_path / "latch", exist_ok=True)


# ============================================ 1. readiness has no clock

def test_static_readiness_passes_outside_the_entry_band(tmp_path,
                                                        monkeypatch):
    """THE WHOLE POINT OF 'STATIC'. The workflow gate answered 'is the tree
    ready' by first waiting for a contract to reach 840:880, so a ready tree
    failed for being dispatched at the wrong minute."""
    root, cid = _cohort(tmp_path)
    spec = _spec(cid)

    monkeypatch.setattr(WC, "authorization_state", lambda: {
        "authorized": True, "why_not": [], "required_auth_id": "OWNER-TEST"})
    monkeypatch.setattr(CR.BUNDLE, "state", lambda: {
        "authorized": True, "why_not": []})

    async def _terms():
        return {"hasAcceptedLatest": True, "verdict": "ACCEPTED"}
    monkeypatch.setattr(CR.MD, "terms_status", _terms)

    async def _pos():
        return {"ok": True, "rows": [], "status": 200}
    monkeypatch.setattr(CR.MD, "positions", _pos)

    # NOTHING about a contract clock is patched, because nothing reads one.
    def _boom(*a, **k):
        raise AssertionError("static readiness must not scan for a contract")
    monkeypatch.setattr(RC, "eligible_symbol", _boom)
    monkeypatch.setattr(RC, "await_entry_band", _boom)

    out = _run(CR.static_readiness(spec, auth_id="OWNER-TEST", root=root))
    assert out["verdict"] == "PASS", out["why_not"]
    assert out["tte_independent"] is True
    assert out["mutations_sent"] == 0
    assert out["checks"]["cohort"]["remaining"] == 12
    assert out["checks"]["spec"]["spec_sha"] == spec.spec_sha()


def test_static_readiness_refuses_a_tree_armed_for_another_grant(
        tmp_path, monkeypatch):
    root, cid = _cohort(tmp_path)
    monkeypatch.setattr(WC, "authorization_state", lambda: {
        "authorized": True, "why_not": [],
        "required_auth_id": "SOMEBODY-ELSE"})
    monkeypatch.setattr(CR.BUNDLE, "state", lambda: {
        "authorized": True, "why_not": []})

    async def _terms():
        return {"hasAcceptedLatest": True}
    monkeypatch.setattr(CR.MD, "terms_status", _terms)

    async def _pos():
        return {"ok": True, "rows": [], "status": 200}
    monkeypatch.setattr(CR.MD, "positions", _pos)

    out = _run(CR.static_readiness(_spec(cid), auth_id="OWNER-TEST",
                                   root=root))
    assert out["verdict"] == "FAIL"
    assert any("armed for" in w for w in out["why_not"])


def test_static_readiness_never_reads_an_unreadable_account_as_flat(
        tmp_path, monkeypatch):
    root, cid = _cohort(tmp_path)
    monkeypatch.setattr(WC, "authorization_state", lambda: {
        "authorized": True, "why_not": [], "required_auth_id": "OWNER-TEST"})
    monkeypatch.setattr(CR.BUNDLE, "state", lambda: {
        "authorized": True, "why_not": []})

    async def _terms():
        return {"hasAcceptedLatest": True}
    monkeypatch.setattr(CR.MD, "terms_status", _terms)

    async def _pos():
        return {"ok": False, "rows": None, "why": "503"}
    monkeypatch.setattr(CR.MD, "positions", _pos)

    out = _run(CR.static_readiness(_spec(cid), auth_id="OWNER-TEST",
                                   root=root))
    assert out["verdict"] == "FAIL"
    assert any("never a flat one" in w for w in out["why_not"])


# ================================ 2/3. arbitrary start, consecutive rolls

class FakeVenue:
    """A BTC15m grid that rolls. `tte` is whatever the test says it is."""

    def __init__(self, script):
        # script: list of (symbol, tte) the scanner sees, in order
        self.script = list(script)
        self.polls = 0

    def scan(self, band=None, cohort=None):
        self.polls += 1
        sym, tte = self.script[min(self.polls - 1, len(self.script) - 1)]
        lo, hi = (band or (0, 1e9))
        if sym is None or not (lo <= tte <= hi):
            return {"eligible": False, "considered": [{"tte_s": tte}],
                    "why": "outside"}
        return {"eligible": True,
                "chosen": {"symbol": sym, "tte_s": tte}}


def test_a_runner_started_mid_contract_waits_and_binds_the_next(tmp_path):
    """Requirement: the runner may start at ANY point in a contract. If the
    current one is no longer eligible it stays alive and takes the next."""
    root, cid = _cohort(tmp_path)
    v = FakeVenue([(BTC15M, 410.0),      # mid-contract: past the band
                   (BTC15M, 120.0),      # still past it
                   (NEXT15M, 870.0)])    # the NEXT contract opens

    slept = []

    async def _sleep(s):
        slept.append(s)

    out = _run(CR.await_contract(_spec(cid), sleep_fn=_sleep,
                                 scan_fn=v.scan, poll_s=1.0))
    assert out["found"] is True
    assert out["symbol"] == NEXT15M, "it must roll, not give up"
    assert out["tte_s"] == 870.0
    assert len(slept) == 2, "it waited across the contract boundary"


def test_discovery_excludes_the_contract_just_finished(tmp_path):
    """A window can end well above its own entry band -- a bound-exceeded
    withdrawal, an exhausted budget -- and the next poll would otherwise hand
    the same symbol straight back."""
    root, cid = _cohort(tmp_path)
    v = FakeVenue([(BTC15M, 870.0), (BTC15M, 860.0), (NEXT15M, 850.0)])

    async def _sleep(s):
        return None

    out = _run(CR.await_contract(_spec(cid), exclude=[BTC15M],
                                 sleep_fn=_sleep, scan_fn=v.scan, poll_s=1.0))
    assert out["symbol"] == NEXT15M


def test_discovery_gives_up_rather_than_idling_forever(tmp_path):
    root, cid = _cohort(tmp_path)
    v = FakeVenue([(None, 5.0)])
    t = {"now": 0.0}

    async def _sleep(s):
        t["now"] += s

    out = _run(CR.await_contract(_spec(cid), sleep_fn=_sleep, scan_fn=v.scan,
                                 poll_s=100.0, give_up_s=300.0,
                                 now_fn=lambda: t["now"]))
    assert out["found"] is False
    assert "two whole contract periods" in out["why"]


def test_three_consecutive_contracts_run_in_one_process(tmp_path):
    """NO WORKFLOW RESTART. One call, three contracts, three windows."""
    root, cid = _cohort(tmp_path)
    plan = _plan(tmp_path, root, cid, windows=3)
    seen = []

    async def _discover(spec, exclude=()):
        nxt = [s for s in (BTC15M, NEXT15M, THIRD15M)
               if s not in set(exclude)]
        return {"found": True, "symbol": nxt[0], "tte_s": 870.0}

    async def _window(plan, symbol, *, readiness, latch_held_by,
                      cohort_id=None):
        seen.append(symbol)
        return {"state": "STOPPED_FLAT_ORDERS",
                "window": {"symbol": symbol, "inventory":
                           {"total_spend_usd": "0", "n_fills": 0},
                           "orders_live_after_stop": 0,
                           "stop_why": "window withdrawn"}}

    async def _gate(symbol, **k):
        return {"ok": True, "verdict": "CLEAR", "open_orders_after": 0}

    rep = _run(CR.run_cohort(plan, readiness=True,
                             window_fn=_window, discover_fn=_discover,
                             gate_fn=_gate, readiness_fn=_READY,
                             latch_fn=lambda *a, **k: {"ok": True}))
    _patch_ready(rep)
    assert seen == [BTC15M, NEXT15M, THIRD15M]
    assert rep["state"] == "STOPPED_WINDOWS_EXHAUSTED"
    assert rep["cohort_state"]["windows_observed"] == 3
    assert rep["cohort_state"]["windows_traded"] == 0, \
        "a zero-fill contract is an OBSERVED window, not a traded one"


def _patch_ready(rep):
    """static_readiness runs for real in run_cohort; these tests stub the
    venue through monkeypatch where they care and otherwise assert on the
    orchestration. This helper just documents that the readiness result is
    part of the report."""
    assert "static_readiness" in rep


# ======================================== 4. the authorized quote interval

@pytest.mark.parametrize("band,tte,want", [
    (None, 870.0, 0),            # standing [0, 300]: silent up here
    (None, 250.0, 2),
    ((60.0, 880.0), 870.0, 2),   # the PROPOSED interval quotes there
    ((60.0, 880.0), 250.0, 2),
    ((60.0, 880.0), 50.0, 0),    # and terminal safety still bites
    ((60.0, 880.0), 20.0, 0),
])
def test_no_trade_outside_the_authorized_quote_interval(tmp_path, band, tte,
                                                        want):
    d = WC.Driver("A", BTC15M, ledger_path=str(tmp_path / ("l%s.jsonl" % tte)),
                  envelope=LE.LIVE_Q1_BTC15M_CAP4, quote_band=band)
    d.inv = CP.Inventory()
    d.guard = CP.WindowGuard(BTC15M, auth_id="A",
                             cohort_prefix=d._family_prefix)
    d.budget_consumed, d.state = 0, WC.RUNNING
    out = d.decide(tte, book=BOOK)
    assert len((out.get("turn") or {}).get("actions") or []) == want


def test_the_quote_interval_is_inside_the_spec_hash():
    """A behavioural change that did not move the hash readiness compares
    would be a silent arm. It moves it."""
    import dataclasses
    a = SPEC.BTC15M_Q1_CAP4
    b = SPEC.BTC15M_Q1_CAP4_W60_880
    assert a.quote_band is None and b.quote_band == (60.0, 880.0)
    assert a.spec_sha() != b.spec_sha()
    c = dataclasses.replace(b, quote_band=(90.0, 880.0))
    assert c.spec_sha() != b.spec_sha(), "any band change moves the hash"
    # and the reviewed identities did NOT move for a refactor
    assert a.spec_sha() == (
        "7fda21b1f989961664f55a3a033b7689758d1bd3471b14d46f7b70563aa8f862")
    assert SPEC.BTC15M_Q1.spec_sha() == (
        "d2f1f05d789387f52fc2e72cb4516342684c560c1b2484f54fe2a282c5e05a3c")


def test_a_quote_interval_may_not_open_below_the_withdraw_floor():
    """Terminal safety near settlement is not a parameter."""
    with pytest.raises(CP.ProbeStop) as e:
        CP.probe_params(action_size="1", quote_band=(10.0, 880.0))
    assert "withdraw floor" in str(e.value)


def test_the_proposed_band_changes_the_interval_and_nothing_else():
    """Pricing, rung count, sizing and aggressiveness are untouched."""
    base = CP.probe_params(action_size="1")
    band = CP.probe_params(action_size="1", quote_band=(60.0, 880.0))
    diffs = [f for f in CP.PR.FIELDS
             if base.params[f].value != band.params[f].value]
    assert diffs == ["TTE_policy"], diffs
    assert (SPEC.BTC15M_Q1_CAP4.envelope_obj.sha()
            == SPEC.BTC15M_Q1_CAP4_W60_880.envelope_obj.sha())


# ============================== 5. nothing leaks through a rollover

def test_orders_still_resting_on_the_old_contract_stop_the_cohort(tmp_path):
    async def _state(sym):
        return {"open_orders": [{"clientOrderId": "leftover"}]}

    async def _pos():
        return {"ok": True, "rows": []}

    g = _run(CR.rollover_gate(BTC15M, state_fn=_state, positions_fn=_pos))
    assert g["ok"] is False
    assert g["verdict"] == "ORDERS_REMAIN"
    assert "rather than binding a second contract" in g["why"]


def test_an_unreadable_order_list_at_rollover_fails_closed(tmp_path):
    async def _state(sym):
        return {"open_orders": None}

    async def _pos():
        return {"ok": True, "rows": []}

    g = _run(CR.rollover_gate(BTC15M, state_fn=_state, positions_fn=_pos))
    assert g["ok"] is False and g["verdict"] == "UNREADABLE"
    assert "never an empty one" in g["why"]


def test_position_uncertainty_at_rollover_fails_closed(tmp_path):
    async def _state(sym):
        return {"open_orders": []}

    async def _pos():
        return {"ok": False, "rows": None, "why": "503"}

    g = _run(CR.rollover_gate(BTC15M, state_fn=_state, positions_fn=_pos))
    assert g["ok"] is False and g["verdict"] == "POSITION_UNREADABLE"
    assert "flat is not assumed" in g["why"]


def test_a_refused_rollover_stops_the_cohort_rather_than_skipping(tmp_path):
    root, cid = _cohort(tmp_path)
    plan = _plan(tmp_path, root, cid, windows=5)
    seen = []

    async def _discover(spec, exclude=()):
        nxt = [s for s in (BTC15M, NEXT15M, THIRD15M)
               if s not in set(exclude)]
        return {"found": True, "symbol": nxt[0], "tte_s": 870.0}

    async def _window(plan, symbol, *, readiness, latch_held_by,
                      cohort_id=None):
        seen.append(symbol)
        return {"state": "STOPPED_FLAT_ORDERS",
                "window": {"inventory": {"total_spend_usd": "0",
                                         "n_fills": 0},
                           "orders_live_after_stop": 0}}

    async def _gate(symbol, **k):
        return {"ok": False, "verdict": "ORDERS_REMAIN",
                "why": "one order still listed"}

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_window,
                             discover_fn=_discover, gate_fn=_gate,
                             readiness_fn=_READY,
                             latch_fn=lambda *a, **k: {"ok": True}))
    assert seen == [BTC15M], "it stopped after the first, it did not skip on"
    assert rep["state"] == "STOPPED_ROLLOVER_REFUSED"


def test_each_window_builds_its_own_driver_and_inventory(tmp_path):
    """The structural reason contract N cannot contaminate N+1: there is no
    object shared between them. A fresh Driver means a fresh WorkingOrders,
    a fresh Inventory and a fresh WindowGuard bound to ONE symbol."""
    a = WC.Driver("A", BTC15M, ledger_path=str(tmp_path / "a.jsonl"),
                  envelope=LE.LIVE_Q1_BTC15M_CAP4)
    b = WC.Driver("A", NEXT15M, ledger_path=str(tmp_path / "a.jsonl"),
                  envelope=LE.LIVE_Q1_BTC15M_CAP4)
    assert a.working is not b.working
    assert a.inv is None and b.inv is None
    a.guard = CP.WindowGuard(BTC15M, auth_id="A", cohort_prefix="GEMI-BTC15M")
    with pytest.raises(CP.ProbeStop):
        a.guard.check_symbol(NEXT15M)


# ============================ 7. counters persist across restart

def test_cohort_counters_survive_a_restart(tmp_path):
    root, cid = _cohort(tmp_path)
    st = CR.load_state(cid, root=root, max_windows=10, max_loss_usd="10.00")
    st["windows_observed"] = 4
    st["windows_traded"] = 2
    st["aggregate_loss_usd"] = "3.17"
    st["symbols"] = [BTC15M]
    CR.save_state(st, root=root)

    again = CR.load_state(cid, root=root)
    assert again["windows_observed"] == 4
    assert again["aggregate_loss_usd"] == "3.17"
    assert again["symbols"] == [BTC15M]


def test_the_mutation_budget_is_not_reset_by_a_restart(tmp_path):
    """The authorization budget is the cohort's append-only ledger, and the
    Driver reads its consumed count on construction."""
    root, cid = _cohort(tmp_path, budget=12)
    led = CO.load(cid, root=root).ledger_path
    d = WC.Driver("A", BTC15M, ledger_path=led, max_actions=12,
                  envelope=LE.LIVE_Q1_BTC15M_CAP4)
    slot = d.ledger.attempt(WC.PLACE, {"client_order_id": "x"})
    d.ledger.resolve(slot, WC.ACCEPTED, {"status": 200})
    assert CO.state(cid, root=root)["consumed"] == 1

    # a brand-new process, same ledger
    d2 = WC.Driver("A", NEXT15M, ledger_path=led, max_actions=12,
                   envelope=LE.LIVE_Q1_BTC15M_CAP4)
    assert d2.ledger.consumed() == 1
    assert CO.state(cid, root=root)["remaining"] == 11


def test_an_already_stopped_cohort_is_not_resumed_automatically(tmp_path):
    root, cid = _cohort(tmp_path)
    st = CR.load_state(cid, root=root, max_windows=10, max_loss_usd="10.00")
    st["stopped"] = True
    st["stop_why"] = "AGGREGATE_LOSS: capped"
    CR.save_state(st, root=root)

    plan = _plan(tmp_path, root, cid)

    async def _boom(*a, **k):
        raise AssertionError("a stopped cohort must bind nothing")

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_boom,
                             discover_fn=_boom, gate_fn=_boom,
                             readiness_fn=_READY,
                             latch_fn=lambda *a, **k: {"ok": True}))
    assert rep["state"] in ("STOPPED_ALREADY", "STOPPED_NOT_READY")


# ================================ the aggregate loss stop

def test_the_aggregate_loss_cap_stops_the_cohort(tmp_path):
    root, cid = _cohort(tmp_path)
    plan = _plan(tmp_path, root, cid, windows=10, loss="3.00")
    seen = []

    async def _discover(spec, exclude=()):
        nxt = [s for s in (BTC15M, NEXT15M, THIRD15M)
               if s not in set(exclude)]
        return {"found": True, "symbol": nxt[0], "tte_s": 870.0}

    async def _window(plan, symbol, *, readiness, latch_held_by,
                      cohort_id=None):
        seen.append(symbol)
        return {"state": "STOPPED_FLAT_ORDERS",
                "window": {"inventory": {"total_spend_usd": "2.00",
                                         "n_fills": 2},
                           "orders_live_after_stop": 0}}

    async def _gate(symbol, **k):
        return {"ok": True, "verdict": "CLEAR"}

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_window,
                             discover_fn=_discover, gate_fn=_gate,
                             readiness_fn=_READY,
                             latch_fn=lambda *a, **k: {"ok": True}))
    assert len(seen) == 2, "2.00 + 2.00 >= 3.00, checked BEFORE binding a 3rd"
    assert rep["state"] == "STOPPED_AGGREGATE_LOSS"
    assert rep["cohort_state"]["windows_traded"] == 2


def test_the_loss_measure_never_credits_unconfirmed_economics():
    """Matched inventory is our own bookkeeping until the venue's position
    representation is resolved; a stop that netted it off would trust exactly
    what this programme refuses to trust."""
    econ = CR.window_loss_usd({"window": {"inventory": {
        "total_spend_usd": "1.47", "n_fills": 3, "matched_qty": "1",
        "directional_residual": "1"}}})
    assert econ["reconstructed_loss_usd"] == "1.47"
    assert "no matched-pair offset" in econ["basis"]
    assert "attributable cash movement" in econ["not_realized"]


# ============================ 8. the bounded primitive is untouched

def test_q_and_the_working_order_cap_are_invariant_under_the_runner():
    e = SPEC.BTC15M_Q1_CAP4.envelope_obj
    assert str(e.action_size) == "1"
    assert int(e.max_working_orders) == 2
    assert str(e.max_committed_usd) == "4.00"
    assert str(e.max_residual_usd) == "4.00"
    assert int(e.max_concurrent_markets) == 1
    # the runner holds no bound of its own: it never touches the envelope
    src = open("src/gemini_execution/cohort_runner.py").read()
    for forbidden in ("max_committed_usd =", "max_working_orders =",
                      "action_size =", "max_residual_usd ="):
        assert forbidden not in src, forbidden


def test_one_desired_rung_per_outcome_still_bounds_the_surface(tmp_path):
    """policy authority, not lifecycle capacity -- unchanged by the runner."""
    from src.gemini_execution import live_readiness as LR
    assert LR.policy_max_rungs(SPEC.BTC15M_Q1_CAP4) == 2
    assert LR.lifecycle_max_rungs(SPEC.BTC15M_Q1_CAP4) == 2


def test_the_runner_runs_the_same_window_body_as_the_launcher():
    """ONE implementation of the gate order. A second copy is how a runner
    and a launcher drift apart, and this programme has paid for that."""
    src = open("src/gemini_execution/cohort_runner.py").read()
    assert "RC.run_one_window(" in src
    launcher = open("src/gemini_execution/run_continuous.py").read()
    assert launcher.count("async def run_one_window(") == 1
    assert "return await run_one_window(" in launcher


# ============================ 9. terminal cleanup

def test_a_clear_rollover_requires_zero_controllable_open_orders(tmp_path):
    async def _state(sym):
        return {"open_orders": []}

    async def _pos():
        return {"ok": True, "rows": []}

    g = _run(CR.rollover_gate(BTC15M, state_fn=_state, positions_fn=_pos))
    assert g["ok"] is True and g["verdict"] == "CLEAR"
    assert g["open_orders_after"] == 0
    assert g["position_rows_for_symbol"] == 0


def test_a_position_left_on_the_old_contract_is_preserved_not_netted(
        tmp_path):
    async def _state(sym):
        return {"open_orders": []}

    async def _pos():
        return {"ok": True, "rows": [
            {"instrumentSymbol": BTC15M, "outcome": "yes",
             "totalQuantity": "1"}]}

    g = _run(CR.rollover_gate(BTC15M, state_fn=_state, positions_fn=_pos))
    assert g["ok"] is True, "a settling position does not block the next roll"
    assert g["position_rows_for_symbol"] == 1
    assert g["carried_position"]
    assert "never netted into the next one" in g["note"]


# ============================ 6. passive spread instrumentation

def test_spread_placement_is_a_pure_function_of_the_report():
    rep = {
        "decisions": [{
            "cycle": 1, "utc": "u", "tte_s": 700.0,
            "book_state": {"presented": {
                "readable": True,
                "yes": {"bid": "0.30", "ask": "0.53"},
                "no": {"bid": "0.47", "ask": "0.70"}}},
            "actions": [{"leg": "YES", "price": "0.31", "qty": "1",
                         "action": "OPEN"}],
        }],
        "window": {"order_events": [
            {"kind": "FILL", "leg": "YES", "price": "0.31", "qty": "1",
             "rest_s": 12.5, "utc": "v"}]},
    }
    rows = CR.spread_placement(rep)
    assert len(rows) == 1
    r = rows[0]
    assert r["spread"] == "0.23", "the wide book this exists to measure"
    assert r["from_bid"] == "0.01"
    assert r["from_ask"] == "-0.22"
    assert r["from_mid"] == "-0.105"
    assert r["inside_spread"] is True
    assert r["filled"] is True and r["rest_s"] == 12.5


def test_spread_placement_records_an_unreadable_book_rather_than_skipping():
    rows = CR.spread_placement({"decisions": [{
        "cycle": 9, "tte_s": 300.0,
        "book_state": {"presented": {"readable": False,
                                     "why": "CROSSED book"}}}]})
    assert rows == [{"cycle": 9, "tte_s": 300.0, "book": "UNREADABLE",
                     "why": "CROSSED book"}]


def _executable_source(path):
    """The module with every comment AND docstring removed.

    THE PROSE-VS-CODE TRAP, for the fifth time in this programme. A test that
    greps a module for a forbidden token matches the explanatory comment that
    says the token is forbidden. Stripping `#` lines is not enough: a
    docstring saying "bid improvement is a separate question" reads exactly
    like code that does bid improvement. So this removes both, by parsing.
    """
    import ast as _ast
    tree = _ast.parse(open(path).read())
    for node in _ast.walk(tree):
        if not isinstance(node, (_ast.Module, _ast.ClassDef,
                                 _ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], _ast.Expr)
                and isinstance(body[0].value, _ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [_ast.Pass()]
    return _ast.unparse(_ast.fix_missing_locations(tree))


def test_the_runner_adds_no_midpoint_or_bid_improvement(tmp_path):
    """Requirement: instrument the spread, do not act on it yet."""
    code = _executable_source("src/gemini_execution/cohort_runner.py")
    for forbidden in ("improve", "midpoint", "desired_price ="):
        assert forbidden not in code, forbidden
    # and it never constructs a price at all
    assert "price=" not in code.replace('"price": None', "")


# ================== the 12-per-window / 120-per-cohort budget chain

def test_the_chain_gives_twelve_per_window_and_the_sum_across_the_cohort(
        tmp_path):
    """ONE shared ledger could not express this grant. The Driver's budget is
    `max_actions - consumed(ledger)`, so on one ledger window 2 would start
    with whatever window 1 left and the per-window 12 would decay away."""
    root, ids = _chain(tmp_path, 10)
    plan = _plan(tmp_path, root, ids[0], windows=10, chain=ids)
    b = plan.action_budget(root=root)
    assert b["per_window"] == 12
    assert b["total_budget"] == 120
    assert b["total_remaining"] == 120
    assert b["all_readable"] and b["all_intact"]
    assert plan.as_dict()["max_cohort_actions"] == 120
    # THE NEXT WINDOW BINDS THE FIRST UNSPENT LEDGER, not cohort_ids[i]. A
    # cohort that stopped and resumed has already spent some, and indexing by
    # window number would re-enter a spent one or skip a fresh one depending
    # on which way the count drifted.
    assert plan.ledger_for(0, root=root) == ids[0]
    assert plan.ledger_for(7, root=root) == ids[0], \
        "nothing is spent yet, so the first unspent is still W01"

    # spend W01 completely; the next window must move to W02 and no further
    d = WC.Driver("A", BTC15M,
                  ledger_path=CO.load(ids[0], root=root).ledger_path,
                  max_actions=12, envelope=LE.LIVE_Q1_BTC15M_CAP4)
    for i in range(12):
        slot = d.ledger.attempt(WC.PLACE, {"client_order_id": "x%d" % i})
        d.ledger.resolve(slot, WC.ACCEPTED, {"status": 200})
    assert plan.ledger_for(0, root=root) == ids[1]
    assert plan.action_budget(root=root)["total_remaining"] == 108


def test_spending_one_window_does_not_reduce_the_next_windows_budget(
        tmp_path):
    root, ids = _chain(tmp_path, 3)
    plan = _plan(tmp_path, root, ids[0], windows=3, chain=ids)
    led = CO.load(ids[0], root=root).ledger_path
    d = WC.Driver("A", BTC15M, ledger_path=led, max_actions=12,
                  envelope=LE.LIVE_Q1_BTC15M_CAP4)
    for i in range(5):
        slot = d.ledger.attempt(WC.PLACE, {"client_order_id": "x%d" % i})
        d.ledger.resolve(slot, WC.ACCEPTED, {"status": 200})

    assert CO.state(ids[0], root=root)["remaining"] == 7
    assert CO.state(ids[1], root=root)["remaining"] == 12, \
        "window 2 still has its own full 12"
    assert plan.action_budget(root=root)["total_remaining"] == 31

    d2 = WC.Driver("A", NEXT15M,
                   ledger_path=CO.load(ids[1], root=root).ledger_path,
                   max_actions=12, envelope=LE.LIVE_Q1_BTC15M_CAP4)
    assert d2.ledger.consumed() == 0
    assert d2.budget_left() == 12


def test_every_ledger_hashes_the_state_the_cohort_began_from(tmp_path):
    """Provenance that survives the cohort doing its job.

    A linked list would be wrong here: window 1 spending its budget is the
    normal case, and under a linked list it would flip window 2's provenance
    to tampered and stop a cohort the owner authorized.
    """
    root, ids = _chain(tmp_path, 3)
    for cid in ids:
        assert CO.state(cid, root=root)["predecessor"]["intact"] is True

    # SPENDING window 1 must not disturb anybody's provenance
    d = WC.Driver("A", BTC15M,
                  ledger_path=CO.load(ids[0], root=root).ledger_path,
                  max_actions=12, envelope=LE.LIVE_Q1_BTC15M_CAP4)
    slot = d.ledger.attempt(WC.PLACE, {"client_order_id": "x"})
    d.ledger.resolve(slot, WC.ACCEPTED, {"status": 200})
    for cid in ids:
        assert CO.state(cid, root=root)["predecessor"]["intact"] is True, cid

    # REWRITING THE PRE-COHORT HISTORY must be caught by all of them
    with open(tmp_path / "pred.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"kind":"FORGED"}\n')
    for cid in ids:
        assert CO.state(cid, root=root)["predecessor"]["intact"] is False, cid


def test_a_tampered_chain_stops_the_cohort_before_any_window(tmp_path):
    root, ids = _chain(tmp_path, 3)
    plan = _plan(tmp_path, root, ids[0], windows=3, chain=ids)
    with open(tmp_path / "pred.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"kind":"FORGED"}\n')

    async def _boom(*a, **k):
        raise AssertionError("no window may run on a tampered chain")

    async def _discover(spec, exclude=()):
        return {"found": True, "symbol": BTC15M, "tte_s": 870.0}

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_boom,
                             discover_fn=_discover, gate_fn=_boom,
                             readiness_fn=_READY,
                             latch_fn=lambda *a, **k: {"ok": True}))
    assert rep["state"] == "STOPPED_LEDGER_UNREADABLE"


def test_an_already_spent_window_ledger_is_skipped_not_re_entered(tmp_path):
    """Restart safety: a cohort that died mid-run must not re-spend a ledger
    a previous process already used."""
    root, ids = _chain(tmp_path, 3)
    plan = _plan(tmp_path, root, ids[0], windows=3, chain=ids)
    led = CO.load(ids[0], root=root).ledger_path
    d = WC.Driver("A", BTC15M, ledger_path=led, max_actions=12,
                  envelope=LE.LIVE_Q1_BTC15M_CAP4)
    for i in range(12):
        slot = d.ledger.attempt(WC.PLACE, {"client_order_id": "x%d" % i})
        d.ledger.resolve(slot, WC.ACCEPTED, {"status": 200})
    assert CO.state(ids[0], root=root)["remaining"] == 0

    bound = []

    async def _discover(spec, exclude=()):
        return {"found": True, "symbol": BTC15M, "tte_s": 870.0}

    async def _window(plan, symbol, *, readiness, latch_held_by,
                      cohort_id=None):
        bound.append(cohort_id)
        # A REAL WINDOW SPENDS ITS LEDGER, so the stub does too -- otherwise
        # `first unspent` would correctly hand back the same ledger forever
        # and the test would be asserting against a fiction.
        dd = WC.Driver("A", symbol,
                       ledger_path=CO.load(cohort_id, root=root).ledger_path,
                       max_actions=12, envelope=LE.LIVE_Q1_BTC15M_CAP4)
        for j in range(12):
            sl = dd.ledger.attempt(WC.PLACE, {"client_order_id": "w%d" % j})
            dd.ledger.resolve(sl, WC.ACCEPTED, {"status": 200})
        return {"state": "STOPPED_FLAT_ORDERS",
                "window": {"inventory": {"total_spend_usd": "0",
                                         "n_fills": 0},
                           "orders_live_after_stop": 0}}

    async def _gate(symbol, **k):
        return {"ok": True, "verdict": "CLEAR"}

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_window,
                             discover_fn=_discover, gate_fn=_gate,
                             readiness_fn=_READY,
                             latch_fn=lambda *a, **k: {"ok": True}))
    assert ids[0] not in bound, "a spent ledger was re-entered"
    assert bound == [ids[1], ids[2]], (
        "and a spent ledger costs no WINDOW: three windows were authorized, "
        "W01 was already spent, so windows 1..3 bind W02 and W03 and then "
        "run out of ledgers -- they do not burn a window slot on the skip")


# ============ the arm is the STEP's property, not the TREE's

def test_static_readiness_asserts_the_tree_and_only_observes_the_arm(
        tmp_path, monkeypatch):
    """RUN 35670074043. Every check PASSed -- spec, bundle, family, cohort,
    terms, positions -- and the single why_not was "transport arm:
    GEMINI_CANARY_LIVE is not YES". That is the read-only step being
    read-only, not the tree being unready, and folding it in made this check
    unable to PASS from anywhere except an armed step."""
    root, ids = _chain(tmp_path, 2)
    monkeypatch.setattr(WC, "authorization_state", lambda: {
        "authorized": False,
        "required_auth_id": "OWNER-TEST",
        "arming": {"armed": False,
                   "why_not": "GEMINI_CANARY_LIVE is not YES"},
        "why_not": ["transport arm: GEMINI_CANARY_LIVE is not YES"]})
    monkeypatch.setattr(CR.BUNDLE, "state", lambda: {
        "authorized": True, "why_not": []})

    async def _terms():
        return {"hasAcceptedLatest": True}
    monkeypatch.setattr(CR.MD, "terms_status", _terms)

    async def _pos():
        return {"ok": True, "rows": [], "status": 200}
    monkeypatch.setattr(CR.MD, "positions", _pos)

    out = _run(CR.static_readiness(_spec(ids[0]), auth_id="OWNER-TEST",
                                   root=root, chain=ids))
    assert out["verdict"] == "PASS", out["why_not"]
    a = out["checks"]["authorization"]
    assert a["tree_authorized"] is True
    assert a["transport_armed"] is False, "and it SAYS the arm is absent"
    assert "property of the STEP" in a["transport_note"]


def test_a_tree_whose_executable_moved_still_fails_static_readiness(
        tmp_path, monkeypatch):
    """The half that IS a tree property stays asserted."""
    root, ids = _chain(tmp_path, 2)
    monkeypatch.setattr(WC, "authorization_state", lambda: {
        "authorized": False, "required_auth_id": "OWNER-TEST",
        "arming": {"armed": True},
        "why_not": ["AUTHORIZED_SHA names a different executable than the "
                    "one running; this file changed after it was reviewed."]})
    monkeypatch.setattr(CR.BUNDLE, "state", lambda: {
        "authorized": True, "why_not": []})

    async def _terms():
        return {"hasAcceptedLatest": True}
    monkeypatch.setattr(CR.MD, "terms_status", _terms)

    async def _pos():
        return {"ok": True, "rows": [], "status": 200}
    monkeypatch.setattr(CR.MD, "positions", _pos)

    out = _run(CR.static_readiness(_spec(ids[0]), auth_id="OWNER-TEST",
                                   root=root, chain=ids))
    assert out["verdict"] == "FAIL"
    assert any("changed after it was reviewed" in w for w in out["why_not"])


def test_the_driver_still_requires_the_arm_on_every_mutation(tmp_path):
    """Nothing above loosened the thing that actually stops a send."""
    import inspect
    src = inspect.getsource(WC.Driver.place)
    assert "require_authorization" in src or "authorized()" in src, src[:400]
    # and the arm is still one of authorization_state's OWN refusals -- the
    # readiness check merely declines to treat it as a tree property.
    body = inspect.getsource(WC.authorization_state)
    assert 'reasons.append("transport arm:' in body


# ============ THE DEFECT THAT COST COHORT-1 ITS FIRST DISPATCH

def _cli_plan(monkeypatch, root, spec, **kw):
    """Build the plan THE WAY main() DOES, by driving main() and catching it.

    The point of going through main() rather than constructing a CohortPlan
    here is that constructing one here is exactly what every other test did,
    and that is why none of them caught it: `cohort_ids` was on the dataclass,
    ten ledgers existed, five tests covered them, and the CLI never passed the
    field. A test that builds the object itself cannot see a caller that
    forgets to.
    """
    seen = {}

    async def _capture(plan, **k):
        seen["plan"] = plan
        return {"state": "STOPPED_WINDOWS_EXHAUSTED", "cohort_state": {}}

    monkeypatch.setattr(CR, "run_cohort", _capture)
    argv = ["--auth-id", "OWNER-TEST", "--spec", spec.spec_id,
            "--max-windows", "10", "--max-loss-usd", "10.00",
            "--root", root, "--out", str(kw.get("out", "/dev/null"))]
    CR.main(argv)
    return seen["plan"]


def test_the_CLI_built_plan_actually_carries_the_whole_ledger_chain(
        tmp_path, monkeypatch):
    """COHORT-1 run 35671834127: `cohort_ids` defaulted to (), both
    `ledger_for` and `action_budget` fell back to the single head ledger, and
    two windows ran on ONE 12-slot budget before stopping at 12/12."""
    import dataclasses
    root, ids = _chain(tmp_path, 10, prefix="COHORT-CLI-W")
    spec = dataclasses.replace(SPEC.BTC15M_Q1_CAP4_W60_880,
                               live_cohort=ids[0])
    monkeypatch.setitem(SPEC.SPECS, spec.spec_id, spec)

    plan = _cli_plan(monkeypatch, root, spec, out=tmp_path / "r.json")

    assert plan.cohort_ids == ids, "the CLI dropped the chain"
    assert len(plan.cohort_ids) == 10
    b = plan.action_budget(root=root)
    assert b["total_budget"] == 120, "12 here is the COHORT-1 defect"
    assert b["total_remaining"] == 120
    assert b["per_window"] == 12


def test_discover_chain_resolves_the_head_to_its_whole_chain(tmp_path):
    root, ids = _chain(tmp_path, 10, prefix="COHORT-DISC-W")
    import dataclasses
    spec = dataclasses.replace(SPEC.BTC15M_Q1_CAP4_W60_880,
                               live_cohort=ids[0])
    assert CR.discover_chain(spec, root=root) == ids
    # a short grant yields a short chain, not a padded one
    (tmp_path / "b").mkdir(exist_ok=True)
    root2, ids2 = _chain(tmp_path / "b", 3, prefix="COHORT-SHORT-W")
    spec2 = dataclasses.replace(spec, live_cohort=ids2[0])
    assert CR.discover_chain(spec2, root=root2) == ids2


def test_a_head_that_is_not_part_of_a_chain_yields_just_itself(tmp_path):
    import dataclasses
    root, cid = _cohort(tmp_path, cid="COHORT-SOLO")
    spec = dataclasses.replace(SPEC.BTC15M_Q1_CAP4_W60_880, live_cohort=cid)
    assert CR.discover_chain(spec, root=root) == (cid,)


# ============ evidence survives every exit

def test_an_authorized_stop_is_an_outcome_not_a_job_failure(
        tmp_path, monkeypatch):
    """Returning 1 for every stop but WINDOWS_EXHAUSTED turned COHORT-1's
    clean budget stop into a red step, and the evidence commit -- conditioned
    on the job succeeding -- was skipped. The record died with the runner."""
    assert "STOPPED_MUTATION_BUDGET" in CR.AUTHORIZED_STOPS
    assert "STOPPED_AGGREGATE_LOSS" in CR.AUTHORIZED_STOPS
    assert "STOPPED_WINDOWS_EXHAUSTED" in CR.AUTHORIZED_STOPS
    # and a genuine mechanical failure is NOT one
    for bad in ("STOPPED_ROLLOVER_REFUSED", "STOPPED_LEDGER_UNREADABLE",
                "STOPPED_NOT_READY", "STOPPED_RUNNER_ERROR"):
        assert bad not in CR.AUTHORIZED_STOPS, bad


@pytest.mark.parametrize("state,rc", [
    ("STOPPED_MUTATION_BUDGET", 0),
    ("STOPPED_WINDOWS_EXHAUSTED", 0),
    ("STOPPED_AGGREGATE_LOSS", 0),
    ("STOPPED_ROLLOVER_REFUSED", 1),
    ("STOPPED_LEDGER_UNREADABLE", 1),
])
def test_the_exit_code_distinguishes_an_authorized_stop_from_a_failure(
        tmp_path, monkeypatch, state, rc):
    import dataclasses
    root, ids = _chain(tmp_path, 2, prefix="COHORT-RC-W")
    spec = dataclasses.replace(SPEC.BTC15M_Q1_CAP4_W60_880,
                               live_cohort=ids[0])
    monkeypatch.setitem(SPEC.SPECS, spec.spec_id, spec)
    out = tmp_path / "rc.json"

    async def _stub(plan, **k):
        return {"state": state, "why": "x", "cohort_state": {}}
    monkeypatch.setattr(CR, "run_cohort", _stub)

    got = CR.main(["--auth-id", "OWNER-TEST", "--spec", spec.spec_id,
                   "--max-windows", "2", "--max-loss-usd", "10.00",
                   "--root", root, "--out", str(out)])
    assert got == rc
    assert out.exists(), "the report must be written on EVERY outcome"
    assert json.loads(out.read_text())["state"] == state


def test_the_report_survives_an_exception_inside_the_runner(
        tmp_path, monkeypatch):
    """A report written only on the happy path is absent exactly when it
    matters most."""
    import dataclasses
    root, ids = _chain(tmp_path, 2, prefix="COHORT-EXC-W")
    spec = dataclasses.replace(SPEC.BTC15M_Q1_CAP4_W60_880,
                               live_cohort=ids[0])
    monkeypatch.setitem(SPEC.SPECS, spec.spec_id, spec)
    out = tmp_path / "exc.json"

    async def _boom(plan, **k):
        raise RuntimeError("the runner died mid-cohort")
    monkeypatch.setattr(CR, "run_cohort", _boom)

    with pytest.raises(RuntimeError):
        CR.main(["--auth-id", "OWNER-TEST", "--spec", spec.spec_id,
                 "--max-windows", "2", "--max-loss-usd", "10.00",
                 "--root", root, "--out", str(out)])
    assert out.exists()
    d = json.loads(out.read_text())
    assert d["state"] == "STOPPED_RUNNER_ERROR"
    assert "the runner died mid-cohort" in d["runner_error"]
    assert d["plan"]["cohort_ids"] == list(ids), \
        "and it still names the chain it was running"


def test_the_report_survives_a_cancellation(tmp_path, monkeypatch):
    """A cancelled or timed-out job raises BaseException, not Exception."""
    import dataclasses
    root, ids = _chain(tmp_path, 2, prefix="COHORT-CAN-W")
    spec = dataclasses.replace(SPEC.BTC15M_Q1_CAP4_W60_880,
                               live_cohort=ids[0])
    monkeypatch.setitem(SPEC.SPECS, spec.spec_id, spec)
    out = tmp_path / "can.json"

    async def _killed(plan, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(CR, "run_cohort", _killed)

    with pytest.raises(KeyboardInterrupt):
        CR.main(["--auth-id", "OWNER-TEST", "--spec", spec.spec_id,
                 "--max-windows", "2", "--max-loss-usd", "10.00",
                 "--root", root, "--out", str(out)])
    assert out.exists()
    assert json.loads(out.read_text())["state"] == "STOPPED_RUNNER_ERROR"


def test_the_cohort_state_file_is_written_before_the_process_can_die(
        tmp_path):
    """The per-window counters are fsynced after EVERY window, not at the
    end: a cohort that dies on window 7 must not read as never having run."""
    import inspect
    src = inspect.getsource(CR.run_cohort)
    i = src.index("st[\"windows_observed\"] += 1")
    j = src.index("save_state(st, root=plan.root)", i)
    k = src.index("gate(symbol)", i)
    assert i < j < k, ("the counters must be saved after the window and "
                       "BEFORE the rollover gate that can stop the cohort")
    assert "os.fsync" in inspect.getsource(CR.save_state)


# ============ COHORT-1's recovery: the tombstone must hold

COHORT1 = "COHORT-2026-09-21-BTC15M-COHORT1-W01"


def _cohort1_chain():
    return CR.discover_chain(SPEC.BTC15M_Q1_CAP4_W60_880)


def test_cohort1_W01_is_permanently_spent_and_reconstructs_no_trade():
    """Run 35671834127 really spent these twelve slots across two BTC15m
    windows and the rows died with the runner. They are reserved so the
    budget cannot be spent twice, and they invent nothing."""
    st = CO.state(COHORT1)
    assert (st["consumed"], st["budget"], st["remaining"]) == (12, 12, 0)

    rows = [json.loads(l) for l in
            open(CO.load(COHORT1).ledger_path) if l.strip()]
    assert len(rows) == 12
    for r in rows:
        assert r["outcome"] == "TOMBSTONE_UNRECOVERABLE"
        assert r["recovery"] is True
        assert r["source_run"] == "35671834127"
        assert r["symbol"] == "UNRECOVERABLE"
        # NOTHING is reconstructed: no side, price, size or order id
        for fabricated in ("price", "qty", "quantity", "leg", "side",
                           "client_order_id", "venue_order_id"):
            assert fabricated not in r, fabricated


def test_cohort1_carries_its_observed_facts_without_inventing_details():
    st = CR.load_state(COHORT1)
    assert st["windows_observed"] == 2 and st["windows_traded"] == 2
    assert st["aggregate_loss_usd"] == "3.77"
    rec = st["recovery"]
    assert rec["tombstoned_slots"] == 12
    assert rec["aggregate_loss_carried_forward_usd"] == "3.77"
    assert rec["remaining_loss_budget_usd"] == "6.23"
    w = st["windows"][0]
    assert w["recovered"] is True
    assert "NOT REALIZED" in w["labels"]
    # the unknowns are NAMED as unknown rather than filled in
    assert any("bound symbols" in u for u in w["what_is_NOT_known"])
    assert any("ACK, fill, cancel" in u for u in w["what_is_NOT_known"])


def test_cohort1_resumes_on_W02_with_108_slots_and_623_of_loss_budget():
    ids = _cohort1_chain()
    assert len(ids) == 10
    plan = CR.CohortPlan(
        auth_id="OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-COHORT-1",
        spec=SPEC.BTC15M_Q1_CAP4_W60_880, max_windows=10,
        max_aggregate_loss_usd="10.00", cohort_ids=ids)
    assert plan.ledger_for(0) == ids[1], "W01 must never be re-entered"
    b = plan.action_budget()
    assert (b["total_budget"], b["total_consumed"], b["total_remaining"]) == (
        120, 12, 108)
    st = CR.load_state(plan.head)
    from decimal import Decimal as D
    assert D("10.00") - D(st["aggregate_loss_usd"]) == D("6.23")
    assert plan.max_windows - st["windows_observed"] == 8
    for cid in ids[1:]:
        s = CO.state(cid)
        assert (s["consumed"], s["remaining"]) == (0, 12), cid
        assert s["predecessor"]["intact"] is True, cid


# ============ resuming a cohort is not spending a second one

def _spent_latch(tmp_path, auth="OWNER-TEST"):
    from src.gemini_execution import latch as LATCH
    d = tmp_path / "latch"
    d.mkdir(exist_ok=True)
    LATCH.consume(auth, {"intent_id": "cohort-x", "symbol": "GEMI-BTC15M"},
                  str(d), durable_check=lambda a: False)
    return str(d)


def _resume_plan(tmp_path, root, ids, *, state_dir, windows=10):
    import dataclasses
    spec = dataclasses.replace(SPEC.BTC15M_Q1_CAP4_W60_880,
                               live_cohort=ids[0])
    return CR.CohortPlan(auth_id="OWNER-TEST", spec=spec,
                         max_windows=windows, max_aggregate_loss_usd="10.00",
                         cohort_ids=ids, state_dir=state_dir,
                         out_dir=str(tmp_path / "out"), root=root)


def test_a_partly_observed_cohort_continues_under_its_existing_latch(
        tmp_path):
    """COHORT-1 spent its latch, ran 2 of 10 authorized windows and stopped.
    Resuming must neither spend a second latch -- which would be a lie about
    how many cohorts the grant bought -- nor be refused as a re-run."""
    root, ids = _chain(tmp_path, 10, prefix="COHORT-RES-W")
    sd = _spent_latch(tmp_path)
    plan = _resume_plan(tmp_path, root, ids, state_dir=sd)

    st = CR.load_state(ids[0], root=root, max_windows=10,
                       max_loss_usd="10.00")
    st.update(windows_observed=2, windows_traded=2,
              aggregate_loss_usd="3.77")
    CR.save_state(st, root=root)

    got = _run(CR._consume_cohort_latch(plan, st))
    assert got["ok"] is True
    assert got["consumed"] is False, "no second latch was spent"
    assert got["resumed"] is True
    assert got["windows_observed"] == 2 and got["windows_remaining"] == 8
    assert "No second latch is spent" in got["why"]


def test_a_fresh_cohort_under_a_spent_grant_is_still_refused(tmp_path):
    """The thing the latch exists to stop is unchanged."""
    root, ids = _chain(tmp_path, 10, prefix="COHORT-FRESH-W")
    sd = _spent_latch(tmp_path)
    plan = _resume_plan(tmp_path, root, ids, state_dir=sd)
    st = CR.load_state(ids[0], root=root, max_windows=10,
                       max_loss_usd="10.00")     # windows_observed == 0
    got = _run(CR._consume_cohort_latch(plan, st))
    assert got["ok"] is False
    assert "never a re-run" in got["why"]


def test_a_stopped_cohort_is_not_resumed_by_the_latch_path(tmp_path):
    root, ids = _chain(tmp_path, 10, prefix="COHORT-STOP-W")
    sd = _spent_latch(tmp_path)
    plan = _resume_plan(tmp_path, root, ids, state_dir=sd)
    st = CR.load_state(ids[0], root=root, max_windows=10,
                       max_loss_usd="10.00")
    st.update(windows_observed=2, stopped=True, stop_why="AGGREGATE_LOSS: x")
    got = _run(CR._consume_cohort_latch(plan, st))
    assert got["ok"] is False and "STOPPED" in got["why"]


def test_a_cohort_at_its_window_count_cannot_continue(tmp_path):
    root, ids = _chain(tmp_path, 10, prefix="COHORT-FULL-W")
    sd = _spent_latch(tmp_path)
    plan = _resume_plan(tmp_path, root, ids, state_dir=sd, windows=10)
    st = CR.load_state(ids[0], root=root, max_windows=10,
                       max_loss_usd="10.00")
    st.update(windows_observed=10)
    got = _run(CR._consume_cohort_latch(plan, st))
    assert got["ok"] is False
    assert "10 of 10 windows observed" in got["why"]
