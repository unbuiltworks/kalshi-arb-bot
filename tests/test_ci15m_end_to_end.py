"""The exact desired live chain, end to end, on real producer schemas.

    discover eligible BTC15m
      -> real ladder 0.60/0.70
      -> positions succeeds with []
      -> frozen policy OPEN UP @ 0.70
      -> Treatment T payload: BUY q1 LIMIT 0.70 makerOrCancel=false
      -> ACK accepted (terminal FILLED, or non-terminal then polled)
      -> terminal history reports FILLED 1 @ 0.70
      -> positions reports 1 UP
      -> reconciliation yields residual UP q1 / $0.70
      -> the frozen policy makes its NEXT decision

The next decision may be HOLD, EXTEND or COMPLEMENT depending on the next
book. It must NEVER be another OPEN. Nothing here contacts a venue: every
producer is replaced by a response in its DOCUMENTED shape, and the
adapters are the real ones.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D

import pytest

from src.gemini_execution import ci15m_adapters as AD
from src.gemini_execution import ci15m_budget as BUD
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import ci15m_treatments as TREAT
from src.gemini_execution import metadata, payload, transport
from src.gemini_execution.ci15m_policy import POLICY, QUANTITY
from src.gemini_execution.continuous_probe import (COMPLETE, EXTEND, HOLD,
                                                   OPEN)

SYMBOL = "GEMI-BTC15M2610031845-UP"
TICKER = "BTC15M2610031845"
CID = "CI15M-E2E"

#: producer-shaped responses, each matching the contract its producer emits
LADDER = {"readable": True, "snapshot_age_s": 0.2, "venue_ts": "1",
          "bids": {"best": "0.60", "best_qty": "500", "levels": []},
          "asks": {"best": "0.70", "best_qty": "400", "levels": []}}
CONTRACT_PRICES = {"readable": True, "identity_consistent": True,
                   "yes": {"bid": "0.58", "ask": "0.66"},
                   "no": {"bid": "0.34", "ask": "0.42"}}
POSITIONS_EMPTY = {"path": "/v1/prediction-markets/positions", "rows": [],
                   "status": 200, "ok": True, "total": 0}
POSITIONS_ONE_UP = {"path": "/v1/prediction-markets/positions", "status": 200,
                    "ok": True, "total": 1,
                    "rows": [{"symbol": SYMBOL, "outcome": "yes",
                              "totalQuantity": "1"}]}


def _ack(status, filled):
    """transport.place's documented envelope around a venue order object."""
    return {"ok": True, "status": 201, "classification": "ACCEPTED",
            "request": {"makerOrCancel": False},
            "body": {"orderId": 145828836799999001, "symbol": SYMBOL,
                     "side": "buy", "outcome": "yes", "orderType": "limit",
                     "timeInForce": "good-til-cancel", "price": "0.70",
                     "quantity": "1", "filledQuantity": filled,
                     "remainingQuantity": str(1 - int(float(filled))),
                     "status": status,
                     "createdAt": "2026-10-03T18:38:00.000Z"}}


HISTORY_FILLED = {
    "readable": True, "verdict": "ORDERS_FOUND", "total_rows": 1,
    "path": metadata.ORDER_HISTORY_PATH, "symbol": SYMBOL,
    "unreadable_statuses": [],
    "per_status": [{"status_filter": "filled", "orders": [
        {"orderId": 145828836799999001, "symbol": SYMBOL, "status": "filled",
         "filledQuantity": "1", "remainingQuantity": "0", "price": "0.70",
         "outcome": "yes", "side": "buy"}]}]}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = str(tmp_path / "cohorts")
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda c, **k: {"readable": True, "slots": [],
                                        "count": 0, "why": None})
    monkeypatch.setattr(P, "AUTHORIZED_POLICY_FINGERPRINT",
                        __import__("src.gemini_execution.ci15m_policy",
                                   fromlist=["fingerprint"]).fingerprint())
    BUD.create(CID, root=root, profile=BUD.MECHANICS)
    BUD.open_window(CID, TICKER, root=root)
    return root


