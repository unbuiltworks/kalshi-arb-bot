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
from decimal import Decimal as D

import pytest

from src.gemini_execution import cohort as CO
from src.gemini_execution import cohort_economics as CE
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


async def _ok_gate(symbol, **k):
    return {"ok": True, "verdict": "CLEAR"}


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
                      cohort_id=None, status=None):
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
                      cohort_id=None, status=None):
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
                      cohort_id=None, status=None):
        seen.append(symbol)
        # 2.50 out and ONE winning contract back: a settlement-inclusive
        # loss of 1.50, not the 2.50 of turnover the old measure counted.
        return {"state": "STOPPED_FLAT_ORDERS",
                "window": {"symbol": symbol,
                           "inventory": {"total_spend_usd": "2.50",
                                         "yes_qty": 0, "no_qty": 1,
                                         "n_fills": 2},
                           "orders_live_after_stop": 0}}

    async def _res(symbol):
        return {"status": "settled", "resolutionSide": "no"}

    async def _gate(symbol, **k):
        return {"ok": True, "verdict": "CLEAR"}

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_window,
                             discover_fn=_discover, gate_fn=_gate,
                             readiness_fn=_READY, resolution_fn=_res,
                             latch_fn=lambda *a, **k: {"ok": True}))
    assert len(seen) == 2, "1.50 + 1.50 >= 3.00, checked BEFORE binding a 3rd"
    assert rep["state"] == "STOPPED_AGGREGATE_LOSS"
    assert rep["cohort_state"]["windows_traded"] == 2


# ============ the loss measure is SETTLEMENT-INCLUSIVE, not turnover

def _win(spend, yes=0, no=0, fills=1, events=None, symbol="S"):
    return {"window": {"symbol": symbol,
                       "inventory": {"total_spend_usd": str(spend),
                                     "yes_qty": yes, "no_qty": no,
                                     "n_fills": fills},
                       "order_events": list(events or [])}}


def test_an_unsettled_window_contributes_nothing_to_cohort_loss():
    """The defect that stopped COHORT-1. Gross BUY outflow is TURNOVER: for a
    strategy that buys both legs of a binary and is paid on one, it climbs at
    about half of turnover whether the cohort is winning or losing, so a cap
    on it is a turnover cap wearing a loss cap's name."""
    e = CR.window_loss_usd(_win("2.52", yes=3, no=4))
    assert e["state"] == CE.PENDING
    assert e["gross_fill_spend_usd"] == "2.52"
    # the turnover is RECORDED, and it is not the loss
    assert e["settlement_payout_usd"] is None
    assert e["economic_loss_usd"] is None
    roll = CE.cohort_economics([e])
    assert roll["cumulative_economic_loss_usd"] == "0"
    assert roll["windows_pending_settlement"] == 1


def test_an_unresolved_market_never_has_its_payout_guessed():
    """Its risk is bounded where it has always been bounded -- the per-market
    committed/residual/exposure gates -- not by inventing a payout."""
    for res in (None, {}, {"status": "active", "resolutionSide": None},
                {"status": "settled", "resolutionSide": None}):
        e = CR.window_loss_usd(_win("2.52", yes=3), res)
        assert e["state"] == CE.PENDING, res
        assert e["settlement_payout_usd"] is None, res
        assert e["settlement_implied_net_usd"] is None, res


def test_a_settled_window_is_priced_from_payout_fees_and_rebates():
    e = CR.window_loss_usd(
        _win("2.52", yes=3, no=4),
        {"status": "settled", "resolutionSide": "yes"})
    assert e["state"] == CE.SETTLED
    assert e["settlement_payout_usd"] == "3"      # 3 winning YES at $1
    assert e["settlement_implied_net_usd"] == "0.48"
    assert e["economic_loss_usd"] == "-0.48"
    assert e["realized_net_usd"] is None, "settlement-IMPLIED is not REALIZED"


def test_a_profitable_window_reduces_cumulative_economic_loss():
    win = CR.window_loss_usd(_win("0.71", yes=2),
                             {"status": "settled", "resolutionSide": "yes"})
    lose = CR.window_loss_usd(_win("2.40", yes=4, no=2),
                              {"status": "settled", "resolutionSide": "no"})
    assert win["economic_loss_usd"] == "-1.29"
    assert lose["economic_loss_usd"] == "0.4"
    roll = CE.cohort_economics([win, lose])
    assert roll["cumulative_economic_loss_usd"] == "-0.89"
    assert roll["settlement_implied_net_usd"] == "0.89"


def test_fees_are_venue_confirmed_and_an_absent_fee_is_not_zero():
    evs = [{"X": "FILLED", "n": "0.02"}, {"X": "FILLED"},
           {"X": "CANCELLED", "n": "9.99"}]
    e = CR.window_loss_usd(_win("1.00", yes=2, events=evs),
                           {"status": "settled", "resolutionSide": "yes"})
    assert e["fees_usd"] == "0.02", "the cancelled row is not an execution"
    p = e["fee_provenance"]
    assert p["fills_seen"] == 2 and p["fees_missing"] == 1, (
        "no fees and no fee DATA must never read the same")
    assert e["settlement_implied_net_usd"] == "0.98"


