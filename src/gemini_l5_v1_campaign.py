"""v1-DRIVEN q1 campaign: the frozen L5 model controls the actions.

The v1 policy is the decision maker for opening, post-fill relation, and
completion timing; the proven transport/cancel/reservation/settlement
shell is reused verbatim. Pre-GO review fixes incorporated:

  * STATUS CONTRACT — terminal statuses are declared here and recognized
    by the outer owner (MATCHED_V1_COMPLETION as matched;
    V1_ABSTAIN_FLAT / NO_FEE_SAFE_MAKER_PRICE as no-trade).
  * V1 STATE FIDELITY — after the first authoritative fill, v1 is re-asked
    with the ACTUAL EXECUTION PRICE of that fill (the reconciler's field
    chain avgExecutionPrice -> avg_execution_price -> price; fail-closed
    if a filled row carries none) as `last_price`, held fixed until
    another fill; only TTE and residual evolve between re-evaluations.
    Completion economics also use the actual fill price.
  * DURABLE EXACT-ID SAFETY — every client ID is appended to the durable
    cycle state and fsync'd BEFORE its order.place call, so exception
    cleanup always knows every ID that could exist at the venue. After a
    complement post, `_find_row` must return a row or the campaign fails
    closed. Before any terminal return every accepted ID is PROVEN
    inactive via the shell's `_exact_active` check.
  * TRANSFER_INITIALIZER LABELING — v1's flat states predict the next
    RELATION, not a directional first side; the opening side comes from
    the Gemini risk/queue adapter and is explicitly labeled
    `TRANSFER_INITIALIZER`, not validated L5 side/timing behavior.

Decision contract (pure, unit-tested):
  decide_flat: in-distribution + modal "open" -> OPEN_ONE_SIDE else ABSTAIN.
  decide_after_fill: modal "complete" -> COMPLETE; modal "extend" ->
  ABSTAIN_EXTEND_CAPPED (q=1 residual cap; re-asked as tte decays);
  off-distribution / "sell" -> ABSTAIN. No automatic complement exists.

Strict accounting persisted in the durable state: per-order limit price,
authoritative execution price, filled qty, maker fees, turnover, matched
and residual qty, every policy decision, settlement_required.
realized_pnl stays None until Gemini settlement; rebates/rewards are
accounted separately. Dual-key arming identical to the pair engine.
NOT armed here.
"""
from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal as D
from typing import Any, Mapping, Optional

from src import gemini_l5_ladder_q1 as ladder
from src import gemini_l5_pair_q1 as pair
from src import gemini_l5_v1_policy as v1_policy
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

ONE = D("1")
ZERO = D("0")
MIN_PRICE = pair.MIN_PRICE
MAX_PRICE = pair.MAX_PRICE
STOP_BEFORE_EXPIRY_SECONDS = pair.STOP_BEFORE_EXPIRY_SECONDS
REEVAL_INTERVAL_SECONDS = 5.0

# Terminal status contract (owner imports these)
STATUS_MATCHED = "MATCHED_V1_COMPLETION"
STATUS_RESIDUAL_HOLD = "ONE_SIDED_RESIDUAL_POLICY_HOLD"
STATUS_LATE_FILL_HOLD = "LATE_FILL_RESIDUAL_HOLD"
NO_TRADE_STATUSES = frozenset({"V1_ABSTAIN_FLAT", "NO_FEE_SAFE_MAKER_PRICE"})

INITIALIZER_LABEL = "TRANSFER_INITIALIZER"   # NOT validated L5 side/timing

# Forced-completion risk overlay (issue #167 simplified transfer config).
# EXPLICITLY a transfer/risk adapter, NOT validated L5 behavior: after the
# first authoritative fill the fee-safe reserve-bound complement is posted
# immediately, whatever v1's relation verdict says; the frozen v1 decision
# is still recomputed and logged on every re-evaluation for fidelity
# analysis. Evidence: extension study (l5-canonical@496b62ab) — modeled
# P(loss) 44.2% hold vs 33.7% forced completion; BTC-05M contra-flow
# 6.1%@45s -> 15.8%@180s, hence the longer default rest.
POSTURE_ENV = "GEMINI_L5_FORCED_COMPLETION"
FC_FIRST_WAIT_ENV = "GEMINI_L5_FC_FIRST_WAIT_SECONDS"
FC_DEFAULT_FIRST_WAIT_S = 180.0
POSTURE_LABEL = "FORCED_COMPLETION_RISK_OVERLAY"