def _run_chain(monkeypatch, env, *, ack_status, ack_filled, next_book):
    """Walk the chain with the REAL adapters and a stubbed venue."""
    trace = {}

    # --- 1. discovery, in the producer's shape
    events = [{"ticker": TICKER, "contracts": [
        {"strike": {"value": "84853.9",
                    "availableAt": "2026-10-03T18:30:00Z"},
         "expiryDate": "2026-10-03T18:45:00.000Z"}]}]
    mkts = AD.markets_of(events)
    assert len(mkts) == 1 and mkts[0].strike_published
    now = (mkts[0].expiry_ts or 0) - 400.0
    sel = P.select_market(
        [{"symbol": m.symbol, "strike": {"value": m.strike_value,
                                         "availableAt": m.strike_available_at},
          "expiry_ts": m.expiry_ts} for m in mkts], now)
    assert sel["selected"], sel["considered"]
    trace["discovered"] = sel["selected"]["ticker"]

    # --- 2. the executable ladder
    monkeypatch.setattr(metadata, "contract_depth", lambda s, **k: LADDER)
    monkeypatch.setattr(metadata, "outcome_book", lambda s: CONTRACT_PRICES)
    bk = P.book_from_venue(SYMBOL)
    assert bk["readable"] and bk["bid"] == D("0.60") and bk["ask"] == D("0.70")
    tel = bk["contract_price_telemetry"]
    assert tel["contract_ask"] == "0.66" and tel["ladder_ask"] == "0.70"
    trace["ladder"] = (str(bk["bid"]), str(bk["ask"]))

    # --- 3. the AUTHENTICATED FILL RECORD is the authority; positions is
    #        telemetry only (for BTC15m it has never carried a contract).
    state = {"filled": False}

    async def hist(**kw):
        assert kw.get("include_rows") is True
        orders = [] if not state["filled"] else [{
            "orderId": 145828836799999001, "symbol": SYMBOL, "side": "buy",
            "outcome": "yes", "status": "filled", "price": "0.70",
            "quantity": "1", "filledQuantity": "1",
            "avgExecutionPrice": "0.70"}]
        return {"readable": True, "verdict": "ORDERS_FOUND",
                "total_rows": len(orders), "symbol": SYMBOL,
                "unreadable_statuses": [],
                "per_status": [{"status_filter": "filled",
                                "orders": orders}]}

    async def my(sym):
        rows = [] if not state["filled"] else [{
            "tid": 1, "order_id": "145828836799999001", "symbol": SYMBOL,
            "price": "0.70", "amount": "1", "fee_amount": "0.02",
            "aggressor": True, "timestampms": 1}]
        return {"rows": rows, "status": 200}

    async def positions():
        return POSITIONS_EMPTY
    monkeypatch.setattr(metadata, "order_history", hist)
    monkeypatch.setattr(P, "_mytrades", my)
    monkeypatch.setattr(metadata, "positions", positions)

    inv = P.new_inventory()
    rec0 = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=env))
    assert rec0["ok"] and rec0["signed"] == D("0")
    trace["positions_before"] = str(rec0["signed"])

    # --- 4. the frozen policy decides
    dec = POLICY["decide_once"](inv, bid=bk["bid"], ask=bk["ask"],
                                bid_size=bk["bid_size"],
                                ask_size=bk["ask_size"], tte=400.0,
                                qty=QUANTITY, n_extends=0, actions=0)
    assert dec["action"] == OPEN and dec["side"] == "UP"
    assert dec["price"] == D("0.70"), "the ladder ask, not the contract price"
    trace["decision"] = (dec["action"], dec["side"], str(dec["price"]))

    # --- 5. Treatment T payload
    t = TREAT.taker_price("UP", bk["bid"], bk["ask"],
                          ceiling=POLICY["HARD_MAX_PRICE"])
    assert t["ok"] and t["price"] == D("0.70") and t["maker_or_cancel"] is False
    intent = P.intent_of(TICKER, inv, dec, 0, reconciled_signed="0",
                         ladder_bid=bk["bid"], ladder_ask=bk["ask"],
                         telemetry=tel)
    body = P.assert_payload_is_q1(SYMBOL, intent)
    assert body == {"symbol": SYMBOL, "orderType": "limit", "side": "buy",
                    "quantity": "1", "price": "0.70", "outcome": "yes",
                    "timeInForce": "good-til-cancel",
                    "makerOrCancel": False}
    trace["payload"] = body

    # --- 6. submit; the ACK may be terminal or not
    row = BUD.consume_slot(CID, TICKER, intent, root=env, remote=None)
    assert P.authorized_intents(CID, root=env), (
        "the submission must be durably authorized before the venue fill "
        "can be attributed back to it")
    ack = AD.PlaceAck.of(_ack(ack_status, ack_filled))
    assert ack.ok
    cls = P.classify_ack(_ack(ack_status, ack_filled)["body"])
    trace["ack"] = cls["state"]

    # --- 7. poll to terminal when the ack is not
    if not cls["terminal"]:
        state["filled"] = True
        monkeypatch.setattr(metadata, "order_history",
                            lambda **kw: _done(HISTORY_FILLED))
        term = asyncio.run(P.poll_terminal(
            CID, SYMBOL, 145828836799999001, attempts=2, pause=0,
            root=env, sleeper=_nosleep))
        assert term["resolved"] and term["state"] == P.TERMINAL_FILLED
        filled_qty, filled_px = term["filled"], D(term["row"]["price"])
    else:
        filled_qty, filled_px = cls["filled"], D("0.70")
    assert filled_qty == D("1") and filled_px == D("0.70")
    trace["terminal"] = ("FILLED", str(filled_qty), str(filled_px))

    # --- 8. inventory + authoritative reconciliation FROM THE FILL RECORD
    state["filled"] = True
    monkeypatch.setattr(metadata, "order_history", hist)
    inv.add(POLICY["make_fill"](0.0, "UP", "taker", filled_px, filled_qty,
                                "e2e OPEN"))
    rec1 = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=env,
                                              ours=inv))
    assert rec1["ok"] and rec1["signed"] == D("1"), rec1
    assert BUD.halted(CID, root=env) is None
    assert inv.residual_side == "UP" and inv.residual == D("1")
    assert inv.c_up == D("0.70")
    trace["reconciled"] = {"venue": str(rec1["signed"]),
                           "residual_side": inv.residual_side,
                           "residual_qty": str(inv.residual),
                           "residual_principal": str(inv.residual
                                                     * inv.avg_up)}

    # --- 9. the frozen policy's NEXT decision, on the supplied next book
    nb, na = next_book
    nxt = POLICY["decide_once"](inv, bid=nb, ask=na, bid_size=500,
                                ask_size=500, tte=300.0, qty=QUANTITY,
                                n_extends=0, actions=1)
    trace["next"] = (nxt["action"], nxt.get("side"),
                     None if nxt.get("price") is None else str(nxt["price"]))
    return trace, nxt, inv


