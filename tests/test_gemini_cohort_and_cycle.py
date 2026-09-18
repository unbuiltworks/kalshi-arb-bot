"""The new-cohort budget, the corrected cycle order, and the exact replay action.

CONTINUOUS-2 through -4 spent all 12 slots of the original ledger, and that
ledger is committed. The obvious way to run again -- reset the counter --
destroys the only durable record that those mutations happened. A cohort is the
alternative: a fresh bounded budget with its own empty ledger, which records
the predecessor's hash and leaves it byte-for-byte untouched.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from decimal import Decimal as D

import pytest

from src.gemini_execution import cohort as CO
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import ws_continuous as WC

SYMBOL = "GEMI-BTC05M2609181730-UP"
HISTORICAL = "analysis/gemini/continuous_ledger.jsonl"
LIVE_COHORT = "COHORT-2026-09-18-C5"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop(
        ).run_until_complete(coro)


# ================================================================== COHORT

def test_the_live_cohort_exists_with_its_own_budget_and_ledger():
    s = CO.state(LIVE_COHORT)
    assert s["budget"] == 6
    assert s["consumed"] == 0 and s["remaining"] == 6
    assert s["ledger_path"].endswith("%s/ledger.jsonl" % LIVE_COHORT)
    assert s["envelope_max_actions"] == 12


def test_the_cohort_records_the_historical_ledger_and_it_is_intact():
    """THE WHOLE POINT. A cohort that cannot prove the history it succeeded is
    unchanged is indistinguishable from a reset."""
    s = CO.state(LIVE_COHORT)
    pred = s["predecessor"]
    assert pred["intact"] is True
    assert pred["recorded_consumed"] == 12
    assert pred["current_consumed"] == 12
    live = hashlib.sha256(open(HISTORICAL, "rb").read()).hexdigest()
    assert pred["recorded_sha256"] == live


def test_a_changed_historical_ledger_is_detected(tmp_path):
    hist = tmp_path / "hist.jsonl"
    hist.write_text('{"kind":"PLACE","slot":0}\n', encoding="utf-8")
    c = CO.create("C-DETECT", 3, root=str(tmp_path / "co"),
                  predecessor_ledger=str(hist))
    assert CO.verify_predecessor(c)["intact"] is True
    hist.write_text('{"kind":"PLACE","slot":0}\n{"kind":"PLACE","slot":1}\n',
                    encoding="utf-8")
    out = CO.verify_predecessor(c)
    assert out["intact"] is False
    assert "byte-for-byte" in out["why"]


def test_creating_a_cohort_does_not_touch_the_predecessor(tmp_path):
    hist = tmp_path / "hist.jsonl"
    hist.write_text('{"kind":"CANCEL","slot":0}\n', encoding="utf-8")
    before = hist.read_bytes()
    CO.create("C-UNTOUCHED", 2, root=str(tmp_path / "co"),
              predecessor_ledger=str(hist))
    assert hist.read_bytes() == before


def test_a_second_create_is_refused_not_a_no_op(tmp_path):
    """NO AUTOMATIC CREATION. 'create it again' and 'I did not realise one
    existed' look identical from here, and only one is safe."""
    hist = tmp_path / "h.jsonl"
    hist.write_text("", encoding="utf-8")
    root = str(tmp_path / "co")
    CO.create("C-ONCE", 2, root=root, predecessor_ledger=str(hist))
    with pytest.raises(CO.CohortRefused) as ei:
        CO.create("C-ONCE", 2, root=root, predecessor_ledger=str(hist))
    assert "already exists" in str(ei.value)


def test_a_cohort_may_not_exceed_the_envelope(tmp_path):
    hist = tmp_path / "h.jsonl"
    hist.write_text("", encoding="utf-8")
    root = str(tmp_path / "co")
    with pytest.raises(CO.CohortRefused) as ei:
        CO.create("C-BIG", CP.MAX_ACTIONS + 1, root=root,
                  predecessor_ledger=str(hist))
    assert "exceeds the envelope" in str(ei.value)
    # and it is REFUSED rather than clamped
    assert not CO.exists("C-BIG", root=root)


