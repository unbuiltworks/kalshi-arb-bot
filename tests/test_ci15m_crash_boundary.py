"""The crash-boundary invariant: a fill that outlives the process.

Authenticated venue evidence is authoritative for what EXECUTED. Our
durable ledger is authoritative for what was AUTHORIZED. The crash boundary
is where those two must be reconciled, and the distinction that matters is:

    a venue fill matching an authorized durable submission is OURS.
    If our local terminal ledger is missing it, that is a CRASH to recover
    from -- the authorization is on disk and the venue executed it.

    a venue fill matching NO authorized submission is FOREIGN. It HALTs.
    We do not absorb a position we cannot account for.

Collapsing those two into "inventory disagreement" would either strand
every crash or silently absorb every stranger's fill. Both are wrong.

WHY ATTRIBUTION IS BY MATCHING AND NOT BY A CLIENT ORDER ID:
`transport.place` REFUSES clientOrderId -- the documented OrderRequest
schema does not admit it and canary #1 was answered 400 for sending one --
so a venue fill cannot carry our tag.
"""
from __future__ import annotations

import asyncio
import importlib
from decimal import Decimal as D

import pytest

from src.gemini_execution import ci15m_budget as BUD
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import metadata
from src.gemini_execution.ci15m_policy import POLICY, QUANTITY

CID = "CI15M-CRASH"
TICKER = "BTC15M2610032000"
SYMBOL = "GEMI-%s-UP" % TICKER
OID = "145828836717890000"

ORDER = {"orderId": int(OID), "symbol": SYMBOL, "side": "buy",
         "outcome": "yes", "status": "filled", "price": "0.34",
         "quantity": "1", "filledQuantity": "1", "remainingQuantity": "0",
         "avgExecutionPrice": "0.34"}
TRADE = {"tid": 9900001, "order_id": OID, "symbol": SYMBOL, "price": "0.34",
         "amount": "1", "fee_amount": "0.02", "aggressor": True,
         "timestampms": 1791057107961}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "cohorts")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [0],
                                        "count": 1, "why": None})
    BUD.create(CID, root=root, profile=BUD.MECHANICS)
    BUD.open_window(CID, TICKER, root=root)
    return root


def _slot_is_durable(root, action="OPEN", price="0.34"):
    """Step 1: an authorized durable slot exists. This is what `consume_slot`
    fsyncs BEFORE the venue is touched and what the latch ref carries."""
    return BUD.consume_slot(CID, TICKER, {
        "action": action, "side": "UP", "outcome": "yes", "price": price,
        "quantity": "1", "unmatched_shares_after": "1",
        "unmatched_principal_after": price, BUD.RECONCILED_KEY: "0"},
        root=root, remote=None)


def _venue_says(monkeypatch, orders, trades):
    async def hist(**kw):
        assert kw.get("include_rows") is True
        return {"readable": True, "verdict": "ORDERS_FOUND",
                "total_rows": len(orders), "symbol": SYMBOL,
                "unreadable_statuses": [],
                "per_status": [{"status_filter": "filled",
                                "orders": orders}]}

    async def my(sym):
        return {"rows": trades, "status": 200}

    async def pos():
        return {"ok": True, "rows": [], "status": 200, "total": 0}
    monkeypatch.setattr(metadata, "order_history", hist)
    monkeypatch.setattr(P, "_mytrades", my)
    monkeypatch.setattr(metadata, "positions", pos)


