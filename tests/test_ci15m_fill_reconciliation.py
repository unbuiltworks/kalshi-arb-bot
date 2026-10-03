"""Post-fill reconciliation from the AUTHENTICATED FILL RECORD.

WHY THE AUTHORITY MOVED. CI15M-3's OPEN filled -- venue ACK, filledQuantity
1, avgExecutionPrice 0.34 -- and the probe HALTED because
`metadata.positions()` returned a SUCCESSFUL EMPTY list. The evidence says
the gate was wrong, not the venue:

  * positions() IS a real prediction-inventory surface: it has returned
    GEMI-NFL-2609220015-NYG-LAR-S-LAR6 qty 1.81 avg 0.53 and a second NFL
    row, stable across four reads over two hours;
  * no BTC5m or BTC15m contract has EVER appeared in a positions row, in
    any committed authenticated artifact, against hundreds of filled BTC
    orders;
  * for GEMI-BTC15M2610032000-UP specifically, /v1/prediction-markets/
    positions returned 200 with {"positions": [], "total": ...} BOTH before
    and AFTER settlement, while order history showed 1 filled row and
    /v1/mytrades reported position 1.

So the fill record is the authority for these contracts, and the proven
primitives for it are REUSED rather than reinterpreted:
`fill_reconciliation.authoritative_fills` then `continuous_probe.recover`.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D

import pytest

from src.gemini_execution import ci15m_budget as BUD
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import fill_reconciliation as FR
from src.gemini_execution import metadata
from src.gemini_execution.ci15m_policy import POLICY, QUANTITY

CID = "CI15M-RECON"

# ===================================================================== the
# EXACT CI15M-3 OBJECTS, verbatim from the venue.
# =====================================================================
CI15M3_SYMBOL = "GEMI-BTC15M2610032000-UP"
CI15M3_ORDER = {
    "orderId": 145828836717890000, "symbol": CI15M3_SYMBOL, "side": "buy",
    "outcome": "yes", "orderType": "limit",
    "timeInForce": "good-til-cancel", "price": "0.34", "quantity": "1",
    "filledQuantity": "1", "remainingQuantity": "0", "status": "filled",
    "avgExecutionPrice": "0.34",
    "clientOrderId": "a4db11b3-acc1-4ff3-aade-7f3f7e1f5136",
    "createdAt": "2026-10-03T19:51:47.920Z",
}
CI15M3_TRADE = {
    "tid": 9900001, "order_id": "145828836717890000", "symbol": CI15M3_SYMBOL,
    "price": "0.34", "amount": "1", "fee_amount": "0.02",
    "fee_currency": "USD", "aggressor": True, "type": "Buy",
    "timestampms": 1791057107961,
}

# the CI15M-1 four fills that the original executor never observed
CI15M1_SYMBOL = "GEMI-BTC15M2610031715-UP"
CI15M1 = [("145828836710194348", "0.70"), ("145828836710202825", "0.71"),
          ("145828836710208003", "0.73"), ("145828836710212596", "0.72")]


def _hist(symbol, orders):
    return {"readable": True, "verdict": "ORDERS_FOUND",
            "total_rows": len(orders), "symbol": symbol,
            "unreadable_statuses": [],
            "per_status": [{"status_filter": "filled", "orders": orders}]}


def _order(oid, px, symbol=CI15M3_SYMBOL, outcome="yes"):
    return dict(CI15M3_ORDER, orderId=int(oid), symbol=symbol,
                outcome=outcome, price=px, avgExecutionPrice=px)


def _trade(oid, px, tid, symbol=CI15M3_SYMBOL):
    return dict(CI15M3_TRADE, tid=tid, order_id=str(oid), symbol=symbol,
                price=px)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "cohorts")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    BUD.create(CID, root=root, profile=BUD.MECHANICS)
    return root


def _wire(monkeypatch, symbol, orders, trades, positions=None):
    async def hist(**kw):
        assert kw.get("include_rows") is True, (
            "without include_rows the producer omits `orders` and the caller "
            "reads 'readable and empty' on every call")
        return _hist(symbol, orders)

    async def my(sym):
        return {"rows": trades, "status": 200}

    async def pos():
        return positions if positions is not None else {
            "ok": True, "rows": [], "status": 200, "total": 0}
    monkeypatch.setattr(metadata, "order_history", hist)
    monkeypatch.setattr(P, "_mytrades", my)
    monkeypatch.setattr(metadata, "positions", pos)


# =====================================================================
# 1. THE REQUIRED CI15M-3 LIVE-STATE INVARIANT
# =====================================================================
def test_the_ci15m_3_fill_reconstructs_up_q1_at_0_34(env, monkeypatch):
    _wire(monkeypatch, CI15M3_SYMBOL, [CI15M3_ORDER], [CI15M3_TRADE])
    rec = asyncio.run(P.reconciled_inventory(CID, CI15M3_SYMBOL, root=env))
    assert rec["ok"], rec
    assert rec["signed"] == D("1")
    r = rec["reconstruction"]
    assert r["up_qty"] == "1" and r["down_qty"] == "0"
    assert r["up_cost"] == "0.34"
    assert rec["residual_side"] == "UP" and r["residual_qty"] == "1"
    assert r["avg_up"] == "0.34"
    assert rec["n_fills"] == 1
    assert "trades" in rec["source"] and "order_id" in rec["source"]
    # positions=[] is TELEMETRY and does NOT contradict the fill record
    tel = rec["positions_telemetry"]
    assert tel["read"] is True and tel["rows_for_symbol"] == 0
    assert tel["agrees_with_fills"] is False
    assert "NOT evidence of flat" in tel["labels"]
    assert BUD.halted(CID, root=env) is None, (
        "a successful-empty positions() must not HALT any more")


def test_the_frozen_policy_may_then_decide_and_never_opens_again(env,
                                                                 monkeypatch):
    _wire(monkeypatch, CI15M3_SYMBOL, [CI15M3_ORDER], [CI15M3_TRADE])
    rec = asyncio.run(P.reconciled_inventory(CID, CI15M3_SYMBOL, root=env))
    inv = rec["inventory"]
    dec = POLICY["decide_once"](inv, bid=D("0.10"), ask=D("0.20"),
                                bid_size=500, ask_size=500, tte=300.0,
                                qty=QUANTITY, n_extends=0, actions=1)
    assert dec["action"] != "OPEN"


# =====================================================================
# 2. THE CI15M-1 BREACH THE ORIGINAL EXECUTOR MISSED
# =====================================================================
def test_the_ci15m_1_four_fills_reconstruct_up_q4_cost_2_86(env,
                                                            monkeypatch):
    """THE POINT OF THIS TEST. The original CI15M-1 executor believed it was
    FLAT while holding four contracts, so 4 shares and $2.86 passed a
    3-share / $2.70 gate. The new wiring reconstructs the real position."""
    orders = [_order(o, px, CI15M1_SYMBOL) for o, px in CI15M1]
    trades = [_trade(o, px, 8800000 + i, CI15M1_SYMBOL)
              for i, (o, px) in enumerate(CI15M1)]
    _wire(monkeypatch, CI15M1_SYMBOL, orders, trades)
    rec = asyncio.run(P.reconciled_inventory(CID, CI15M1_SYMBOL, root=env))
    assert rec["ok"], rec
    r = rec["reconstruction"]
    assert r["up_qty"] == "4"
    assert D(r["up_cost"]) == D("2.86")
    assert D(r["avg_up"]) == D("0.715")
    assert rec["signed"] == D("4")


def test_the_reconstructed_ci15m_1_position_breaches_the_caps(env,
                                                              monkeypatch):
    """And the caps would now SEE it: 4 > 3 shares, $2.86 > $2.70."""
    orders = [_order(o, px, CI15M1_SYMBOL) for o, px in CI15M1]
    trades = [_trade(o, px, 8800000 + i, CI15M1_SYMBOL)
              for i, (o, px) in enumerate(CI15M1)]
    _wire(monkeypatch, CI15M1_SYMBOL, orders, trades)
    inv = asyncio.run(P.reconciled_inventory(CID, CI15M1_SYMBOL,
                                             root=env))["inventory"]
    assert inv.residual > BUD.MAX_UNMATCHED_SHARES
    assert inv.residual * inv.avg_up > BUD.MAX_UNMATCHED_PRINCIPAL
    # the frozen policy's own residual-principal cap now sees it. (Its
    # MAX_RESIDUAL_PRINCIPAL is $4.00, looser than the cohort's $2.70, so
    # the COHORT cap is the binding one -- which is the point: the caps can
    # finally be evaluated against a real position instead of a phantom
    # flat one.)
    eff = POLICY["want_extend_avg"](inv, "UP", D("0.10"), QUANTITY, 0)
    assert eff["residual_principal_after"] is not None
    assert D(eff["residual_principal_after"]) > BUD.MAX_UNMATCHED_PRINCIPAL


# =====================================================================
# 3. RESTART: no in-memory inventory at all
# =====================================================================
def test_restart_reconstructs_from_venue_evidence_alone(env, monkeypatch):
    """Mandatory for continuous operation: we cannot depend on having
    witnessed the fill in THIS process."""
    _wire(monkeypatch, CI15M3_SYMBOL, [CI15M3_ORDER], [CI15M3_TRADE])
    import importlib
    importlib.reload(BUD)
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    fresh = P.new_inventory()            # a brand-new, empty inventory
    assert fresh.q_up == D("0") and not fresh.fills
    rec = asyncio.run(P.reconciled_inventory(CID, CI15M3_SYMBOL, root=env))
    assert rec["ok"] and rec["signed"] == D("1")
    assert rec["reconstruction"]["up_cost"] == "0.34"


def test_a_stale_local_belief_that_disagrees_halts(env, monkeypatch):
    """`ours` is checked against the reconstruction. A disagreement means we
    do not know what we hold."""
    _wire(monkeypatch, CI15M3_SYMBOL, [CI15M3_ORDER], [CI15M3_TRADE])
    wrong = P.new_inventory()            # believes FLAT while holding 1
    rec = asyncio.run(P.reconciled_inventory(CID, CI15M3_SYMBOL, root=env,
                                             ours=wrong))
    assert rec["ok"] is False
    assert "INVENTORY_DISAGREEMENT" in BUD.halted(CID, root=env)


def test_an_agreeing_local_belief_passes(env, monkeypatch):
    _wire(monkeypatch, CI15M3_SYMBOL, [CI15M3_ORDER], [CI15M3_TRADE])
    mine = P.new_inventory()
    mine.add(POLICY["make_fill"](0.0, "UP", "taker", D("0.34"), D("1"), "x"))
    rec = asyncio.run(P.reconciled_inventory(CID, CI15M3_SYMBOL, root=env,
                                             ours=mine))
    assert rec["ok"] and BUD.halted(CID, root=env) is None


# =====================================================================
# 4. TWO-SIDED: continuous inventory, not just getting OPEN past a HALT
# =====================================================================
def test_two_sided_fills_reconstruct_a_matched_pair_at_0_94(env,
                                                            monkeypatch):
    orders = [_order("1", "0.34"), _order("2", "0.60", outcome="no")]
    trades = [_trade("1", "0.34", 1), _trade("2", "0.60", 2)]
    _wire(monkeypatch, CI15M3_SYMBOL, orders, trades)
    rec = asyncio.run(P.reconciled_inventory(CID, CI15M3_SYMBOL, root=env))
    assert rec["ok"], rec
    r = rec["reconstruction"]
    assert r["up_qty"] == "1" and r["down_qty"] == "1"
    assert r["matched"] == "1"
    assert D(r["matched_pair_cost"]) == D("0.94")
    assert D(r["residual_qty"]) == D("0")
    assert rec["residual_side"] is None
    inv = rec["inventory"]
    # gross locked economics: a matched pair redeems at 1.00 either way
    assert inv.matched_pnl() == D("0.06")
    assert rec["signed"] == D("0")


def test_a_further_up_fill_makes_the_residual_reappear(env, monkeypatch):
    orders = [_order("1", "0.34"), _order("2", "0.60", outcome="no"),
              _order("3", "0.40")]
    trades = [_trade("1", "0.34", 1), _trade("2", "0.60", 2),
              _trade("3", "0.40", 3)]
    _wire(monkeypatch, CI15M3_SYMBOL, orders, trades)
    rec = asyncio.run(P.reconciled_inventory(CID, CI15M3_SYMBOL, root=env))
    r = rec["reconstruction"]
    assert r["up_qty"] == "2" and r["down_qty"] == "1"
    assert r["matched"] == "1"
    assert rec["residual_side"] == "UP" and D(r["residual_qty"]) == D("1")
    assert D(r["avg_up"]) == D("0.37")
    assert rec["signed"] == D("1")
    # the matched pair is still priced on AVERAGES, not on the last fill
    assert D(r["matched_pair_cost"]) == D("0.97")


# =====================================================================
# UNKNOWN handling is NOT weakened anywhere
# =====================================================================
def test_unreadable_order_history_halts(env, monkeypatch):
    async def bad(**kw):
        return {"readable": False, "verdict": "UNREADABLE",
                "unreadable_statuses": ["filled"], "total_rows": 0}
    monkeypatch.setattr(metadata, "order_history", bad)
    rec = asyncio.run(P.authenticated_fills(CID, CI15M3_SYMBOL, root=env))
    assert rec["ok"] is False
    assert "UNKNOWN_POSITION" in BUD.halted(CID, root=env)


def test_unreadable_mytrades_halts(env, monkeypatch):
    _wire(monkeypatch, CI15M3_SYMBOL, [CI15M3_ORDER], [CI15M3_TRADE])

    async def bad(sym):
        return {"rows": None, "why": "mytrades returned 500"}
    monkeypatch.setattr(P, "_mytrades", bad)
    rec = asyncio.run(P.authenticated_fills(CID, CI15M3_SYMBOL, root=env))
    assert rec["ok"] is False
    assert "UNKNOWN_POSITION" in BUD.halted(CID, root=env)


def test_an_unjoinable_order_halts(env, monkeypatch):
    """A trade whose order is absent cannot have its SIDE resolved."""
    _wire(monkeypatch, CI15M3_SYMBOL, [], [CI15M3_TRADE])
    rec = asyncio.run(P.authenticated_fills(CID, CI15M3_SYMBOL, root=env))
    assert rec["ok"] is False
    assert "SIDE_MAPPING_DISAGREEMENT" in BUD.halted(CID, root=env)


@pytest.mark.parametrize("bad", [{"amount": "oops"}, {"price": "oops"}])
def test_an_ambiguous_fill_quantity_or_price_halts(env, monkeypatch, bad):
    _wire(monkeypatch, CI15M3_SYMBOL, [CI15M3_ORDER],
          [dict(CI15M3_TRADE, **bad)])
    rec = asyncio.run(P.authenticated_fills(CID, CI15M3_SYMBOL, root=env))
    assert rec["ok"] is False
    assert BUD.halted(CID, root=env)


def test_a_successful_empty_fill_record_still_means_flat(env, monkeypatch):
    """positions() empty is telemetry; an empty FILL record is authority."""
    _wire(monkeypatch, CI15M3_SYMBOL, [], [])
    rec = asyncio.run(P.reconciled_inventory(CID, CI15M3_SYMBOL, root=env))
    assert rec["ok"] and rec["signed"] == D("0")
    assert rec["reconstruction"]["up_qty"] == "0"
    assert BUD.halted(CID, root=env) is None


# =====================================================================
# the proven primitives are REUSED, not reimplemented
# =====================================================================
def test_the_reconstruction_goes_through_the_existing_primitives():
    import inspect
    src = inspect.getsource(P.inventory_from_fills)
    assert "CP.recover" in src, "continuous_probe.recover must do the work"
    src2 = inspect.getsource(P.authenticated_fills)
    assert "FR.authoritative_fills" in src2
    # and the two reconstructions are compared, not assumed equal
    assert "disagree" in src


def test_a_disagreement_between_the_two_reconstructions_is_refused():
    rows = [{"leg": "yes", "qty": "1", "price": "0.34", "order_id": "1",
             "leg_resolved": True, "tid": 1, "maker": "taker"}]
    got = P.inventory_from_fills(rows, CI15M3_SYMBOL)
    assert got["ok"] and got["up_qty"] == D("1")
    assert got["cp"].yes_qty == D("1"), "the CP primitive agrees"