def test_a_cohort_with_no_slots_is_refused(tmp_path):
    hist = tmp_path / "h.jsonl"
    hist.write_text("", encoding="utf-8")
    for bad in (0, -1):
        with pytest.raises(CO.CohortRefused):
            CO.create("C-ZERO", bad, root=str(tmp_path / "co"),
                      predecessor_ledger=str(hist))


def test_nothing_in_the_package_creates_a_cohort_automatically():
    """A cohort comes into being because a human ran a command."""
    import ast
    import pathlib
    offenders = []
    for p in pathlib.Path("src").rglob("*.py"):
        if p.name == "cohort.py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "create"
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id in ("CO", "cohort")):
                offenders.append(str(p))
    assert not offenders, offenders


def test_no_rollover_between_cohorts(tmp_path):
    """Unused slots do not carry, and spent ones do not follow."""
    hist = tmp_path / "h.jsonl"
    hist.write_text('{"kind":"PLACE","slot":0}\n', encoding="utf-8")
    root = str(tmp_path / "co")
    a = CO.create("C-A", 4, root=root, predecessor_ledger=str(hist))
    with open(a.ledger_path, "a", encoding="utf-8") as fh:
        fh.write('{"kind":"PLACE","slot":0}\n')
    assert CO.state("C-A", root=root)["remaining"] == 3
    b = CO.create("C-B", 4, root=root, predecessor_ledger=str(hist))
    assert CO.state("C-B", root=root)["remaining"] == 4, (
        "a new cohort starts at its own budget, not at what was left")
    assert b.ledger_path != a.ledger_path


# ================================================= THE BUDGET ONLY TIGHTENS

def test_a_cohort_budget_tightens_the_driver(tmp_path):
    d = WC.Driver("A", SYMBOL, ledger_path=str(tmp_path / "l.jsonl"),
                  max_actions=6)
    assert d.max_actions == 6 and d.budget_left() == 6


def test_a_budget_above_the_envelope_is_refused_not_clamped(tmp_path):
    """Silently granting less than was asked for leaves the caller believing
    it holds budget it does not hold."""
    with pytest.raises(CP.ProbeStop) as ei:
        WC.Driver("A", SYMBOL, ledger_path=str(tmp_path / "l.jsonl"),
                  max_actions=CP.MAX_ACTIONS + 1)
    assert "ceiling" in str(ei.value)


def test_without_a_cohort_the_envelope_still_applies(tmp_path):
    d = WC.Driver("A", SYMBOL, ledger_path=str(tmp_path / "l.jsonl"))
    assert d.budget_left() == CP.MAX_ACTIONS == 12


# ============================================ THE CORRECTED CYCLE, IN ORDER

def test_the_live_cycle_runs_in_the_required_order():
    """authoritative read/recovery -> decision -> mutation -> authoritative
    refresh -> reconcile -> next decision."""
    src = open(WC.__file__, encoding="utf-8").read()
    body = src[src.index("while self.state == RUNNING"):
               src.index("except (CP.ProbeStop")]
    i_pre = body.index("_refresh(refresh_fn)")
    i_dec = body.index("self.decide(")
    i_can = body.index("self.cancel(")
    i_post = body.index('"post_cancel"')
    i_place = body.index("self.place(")
    assert i_pre < i_dec < i_can < i_post < i_place, (
        "refresh must precede the decision, and a SECOND refresh must sit "
        "between the cancels and the places")