def test_a_settle_pass_carries_the_confirmed_fee_rather_than_re_deriving_it():
    """Hours later the order events are gone from the state file. Recomputing
    against an empty list would report a real fee as 0.00."""
    e = CE.window_economics(_win("1.00", yes=2),
                            {"status": "settled", "resolutionSide": "yes"},
                            fees_usd="0.02",
                            fee_provenance_in={"fills_seen": 2,
                                               "fees_missing": 1})
    assert e["fees_usd"] == "0.02"
    assert e["fee_provenance"]["carried_forward"] is True
    assert e["settlement_implied_net_usd"] == "0.98"


def test_rebates_are_credited_only_and_default_to_unknown():
    e = CR.window_loss_usd(_win("0.71", yes=2),
                           {"status": "settled", "resolutionSide": "yes"})
    assert e["rebates_usd"] is None, "unread is not zero and is never assumed"
    assert e["settlement_implied_net_usd"] == "1.29"
    credited = CR.window_loss_usd(
        _win("0.71", yes=2), {"status": "settled", "resolutionSide": "yes"},
        rebates_usd="0.03")
    assert credited["rebates_usd"] == "0.03"
    assert credited["settlement_implied_net_usd"] == "1.32"
    assert CE.cohort_economics([e])["rebates_credited_usd"] == "0"


def test_an_unrecoverable_window_is_never_costed_from_its_turnover():
    u = CE.unrecoverable_window(
        None, gross_spend_usd="3.77",
        why="the per-action ledger was lost with the failed first dispatch")
    assert u["gross_fill_spend_usd"] == "3.77"
    assert u["economic_loss_usd"] is None
    assert u["settlement_implied_net_usd"] is None
    roll = CE.cohort_economics([u])
    assert roll["windows_unrecoverable"] == 1
    assert roll["cumulative_economic_loss_usd"] == "0", (
        "3.77 of gross outflow is not established loss and is never carried "
        "forward as though it were")


def test_nothing_in_the_rollup_is_ever_labelled_realized():
    rows = [CR.window_loss_usd(_win("0.71", yes=2),
                               {"status": "settled", "resolutionSide": "yes"}),
            CR.window_loss_usd(_win("2.52", yes=3))]
    assert all(r["realized_net_usd"] is None for r in rows)
    assert CE.cohort_economics(rows)["realized_net_usd"] is None


def test_EP016_recomputes_to_plus_1_14_rather_than_6_86_of_loss():
    """COHORT-1's four recoverable settled windows, from their own records."""
    obs = [("2.52", 3, 4, "yes"), ("0.71", 2, 0, "yes"),
           ("2.40", 4, 2, "no"), ("1.23", 2, 1, "no")]
    rows = [CR.window_loss_usd(_win(sp, yes=y, no=n),
                               {"status": "settled", "resolutionSide": side})
            for sp, y, n, side in obs]
    roll = CE.cohort_economics(rows + [CE.unrecoverable_window(
        None, gross_spend_usd="3.77", why="record lost")])

    assert roll["windows_settled"] == 4
    assert roll["settlement_payout_usd"] == "8"
    assert roll["settlement_implied_net_usd"] == "1.14"
    assert roll["cumulative_economic_loss_usd"] == "-1.14"
    # what the OLD measure would have called loss, from the same four rows
    old = sum(D(sp) for sp, _, _, _ in obs)
    assert str(old) == "6.86"
    assert D(roll["cumulative_economic_loss_usd"]) < 0 < old
    # and the lost pair stays out of it entirely
    assert roll["gross_fill_spend_usd"] == "10.63"
    assert roll["windows_unrecoverable"] == 1