# =====================================================================
# THE FORWARD BOUNDARY: fill lands, process dies before persisting it
# =====================================================================
def test_a_crash_before_terminal_persistence_recovers_the_fill(env,
                                                               monkeypatch):
    """Steps 1-9 of the required sequence, in order."""
    root = env
    # 1. authorized durable slot exists
    row = _slot_is_durable(root)
    assert row["slot"] == 0
    assert BUD.consumed(CID, root=root) == 1
    # 2-3. the order reached Gemini and filled q1
    _venue_says(monkeypatch, [ORDER], [TRADE])
    # 4. the process died before local terminal-fill persistence: there is
    #    NO FILLED status row on disk
    rows = BUD._rows(BUD._paths(CID, root)["ledger"])
    assert not any(r.get("status") == "FILLED" for r in rows)
    assert BUD.open_state(CID, TICKER, root=root) == BUD.OPEN_SUBMITTED

    # 5. a fresh process: no in-memory inventory at all
    importlib.reload(BUD)
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [0],
                                        "count": 1, "why": None})
    fresh = P.new_inventory()
    assert fresh.q_up == D("0") and not fresh.fills

    # 6. the order-history + mytrades join attributes the fill to THIS
    #    cohort's authorized submission
    got = asyncio.run(P.authenticated_fills(CID, SYMBOL, root=root))
    assert got["ok"], got
    assert got["attribution"] == {"intents": 1, "attributed": 1}
    assert got["rows"][0]["_slot"] == 0, "attributed to the authorized slot"

    # 7. recover() reconstructs q1
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=root))
    assert rec["ok"] and rec["signed"] == D("1")
    assert rec["reconstruction"]["up_qty"] == "1"
    assert rec["reconstruction"]["up_cost"] == "0.34"
    assert BUD.halted(CID, root=root) is None, (
        "a crash-missing fill of OURS must not be an irreconcilable "
        "disagreement")

    # 8. durable state is repaired
    BUD.record_status(CID, TICKER, slot=0, status="FILLED",
                      detail={"recovered_from": "authenticated venue fills",
                              "price": "0.34", "quantity": "1"}, root=root)
    BUD.record_status(CID, TICKER, slot=0, status="INVENTORY_RECONCILED",
                      detail={"signed": "1"}, root=root)
    assert BUD.open_state(CID, TICKER, root=root) == BUD.OPEN_FILLED

    # 9. no duplicate OPEN is permitted
    with pytest.raises(BUD.BudgetRefused) as e:
        _slot_is_durable(root, price="0.35")
    assert "OPEN_ALREADY_ATTEMPTED" in str(e.value)


def test_the_recovered_inventory_drives_the_next_decision_not_an_open(env,
                                                                      monkeypatch):
    _slot_is_durable(env)
    _venue_says(monkeypatch, [ORDER], [TRADE])
    inv = asyncio.run(P.reconciled_inventory(CID, SYMBOL,
                                             root=env))["inventory"]
    dec = POLICY["decide_once"](inv, bid=D("0.10"), ask=D("0.20"),
                                bid_size=500, ask_size=500, tte=200.0,
                                qty=QUANTITY, n_extends=0, actions=1)
    assert dec["action"] != "OPEN"


# =====================================================================
# THE DISTINCTION: foreign fills HALT
# =====================================================================
def test_a_foreign_fill_with_no_authorized_submission_halts(env,
                                                            monkeypatch):
    """No slot was ever consumed, so this fill is nobody's business of
    ours. It must NOT be absorbed into inventory."""
    _venue_says(monkeypatch, [ORDER], [TRADE])
    got = asyncio.run(P.authenticated_fills(CID, SYMBOL, root=env))
    assert got["ok"] is False
    assert "UNATTRIBUTABLE_FILL" in BUD.halted(CID, root=env)


def test_an_extra_fill_beyond_what_was_authorized_halts(env, monkeypatch):
    """One authorization, TWO fills. The genuine one cannot shelter the
    second: each intent absorbs at most one fill."""
    _slot_is_durable(env)
    second = dict(TRADE, tid=9900002, order_id="145828836717899999")
    _venue_says(monkeypatch, [ORDER, dict(ORDER, orderId=145828836717899999)],
                [TRADE, second])
    got = asyncio.run(P.authenticated_fills(CID, SYMBOL, root=env))
    assert got["ok"] is False
    assert len(got["foreign"]) == 1
    assert "UNATTRIBUTABLE_FILL" in BUD.halted(CID, root=env)


def test_a_fill_above_the_authorized_limit_is_not_attributable(env,
                                                               monkeypatch):
    """A capped marketable limit never pays above its limit, so a dearer
    fill is not ours."""
    _slot_is_durable(env, price="0.34")
    _venue_says(monkeypatch, [dict(ORDER, price="0.44")],
                [dict(TRADE, price="0.44")])
    got = asyncio.run(P.authenticated_fills(CID, SYMBOL, root=env))
    assert got["ok"] is False
    assert "UNATTRIBUTABLE_FILL" in BUD.halted(CID, root=env)


def test_a_fill_larger_than_authorized_is_not_attributable(env, monkeypatch):
    _slot_is_durable(env)
    _venue_says(monkeypatch, [dict(ORDER, filledQuantity="2")],
                [dict(TRADE, amount="2")])
    got = asyncio.run(P.authenticated_fills(CID, SYMBOL, root=env))
    assert got["ok"] is False