emit = pair.emit


# ---------------- pure decisions (unit-tested, no I/O) ----------------

def decide_flat(verdict: Mapping[str, Any]) -> str:
    if not verdict.get("allow"):
        return "ABSTAIN"
    if verdict.get("modal_relation") != "open":
        return "ABSTAIN"
    return "OPEN_ONE_SIDE"


def decide_after_fill(verdict: Mapping[str, Any]) -> str:
    if not verdict.get("allow"):
        return "ABSTAIN"
    modal = verdict.get("modal_relation")
    if modal == "complete":
        return "COMPLETE"
    if modal == "extend":
        return "ABSTAIN_EXTEND_CAPPED"      # q=1 residual cap forbids extend
    return "ABSTAIN"


def forced_completion_enabled() -> bool:
    return os.getenv(POSTURE_ENV, "").strip() in {"1", "true", "TRUE", "yes", "on"}


def effective_first_wait(first_wait: float) -> float:
    """Under the forced-completion overlay the first-side maker rest
    defaults to 180s (env-overridable); otherwise unchanged. The caller
    still clamps to MIN_TTE and the pre-expiry stop."""
    if not forced_completion_enabled():
        return float(first_wait)
    return float(os.getenv(FC_FIRST_WAIT_ENV, str(FC_DEFAULT_FIRST_WAIT_S)))


def post_fill_action(verdict: Mapping[str, Any]) -> str:
    """The action actually taken after a fill. Overlay ON: always attempt
    the economically safe complement (v1 verdict logged, never suppressive).
    Overlay OFF: pure v1 (decide_after_fill). No path ever extends."""
    if forced_completion_enabled():
        return "COMPLETE"
    return decide_after_fill(verdict)


def choose_open_side(bid: D, bid_size: D, ask: D, ask_size: D) -> Optional[dict]:
    """TRANSFER_INITIALIZER risk adapter — NOT validated L5 behavior.

    v1's relation vocabulary is side-symmetric on a flat book, so the
    opening side among the two policy-equivalent "open" actions is chosen
    by economics only: fee-safe 20-80c maker price, deeper queue as
    tiebreak. Returns {side, price} or None if neither side is safe."""
    yes_price = D(str(bid))                     # join best bid = buy YES maker
    no_price = (ONE - D(str(ask)))              # join best ask = buy NO maker
    candidates = []
    if MIN_PRICE <= yes_price <= MAX_PRICE:
        candidates.append({"side": "YES", "price": yes_price,
                           "queue": D(str(bid_size))})
    if MIN_PRICE <= no_price <= MAX_PRICE:
        candidates.append({"side": "NO", "price": no_price,
                           "queue": D(str(ask_size))})
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c["queue"], c["side"]))
    return {"side": candidates[0]["side"], "price": candidates[0]["price"]}


def complement_price(first_side: str, first_exec_price: D,
                     min_edge: D) -> Optional[D]:
    """Completion-reserve bound against the ACTUAL first-fill execution
    price: complement must lock >= min_edge after both maker fees,
    inside 20-80c. Returns None if no such price exists."""
    px = (ONE - D(str(first_exec_price)) - D(str(min_edge))).quantize(pair.TICK)
    while px >= MIN_PRICE:
        y, n = ((D(str(first_exec_price)), px) if first_side == "YES"
                else (px, D(str(first_exec_price))))
        if px <= MAX_PRICE and ladder.edge(y, n) >= D(str(min_edge)):
            return px
        px -= pair.TICK
    return None


def authoritative_fill_price(row: Mapping[str, Any] | None) -> D:
    """Execution price via the settlement reconciler's exact field chain.
    Fails closed on a filled row that carries no price."""
    if not row:
        raise RuntimeError("authoritative fill row missing")
    raw = (row.get("avgExecutionPrice") or row.get("avg_execution_price")
           or row.get("price"))
    if raw in (None, ""):
        raise RuntimeError("filled row carries no execution price")
    return D(str(raw))


def _residual_shares(yes_qty: D, no_qty: D) -> float:
    return float(yes_qty - no_qty)


# ---------------- durable-first order placement ----------------