def test_the_aggregate_stop_reads_the_settlement_inclusive_number(tmp_path):
    """A cohort whose settled windows are PROFITABLE must not be stopped."""
    root, cid = _cohort(tmp_path)
    plan = _plan(tmp_path, root, cid, windows=2, loss="3.00")
    seen = []

    async def _discover(spec, exclude=()):
        nxt = [s for s in (BTC15M, NEXT15M, THIRD15M) if s not in set(exclude)]
        return {"found": True, "symbol": nxt[0], "tte_s": 870.0}

    async def _window(plan, symbol, *, readiness, latch_held_by,
                      cohort_id=None, status=None):
        seen.append(symbol)
        # 2.52 out, 3 winning YES back: the OLD measure called this 2.52 of
        # loss and would have stopped the cohort on the second window.
        return {"state": "STOPPED_FLAT_ORDERS",
                "window": {"symbol": symbol,
                           "inventory": {"total_spend_usd": "2.52",
                                         "yes_qty": 3, "no_qty": 4,
                                         "n_fills": 7},
                           "orders_live_after_stop": 0}}

    async def _res(symbol):
        return {"status": "settled", "resolutionSide": "yes"}

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_window,
                             discover_fn=_discover,
                             gate_fn=_ok_gate, readiness_fn=_READY,
                             resolution_fn=_res,
                             latch_fn=lambda *a, **k: {"ok": True}))
    assert len(seen) == 2, "profitable windows must not trip the loss cap"
    assert rep["state"] == "STOPPED_WINDOWS_EXHAUSTED"
    assert D(rep["cohort_state"]["aggregate_loss_usd"]) < 0


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
                      cohort_id=None, status=None):
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
    """INVARIANTS, NOT A SNAPSHOT. These numbers MOVE: the cohort resumed and
    ran four more windows, so pinning `windows_observed == 2` and
    `aggregate_loss_usd == "3.77"` pinned one minute of history and went red
    the moment the system did what it was authorized to do. What must hold
    forever is that the RECOVERED entry keeps its own facts, names its own
    gaps, and invents nothing."""
    st = CR.load_state(COHORT1)
    rec = st["recovery"]
    assert rec["tombstoned_slots"] == 12
    assert rec["aggregate_loss_carried_forward_usd"] == "3.77"
    assert rec["remaining_loss_budget_usd"] == "6.23"
    # the prior stop is preserved rather than erased by the resume
    assert st["prior_stop"]["state"] == "STOPPED_MUTATION_BUDGET"
    assert st["resumed"]["fix_commit"] == "830908f"

    w = next(x for x in st["windows"] if x.get("recovered"))
    assert w["source_run"] == "35671834127"
    assert w["what_is_known"]["windows_observed"] == 2
    assert w["what_is_known"]["windows_traded"] == 2
    assert w["what_is_known"]["aggregate_reconstructed_loss_usd"] == "3.77"
    assert "NOT REALIZED" in w["labels"]
    assert any("bound symbols" in u for u in w["what_is_NOT_known"])
    assert any("ACK, fill, cancel" in u for u in w["what_is_NOT_known"])

    # and the cohort's own counters only ever move FORWARD from that entry
    assert st["windows_observed"] >= 2
    assert st["windows_traded"] >= 2

    # THE 3.77 IS PROVENANCE, NOT LOSS. It was produced by the broken
    # gross-outflow metric, its settlement was never observed, and its true
    # economic P&L is UNKNOWN. It is preserved above and excluded below --
    # carrying it into the cap would be counting turnover as loss twice over.
    unrec = st["unrecoverable"]
    assert [u["gross_fill_spend_usd"] for u in unrec] == ["3.77"]
    assert all(u["economic_loss_usd"] is None for u in unrec)
    assert all(u["settlement_implied_net_usd"] is None for u in unrec)
    assert st["economics"]["windows_unrecoverable"] == len(unrec)
    assert D(st["aggregate_loss_usd"]) == D(
        st["economics"]["cumulative_economic_loss_usd"])
    assert D(st["aggregate_loss_usd"]) < D("3.77"), (
        "the unrecoverable gross outflow is not carried forward as loss")
    assert st["economics"]["realized_net_usd"] is None
    assert st["accounting_correction"]["old_aggregate_loss_usd"] == "10.63"
    assert st["accounting_correction"]["unrecoverable_excluded_usd"] == "3.77"


def test_cohort1_never_re_enters_a_spent_ledger_and_cannot_exceed_its_grant():
    """Also invariants. The remaining capacity FALLS as the cohort runs, so
    `total_remaining == 108` was a snapshot; what must hold is that W01 stays
    permanently spent, the chain is ten ledgers of twelve, and nothing the
    cohort does can take it past 120 or past its authorized window count."""
    ids = _cohort1_chain()
    assert len(ids) == 10
    plan = CR.CohortPlan(
        auth_id="OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-COHORT-1",
        spec=SPEC.BTC15M_Q1_CAP4_W60_880, max_windows=10,
        max_aggregate_loss_usd="10.00", cohort_ids=ids)
    b = plan.action_budget()
    assert b["total_budget"] == 120 and b["per_window"] == 12
    assert 0 <= b["total_remaining"] <= 108, (
        "W01's twelve are gone for good, so the ceiling is 108")
    assert b["total_consumed"] >= 12
    assert b["all_readable"] and b["all_intact"]

    # W01 is spent, and whichever ledger is next is never W01
    assert CO.state(ids[0])["remaining"] == 0
    if b["total_remaining"] > 0:
        assert plan.ledger_for(0) != ids[0]
    else:
        with pytest.raises(IndexError):
            plan.ledger_for(0)

    st = CR.load_state(plan.head)
    assert st["windows_observed"] <= plan.max_windows
    # every ledger that HAS been spent was spent to its bound, never past it
    for row in b["ledgers"]:
        assert 0 <= row["consumed"] <= 12, row


# ======== the 12 actions are PER MARKET; the remainder dies with the market

def _spend(root, cid, n, *, kind="PLACE"):
    """Spend n slots in one cohort's ledger, the way the driver spends them."""
    path = CO.state(cid, root=root)["ledger_path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"kind": kind, "slot": i,
                                 "outcome": "ACKED"}) + "\n")
    return CO.state(cid, root=root)["consumed"]


@pytest.mark.parametrize("consumed", [0, 4, 8, 12])
def test_the_next_market_always_starts_on_a_whole_fresh_ledger(tmp_path,
                                                               consumed):
    """THE DEFECT. COHORT-1's window 4 was handed W02's remaining 4 slots
    after window 3 spent 8 of them, and stopped with "no mutation slots left
    to cancel with" fourteen cycles in. A market is granted TWELVE actions or
    it is not granted a ledger at all."""
    root, ids = _chain(tmp_path, 3)
    plan = _plan(tmp_path, root, ids[0], windows=3, chain=ids)

    first = plan.ledger_for(0, root=root)
    assert first == ids[0]
    assert _spend(root, first, consumed) == consumed

    nxt = plan.ledger_for(1, root=root)
    if consumed == 0:
        assert nxt == ids[0], "an UNTOUCHED ledger is not forfeit"
    else:
        assert nxt == ids[1], (
            "%d/12 spent: the remaining %d die with that market"
            % (consumed, 12 - consumed))
        st = CO.state(nxt, root=root)
        assert (st["budget"], st["consumed"], st["remaining"]) == (12, 0, 12)