def test_a_fill_on_the_wrong_side_is_not_attributable(env, monkeypatch):
    """We authorized UP/yes; the venue reports a NO fill."""
    _slot_is_durable(env)
    _venue_says(monkeypatch, [dict(ORDER, outcome="no")],
                [dict(TRADE)])
    got = asyncio.run(P.authenticated_fills(CID, SYMBOL, root=env))
    assert got["ok"] is False
    assert "UNATTRIBUTABLE_FILL" in BUD.halted(CID, root=env)


def test_a_fill_cheaper_than_the_limit_IS_attributable(env, monkeypatch):
    """The negative control: a capped limit may fill BETTER than its limit,
    and that is still ours."""
    _slot_is_durable(env, price="0.40")
    _venue_says(monkeypatch, [dict(ORDER, price="0.34")], [TRADE])
    got = asyncio.run(P.authenticated_fills(CID, SYMBOL, root=env))
    assert got["ok"] is True and got["attribution"]["attributed"] == 1
    assert BUD.halted(CID, root=env) is None


# =====================================================================
# THE INVERSE BOUNDARY: slot durable, submission ambiguous, process dies
# =====================================================================
def _terminal_after_restart(root, monkeypatch, orders, trades):
    """What a fresh process can establish about an ambiguous submission:
    FILLED, CANCELLED or UNKNOWN -- before any new order."""
    _venue_says(monkeypatch, orders, trades)
    return asyncio.run(P.authenticated_fills(CID, SYMBOL, root=root))


def test_ambiguous_then_restart_determines_FILLED(env, monkeypatch):
    _slot_is_durable(env)
    got = _terminal_after_restart(env, monkeypatch, [ORDER], [TRADE])
    assert got["ok"] and got["attribution"]["attributed"] == 1
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=env))
    assert rec["signed"] == D("1")
    assert BUD.halted(CID, root=env) is None


def test_ambiguous_then_restart_determines_CANCELLED(env, monkeypatch):
    """The venue has no fill for the authorized submission: it was
    cancelled. Inventory is flat and that is a FACT, not UNKNOWN -- and the
    slot stays spent, so nothing is retried."""
    _slot_is_durable(env)
    got = _terminal_after_restart(env, monkeypatch, [], [])
    assert got["ok"] and got["n"] == 0
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=env))
    assert rec["ok"] and rec["signed"] == D("0")
    assert BUD.halted(CID, root=env) is None
    assert BUD.consumed(CID, root=env) == 1, "the slot stays SPENT"
    # and the market is not reopened
    with pytest.raises(BUD.BudgetRefused):
        _slot_is_durable(env, price="0.35")


@pytest.mark.parametrize("break_it", ["history", "mytrades"])
def test_ambiguous_then_restart_UNKNOWN_still_halts(env, monkeypatch,
                                                    break_it):
    """If a fresh process cannot establish the submission's fate, it HALTs
    before any new order. UNKNOWN is not CANCELLED."""
    _slot_is_durable(env)
    _venue_says(monkeypatch, [ORDER], [TRADE])
    if break_it == "history":
        async def bad(**kw):
            return {"readable": False, "verdict": "UNREADABLE",
                    "unreadable_statuses": ["filled"], "total_rows": 0}
        monkeypatch.setattr(metadata, "order_history", bad)
    else:
        async def bad2(sym):
            return {"rows": None, "why": "mytrades returned 500"}
        monkeypatch.setattr(P, "_mytrades", bad2)
    got = asyncio.run(P.authenticated_fills(CID, SYMBOL, root=env))
    assert got["ok"] is False
    assert "UNKNOWN_POSITION" in BUD.halted(CID, root=env)


def test_no_new_order_is_permitted_while_halted(env, monkeypatch):
    _slot_is_durable(env)
    BUD.halt(CID, "UNKNOWN_POSITION", "could not establish fate", root=env)
    with pytest.raises(BUD.BudgetRefused) as e:
        BUD.consume_slot(CID, TICKER, {
            "action": "EXTEND", "side": "UP", "outcome": "yes",
            "price": "0.20", "quantity": "1", "extends_before": "0",
            "unmatched_shares_after": "2",
            "unmatched_principal_after": "0.54",
            BUD.RECONCILED_KEY: "1"}, root=env, remote=None)
    assert "HALT" in str(e.value)


# =====================================================================
# the durable authorizations really are durable
# =====================================================================
def test_the_authorized_intents_survive_a_reload(env):
    _slot_is_durable(env)
    importlib.reload(BUD)
    got = P.authorized_intents(CID, root=env)
    assert len(got) == 1
    assert got[0]["slot"] == 0 and got[0]["outcome"] == "yes"
    assert got[0]["price"] == "0.34" and got[0]["action"] == "OPEN"


