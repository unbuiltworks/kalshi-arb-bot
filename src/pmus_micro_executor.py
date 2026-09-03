"""Penny-scale PMUS executor for bounded L5 live probes.

This module is deliberately separate from ``pmus_q1_executor_v4``.  The
proven q=1 path keeps its existing constants and behavior; MICRO mode adds
fractional venue-aware quantity only when an extra arming key is present.

Safety invariants:
* one invocation == one market/campaign; no auto-restart or scaling
* q1 dual-key arming AND PMUS_MICRO_LIVE=YES are both required
* market metadata must explicitly advertise minimumTradeQty < 1
* first leg targets about one cent; every possible leg is capped at two cents
  at the entry-time binary complement bound (gross pair capital <= about 4c)
* current 0.005 tick is required; any other live tick fails closed
* Decimal quantity survives request, fill, ledger and residual accounting
* a partial complement is never treated as matched
* any unexpected residual above the actual first-fill quantity is reported
  as a cap breach, never rounded/truncated
* all q1 economics, post-only checks, GET retry behavior and deadman logic are
  reused; POST mutations are never retried
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Mapping, Optional

from src import pmus_q1_executor_v4 as q1

MICRO_LIVE_ENV = "PMUS_MICRO_LIVE"
TARGET_NOTIONAL_ENV = "PMUS_MICRO_TARGET_NOTIONAL"
MAX_LEG_NOTIONAL_ENV = "PMUS_MICRO_MAX_LEG_NOTIONAL"
DEFAULT_TARGET_NOTIONAL = D("0.01")
DEFAULT_MAX_LEG_NOTIONAL = D("0.02")

Transport = q1.Transport
Halt = q1.Halt
RejectedNoFill = q1.RejectedNoFill


def _d(value: Any) -> Optional[D]:
    return q1._d(value)


def micro_armed() -> bool:
    return q1.armed() and os.getenv(MICRO_LIVE_ENV, "").strip().upper() == "YES"


def _positive_env(name: str, default: D) -> D:
    try:
        value = D(os.getenv(name, str(default)).strip())
    except Exception as exc:
        raise Halt(f"MICRO_BAD_{name}:{type(exc).__name__}") from exc
    if value <= 0:
        raise Halt(f"MICRO_BAD_{name}:nonpositive")
    return value


def choose_micro_quantity(entry_price: D, minimum_trade_qty: D,
                          target_notional: D = DEFAULT_TARGET_NOTIONAL,
                          max_leg_notional: D = DEFAULT_MAX_LEG_NOTIONAL) -> D:
    """Return a legal min-qty multiple while bounding either binary leg.

    Quantity first aims for ``target_notional`` on the maker entry.  It is
    then capped so neither the entry nor a worst-case binary complement at
    ``1-entry`` can exceed ``max_leg_notional``.  This intentionally chooses
    *less* than one cent when necessary rather than allowing a cheap entry to
    imply a large expensive complement.
    """
    p = D(str(entry_price))
    minq = D(str(minimum_trade_qty))
    target = D(str(target_notional))
    max_leg = D(str(max_leg_notional))
    if p <= 0 or p >= q1.ONE:
        raise Halt(f"MICRO_BAD_ENTRY_PRICE:{p}")
    if minq <= 0 or minq >= q1.ONE:
        raise Halt(f"MICRO_FRACTIONAL_QTY_UNSUPPORTED:{minq}")
    if target <= 0 or max_leg <= 0 or target > max_leg:
        raise Halt("MICRO_BAD_NOTIONAL_BOUNDS")

    worst_leg_px = max(p, q1.ONE - p)
    max_units = (max_leg / (worst_leg_px * minq)).to_integral_value(
        rounding=ROUND_DOWN)
    if max_units < 1:
        raise Halt(
            f"MICRO_MIN_LOT_EXCEEDS_MAX:price={p}:minq={minq}:max={max_leg}")

    target_units = (target / (p * minq)).to_integral_value(rounding=ROUND_DOWN)
    if target_units < 1:
        target_units = D("1")
    units = min(target_units, max_units)
    qty = units * minq
    if qty <= 0 or qty * p > max_leg or qty * (q1.ONE - p) > max_leg:
        raise Halt("MICRO_QTY_BOUND_VIOLATION")
    return qty


def market_micro_spec(t: Transport, slug: str, entry_price: D) -> dict[str, D]:
    resp = t.request("GET", f"/v1/market/slug/{slug}", base=q1.GATEWAY)
    market = resp.get("market") if isinstance(resp, Mapping) else None
    if not isinstance(market, Mapping):
        raise Halt("MICRO_MARKET_METADATA_UNPARSEABLE")
    minq = _d(market.get("minimumTradeQty"))
    tick = _d(market.get("orderPriceMinTickSize"))
    if minq is None or tick is None:
        raise Halt("MICRO_MARKET_METADATA_MISSING")
    # v1 MICRO deliberately reuses the audited q1 tick math. A different
    # product tick gets a safe refusal until the pricing helpers are adapted.
    if tick != q1.TICK:
        raise Halt(f"MICRO_TICK_UNSUPPORTED:{tick}")
    target = _positive_env(TARGET_NOTIONAL_ENV, DEFAULT_TARGET_NOTIONAL)
    max_leg = _positive_env(MAX_LEG_NOTIONAL_ENV, DEFAULT_MAX_LEG_NOTIONAL)
    qty = choose_micro_quantity(entry_price, minq, target, max_leg)
    return {
        "qty": qty,
        "minimum_trade_qty": minq,
        "tick": tick,
        "target_notional": target,
        "max_leg_notional": max_leg,
        "entry_notional": qty * D(str(entry_price)),
        "worst_complement_notional": qty * (q1.ONE - D(str(entry_price))),
    }


def _body(slug: str, outcome_side: str, price: D, qty: D,
          post_only: bool) -> dict[str, Any]:
    qty = D(str(qty))
    if qty <= 0:
        raise Halt("MICRO_NONPOSITIVE_QTY")
    return {
        "marketSlug": slug,
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(D(str(price))), "currency": "USD"},
        # PMUS documents quantity as number<double> when minimumTradeQty < 1.
        "quantity": float(qty),
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "participateDontInitiate": bool(post_only),
        "outcomeSide": outcome_side,
        "action": "ORDER_ACTION_BUY",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }


def authoritative_fill(order: Mapping[str, Any], requested_qty: D,
                       *, accept_partial: bool = False) -> Optional[dict]:
    state = str(order.get("state") or order.get("status") or
                order.get("orderStatus") or "").upper()
    raw = order.get("cumQuantity")
    if raw is None:
        raw = order.get("filledQuantity")
    if raw is None:
        raw = order.get("cumQty")
    filled = _d(raw) or D("0")
    requested = D(str(requested_qty))
    if filled <= 0:
        return None
    if not accept_partial and filled < requested and "FILLED" not in state:
        return None
    for key in ("avgPx", "avgPrice", "averagePrice", "avgExecutionPrice"):
        px = _d(order.get(key))
        if px is not None and px > 0:
            return {"price": px, "qty": filled,
                    "partial": bool(filled < requested)}
    raise Halt("AMBIGUOUS_FILL_PRICE")


def place_limit_buy(t: Transport, slug: str, outcome_side: str,
                    price: D, qty: D, stage: str,
                    *, post_only: bool = True) -> str:
    price = D(str(price)); qty = D(str(qty))
    body = _body(slug, outcome_side, price, qty, post_only)
    preview = t.request("POST", "/v1/order/preview", {"request": body})
    po = preview.get("order") if isinstance(preview, Mapping) else None
    if not isinstance(po, Mapping):
        raise Halt(f"PREVIEW_REJECTED:{stage}")
    if po.get("marketSlug") not in (None, slug):
        raise Halt(f"PREVIEW_MARKET_MISMATCH:{stage}")

    fresh = q1.outcome_bbo(t, slug, outcome_side)
    if post_only and price >= fresh["ask"]:
        raise Halt(f"POST_ONLY_STALE_REFUSE:{stage}:price={price}:ask={fresh['ask']}")
    if not post_only and price < fresh["ask"]:
        raise Halt(f"TAKER_PRICE_STALE:{stage}:limit={price}:ask={fresh['ask']}")

    q1.persist_intent({
        "stage": stage, "slug": slug, "side": outcome_side,
        "price": str(price), "qty": str(qty), "micro": True,
        "post_only": bool(post_only),
        "submitted_utc": datetime.now(timezone.utc).isoformat(),
    })
    # Exactly one order mutation. Never replay on ambiguity.
    resp = t.request("POST", "/v1/orders", body)
    order_id = str((resp.get("id") if isinstance(resp, Mapping) else None) or
                   (resp.get("orderId") if isinstance(resp, Mapping) else None) or
                   ((resp.get("order") or {}).get("id")
                    if isinstance(resp, Mapping) and
                    isinstance(resp.get("order"), Mapping) else None) or "")
    if not order_id:
        raise Halt(f"NO_ORDER_ID:{stage}")
    q1.persist_intent({"stage": stage + ":accepted", "order_id": order_id,
                       "slug": slug, "qty": str(qty), "micro": True})
    q1.ledger_append({"kind": "submitted", "stage": stage, "slug": slug,
                      "side": outcome_side, "price": str(price),
                      "qty": str(qty), "micro": True,
                      "post_only": bool(post_only), "order_id": order_id})
    return order_id


def wait_for_fill(t: Transport, order_id: str, requested_qty: D, until: float,
                  clock=time.monotonic, sleep=time.sleep) -> Optional[dict]:
    while clock() < until:
        fill = authoritative_fill(q1._order_status(t, order_id), requested_qty)
        if fill is not None:
            return fill
        sleep(q1.POLL_S)
    return None


def _terminal_positive_fill(t: Transport, order_id: str,
                            requested_qty: D) -> Optional[dict]:
    try:
        return authoritative_fill(q1._order_status(t, order_id), requested_qty,
                                  accept_partial=True)
    except RejectedNoFill:
        return None


def _residual_report(report: Mapping[str, Any], status: str, first_cost: D,
                     first_qty: D, reason: str, t: Transport, slug: str,
                     **extra: Any) -> dict:
    try:
        residual = abs(q1._position_for_slug(t, slug))
        verified = True
    except Exception:
        residual = None
        verified = False
    cap = D(str(first_qty))
    breached = residual is not None and residual > cap
    final_status = "MICRO_RESIDUAL_CAP_BREACH" if breached else status
    return {
        **report, "status": final_status, "reason": reason,
        "first_cost": str(first_cost), "first_qty": str(first_qty),
        "residual": str(residual) if residual is not None else None,
        "residual_verified": verified,
        "residual_cap_qty": str(cap),
        "residual_cap_breached": bool(breached),
        "residual_action": os.getenv(q1.RESIDUAL_ACTION_ENV, "hold"),
        **extra,
    }


def manage_complement(t: Transport, slug: str, first_cost: D, qty: D,
                      stop_at: float, *, clock=time.monotonic,
                      sleep=time.sleep) -> dict:
    bound = q1.complement_bound(first_cost)
    if bound is None:
        return {"status": "ONE_SIDED_HOLD", "reason": "no_fee_safe_complement"}

    last_reason = "deadline"
    for attempt in range(1, q1.MAX_COMPLEMENT_ATTEMPTS + 1):
        if clock() >= stop_at:
            break
        try:
            bbo = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_NO")
        except Exception as exc:
            last_reason = f"opp_bbo:{type(exc).__name__}"
            sleep(min(1.0, max(0.0, stop_at - clock())))
            continue
        ask = bbo["ask"]

        if q1.taker_completion_ok(first_cost, ask):
            try:
                oid = place_limit_buy(t, slug, "OUTCOME_SIDE_NO", ask, qty,
                                      f"micro_complement_taker_{attempt}",
                                      post_only=False)
                fill = wait_for_fill(t, oid, qty,
                                     min(stop_at, clock() + 5.0), clock, sleep)
                if fill is not None:
                    return {"status": "MATCHED", "fill": fill,
                            "mode": "taker", "attempt": attempt,
                            "taker_fee": q1.taker_fee(fill["price"])}
                q1.cancel_exact(t, oid)
                partial = _terminal_positive_fill(t, oid, qty)
                if partial is not None:
                    if partial["qty"] >= qty:
                        return {"status": "MATCHED", "fill": partial,
                                "mode": "taker_cancel_race", "attempt": attempt,
                                "taker_fee": q1.taker_fee(partial["price"])}
                    return {"status": "PARTIAL_COMPLEMENT_RESIDUAL",
                            "reason": "taker_partial_fill", "fill": partial,
                            "attempt": attempt}
                last_reason = "taker_no_fill"
                continue
            except RejectedNoFill:
                last_reason = "taker_rejected_zero_fill"
                continue
            except Halt as exc:
                last_reason = str(exc)
                continue

        maker_px = q1._maker_complement_price(bound, ask)
        if maker_px is None:
            last_reason = f"no_legal_maker_price:ask={ask}:bound={bound}"
            sleep(min(1.0, max(0.0, stop_at - clock())))
            continue
        try:
            oid = place_limit_buy(t, slug, "OUTCOME_SIDE_NO", maker_px, qty,
                                  f"micro_complement_maker_{attempt}",
                                  post_only=True)
        except RejectedNoFill:
            last_reason = "maker_rejected_zero_fill"
            continue
        except Halt as exc:
            last_reason = str(exc)
            if "POST_ONLY_STALE_REFUSE" in last_reason:
                continue
            break

        slice_end = min(stop_at, clock() + q1.COMPLEMENT_SLICE_S)
        try:
            fill = wait_for_fill(t, oid, qty, slice_end, clock, sleep)
        except RejectedNoFill:
            last_reason = "maker_rejected_zero_fill"
            continue
        if fill is not None:
            return {"status": "MATCHED", "fill": fill,
                    "mode": "maker", "attempt": attempt, "taker_fee": D("0")}
        try:
            q1.cancel_exact(t, oid)
            partial = _terminal_positive_fill(t, oid, qty)
            if partial is not None:
                if partial["qty"] >= qty:
                    return {"status": "MATCHED", "fill": partial,
                            "mode": "maker_cancel_race", "attempt": attempt,
                            "taker_fee": D("0")}
                return {"status": "PARTIAL_COMPLEMENT_RESIDUAL",
                        "reason": "maker_partial_fill", "fill": partial,
                        "attempt": attempt}
        except Exception as exc:
            last_reason = f"cancel:{type(exc).__name__}"
            break
        last_reason = "maker_slice_no_fill"

    return {"status": "ONE_SIDED_HOLD", "reason": last_reason,
            "bound": str(bound), "attempts": q1.MAX_COMPLEMENT_ATTEMPTS}


def run_campaign(t: Optional[Transport] = None,
                 clock=time.monotonic, sleep=time.sleep) -> dict:
    slug = os.getenv(q1.MARKET_ENV, "").strip()
    report: dict[str, Any] = {
        "campaign": "pmus-micro-v1", "slug": slug,
        "realized_pnl": None,
        "labels": "submitted->filled->settled->realized; fees/rebates separate",
    }
    if not micro_armed():
        return {**report, "status": "DISARMED",
                "note": "q1 dual-key and MICRO key are all required; no network activity"}
    if not q1.has_credentials():
        return {**report, "status": "NO_CREDENTIALS"}
    if not q1.gate_asserted(slug):
        return {**report, "status": "GATE_NOT_CLEARED"}
    if q1.realized_today() <= q1.DAILY_LOSS_STOP:
        return {**report, "status": "DAILY_LOSS_STOP"}

    t = t or Transport()
    first_wait = float(os.getenv(q1.FIRST_WAIT_ENV, "180"))
    deadline_s = float(os.getenv(q1.DEADLINE_ENV, "300"))
    start = clock(); stop_at = start + deadline_s
    first_cost: Optional[D] = None
    first_qty: Optional[D] = None
    try:
        # Stronger than q1 target-only preflight: no open order or nonzero
        # position anywhere in the account before a MICRO first leg.
        from src import pmus_aws_supervisor as supervisor
        block = supervisor.account_block_reason(t)
        if block:
            return {**report, "status": "PREFLIGHT_FAILED", "reason": block}
        reason = q1.preflight(t, slug)
        if reason:
            return {**report, "status": "PREFLIGHT_FAILED", "reason": reason}

        entry = q1.outcome_bbo(t, slug, "OUTCOME_SIDE_YES")["bid"]
        if not q1.entry_ok(entry):
            return {**report, "status": "ENTRY_OUT_OF_BAND", "bid": str(entry)}
        spec = market_micro_spec(t, slug, entry)
        requested_qty = spec["qty"]
        report.update({
            "qty": str(requested_qty),
            "minimum_trade_qty": str(spec["minimum_trade_qty"]),
            "tick": str(spec["tick"]),
            "entry_notional": str(spec["entry_notional"]),
            "worst_complement_notional": str(spec["worst_complement_notional"]),
            "max_leg_notional": str(spec["max_leg_notional"]),
        })

        try:
            first_id = place_limit_buy(t, slug, "OUTCOME_SIDE_YES", entry,
                                       requested_qty, "micro_first_leg",
                                       post_only=True)
            fill1 = wait_for_fill(t, first_id, requested_qty,
                                  min(start + first_wait, stop_at), clock, sleep)
        except RejectedNoFill as rej:
            return {**report, "status": "REJECTED_NO_FILL",
                    "reason": str(rej), "residual": "0"}
        except Halt as halt:
            return {**report, "status": "NO_FILL_SAFE_REFUSAL",
                    "reason": str(halt), "residual": "0"}

        if fill1 is None:
            cancel_ok = q1.cancel_exact(t, first_id)
            late = _terminal_positive_fill(t, first_id, requested_qty)
            if late is None:
                return {**report, "status": "NO_FILL",
                        "cancel_verified": cancel_ok, "residual": "0"}
            fill1 = late
            q1.ledger_append({"kind": "filled", "stage": "micro_first_leg_late",
                              "price": str(fill1["price"]),
                              "qty": str(fill1["qty"]), "micro": True})

        first_cost = D(str(fill1["price"])); first_qty = D(str(fill1["qty"]))
        if first_qty <= 0 or first_qty > requested_qty:
            raise Halt(f"MICRO_FIRST_FILL_QTY_INVALID:{first_qty}>{requested_qty}")
        q1.ledger_append({"kind": "filled", "stage": "micro_first_leg",
                          "price": str(first_cost), "qty": str(first_qty),
                          "micro": True,
                          "maker_rebate_separate_per_contract":
                          str(q1.maker_rebate(first_cost))})

        comp = manage_complement(t, slug, first_cost, first_qty, stop_at,
                                 clock=clock, sleep=sleep)
        if comp.get("status") != "MATCHED":
            partial = comp.get("fill") if isinstance(comp.get("fill"), Mapping) else None
            return _residual_report(
                report, "COMPLEMENT_UNFILLED_RESIDUAL_HOLD", first_cost,
                first_qty, str(comp.get("reason") or "completion_deadline"),
                t, slug,
                complement_bound=str(q1.complement_bound(first_cost)),
                complement_attempts=comp.get("attempts") or comp.get("attempt"),
                partial_complement_qty=(str(partial.get("qty"))
                                        if partial is not None else None),
            )

        fill2 = comp["fill"]
        comp_qty = D(str(fill2["qty"]))
        if comp_qty < first_qty:
            return _residual_report(report, "COMPLEMENT_PARTIAL_RESIDUAL_HOLD",
                                    first_cost, first_qty,
                                    "complement_qty_below_first_qty", t, slug,
                                    complement_qty=str(comp_qty))
        pair_cost = first_cost + D(str(fill2["price"]))
        taker_cost = D(str(comp.get("taker_fee") or 0))
        edge_per_contract = q1.ONE - pair_cost - taker_cost
        if edge_per_contract < q1.MIN_EDGE:
            raise Halt(f"POST_FILL_EDGE_VIOLATION:{edge_per_contract}")
        locked_edge_usd = edge_per_contract * first_qty
        q1.ledger_append({"kind": "filled", "stage": "micro_complement",
                          "price": str(fill2["price"]), "qty": str(comp_qty),
                          "micro": True, "mode": comp.get("mode"),
                          "taker_fee_per_contract": str(taker_cost),
                          "maker_rebate_separate_per_contract":
                          str(D("0") if taker_cost else q1.maker_rebate(fill2["price"]))})
        return {
            **report, "status": "MATCHED", "first_cost": str(first_cost),
            "first_qty": str(first_qty), "comp_cost": str(fill2["price"]),
            "comp_qty": str(comp_qty), "pair_cost_per_contract": str(pair_cost),
            "completion_mode": comp.get("mode"),
            "completion_attempt": comp.get("attempt"),
            "taker_fee_per_contract": str(taker_cost),
            "locked_edge_per_contract_pre_rebate": str(edge_per_contract),
            "locked_edge_usd_pre_rebate": str(locked_edge_usd),
            "rebates_separate": True,
            "settlement": "PENDING — realized only after authoritative settlement reconcile",
            "residual": "0",
        }
    except Halt as halt:
        q1.ledger_append({"kind": "halt", "micro": True, "reason": str(halt)})
        if first_cost is not None and first_qty is not None:
            return _residual_report(report, "HALT_WITH_RESIDUAL_HOLD",
                                    first_cost, first_qty, str(halt), t, slug)
        return {**report, "status": "HALT", "reason": str(halt), "residual": "0"}
    except Exception as exc:
        reason = f"UNEXPECTED_{type(exc).__name__}:{str(exc)[:160]}"
        q1.ledger_append({"kind": "halt", "micro": True, "reason": reason})
        if first_cost is not None and first_qty is not None:
            return _residual_report(report, "HALT_WITH_RESIDUAL_HOLD",
                                    first_cost, first_qty, reason, t, slug)
        return {**report, "status": "HALT", "reason": reason, "residual": "0"}
    finally:
        if t is not None and slug:
            q1.cancel_all_deadman(t, slug)


def main() -> int:
    report = run_campaign()
    print(json.dumps(report, indent=1, default=str))
    q1.ledger_append({"kind": "campaign_report", "micro": True, **report})
    safe = {"MATCHED", "NO_FILL", "DISARMED", "REJECTED_NO_FILL",
            "NO_FILL_SAFE_REFUSAL", "COMPLEMENT_UNFILLED_RESIDUAL_HOLD",
            "COMPLEMENT_PARTIAL_RESIDUAL_HOLD"}
    return 0 if report.get("status") in safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