def test_a_partially_spent_ledger_is_never_handed_to_a_second_market(tmp_path):
    root, ids = _chain(tmp_path, 4)
    plan = _plan(tmp_path, root, ids[0], windows=4, chain=ids)
    for i, spent in enumerate((1, 11, 12)):
        cid = plan.ledger_for(i, root=root)
        assert cid == ids[i], "each market gets the NEXT untouched ledger"
        _spend(root, cid, spent)
    assert plan.ledger_for(3, root=root) == ids[3]
    for cid, spent in zip(ids, (1, 11, 12)):
        assert CO.state(cid, root=root)["consumed"] == spent, (
            "a spent ledger is never re-entered and never topped up")


def test_unused_slots_do_not_inflate_what_the_cohort_can_still_bind(tmp_path):
    root, ids = _chain(tmp_path, 3)
    plan = _plan(tmp_path, root, ids[0], windows=3, chain=ids)
    _spend(root, ids[0], 1)          # 11 left, and they are FORFEIT

    b = plan.action_budget(root=root)
    assert b["total_remaining"] == 35, "11 forfeit + 12 + 12, counted raw"
    assert b["fresh_ledgers"] == 2
    assert b["bindable_slots"] == 24, (
        "what a market can actually be given -- WHOLE ledgers only")
    assert b["fresh_ledger_ids"] == [ids[1], ids[2]]


def test_the_cohort_stops_when_no_WHOLE_ledger_remains(tmp_path):
    """Not when the slots run out -- when the LEDGERS do."""
    root, ids = _chain(tmp_path, 2)
    plan = _plan(tmp_path, root, ids[0], windows=5, chain=ids)
    _spend(root, ids[0], 1)
    _spend(root, ids[1], 1)
    assert plan.action_budget(root=root)["total_remaining"] == 22

    async def _discover(spec, exclude=()):
        return {"found": True, "symbol": BTC15M, "tte_s": 870.0}

    async def _window(plan, symbol, **k):        # pragma: no cover - must not
        raise AssertionError("a market must never be bound without a ledger")

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_window,
                             discover_fn=_discover, gate_fn=_ok_gate,
                             readiness_fn=_READY,
                             latch_fn=lambda *a, **k: {"ok": True}))
    assert rep["state"] == "STOPPED_MUTATION_BUDGET"
    assert "never given another market's remainder" in rep["why"]


def test_every_bound_market_records_which_ledger_it_was_given(tmp_path):
    root, ids = _chain(tmp_path, 3)
    plan = _plan(tmp_path, root, ids[0], windows=2, chain=ids)
    order = []

    async def _discover(spec, exclude=()):
        nxt = [s for s in (BTC15M, NEXT15M, THIRD15M) if s not in set(exclude)]
        return {"found": True, "symbol": nxt[0], "tte_s": 870.0}

    async def _window(plan, symbol, *, readiness, latch_held_by,
                      cohort_id=None, status=None):
        order.append((symbol, cohort_id))
        _spend(root, cohort_id, 4)               # this market used 4 of 12
        return {"state": "STOPPED_FLAT_ORDERS",
                "window": {"symbol": symbol,
                           "inventory": {"total_spend_usd": "0",
                                         "n_fills": 0},
                           "orders_live_after_stop": 0}}

    rep = _run(CR.run_cohort(plan, readiness=True, window_fn=_window,
                             discover_fn=_discover, gate_fn=_ok_gate,
                             readiness_fn=_READY,
                             latch_fn=lambda *a, **k: {"ok": True}))
    assert rep["state"] == "STOPPED_WINDOWS_EXHAUSTED"
    assert [c for _, c in order] == [ids[0], ids[1]], (
        "the second market got a FRESH ledger, not the first's remaining 8")

    bindings = [w["ledger_binding"] for w in rep["cohort_state"]["windows"]]
    assert [b["cohort_id"] for b in bindings] == [ids[0], ids[1]]
    assert [b["symbol"] for b in bindings] == [s for s, _ in order]
    for b in bindings:
        assert (b["granted"], b["consumed"], b["forfeited"]) == (12, 4, 8)
        assert "never inherited by the next one" in b["note"]


def test_cleanup_cancellation_survives_a_fully_spent_strategy_budget():
    """CONTINUOUS-6 ended with zero strategy slots and an order still resting.
    A cleanup that borrowed from the strategy budget could never run when it
    is most needed, so it has its own and can only CANCEL."""
    import ast
    assert WC.MAX_CLEANUP_MUTATIONS >= 1

    # THE CLEANUP PATH DOES NOT CONSULT THE STRATEGY BUDGET AT ALL, and it
    # can only cancel. Read from the code, not from the docstring that says
    # so: an assertion satisfied by prose is no assertion.
    tree = ast.parse(open("src/gemini_execution/ws_continuous.py").read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "withdraw_orders")
    names = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "cleanup_consumed" in names
    assert "budget_left" not in names, (
        "cleanup must not be drawn from the strategy budget")
    assert "budget_consumed" not in names
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "_cleanup_cancel" in called
    assert not {"_place", "place", "_send_place"} & called, "cancel only"

    d = object.__new__(WC.Driver)
    d._max_actions = 12
    d.budget_consumed = 12
    assert d.budget_left() == 0, "12/12: the strategy budget is exhausted"
    assert WC.MAX_CLEANUP_MUTATIONS > d.budget_left(), (
        "cleanup must not be drawn from a budget that is already spent")