async def _nosleep(_s):
    return None


async def _done(v):
    return v


# =====================================================================
# the chain, with a TERMINAL ack
# =====================================================================
def test_the_full_chain_with_a_terminal_filled_ack_then_hold(env,
                                                             monkeypatch):
    # next book 0.10/0.20: DOWN costs 0.90 -> pair 1.60, refused;
    # UP at 0.20 is 50c under the 0.70 average -> an EXTEND qualifies.
    trace, nxt, inv = _run_chain(monkeypatch, env, ack_status="filled",
                                 ack_filled="1", next_book=(D("0.10"),
                                                            D("0.20")))
    assert trace["ack"] == P.ACK_FILLED
    assert trace["reconciled"]["residual_qty"] == "1"
    assert trace["reconciled"]["residual_principal"] == "0.70"
    assert nxt["action"] != OPEN, "a second OPEN must never be produced"
    assert nxt["action"] == EXTEND


def test_the_full_chain_with_a_NONTERMINAL_ack(env, monkeypatch):
    """The CI15M-1 shape: the ack says `open`, so it must be POLLED to a
    terminal state rather than recorded as NOT_FILLED."""
    trace, nxt, inv = _run_chain(monkeypatch, env, ack_status="open",
                                 ack_filled="0",
                                 next_book=(D("0.10"), D("0.20")))
    assert trace["ack"] == P.ACK_RESTING
    assert trace["terminal"] == ("FILLED", "1", "0.70")
    assert trace["reconciled"]["venue"] == "1"
    assert nxt["action"] != OPEN


@pytest.mark.parametrize("next_book,expect", [
    # holding 1 UP at an average of 0.70:
    #   EXTEND needs ask <= 0.65; COMPLEMENT needs 1-bid <= 0.96-0.70 = 0.26
    ((D("0.10"), D("0.20")), EXTEND),    # UP 0.20, 50c better; DOWN 0.90 x
    ((D("0.30"), D("0.40")), EXTEND),    # UP 0.40, 30c better; DOWN 0.70 x
    ((D("0.68"), D("0.72")), HOLD),      # UP 0.72 not 5c better; DOWN 0.32 x
    ((D("0.80"), D("0.95")), COMPLETE),  # UP over ceiling; DOWN 0.20, pair .90
])
def test_the_next_decision_is_never_another_open(env, monkeypatch,
                                                 next_book, expect):
    """The chain passes whether the next action is HOLD, EXTEND or
    COMPLEMENT -- what it forbids is a second OPEN. The expectation is
    asserted too, so this cannot pass by accident."""
    trace, nxt, inv = _run_chain(monkeypatch, env, ack_status="filled",
                                 ack_filled="1", next_book=next_book)
    assert nxt["action"] == expect, (next_book, nxt["reason"][:200])
    assert nxt["action"] != OPEN


def test_a_complement_reaching_the_pair_ceiling_is_taken(env, monkeypatch):
    """With 1 UP at 0.70, DOWN must cost <= 0.26 to reach a 0.96 pair, so
    the ladder bid must be >= 0.74."""
    trace, nxt, inv = _run_chain(monkeypatch, env, ack_status="filled",
                                 ack_filled="1",
                                 next_book=(D("0.80"), D("0.95")))
    assert nxt["action"] == COMPLETE and nxt["side"] == "DOWN"
    assert nxt["price"] == D("0.20")          # 1 - 0.80
    assert nxt["action"] != OPEN