def test_a_fill_between_cancel_and_replace_is_seen_before_placing(
        tmp_path, monkeypatch):
    """REQUIREMENT 5b. The CONTINUOUS-4 race, pinned."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = WC.Driver("A", SYMBOL, ledger_path=str(tmp_path / "l.jsonl"),
                  max_actions=6)
    d.inv = CP.Inventory()
    d.state = WC.AUTHORIZED
    filled = {"yet": False}
    seen = []

    async def refresh():
        seen.append(filled["yet"])
        f = ([{"leg": "NO", "qty": "0.01", "price": "0.14"}]
             if filled["yet"] else [])
        return {"fills": f, "open_orders": []}

    async def cancel(session, order):
        filled["yet"] = True                 # the fill lands during the cancel
        return {"cancelled": False, "cancel_confirmed": False}

    placed = []

    async def place(session, rung):
        placed.append(d.inv.report())
        d.stop(WC.STOPPED, "one place is enough for the proof")
        return {"placed": True}

    monkeypatch.setattr(d, "cancel", cancel)
    monkeypatch.setattr(d, "place", place)
    monkeypatch.setattr(d, "decide", lambda tte: {
        "state": WC.RUNNING, "plan": {
            "cancel": [{"order": {"client_order_id": "c1"}}],
            "place": [{"rung": {"leg": "YES", "price": "0.91",
                                "qty": "0.01"}}]}})
    for m in ("preflight", "authorize", "recover", "bind"):
        monkeypatch.setattr(d, m, (lambda *a, **k: {}))

    class S:
        events = None

        async def request(self, *a, **k):
            return {"status": 200}

    _run(d.run_window(S(), tte_of=lambda: 114.0, refresh_fn=refresh,
                      interval_s=0.0, max_cycles=1))
    assert seen == [False, True], "the second refresh must see the new fill"
    assert placed and D(placed[0]["no_qty"]) == D("0.01"), (
        "the placement was sized against stale zero inventory")


def test_an_empty_cancel_ack_cannot_erase_a_filled_order(tmp_path,
                                                         monkeypatch):
    """REQUIREMENT 5a, on the REAL ack the venue sent."""
    monkeypatch.setattr(WC, "require_authorization", lambda *a, **k: None)
    d = WC.Driver("A", SYMBOL, ledger_path=str(tmp_path / "l.jsonl"))
    d.inv = CP.Inventory()
    d.state = WC.AUTHORIZED
    d.working.add("c1", leg="NO", price="0.14", qty="0.01", now=1.0,
                  venue_order_id="145828836246611835")

    class S:
        events = None

        async def request(self, *a, **k):
            return {"status": 200,
                    "result": {"orderId": "", "symbol": "", "status": ""}}

    out = _run(d.cancel(S(), d.working.rungs()[0]))
    assert out["cancelled"] is False
    assert len(d.working.rungs()) == 1


# ================================ THE EXACT CORRECTED ACTION AT THE C4 STATE

#: identity-consistent book. RECONSTRUCTED -- the book at 17:28:05 was not
#: recorded. It is held IDENTICAL across both runs below, so inventory is the
#: only variable and the action difference is attributable to it alone.
BOOK = {"readable": True, "identity_consistent": True,
        "yes": {"bid": "0.91", "ask": "0.93"},
        "no": {"bid": "0.07", "ask": "0.09"}}


def _plan_for(inv):
    st = CP.state_from_venue(SYMBOL, 114.0, inv, now=1789752485.0, book=BOOK)
    st["state_age_s"] = 0.2
    return CP.plan(st, inv, actions_taken=5, now=1789752485.0)


def test_the_replay_inventory_is_exactly_the_continuous4_state():
    inv = CP.Inventory()
    inv.apply_fill("NO", "0.01", "0.14", source="venue_restart")
    r = inv.report()
    assert r["no_qty"] == "0.01"
    assert r["yes_qty"] == "0"
    assert D(r["directional_residual"]) == D("-0.01")
    assert D(r["inventory_imbalance"]) == D("0.01")
    assert D(r["total_spend_usd"]) == D("0.0014")
    assert r["avg_no_cost"] == "0.14"
    assert r["n_fills"] == 1


def test_the_corrected_controller_changes_its_action():
    """THE RESEARCH QUESTION, answered deterministically.

    Same book, same prices, same sizes. Only the inventory differs, and the
    L5 ACTION CLASSIFICATION changes with it -- which is the whole behavioural
    model: OPEN vs EXTEND vs COMPLETE.
    """
    flat = CP.Inventory()
    real = CP.Inventory()
    real.apply_fill("NO", "0.01", "0.14", source="venue_restart")

    got_flat = {a["leg"]: a["action"] for a in _plan_for(flat)["actions"]}
    got_real = {a["leg"]: a["action"] for a in _plan_for(real)["actions"]}

    assert got_flat == {"YES": "OPEN", "NO": "OPEN"}
    assert got_real == {"YES": "COMPLETE", "NO": "EXTEND"}


def test_the_prices_are_identical_so_only_the_action_moved():
    flat = CP.Inventory()
    real = CP.Inventory()
    real.apply_fill("NO", "0.01", "0.14", source="venue_restart")
    pf = {a["leg"]: a["price"] for a in _plan_for(flat)["actions"]}
    pr = {a["leg"]: a["price"] for a in _plan_for(real)["actions"]}
    assert pf == pr, "the book is held constant; only inventory changed"


def test_classification_comes_from_inventory_alone_not_price():
    """`classify` takes no price. This is why the fix is the whole story: a
    controller handed a fictitious flat book cannot classify correctly no
    matter how good its pricing is."""
    import inspect
    assert "price" not in inspect.signature(CP.classify).parameters
    inv = CP.Inventory()
    inv.apply_fill("NO", "0.01", "0.14", source="venue_restart")
    assert CP.classify("Up", inv) == CP.COMPLETE
    assert CP.classify("Down", inv) == CP.EXTEND
    assert CP.classify("Up", CP.Inventory()) == CP.OPEN


def test_no_pair_cost_gate_exists_in_this_path():
    """DELIBERATE, AND NOW RULED ON. `would_breach` bounds imbalance, per-leg
    size and total spend -- and nothing bounds what a COMPLETE costs relative
    to the leg it completes. A COMPLETE at 0.91 against a NO held at 0.14 is a
    1.05 pair, and this path allows it.

    OWNER DECISION 2026-09-18: no pair-cost ceiling is added for CONTINUOUS-5.
    `<= 0.96` is classified a RISK OVERLAY, not strategy fidelity. Of 33,765
    canonical L5 COMPLETEs, 42.75% were above $1.00 and 33.60% above $1.05;
    in the narrow state shaped like ours 78.76% were above $1.05. Preventing a
    1.05 COMPLETE would materially alter the behaviour being validated. See
    L5_OPERATING_MODEL.md section 2d and L5_COMPLETE_BOUNDARY_FINDINGS.md.

    This test pins the ABSENCE of every ceiling the decision enumerates, so a
    gate cannot reappear silently and be mistaken for L5 fidelity. Fidelity is
    not a profitability claim: whether >$1 completes pay on Gemini is what the
    live window must measure.
    """
    inv = CP.Inventory()
    inv.apply_fill("NO", "0.01", "0.14", source="venue_restart")
    assert CP.would_breach("YES", "0.91", inv) is None
    assert D("0.91") + D("0.14") == D("1.05") > D("1.00")

    # every enumerated ceiling is absent, not merely the 0.96 one. Each price
    # below completes the held 0.14 leg at a pair above one of the candidate
    # ceilings, and the risk gate admits all of them.
    for price, pair in (("0.83", "0.97"), ("0.85", "0.99"),
                        ("0.87", "1.01"), ("0.91", "1.05"), ("0.95", "1.09")):
        assert CP.would_breach("YES", price, inv) is None, price
        assert D(price) + D("0.14") == D(pair)
    # and the classification of the dear rung is still COMPLETE: the decision
    # changes no label, it declines to add a bound.
    assert CP.classify("Up", inv) == CP.COMPLETE