def test_attribution_reads_the_ledger_not_memory(env, monkeypatch):
    """The attribution source must be the durable ledger, so it works in a
    process that never saw the submission."""
    _slot_is_durable(env)
    import inspect
    src = inspect.getsource(P.authorized_intents)
    assert "BUD._rows" in src and "PLACE" in src
    _venue_says(monkeypatch, [ORDER], [TRADE])
    att = P.attribute_fills(CID, SYMBOL, [
        {"leg": "yes", "price": "0.34", "qty": "1", "tid": 1,
         "order_id": OID, "leg_resolved": True}], root=env)
    assert att["ok"] and att["n_attributed"] == 1


# =====================================================================
# CI15M-4: the MECHANICS2 profile and the per-decision telemetry
# =====================================================================
def test_the_mechanics2_profile_is_two_windows_and_six_orders(tmp_path,
                                                              monkeypatch):
    root = str(tmp_path / "m2")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    BUD.create("CI15M-4", root=root, profile=BUD.MECHANICS2)
    prof = BUD.profile_of("CI15M-4", root=root)
    assert prof["name"] == BUD.MECHANICS2
    assert prof["windows"] == 2
    assert prof["orders_total"] == 6
    assert prof["orders_per_window"] == 3


def test_mechanics_is_not_mistaken_for_mechanics2(tmp_path, monkeypatch):
    """MECHANICS is a PREFIX of MECHANICS2; a substring match would have
    read a MECHANICS2 cohort as the one-window profile."""
    root = str(tmp_path / "m1")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    BUD.create("CI15M-X", root=root, profile=BUD.MECHANICS)
    assert BUD.profile_of("CI15M-X", root=root)["name"] == BUD.MECHANICS
    assert BUD.profile_of("CI15M-X", root=root)["windows"] == 1


def test_decision_telemetry_carries_every_required_field(env):
    inv = P.new_inventory()
    inv.add(POLICY["make_fill"](0.0, "UP", "taker", D("0.34"), D("1"), "x"))
    bk = {"bid": D("0.40"), "ask": D("0.55"), "bid_size": D("500"),
          "ask_size": D("400")}
    dec = {"action": "HOLD", "stop": None, "side": None, "price": None,
           "reason": "waiting"}
    row = P.decision_telemetry("BTC15M2610032000", inv, bk, 300.0, dec,
                               t=1.0, n_extends=0)
    for k in ("utc", "tte", "up_bid", "up_ask", "down_bid", "down_ask",
              "up_qty", "up_cost", "up_avg", "down_qty", "down_cost",
              "down_avg", "matched_qty", "matched_cost",
              "matched_pair_cost", "residual_side", "residual_qty",
              "residual_principal",
              "required_complement_price_for_0.96",
              "opposite_executable_ask", "complement_reachable_at_0.96",
              "policy_action", "submitted_price", "locked"):
        assert k in row, k
    # DOWN is synthesized: acquisition 1-bid, bid 1-ask
    assert row["down_ask"] == "0.60" and row["down_bid"] == "0.45"
    assert row["residual_side"] == "UP" and row["residual_qty"] == "1"
    assert row["residual_principal"] == "0.34"
    # holding 1 UP at 0.34, a 0.96 pair needs DOWN at <= 0.62
    assert row["required_complement_price_for_0.96"] == "0.62"
    assert row["opposite_executable_ask"] == "0.60"
    assert row["complement_reachable_at_0.96"] is True


def test_the_opposite_ask_is_preserved_even_on_a_hold(env):
    """The datum that later answers 'how long until the complement became
    reachable' is written on every HOLD, not only when we act."""
    inv = P.new_inventory()
    inv.add(POLICY["make_fill"](0.0, "UP", "taker", D("0.34"), D("1"), "x"))
    bk = {"bid": D("0.10"), "ask": D("0.25"), "bid_size": D("9"),
          "ask_size": D("9")}
    row = P.decision_telemetry("T", inv, bk, 200.0,
                               {"action": "HOLD", "reason": "too dear"},
                               t=2.0, n_extends=0)
    assert row["policy_action"] == "HOLD"
    assert row["opposite_executable_ask"] == "0.90"      # 1 - 0.10
    assert row["complement_reachable_at_0.96"] is False  # 0.90 > 0.62
    BUD.record_telemetry(CID, row, root=env)
    assert BUD.telemetry(CID, root=env)[-1]["opposite_executable_ask"] \
        == "0.90"