# =====================================================================
# 4. the safety behaviour that saved CI15M-2 must still hold
# =====================================================================
def test_an_unreadable_fill_record_still_halts(env, monkeypatch):
    """The AUTHORITY moved to the fill record; the fail-closed rule did
    not. An unreadable fill record still HALTs."""
    async def bad(**kw):
        return {"readable": False, "verdict": "UNREADABLE",
                "unreadable_statuses": ["filled"], "total_rows": 0}
    monkeypatch.setattr(metadata, "order_history", bad)
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=env))
    assert rec["ok"] is False
    assert "UNKNOWN_POSITION" in BUD.halted(CID, root=env)


def _authorize(root, specs):
    """Seed the DURABLE authorized submissions a venue fill presupposes.
    A fill matching no authorization is FOREIGN and halts on its own; these
    tests exercise what happens when the fills ARE ours."""
    for slot, (outcome, price) in enumerate(specs):
        BUD._append_durable(BUD._paths(CID, root)["ledger"], {
            "kind": "PLACE", "slot": slot, "cohort_id": CID,
            "utc": "2026-10-03T19:00:00+00:00", "ticker": TICKER,
            "status": "SUBMITTED",
            "intent": {"action": "OPEN", "side": "UP", "outcome": outcome,
                       "price": price, "quantity": "1"}})


def test_a_fill_record_ledger_disagreement_still_halts(env, monkeypatch):
    _authorize(env, [("yes", "0.70"), ("yes", "0.70")])

    async def two(**kw):
        return {"readable": True, "verdict": "ORDERS_FOUND", "total_rows": 2,
                "symbol": SYMBOL, "unreadable_statuses": [],
                "per_status": [{"status_filter": "filled", "orders": [
                    {"orderId": 1, "symbol": SYMBOL, "outcome": "yes",
                     "status": "filled", "price": "0.70",
                     "filledQuantity": "1"},
                    {"orderId": 2, "symbol": SYMBOL, "outcome": "yes",
                     "status": "filled", "price": "0.70",
                     "filledQuantity": "1"}]}]}

    async def my(sym):
        return {"rows": [
            {"tid": 1, "order_id": "1", "price": "0.70", "amount": "1"},
            {"tid": 2, "order_id": "2", "price": "0.70", "amount": "1"}],
            "status": 200}
    monkeypatch.setattr(metadata, "order_history", two)
    monkeypatch.setattr(P, "_mytrades", my)
    inv = P.new_inventory()
    inv.add(POLICY["make_fill"](0.0, "UP", "taker", D("0.70"), QUANTITY, "x"))
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=env,
                                             ours=inv))
    assert rec["ok"] is False
    assert "INVENTORY_DISAGREEMENT" in BUD.halted(CID, root=env)


def test_a_successful_empty_fill_record_is_flat_not_unknown(env,
                                                            monkeypatch):
    """The fix is correct interpretation of SUCCESSFUL reads -- not
    assuming missing information means flat."""
    async def none(**kw):
        return {"readable": True, "verdict": "NO_ORDERS_ON_RECORD",
                "total_rows": 0, "symbol": SYMBOL,
                "unreadable_statuses": [],
                "per_status": [{"status_filter": "filled", "orders": []}]}

    async def my(sym):
        return {"rows": [], "status": 200}
    monkeypatch.setattr(metadata, "order_history", none)
    monkeypatch.setattr(P, "_mytrades", my)
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=env))
    assert rec["ok"] and rec["signed"] == D("0")
    assert BUD.halted(CID, root=env) is None


def test_a_successful_empty_positions_is_only_telemetry(env, monkeypatch):
    """positions() empty must NOT contradict a reconstructed fill ledger."""
    _authorize(env, [("yes", "0.70")])

    async def one(**kw):
        return {"readable": True, "verdict": "ORDERS_FOUND", "total_rows": 1,
                "symbol": SYMBOL, "unreadable_statuses": [],
                "per_status": [{"status_filter": "filled", "orders": [
                    {"orderId": 1, "symbol": SYMBOL, "outcome": "yes",
                     "status": "filled", "price": "0.70",
                     "filledQuantity": "1"}]}]}

    async def my(sym):
        return {"rows": [{"tid": 1, "order_id": "1", "price": "0.70",
                          "amount": "1"}], "status": 200}

    async def empty():
        return POSITIONS_EMPTY
    monkeypatch.setattr(metadata, "order_history", one)
    monkeypatch.setattr(P, "_mytrades", my)
    monkeypatch.setattr(metadata, "positions", empty)
    rec = asyncio.run(P.reconciled_inventory(CID, SYMBOL, root=env))
    assert rec["ok"] and rec["signed"] == D("1")
    assert rec["positions_telemetry"]["agrees_with_fills"] is False
    assert BUD.halted(CID, root=env) is None