async def place_order_durable(maker: Any, state: dict, spec: dict,
                              symbol: str) -> bool:
    """Reserve-then-act: the client ID is persisted into the durable cycle
    state BEFORE order.place, so exception cleanup always knows it. On
    reject the persisted entry is marked rejected. On accept, _find_row
    must locate the exact row or we fail closed."""
    entry = {"side": spec["side"], "price": str(spec["price"]),
             "id": spec["id"], "accepted": False}
    state.setdefault("orders", []).append(entry)
    pair._write_state(state)
    ack = await maker.request("order.place", {
        "symbol": symbol, "side": "BUY", "type": "LIMIT",
        "timeInForce": "MOC", "price": str(spec["price"]),
        "quantity": "1", "eventOutcome": spec["side"],
        "clientOrderId": spec["id"]}, timeout=4.0)
    if int(ack.get("status", 0)) != 200:
        entry["accepted"] = False
        entry["rejected"] = True
        pair._write_state(state)
        return False
    entry["accepted"] = True
    pair._write_state(state)
    spec["row"] = await ladder.base._find_row(spec["id"], symbol)
    if spec["row"] is None:
        raise RuntimeError(
            f"accepted exact v1 order not discoverable id={spec['id']}")
    return True


async def prove_all_inactive(specs: list, symbol: str) -> None:
    """Before any terminal return: every accepted exact ID must be proven
    no longer active at the venue (filled or cancelled)."""
    for spec in specs:
        if await pair._exact_active(spec["id"], symbol):
            raise RuntimeError(
                f"exact order still active at terminal id={spec['id']}")


def _record_decision(state: dict, stage: str, decision: str,
                     verdict: Mapping[str, Any]) -> None:
    state.setdefault("policy_decisions", []).append({
        "stage": stage, "decision": decision,
        "state": verdict.get("state"), "modal": verdict.get("modal_relation"),
        "allow": verdict.get("allow")})
    pair._write_state(state)


# ---------------- campaign runner (reuses proven shell I/O) ----------------