# ============ THROUGH THE PRODUCTION CALL PATH, not helper construction

def _cli_cohort(tmp_path, monkeypatch, *, windows, spend, ledger_rows,
                resolutions, max_loss="10.00", max_drawdown="0",
                n_ledgers=None):
    """Drive main() for real, faking ONLY the venue.

    Everything between the command line and the venue is the production
    object: main() parses, discover_chain() resolves, CohortPlan is built the
    way main() builds it, run_cohort() binds ledgers, spends the budget on
    disk, prices each window through cohort_economics, writes the live status
    and the report, and returns the exit code the workflow sees.

    WHY THIS SHAPE. Every previous test constructed a CohortPlan itself, and
    that is precisely why none of them caught the CLI dropping `cohort_ids`.
    A test that builds the object cannot see a caller that forgets to.
    """
    import dataclasses
    root, ids = _chain(tmp_path, n_ledgers or (windows + 2),
                       prefix="COHORT-INT-W")
    spec = dataclasses.replace(SPEC.BTC15M_Q1_CAP4_W60_880, live_cohort=ids[0])
    monkeypatch.setitem(SPEC.SPECS, spec.spec_id, spec)

    symbols = ["GEMI-BTC15M26092223%02d-UP" % (n * 15) for n in range(4)]
    bound = []

    async def _discover(s, exclude=()):
        left = [x for x in symbols if x not in set(exclude)]
        if not left:
            return {"found": False, "why": "no further contract"}
        return {"found": True, "symbol": left[0], "tte_s": 870.0}

    async def _one_window(rep, auth_id, symbol, *, ledger_path, **k):
        bound.append((symbol, k.get("cohort_id")))
        # spend slots on the REAL ledger the runner bound, as the driver does
        with open(ledger_path, "a", encoding="utf-8") as fh:
            for i in range(ledger_rows):
                fh.write(json.dumps({"kind": "PLACE", "slot": i,
                                     "outcome": "ACKED"}) + "\n")
        y, n = spend[symbol]["yes"], spend[symbol]["no"]
        rep.update({"state": "STOPPED_FLAT_ORDERS",
                    "window": {"symbol": symbol,
                               "inventory": {
                                   "total_spend_usd": spend[symbol]["usd"],
                                   "yes_qty": y, "no_qty": n,
                                   "n_fills": y + n},
                               "orders_live_after_stop": 0,
                               "stop_why": "end of quoting interval"}})
        return rep

    async def _res(symbol):
        return resolutions.get(symbol) or {}

    async def _gate(symbol, **k):
        return {"ok": True, "verdict": "CLEAR"}

    monkeypatch.setattr(CR.RC, "run_one_window", _one_window)
    monkeypatch.setattr(CR, "await_contract", _discover)
    monkeypatch.setattr(CR, "rollover_gate", _gate)
    monkeypatch.setattr(CR, "resolution_for", _res)
    monkeypatch.setattr(CR, "static_readiness", _READY)

    out = tmp_path / "run.json"
    code = CR.main(["--auth-id", "OWNER-TEST", "--spec", spec.spec_id,
                    "--max-windows", str(windows), "--max-loss-usd", max_loss,
                    "--max-drawdown-usd", max_drawdown,
                    "--root", root, "--readiness",
                    "--state-dir", str(tmp_path / "latch"),
                    "--out-dir", str(tmp_path / "out"), "--out", str(out)])
    return {"code": code, "root": root, "ids": ids, "bound": bound,
            "report": json.loads(out.read_text()), "out_dir": tmp_path / "out"}


def test_CLI_every_market_gets_its_own_whole_12_slot_ledger(tmp_path,
                                                            monkeypatch):
    """THE FIX, THROUGH main(). Each window spends 4 of its 12; the next
    market must still start at 0/12 rather than inherit the remaining 8."""
    sy = ["GEMI-BTC15M26092223%02d-UP" % (n * 15) for n in range(4)]
    got = _cli_cohort(
        tmp_path, monkeypatch, windows=3, ledger_rows=4,
        spend={s: {"usd": "0.80", "yes": 1, "no": 1} for s in sy},
        resolutions={})

    assert [c for _, c in got["bound"]] == list(got["ids"][:3]), (
        "three markets, three DIFFERENT fresh ledgers")
    for cid in got["ids"][:3]:
        st = CO.state(cid, root=got["root"])
        assert (st["budget"], st["consumed"]) == (12, 4), cid
    # the remaining 8 of each are FORFEIT, not available to anyone
    b = got["report"]["action_budget"]
    assert b["fresh_ledgers"] == len(got["ids"]) - 2
    assert b["bindable_slots"] == 12 * b["fresh_ledgers"]

    for w in got["report"]["cohort_state"]["windows"]:
        lb = w["ledger_binding"]
        assert (lb["granted"], lb["consumed"], lb["forfeited"]) == (12, 4, 8)
        assert lb["symbol"] == w["symbol"]


