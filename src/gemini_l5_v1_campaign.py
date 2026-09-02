"""v1-DRIVEN q1 campaign: the frozen L5 model controls the actions.

Addresses the review correction on the earlier integration: the v1 policy
is no longer a filter in front of the fixed pair engine — it IS the
decision maker for opening, post-fill relation, and completion timing.
The proven transport/cancel/reservation/settlement shell is reused
verbatim (GeminiMakerSession, exact-ID cancel, authoritative fill
reconciliation, durable state file, q1 fill-bound assertion); only the
SEQUENCING is v1's.

Decision contract (pure functions, unit-tested without network):
  decide_flat(verdict)        -> "OPEN_ONE_SIDE" | "ABSTAIN"
      From flat inventory the model must be in-distribution and modally
      "open"; then exactly ONE maker order is placed. v1's relation
      vocabulary is side-symmetric on a flat book, so WHICH side is
      delegated to the risk adapter (fee-safe 20-80c maker price, deeper
      queue as tiebreak) — economics as adapter, never as the brain.
      BOTH SIDES ARE NEVER POSTED SIMULTANEOUSLY: the empirical L5
      both-sides-per-window pattern emerges from sequential decisions,
      and the fill-level model never emits a "post both" action.
  decide_after_fill(verdict)  -> "COMPLETE" | "ABSTAIN_EXTEND_CAPPED"
                                 | "ABSTAIN"
      After an authoritative fill the Gemini inventory state is rebuilt
      and v1 is re-asked with the ACTUAL residual. Modal "complete" →
      place the complement maker order (completion-reserve bounded).
      Modal "extend" is INFEASIBLE under the q=1 residual cap → abstain
      and hold (re-evaluated as tte decays; if v1 flips to complete, the
      complement is placed then). Off-distribution or modal "sell" →
      abstain-hold. There is NO automatic complement: one side filling
      is not, by itself, a reason to act.

Bounds for the FIRST live probe (per review): exactly one market, one
campaign, q=1, no auto-restart; the campaign terminates on no-fill
timeout, one completed matched q1, or any residual/safety condition —
residual is held to settlement (L5's own behavior) and reported, never
re-traded. Dual-key arming identical to the pair engine. NOT armed here.
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

ONE = D("1")
ZERO = D("0")
MIN_PRICE = pair.MIN_PRICE
MAX_PRICE = pair.MAX_PRICE
STOP_BEFORE_EXPIRY_SECONDS = pair.STOP_BEFORE_EXPIRY_SECONDS
REEVAL_INTERVAL_SECONDS = 5.0

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


def choose_open_side(bid: D, bid_size: D, ask: D, ask_size: D) -> Optional[dict]:
    """Risk adapter: among the two policy-equivalent open actions, pick the
    side whose MAKER price is fee-safe inside 20-80c; deeper queue breaks
    ties. Returns {side, price} or None if neither side is safe."""
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


def complement_price(first_side: str, first_price: D, min_edge: D) -> Optional[D]:
    """Completion-reserve bound: complement must lock >= min_edge after
    both maker fees, inside 20-80c. Returns None if no such price."""
    from src.gemini_l5_ladder_q1 import edge
    px = (ONE - D(str(first_price)) - D(str(min_edge))).quantize(pair.TICK)
    while px >= MIN_PRICE:
        y, n = ((D(str(first_price)), px) if first_side == "YES"
                else (px, D(str(first_price))))
        if px <= MAX_PRICE and edge(y, n) >= D(str(min_edge)):
            return px
        px -= pair.TICK
    return None


def _residual_shares(yes_qty: D, no_qty: D) -> float:
    return float(yes_qty - no_qty)


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

    # --- v1 decides the opening action from flat state ---
    bid, bid_size, ask, ask_size = await ladder.fresh_top_retry(event.symbol)
    verdict = dict(v1_policy.gate_candidate(bid, ask, event.seconds_to_expiry))
    open_decision = decide_flat(verdict)
    emit("V1_FLAT_DECISION", decision=open_decision, order_entry=False,
         realized_pnl=None, symbol=event.symbol, **verdict)
    if open_decision != "OPEN_ONE_SIDE":
        emit("COMPLETE", status="V1_ABSTAIN_FLAT", order_entry=False,
             realized_pnl=None)
        return
    chosen = choose_open_side(bid, bid_size, ask, ask_size)
    if chosen is None:
        emit("COMPLETE", status="NO_FEE_SAFE_MAKER_PRICE", order_entry=False,
             realized_pnl=None)
        return

    spec = {"side": chosen["side"], "price": chosen["price"],
            "id": ladder.cid(sid, chosen["side"], 0), "row": None}
    comp_spec: Optional[dict] = None
    state: dict[str, Any] = {
        "terminal": False, "sid": sid, "driver": "V1_POLICY",
        "policy_model_sha256_16": verdict["model_sha256_16"],
        "symbol": event.symbol, "event": event.ticker,
        "orders": [{"side": spec["side"], "price": str(spec["price"]),
                    "id": spec["id"]}],
    }
    pair._write_state(state)
    emit("V1_OPEN", side=spec["side"], price=str(spec["price"]),
         state=verdict["state"], order_entry=True, realized_pnl=None)

    maker = ladder.base.GeminiMakerSession()
    await maker.connect()
    try:
        ack = await maker.request("order.place", {
            "symbol": event.symbol, "side": "BUY", "type": "LIMIT",
            "timeInForce": "MOC", "price": str(spec["price"]),
            "quantity": "1", "eventOutcome": spec["side"],
            "clientOrderId": spec["id"]}, timeout=4.0)
        if int(ack.get("status", 0)) != 200:
            state.update({"terminal": True, "status": "POST_REJECT"})
            pair._write_state(state)
            emit("COMPLETE", status="POST_REJECT", order_entry=False,
                 realized_pnl=None)
            return
        spec["row"] = await ladder.base._find_row(spec["id"], event.symbol)
        if spec["row"] is None:
            raise RuntimeError("accepted exact v1 order not discoverable")

        # --- wait for the first fill ---
        first_deadline = min(
            time.monotonic() + float(first_wait),
            time.monotonic() + max(1.0, event.seconds_to_expiry -
                                   STOP_BEFORE_EXPIRY_SECONDS))
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
            yes_q = fills.get(spec["id"], ZERO) if spec["side"] == "YES" else ZERO
            no_q = fills.get(spec["id"], ZERO) if spec["side"] == "NO" else ZERO
            status = ("NO_FILL_TIMEOUT" if yes_q == ZERO and no_q == ZERO
                      else "LATE_FILL_RESIDUAL_HOLD")
            state.update({"terminal": True, "status": status,
                          "yes_qty": str(yes_q), "no_qty": str(no_q)})
            pair._write_state(state)
            emit("COMPLETE", status=status, order_entry=False,
                 realized_pnl=None,
                 settlement_required=(yes_q > ZERO or no_q > ZERO))
            return

        # --- v1 decides the post-fill relation, re-asked as state evolves ---
        t_entry = time.monotonic()
        tte_entry = float(event.seconds_to_expiry)
        stop_at = t_entry + max(1.0, tte_entry - STOP_BEFORE_EXPIRY_SECONDS)
        comp_deadline = min(time.monotonic() + float(complement_wait), stop_at)
        completed = False
        while time.monotonic() < comp_deadline and not completed:
            fills = await ladder.fills(specs, event.symbol)
            first_qty = fills.get(spec["id"], ZERO)
            if first_qty == ZERO:
                await asyncio.sleep(REEVAL_INTERVAL_SECONDS)
                continue
            yes_q = first_qty if spec["side"] == "YES" else ZERO
            no_q = first_qty if spec["side"] == "NO" else ZERO
            if comp_spec is not None:
                comp_fill = (await ladder.fills([comp_spec], event.symbol)
                             ).get(comp_spec["id"], ZERO)
                if comp_spec["side"] == "YES":
                    yes_q += comp_fill
                else:
                    no_q += comp_fill
                if comp_fill >= ONE:
                    completed = True
                    break
            tte_now = tte_entry - (time.monotonic() - t_entry)
            b, _, a, _ = await ladder.fresh_top_retry(event.symbol)
            verdict = dict(v1_policy.evaluate(
                float((D(str(b)) + D(str(a))) / 2), max(tte_now, 0.0),
                _residual_shares(yes_q, no_q)))
            decision = decide_after_fill(verdict)
            emit("V1_POST_FILL_DECISION", decision=decision,
                 residual=str(yes_q - no_q), order_entry=False,
                 realized_pnl=None, **verdict)
            if decision == "COMPLETE" and comp_spec is None:
                comp_side = "NO" if spec["side"] == "YES" else "YES"
                px = complement_price(spec["side"], spec["price"], min_edge)
                if px is None:
                    emit("V1_COMPLETE_INFEASIBLE", reason="no_fee_safe_price",
                         order_entry=False, realized_pnl=None)
                else:
                    comp_spec = {"side": comp_side, "price": px,
                                 "id": ladder.cid(sid, comp_side, 1),
                                 "row": None}
                    ack = await maker.request("order.place", {
                        "symbol": event.symbol, "side": "BUY",
                        "type": "LIMIT", "timeInForce": "MOC",
                        "price": str(px), "quantity": "1",
                        "eventOutcome": comp_side,
                        "clientOrderId": comp_spec["id"]}, timeout=4.0)
                    if int(ack.get("status", 0)) != 200:
                        comp_spec = None
                        emit("V1_COMPLEMENT_POST_REJECT", order_entry=False,
                             realized_pnl=None)
                    else:
                        comp_spec["row"] = await ladder.base._find_row(
                            comp_spec["id"], event.symbol)
                        specs = [spec, comp_spec]
                        emit("V1_COMPLEMENT_POSTED", side=comp_side,
                             price=str(px), order_entry=True)
            await asyncio.sleep(REEVAL_INTERVAL_SECONDS)

        # --- terminal reconcile (proven shell semantics) ---
        await asyncio.gather(*(ladder.cancel(maker, s, event.symbol)
                               for s in specs), return_exceptions=True)
        await asyncio.sleep(0.35)
        fills = await ladder.fills(specs, event.symbol)
        yes_q = sum((q for s, q in ((x, fills.get(x["id"], ZERO))
                                    for x in specs) if s["side"] == "YES"),
                    ZERO)
        no_q = sum((q for s, q in ((x, fills.get(x["id"], ZERO))
                                   for x in specs) if s["side"] == "NO"), ZERO)
        if yes_q > ONE or no_q > ONE:
            raise RuntimeError(f"q1 v1 fill bound violated yes={yes_q} no={no_q}")
        status = ("MATCHED_V1_COMPLETION" if yes_q == ONE and no_q == ONE
                  else "ONE_SIDED_RESIDUAL_POLICY_HOLD"
                  if yes_q > ZERO or no_q > ZERO
                  else "NO_AUTHORITATIVE_FILL")
        state.update({"terminal": True, "status": status,
                      "yes_qty": str(yes_q), "no_qty": str(no_q)})
        pair._write_state(state)
        emit("COMPLETE", status=status, yes_qty=str(yes_q), no_qty=str(no_q),
             settlement_required=(yes_q > ZERO or no_q > ZERO),
             order_entry=False, realized_pnl=None)
    finally:
        await maker.close()