async def run(*, min_edge: D = pair.DEFAULT_MIN_PAIR_EDGE,
              first_wait: float = pair.DEFAULT_FIRST_WAIT,
              complement_wait: float = pair.DEFAULT_COMPLEMENT_WAIT,
              min_tte: float = pair.DEFAULT_MIN_TTE) -> None:
    if not pair.armed():
        emit("DISARMED", driver="V1_POLICY", q_per_side=1,
             max_new_residual_qty=1, order_entry=False)
        return
    if not v1_policy.enabled():
        emit("STOP", reason="V1_POLICY_NOT_ENABLED", order_entry=False,
             realized_pnl=None)
        return
    if not await pair._gemini_terms_ok():
        raise RuntimeError("terms not accepted")
    if await pair._gemini_available_usd() < D("4"):
        raise RuntimeError("insufficient bounded pair balance")

    event = await pair.discover_eth5m()
    if event is None or event.seconds_to_expiry < float(min_tte):
        emit("STOP", reason="NO_EVENT_WITH_ENOUGH_TTE", order_entry=False,
             realized_pnl=None)
        return
    sid = os.getenv(pair.SID_ENV, "").strip()
    min_edge = D(str(min_edge))
    first_wait = effective_first_wait(first_wait)
    posture = forced_completion_enabled()

    # --- v1 decides the opening action from flat state ---
    bid, bid_size, ask, ask_size = await ladder.fresh_top_retry(event.symbol)
    verdict = dict(v1_policy.gate_candidate(bid, ask, event.seconds_to_expiry))
    open_decision = decide_flat(verdict)
    state: dict[str, Any] = {
        "terminal": False, "sid": sid, "driver": "V1_POLICY",
        "initializer": INITIALIZER_LABEL,
        "posture": POSTURE_LABEL if posture else "V1_PURE",
        "first_wait_s": first_wait,
        "policy_model_sha256_16": verdict.get("model_sha256_16"),
        "symbol": event.symbol, "event": event.ticker,
        "orders": [], "policy_decisions": [],
    }
    pair._write_state(state)
    _record_decision(state, "FLAT", open_decision, verdict)
    emit("V1_FLAT_DECISION", decision=open_decision, order_entry=False,
         realized_pnl=None, symbol=event.symbol,
         initializer=INITIALIZER_LABEL, **verdict)
    if open_decision != "OPEN_ONE_SIDE":
        state.update({"terminal": True, "status": "V1_ABSTAIN_FLAT",
                      "yes_qty": "0", "no_qty": "0"})
        pair._write_state(state)
        emit("COMPLETE", status="V1_ABSTAIN_FLAT", order_entry=False,
             realized_pnl=None)
        return
    chosen = choose_open_side(bid, bid_size, ask, ask_size)
    if chosen is None:
        state.update({"terminal": True, "status": "NO_FEE_SAFE_MAKER_PRICE",
                      "yes_qty": "0", "no_qty": "0"})
        pair._write_state(state)
        emit("COMPLETE", status="NO_FEE_SAFE_MAKER_PRICE", order_entry=False,
             realized_pnl=None)
        return

    spec = {"side": chosen["side"], "price": chosen["price"],
            "id": ladder.cid(sid, chosen["side"], 0), "row": None}
    comp_spec: Optional[dict] = None
    emit("V1_OPEN", side=spec["side"], price=str(spec["price"]),
         state=verdict["state"], initializer=INITIALIZER_LABEL,
         order_entry=True, realized_pnl=None)

    maker = ladder.base.GeminiMakerSession()
    await maker.connect()
    try:
        if not await place_order_durable(maker, state, spec, event.symbol):
            state.update({"terminal": True, "status": "POST_REJECT",
                          "yes_qty": "0", "no_qty": "0"})
            pair._write_state(state)
            emit("COMPLETE", status="POST_REJECT", order_entry=False,
                 realized_pnl=None)
            return

        # --- wait for the first fill ---
        t_entry = time.monotonic()
        tte_entry = float(event.seconds_to_expiry)
        first_deadline = min(
            t_entry + float(first_wait),
            t_entry + max(1.0, tte_entry - STOP_BEFORE_EXPIRY_SECONDS))
        filled = False
        while time.monotonic() < first_deadline and not filled:
            try:
                update = await asyncio.wait_for(maker.events.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if (isinstance(update, Mapping)
                    and str(update.get("c") or "") == spec["id"]
                    and D(str(update.get("Z") or "0")) > ZERO
                    and str(update.get("X") or "").upper()
                    in {"FILLED", "PARTIALLY_FILLED"}):
                filled = True
                emit("FIRST_FILL", side=spec["side"], client_id=spec["id"],
                     action="ASK_V1", order_entry=True)

        specs = [spec]
        if not filled:
            await ladder.cancel(maker, spec, event.symbol)
            await asyncio.sleep(0.3)
            fills = await ladder.fills(specs, event.symbol)
            qty = fills.get(spec["id"], ZERO)
            yes_q = qty if spec["side"] == "YES" else ZERO
            no_q = qty if spec["side"] == "NO" else ZERO
            await prove_all_inactive(specs, event.symbol)
            status = ("NO_FILL_TIMEOUT" if qty == ZERO
                      else STATUS_LATE_FILL_HOLD)
            _finalize(state, specs, yes_q, no_q, status)
            return

        # --- v1 decides the post-fill relation, re-asked as state evolves.
        # last_price is the AUTHORITATIVE EXECUTION PRICE of our fill,
        # fixed until another fill; only tte/residual evolve. ---
        stop_at = t_entry + max(1.0, tte_entry - STOP_BEFORE_EXPIRY_SECONDS)
        # overlay keeps the complement working until the TTE stop; pure v1
        # keeps the original bounded complement window
        comp_deadline = (stop_at if posture else
                         min(time.monotonic() + float(complement_wait), stop_at))
        completed = False
        first_exec_px: Optional[D] = None
        while time.monotonic() < comp_deadline and not completed:
            fills = await ladder.fills(specs, event.symbol)
            first_qty = fills.get(spec["id"], ZERO)
            if first_qty == ZERO:
                await asyncio.sleep(REEVAL_INTERVAL_SECONDS)
                continue
            if first_exec_px is None:
                first_exec_px = authoritative_fill_price(spec.get("row"))
                state["first_fill_exec_price"] = str(first_exec_px)
                pair._write_state(state)
            yes_q = first_qty if spec["side"] == "YES" else ZERO
            no_q = first_qty if spec["side"] == "NO" else ZERO
            if comp_spec is not None:
                comp_fill = fills.get(comp_spec["id"], ZERO)
                if comp_spec["side"] == "YES":
                    yes_q += comp_fill
                else:
                    no_q += comp_fill
                if comp_fill >= ONE:
                    completed = True
                    break
            tte_now = tte_entry - (time.monotonic() - t_entry)
            verdict = dict(v1_policy.evaluate(
                float(first_exec_px), max(tte_now, 0.0),
                _residual_shares(yes_q, no_q)))
            v1_decision = decide_after_fill(verdict)
            decision = post_fill_action(verdict)
            _record_decision(state, "POST_FILL", decision, verdict)
            state["policy_decisions"][-1]["v1_pure_decision"] = v1_decision
            pair._write_state(state)
            emit("V1_POST_FILL_DECISION", decision=decision,
                 v1_pure_decision=v1_decision,
                 posture=POSTURE_LABEL if posture else "V1_PURE",
                 residual=str(yes_q - no_q), last_price_source="EXEC_PRICE",
                 order_entry=False, realized_pnl=None, **verdict)
            if decision == "COMPLETE" and comp_spec is None:
                comp_side = "NO" if spec["side"] == "YES" else "YES"
                px = complement_price(spec["side"], first_exec_px, min_edge)
                if px is None:
                    emit("V1_COMPLETE_INFEASIBLE", reason="no_fee_safe_price",
                         order_entry=False, realized_pnl=None)
                else:
                    comp_spec = {"side": comp_side, "price": px,
                                 "id": ladder.cid(sid, comp_side, 1),
                                 "row": None}
                    if await place_order_durable(maker, state, comp_spec,
                                                 event.symbol):
                        specs = [spec, comp_spec]
                        emit("V1_COMPLEMENT_POSTED", side=comp_side,
                             price=str(px),
                             posture=POSTURE_LABEL if posture else "V1_PURE",
                             order_entry=True)
                    else:
                        comp_spec = None
                        comp_rejects = int(state.get("complement_rejects", 0)) + 1
                        state["complement_rejects"] = comp_rejects
                        pair._write_state(state)
                        emit("V1_COMPLEMENT_POST_REJECT",
                             rejects=comp_rejects, order_entry=False,
                             realized_pnl=None)
                        if comp_rejects > pair.MAX_COMPLEMENT_REPRICES:
                            raise RuntimeError(
                                "complement post rejected beyond reprice cap")
            await asyncio.sleep(REEVAL_INTERVAL_SECONDS)

        # --- terminal reconcile (proven shell semantics + inactivity proof) ---
        await asyncio.gather(*(ladder.cancel(maker, s, event.symbol)
                               for s in specs), return_exceptions=True)
        await asyncio.sleep(0.35)
        fills = await ladder.fills(specs, event.symbol)
        yes_q = sum((fills.get(s["id"], ZERO) for s in specs
                     if s["side"] == "YES"), ZERO)
        no_q = sum((fills.get(s["id"], ZERO) for s in specs
                    if s["side"] == "NO"), ZERO)
        if yes_q > ONE or no_q > ONE:
            raise RuntimeError(f"q1 v1 fill bound violated yes={yes_q} no={no_q}")
        await prove_all_inactive(specs, event.symbol)
        status = (STATUS_MATCHED if yes_q == ONE and no_q == ONE
                  else STATUS_RESIDUAL_HOLD
                  if yes_q > ZERO or no_q > ZERO
                  else "NO_AUTHORITATIVE_FILL")
        _finalize(state, specs, yes_q, no_q, status)
    finally:
        await maker.close()


def _finalize(state: dict, specs: list, yes_q: D, no_q: D,
              status: str) -> None:
    """Persist strict accounting from authoritative rows, then emit."""
    turnover = ZERO
    fees = ZERO
    order_details = []
    for s in specs:
        row = s.get("row")
        qty = ladder.base._filled(row)
        exec_px = None
        if qty > ZERO:
            exec_px = authoritative_fill_price(row)
            turnover += exec_px * qty
            fees += gemini_predictions_maker_fee(exec_px, qty)
        order_details.append({
            "side": s["side"], "id": s["id"], "limit_price": str(s["price"]),
            "exec_price": str(exec_px) if exec_px is not None else None,
            "filled_qty": str(qty)})
    matched = min(yes_q, no_q)
    state.update({
        "terminal": True, "status": status,
        "yes_qty": str(yes_q), "no_qty": str(no_q),
        "matched_qty": str(matched),
        "residual_qty": str(abs(yes_q - no_q)),
        "maker_turnover": str(turnover), "maker_fees": str(fees),
        "order_details": order_details,
        "settlement_required": bool(yes_q > ZERO or no_q > ZERO),
        "realized_pnl": None,      # only Gemini settlement realizes P&L
    })
    pair._write_state(state)
    emit("COMPLETE", status=status, yes_qty=str(yes_q), no_qty=str(no_q),
         matched_qty=str(matched), maker_turnover=str(turnover),
         maker_fees=str(fees),
         settlement_required=state["settlement_required"],
         order_entry=False, realized_pnl=None)