@pytest.mark.parametrize("rows", [0, 4, 8, 12])
def test_CLI_rollover_always_hands_the_next_market_12_of_12(tmp_path,
                                                            monkeypatch, rows):
    sy = ["GEMI-BTC15M26092223%02d-UP" % (n * 15) for n in range(4)]
    got = _cli_cohort(
        tmp_path, monkeypatch, windows=2, ledger_rows=rows,
        spend={s: {"usd": "0.00", "yes": 0, "no": 0} for s in sy},
        resolutions={})

    first, second = got["bound"][0][1], got["bound"][1][1]
    if rows == 0:
        assert first == second, "an UNTOUCHED ledger is not forfeit"
    else:
        assert first != second, "%d/12 spent: the rest die with that market" % rows
        # and what the second market was handed was WHOLE at the moment of
        # binding: its window record says granted 12, consumed `rows`
        lb = got["report"]["cohort_state"]["windows"][1]["ledger_binding"]
        assert lb["granted"] == 12 and lb["consumed"] == rows


def test_CLI_a_profitable_cohort_is_not_stopped_by_its_own_turnover(
        tmp_path, monkeypatch):
    """EP016, THROUGH main(). The four recorded windows spent 6.86 gross and
    were paid 8.00. The old measure called that 6.86 of loss and would have
    stopped at a 3.00 cap; the corrected one is -1.14 of economic loss."""
    sy = ["GEMI-BTC15M26092223%02d-UP" % (n * 15) for n in range(4)]
    obs = {sy[0]: ("2.52", 3, 4, "yes"), sy[1]: ("0.71", 2, 0, "yes"),
           sy[2]: ("2.40", 4, 2, "no"),  sy[3]: ("1.23", 2, 1, "no")}
    got = _cli_cohort(
        tmp_path, monkeypatch, windows=4, ledger_rows=6, max_loss="3.00",
        spend={s: {"usd": u, "yes": y, "no": n} for s, (u, y, n, _) in obs.items()},
        resolutions={s: {"status": "settled", "resolutionSide": side}
                     for s, (_, _, _, side) in obs.items()})

    assert len(got["bound"]) == 4, "a 3.00 cap must not stop a +1.14 cohort"
    assert got["report"]["state"] == "STOPPED_WINDOWS_EXHAUSTED"
    assert got["code"] == 0, "an authorized stop exits 0 so evidence commits"

    econ = got["report"]["cohort_state"]["economics"]
    assert econ["gross_fill_spend_usd"] == "6.86"
    assert econ["settlement_payout_usd"] == "8"
    assert econ["settlement_implied_net_usd"] == "1.14"
    assert econ["cumulative_economic_loss_usd"] == "-1.14"
    assert econ["realized_net_usd"] is None
    assert got["report"]["cohort_state"]["aggregate_loss_usd"] == "-1.14"


def test_CLI_a_genuinely_losing_cohort_still_stops_on_the_economic_cap(
        tmp_path, monkeypatch):
    """The cap must still bite. 2.50 out, 1 winning contract back = 1.50 of
    real economic loss per window; two of those reach a 3.00 cap."""
    sy = ["GEMI-BTC15M26092223%02d-UP" % (n * 15) for n in range(4)]
    got = _cli_cohort(
        tmp_path, monkeypatch, windows=4, ledger_rows=6, max_loss="3.00",
        spend={s: {"usd": "2.50", "yes": 0, "no": 1} for s in sy},
        resolutions={s: {"status": "settled", "resolutionSide": "no"}
                     for s in sy})
    assert len(got["bound"]) == 2
    assert got["report"]["state"] == "STOPPED_AGGREGATE_LOSS"
    assert got["code"] == 0
    assert "gross turnover" in got["report"]["why"]
    assert got["report"]["cohort_state"]["economics"][
        "cumulative_economic_loss_usd"] == "3"


def test_CLI_an_unresolved_market_never_contributes_to_the_cap(tmp_path,
                                                               monkeypatch):
    """Turnover far past the cap, no resolution: the cohort runs its windows.
    Unresolved risk is bounded by the per-market gates, not by a guess."""
    sy = ["GEMI-BTC15M26092223%02d-UP" % (n * 15) for n in range(4)]
    got = _cli_cohort(
        tmp_path, monkeypatch, windows=3, ledger_rows=6, max_loss="1.00",
        spend={s: {"usd": "3.90", "yes": 4, "no": 4} for s in sy},
        resolutions={s: {"status": "active", "resolutionSide": None}
                     for s in sy})
    assert len(got["bound"]) == 3
    econ = got["report"]["cohort_state"]["economics"]
    assert econ["gross_fill_spend_usd"] == "11.7"
    assert econ["windows_pending_settlement"] == 3
    assert econ["cumulative_economic_loss_usd"] == "0"


def test_CLI_the_report_and_live_status_survive_a_runner_that_explodes(
        tmp_path, monkeypatch):
    """Evidence preservation is unconditional on EVERY exit path."""
    import dataclasses
    root, ids = _chain(tmp_path, 3, prefix="COHORT-BOOM-W")
    spec = dataclasses.replace(SPEC.BTC15M_Q1_CAP4_W60_880, live_cohort=ids[0])
    monkeypatch.setitem(SPEC.SPECS, spec.spec_id, spec)

    async def _boom(*a, **k):
        raise RuntimeError("the runner died mid-cohort")

    monkeypatch.setattr(CR, "run_cohort", _boom)
    out = tmp_path / "run.json"
    with pytest.raises(RuntimeError):
        CR.main(["--auth-id", "OWNER-TEST", "--spec", spec.spec_id,
                 "--max-windows", "3", "--max-loss-usd", "10.00",
                 "--root", root, "--readiness", "--out", str(out),
                 "--out-dir", str(tmp_path / "out")])
    rep = json.loads(out.read_text())
    assert rep["state"] == "STOPPED_RUNNER_ERROR"
    assert "the runner died mid-cohort" in rep["runner_error"]
    assert rep["plan"]["cohort_ids"] == list(ids)


# ============ the second bound: peak-to-trough drawdown

def test_the_drawdown_measure_sees_what_cumulative_loss_cannot():
    """A cohort that made 2.00 and gave back 2.00 reads ZERO cumulative loss
    while its recent behaviour is as bad as it has ever been."""
    def _w(spend, yes, no, side):
        return CR.window_loss_usd(_win(spend, yes=yes, no=no),
                                  {"status": "settled",
                                   "resolutionSide": side})
    rows = [_w("1.00", 2, 0, "yes"),     # +1.00
            _w("1.00", 2, 0, "yes"),     # +1.00, equity 2.00 (the peak)
            _w("1.00", 0, 0, "yes"),     # -1.00, equity 1.00
            _w("1.00", 0, 0, "yes")]     # -1.00, equity 0.00
    roll = CE.cohort_economics(rows)
    assert roll["cumulative_economic_loss_usd"] == "0", "blind to the bleed"
    assert roll["equity_peak_usd"] == "2"
    assert roll["max_drawdown_usd"] == "2", "and this is not"
    assert [c["equity_usd"] for c in roll["equity_curve"]] == \
        ["1", "2", "1", "0"]


def test_drawdown_is_never_negative_and_ignores_unsettled_turnover():
    rows = [CR.window_loss_usd(_win("0.40", yes=2),
                               {"status": "settled",
                                "resolutionSide": "yes"}),
            CR.window_loss_usd(_win("3.90", yes=4, no=4))]   # PENDING
    roll = CE.cohort_economics(rows)
    assert roll["max_drawdown_usd"] == "0", "a cohort at a new high"
    assert roll["gross_fill_spend_usd"] == "4.3"
    assert roll["equity_curve"][-1]["equity_usd"] == "1.6"
    assert len(roll["equity_curve"]) == 1, "SETTLED windows only"


def test_CLI_the_drawdown_stop_fires_before_the_cumulative_one(tmp_path,
                                                               monkeypatch):
    """Two winners then two losers: cumulative loss never reaches its cap and
    the drawdown does. Through main(), on the production call path."""
    sy = ["GEMI-BTC15M26092223%02d-UP" % (n * 15) for n in range(4)]
    obs = {sy[0]: ("0.10", 1, 0, "yes"),   # +0.90
           sy[1]: ("0.10", 1, 0, "yes"),   # +0.90, equity 1.80 (peak)
           sy[2]: ("1.00", 0, 0, "yes"),   # -1.00, equity 0.80, dd 1.00
           sy[3]: ("1.00", 0, 0, "yes")}   # never reached
    got = _cli_cohort(
        tmp_path, monkeypatch, windows=4, ledger_rows=6, max_loss="99.00",
        max_drawdown="1.00",
        spend={s: {"usd": u, "yes": y, "no": n}
               for s, (u, y, n, _) in obs.items()},
        resolutions={s: {"status": "settled", "resolutionSide": side}
                     for s, (_, _, _, side) in obs.items()})

    assert len(got["bound"]) == 3, "stopped BEFORE binding a fourth market"
    assert got["report"]["state"] == "STOPPED_DRAWDOWN"
    assert got["code"] == 0, "an authorized stop exits 0 so evidence commits"
    assert "not a turnover measure" in got["report"]["why"]
    econ = got["report"]["cohort_state"]["economics"]
    assert econ["max_drawdown_usd"] == "1"
    assert econ["equity_peak_usd"] == "1.8"
    assert D(econ["cumulative_economic_loss_usd"]) < D("99.00")


def test_a_zero_drawdown_cap_disables_the_stop_entirely(tmp_path,
                                                        monkeypatch):
    """Additive: every cohort so far ran with no drawdown bound at all."""
    sy = ["GEMI-BTC15M26092223%02d-UP" % (n * 15) for n in range(4)]
    got = _cli_cohort(
        tmp_path, monkeypatch, windows=3, ledger_rows=6, max_loss="99.00",
        spend={s: {"usd": "1.00", "yes": 0, "no": 0} for s in sy},
        resolutions={s: {"status": "settled", "resolutionSide": "yes"}
                     for s in sy})
    assert len(got["bound"]) == 3
    assert got["report"]["state"] == "STOPPED_WINDOWS_EXHAUSTED"
    assert got["report"]["plan"]["max_drawdown_usd"] == "0"


# ============ COHORT-2: the same experiment, twenty contracts

C2 = "COHORT-2026-09-22-BTC15M-COHORT2-W01"


def test_cohort2_is_cohort1_with_a_different_ledger_chain():
    """The claim is NOT "the hash is unchanged" -- `spec_id` is deliberately
    inside the fingerprint, so two declared specs never share an identity.
    The claim is that every OTHER hashed field is identical, which is what
    makes this the same experiment rather than a new one."""
    a, b = SPEC.BTC15M_Q1_CAP4_W60_880, SPEC.BTC15M_Q1_CAP4_W60_880_C2
    fa = {k: v for k, v in a.invocation_fingerprint().items()
          if k != "spec_id"}
    fb = {k: v for k, v in b.invocation_fingerprint().items()
          if k != "spec_id"}
    assert fa == fb, "COHORT-2 is not the same experiment"
    assert a.spec_sha() != b.spec_sha(), "distinct ids, distinct identities"
    assert b.envelope_obj.sha() == a.envelope_obj.sha()
    assert b.quote_band == (60.0, 880.0)
    assert str(b.envelope_obj.action_size) == "1"
    assert b.budget_cohort == C2
    assert b.live_cohort not in b.invocation_fingerprint().values(), (
        "granting a budget must not move the experiment's identity")


def test_the_frozen_bounds_are_byte_identical_across_the_two_cohorts():
    a = SPEC.BTC15M_Q1_CAP4_W60_880.envelope_obj
    b = SPEC.BTC15M_Q1_CAP4_W60_880_C2.envelope_obj
    assert a is b or a.as_dict() == b.as_dict()
    assert str(b.action_size) == "1"
    assert int(b.max_working_orders) == 2
    assert int(b.max_actions) == 12
    assert int(b.max_concurrent_markets) == 1
    assert (D(b.max_committed_usd), D(b.max_residual_usd)) == (D("4"), D("4"))
    assert (D(b.max_yes), D(b.max_no)) == (D("4"), D("4"))
    assert int(b.max_same_side_run) == 4


def test_the_cohort2_chain_is_twenty_whole_untouched_ledgers():
    """On the REAL tree, because a chain that exists only in a tmp_path is
    the exact thing that let COHORT-1 dispatch against one ledger."""
    spec = SPEC.BTC15M_Q1_CAP4_W60_880_C2
    ids = CR.discover_chain(spec)
    assert len(ids) == 20, ids
    assert ids[0] == C2 and ids[-1].endswith("W20")
    plan = CR.CohortPlan(auth_id="PROPOSED", spec=spec, max_windows=20,
                         max_aggregate_loss_usd="8.00",
                         max_drawdown_usd="5.00", cohort_ids=ids)
    b = plan.action_budget()
    assert b["total_budget"] == 240 and b["per_window"] == 12
    assert b["fresh_ledgers"] == 20 and b["bindable_slots"] == 240
    assert b["all_readable"] and b["all_intact"]
    assert plan.ledger_for(0) == C2
    # and it cannot silently reach past its grant
    assert 12 * len(ids) == 240
    assert plan.as_dict()["max_cohort_actions"] == 240


def test_the_cohort2_chain_shares_one_pre_cohort_ancestor():
    """A LINKED list would read window 1 legitimately spending its budget as
    tampering on window 2 and stop the cohort at its second contract."""
    ids = CR.discover_chain(SPEC.BTC15M_Q1_CAP4_W60_880_C2)
    preds = {CO.state(c)["predecessor"]["predecessor_ledger"] for c in ids}
    assert len(preds) == 1, preds
    assert C2 not in preds.pop(), "a ledger in this chain is its own ancestor"


# ============ maker-vs-taker, verified per fill

def test_every_fill_is_checked_against_the_venues_own_maker_flag():
    evs = [{"X": "FILLED", "m": True, "c": "a"},
           {"X": "PARTIALLY_FILLED", "m": True, "c": "b"},
           {"X": "NEW", "m": False, "c": "c"}]
    p = CE.fill_provenance(evs)
    assert (p["fills"], p["maker"], p["taker"], p["unknown"]) == (2, 2, 0, 0)
    assert p["all_maker"] is True
    assert p["taker_client_order_ids"] == []


def test_a_taker_fill_is_named_rather_than_averaged_away():
    """A taker fill under a maker-only envelope is a BOUND VIOLATION."""
    p = CE.fill_provenance([{"X": "FILLED", "m": True, "c": "ok"},
                            {"X": "FILLED", "m": False, "c": "BAD"}])
    assert p["all_maker"] is False
    assert p["taker"] == 1 and p["taker_client_order_ids"] == ["BAD"]


def test_a_fill_with_no_maker_flag_is_UNKNOWN_and_never_counted_as_maker():
    p = CE.fill_provenance([{"X": "FILLED", "c": "a"}])
    assert (p["maker"], p["taker"], p["unknown"]) == (0, 0, 1)
    assert p["all_maker"] is False, "absent is unknown, never maker"
    assert "never counted as maker" in p["note"]


def test_no_fills_is_not_all_maker():
    p = CE.fill_provenance([])
    assert p["fills"] == 0 and p["all_maker"] is False, (
        "a vacuous truth here would read as a verified property")


def test_the_four_recorded_EP016_windows_were_all_maker():
    """From the preserved artifacts, on the real tree."""
    import glob
    files = sorted(glob.glob("analysis/gemini/cohort_window_*.json"))
    assert len(files) == 4, files
    total = 0
    for f in files:
        rep = json.loads(open(f).read())
        p = CE.fill_provenance((rep.get("window") or {}).get("order_events"))
        assert p["all_maker"], (f, p)
        assert p["taker"] == 0 and p["unknown"] == 0
        total += p["fills"]
    assert total == 18, total
