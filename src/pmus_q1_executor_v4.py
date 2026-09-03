"""PMUS q=1 executor v4 — source-controlled live-probe hot path.

DISARMED BY DEFAULT.  This is the consolidated successor to the temporary
AWS v2/v3 runtime patches discovered during the first PMUS live probes.
It preserves the original hard risk bounds while fixing the observed live
failure modes:

* current PMUS REST response/request shapes
* outcome-aware YES/NO BBO math
* preview-first + fresh post-only check immediately before mutation
* bounded GET-only eventual-consistency retries; POST is never replayed
* stage-aware venue rejection handling
* after an authoritative first-leg fill, bounded local complement retries
  inside the original campaign deadline instead of terminating on one
  post-only rejection
* fee-safe late taker completion only when >=1c remains after taker fee

One invocation is still exactly one q=1 campaign.  It never auto-scales,
never opens another market, and never calls realized P&L before a separate
settlement/reconciliation process writes authoritative realized rows.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Mapping, Optional
from urllib.error import HTTPError

from src import pmus_q1_executor as base

# Reuse the already-audited signer, transport, persistence and hard constants.
Transport = base.Transport
API = base.API
GATEWAY = base.GATEWAY
LIVE_ENV = base.LIVE_ENV
SID_ENV = base.SID_ENV
ARM_ENV = base.ARM_ENV
MARKET_ENV = base.MARKET_ENV
GATE_ENV = base.GATE_ENV
STATE_ENV = base.STATE_ENV
FIRST_WAIT_ENV = base.FIRST_WAIT_ENV
DEADLINE_ENV = base.DEADLINE_ENV
RESIDUAL_ACTION_ENV = base.RESIDUAL_ACTION_ENV
KEY_ENV = base.KEY_ENV
SECRET_ENV = base.SECRET_ENV
ONE = base.ONE
TICK = base.TICK
MIN_PRICE = base.MIN_PRICE
MAX_PRICE = base.MAX_PRICE
MIN_EDGE = base.MIN_EDGE
QTY = base.QTY
FIRST_LEG_MAX_COST = base.FIRST_LEG_MAX_COST
CAMPAIGN_RISK_CAP = base.CAMPAIGN_RISK_CAP
RESIDUAL_CAP = base.RESIDUAL_CAP
DAILY_LOSS_STOP = base.DAILY_LOSS_STOP
POLL_S = base.POLL_S
MIN_BUYING_POWER = base.MIN_BUYING_POWER

MAX_COMPLEMENT_ATTEMPTS = 6
COMPLEMENT_SLICE_S = 15.0
STATUS_RETRY_DELAYS = (0.0, 0.20, 0.50, 1.0, 2.0, 3.0)

armed = base.armed
has_credentials = base.has_credentials
gate_asserted = base.gate_asserted
state_dir = base.state_dir
ledger_append = base.ledger_append
realized_today = base.realized_today
persist_intent = base.persist_intent
taker_fee = base.taker_fee
maker_rebate = base.maker_rebate
complement_bound = base.complement_bound
taker_completion_ok = base.taker_completion_ok
entry_ok = base.entry_ok


class Halt(RuntimeError):
    pass


class RejectedNoFill(RuntimeError):
    """Venue-authoritative rejected order with cumQuantity == 0."""


def _d(value: Any) -> Optional[D]:
    if isinstance(value, Mapping):
        value = value.get("value")
    if value is None:
        return None
    try:
        return D(str(value))
    except Exception:
        return None


def _floor_tick(value: D) -> D:
    return (value / TICK).to_integral_value(rounding=ROUND_DOWN) * TICK


def outcome_bbo(t: Transport, slug: str, outcome_side: str) -> dict[str, D]:
    """Return outcome-specific bid/ask from the binary YES BBO.

    PMUS's market BBO is expressed on YES.  A NO BUY at p is economically
    the complement of a YES SELL at 1-p, therefore:
      NO bid = 1 - YES ask
      NO ask = 1 - YES bid
    This distinction is critical for post-only complement safety.
    """
    md = t.request("GET", f"/v1/markets/{slug}/bbo", base=GATEWAY)
    md = md.get("marketData") if isinstance(md, Mapping) else None
    md = md if isinstance(md, Mapping) else {}
    yb = _d(md.get("bestBid"))
    ya = _d(md.get("bestAsk"))
    if yb is None or ya is None:
        raise Halt("BBO_NOT_TWO_SIDED")
    side = str(outcome_side).upper()
    if side.endswith("YES"):
        return {"bid": yb, "ask": ya, "yes_bid": yb, "yes_ask": ya}
    if side.endswith("NO"):
        return {"bid": ONE - ya, "ask": ONE - yb,
                "yes_bid": yb, "yes_ask": ya}
    raise Halt(f"UNKNOWN_OUTCOME_SIDE:{outcome_side}")


def _http_code(exc: Exception) -> Optional[int]:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _order_status(t: Transport, order_id: str) -> dict:
    """GET-only bounded retry. Never replays an order mutation."""
    last: Optional[Exception] = None
    for delay in STATUS_RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            resp = t.request("GET", f"/v1/order/{order_id}")
            order = resp.get("order") if isinstance(resp, Mapping) else None
            order = order if isinstance(order, Mapping) else resp
            if not isinstance(order, Mapping):
                raise Halt("ORDER_STATUS_UNPARSEABLE")
            state = str(order.get("state") or order.get("status") or "").upper()
            raw = order.get("cumQuantity")
            if raw is None:
                raw = order.get("filledQuantity")
            cum = _d(raw) or D("0")
            if "REJECT" in state and cum == 0:
                raise RejectedNoFill(f"ORDER_REJECTED_ZERO_FILL:{order_id}")
            return dict(order)
        except RejectedNoFill:
            raise
        except Exception as exc:
            last = exc
            code = _http_code(exc)
            if code not in (404, 429) and not (
                    isinstance(code, int) and 500 <= code <= 599):
                raise
    if last is not None:
        raise last
    raise Halt("ORDER_STATUS_UNAVAILABLE")


def authoritative_fill(order: Mapping[str, Any]) -> Optional[dict]:
    state = str(order.get("state") or order.get("status") or
                order.get("orderStatus") or "").upper()
    raw = order.get("cumQuantity")
    if raw is None:
        raw = order.get("filledQuantity")
    if raw is None:
        raw = order.get("cumQty")
    filled = _d(raw) or D("0")
    if filled < QTY and "FILLED" not in state:
        return None
    for key in ("avgPx", "avgPrice", "averagePrice", "avgExecutionPrice"):
        px = _d(order.get(key))
        if px is not None and px > 0:
            return {"price": px, "qty": filled or D(str(QTY))}
    raise Halt("AMBIGUOUS_FILL_PRICE")


def _body(slug: str, outcome_side: str, price: D,
          post_only: bool) -> dict[str, Any]:
    return {
        "marketSlug": slug,
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(price), "currency": "USD"},
        "quantity": QTY,
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "participateDontInitiate": bool(post_only),
        "outcomeSide": outcome_side,
        "action": "ORDER_ACTION_BUY",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }


def place_limit_buy(t: Transport, slug: str, outcome_side: str,
                    price: D, stage: str, *, post_only: bool = True) -> str:
    """Preview, fresh outcome-aware BBO check, persist intent, one POST."""
    price = D(str(price))
    body = _body(slug, outcome_side, price, post_only)
    preview = t.request("POST", "/v1/order/preview", {"request": body})
    po = preview.get("order") if isinstance(preview, Mapping) else None
    if not isinstance(po, Mapping):
        raise Halt(f"PREVIEW_REJECTED:{stage}")
    if po.get("marketSlug") not in (None, slug):
        raise Halt(f"PREVIEW_MARKET_MISMATCH:{stage}")

    fresh = outcome_bbo(t, slug, outcome_side)
    if post_only and price >= fresh["ask"]:
        raise Halt(f"POST_ONLY_STALE_REFUSE:{stage}:price={price}:ask={fresh['ask']}")
    if not post_only and price < fresh["ask"]:
        raise Halt(f"TAKER_PRICE_STALE:{stage}:limit={price}:ask={fresh['ask']}")

    persist_intent({
        "stage": stage,
        "slug": slug,
        "side": outcome_side,
        "price": str(price),
        "qty": QTY,
        "post_only": bool(post_only),
        "submitted_utc": datetime.now(timezone.utc).isoformat(),
    })
    # Exactly one mutation. Ambiguous failure is never retried here.
    resp = t.request("POST", "/v1/orders", body)
    order_id = str((resp.get("id") if isinstance(resp, Mapping) else None) or
                   (resp.get("orderId") if isinstance(resp, Mapping) else None) or
                   ((resp.get("order") or {}).get("id")
                    if isinstance(resp, Mapping) and
                    isinstance(resp.get("order"), Mapping) else None) or "")
    if not order_id:
        raise Halt(f"NO_ORDER_ID:{stage}")
    persist_intent({"stage": stage + ":accepted", "order_id": order_id,
                    "slug": slug})
    ledger_append({"kind": "submitted", "stage": stage, "slug": slug,
                   "side": outcome_side, "price": str(price),
                   "post_only": bool(post_only), "order_id": order_id})
    return order_id


def cancel_exact(t: Transport, order_id: str) -> bool:
    order = _order_status(t, order_id)
    slug = str(order.get("marketSlug") or
               ((order.get("marketMetadata") or {}).get("slug")
                if isinstance(order.get("marketMetadata"), Mapping) else "") or "")
    if not slug:
        raise Halt("CANCEL_MARKET_UNVERIFIED")
    t.request("POST", f"/v1/order/{order_id}/cancel", {"marketSlug": slug})
    for _ in range(10):
        try:
            order = _order_status(t, order_id)
        except RejectedNoFill:
            return True
        state = str(order.get("state") or order.get("status") or "").upper()
        if any(x in state for x in ("CANCEL", "EXPIRED", "REJECT")):
            return True
        if "FILLED" in state:
            return False
        time.sleep(0.20)
    return False


def cancel_all_deadman(t: Transport, slug: str) -> None:
    try:
        t.request("POST", "/v1/orders/open/cancel", {"slugs": [slug]})
        opened = t.request("GET", "/v1/orders/open")
        remaining = [o for o in (opened.get("orders") or [])
                     if isinstance(o, Mapping) and o.get("marketSlug") == slug]
        if remaining:
            ledger_append({"kind": "error", "stage": "deadman_verify",
                           "remaining_target_orders": len(remaining)})
    except Exception as exc:
        ledger_append({"kind": "error", "stage": "deadman",
                       "err": str(exc)[:120]})


def wait_for_fill(t: Transport, order_id: str, until: float,
                  clock=time.monotonic, sleep=time.sleep) -> Optional[dict]:
    while clock() < until:
        fill = authoritative_fill(_order_status(t, order_id))
        if fill is not None:
            return fill
        sleep(POLL_S)
    return None


def _position_for_slug(t: Transport, slug: str) -> D:
    pos = t.request("GET", "/v1/portfolio/positions")
    rows = pos.get("positions") if isinstance(pos, Mapping) else None
    if isinstance(rows, Mapping):
        p = rows.get(slug)
        if isinstance(p, Mapping):
            raw = p.get("netPositionDecimal")
            if raw is None:
                raw = p.get("netPosition")
            if raw is None:
                raw = p.get("quantity")
            return _d(raw) or D("0")
    if isinstance(rows, list):
        for p in rows:
            if isinstance(p, Mapping) and p.get("marketSlug") == slug:
                raw = p.get("netPositionDecimal")
                if raw is None:
                    raw = p.get("netPosition")
                if raw is None:
                    raw = p.get("quantity")
                return _d(raw) or D("0")
    return D("0")


def preflight(t: Transport, slug: str) -> Optional[str]:
    bal = t.request("GET", "/v1/account/balances")
    power: Optional[D] = None
    rows = bal.get("balances") if isinstance(bal, Mapping) else None
    if isinstance(rows, list):
        ordered = sorted((r for r in rows if isinstance(r, Mapping)),
                         key=lambda r: 0 if str(r.get("currency", "")).upper() == "USD" else 1)
        for row in ordered:
            power = _d(row.get("buyingPower"))
            if power is not None:
                break
    if power is None and isinstance(bal, Mapping):
        for key in ("buyingPower", "availableBalance", "cashBalance"):
            power = _d(bal.get(key))
            if power is not None:
                break
    if power is None or power < MIN_BUYING_POWER:
        return f"buying_power_unverified_or_low:{power}"
    if _position_for_slug(t, slug) != 0:
        return "existing_position_in_target_market"
    opened = t.request("GET", "/v1/orders/open")
    if opened.get("orders"):
        return "open_orders_present"
    try:
        bbo = outcome_bbo(t, slug, "OUTCOME_SIDE_YES")
    except Halt as exc:
        return str(exc)
    if not entry_ok(bbo["bid"]):
        return f"entry_out_of_band:{bbo['bid']}"
    return None


def _maker_complement_price(bound: D, current_ask: D) -> Optional[D]:
    """Highest safe maker price: <= bound and strictly below current ask."""
    below_ask = _floor_tick(current_ask - TICK)
    px = min(D(str(bound)), below_ask)
    px = _floor_tick(px)
    return px if MIN_PRICE <= px <= MAX_PRICE else None


def _residual_report(report: Mapping[str, Any], status: str,
                     first_cost: D, reason: str, t: Transport,
                     slug: str, **extra: Any) -> dict:
    try:
        residual = int(abs(_position_for_slug(t, slug)))
    except Exception:
        residual = RESIDUAL_CAP
    return {**report, "status": status, "reason": reason,
            "first_cost": str(first_cost),
            "residual": min(residual or RESIDUAL_CAP, RESIDUAL_CAP),
            "residual_action": os.getenv(RESIDUAL_ACTION_ENV, "hold"),
            **extra}


def manage_complement(t: Transport, slug: str, first_cost: D,
                      stop_at: float, *, clock=time.monotonic,
                      sleep=time.sleep) -> dict:
    """Bounded local completion loop after the first leg is authoritative."""
    bound = complement_bound(first_cost)
    if bound is None:
        return {"status": "ONE_SIDED_HOLD", "reason": "no_fee_safe_complement"}

    last_reason = "deadline"
    for attempt in range(1, MAX_COMPLEMENT_ATTEMPTS + 1):
        if clock() >= stop_at:
            break
        try:
            bbo = outcome_bbo(t, slug, "OUTCOME_SIDE_NO")
        except Exception as exc:
            last_reason = f"opp_bbo:{type(exc).__name__}"
            sleep(min(1.0, max(0.0, stop_at - clock())))
            continue
        ask = bbo["ask"]

        # A taker completion is permitted only if its fee still leaves >=1c.
        if taker_completion_ok(first_cost, ask):
            try:
                oid = place_limit_buy(t, slug, "OUTCOME_SIDE_NO", ask,
                                      f"complement_taker_{attempt}",
                                      post_only=False)
                fill = wait_for_fill(t, oid, min(stop_at, clock() + 5.0),
                                     clock, sleep)
                if fill is not None:
                    return {"status": "MATCHED", "fill": fill,
                            "mode": "taker", "attempt": attempt,
                            "taker_fee": taker_fee(fill["price"])}
                cancel_exact(t, oid)
                last_reason = "taker_no_fill"
                continue
            except RejectedNoFill:
                last_reason = "taker_rejected_zero_fill"
                continue
            except Halt as exc:
                last_reason = str(exc)
                continue

        maker_px = _maker_complement_price(bound, ask)
        if maker_px is None:
            last_reason = f"no_legal_maker_price:ask={ask}:bound={bound}"
            sleep(min(1.0, max(0.0, stop_at - clock())))
            continue
        try:
            oid = place_limit_buy(t, slug, "OUTCOME_SIDE_NO", maker_px,
                                  f"complement_maker_{attempt}",
                                  post_only=True)
        except RejectedNoFill:
            last_reason = "maker_rejected_zero_fill"
            continue
        except Halt as exc:
            # Most commonly the BBO changed between our calculation and
            # fresh pre-POST check. Re-read and try a NEW persisted attempt.
            last_reason = str(exc)
            if "POST_ONLY_STALE_REFUSE" in last_reason:
                continue
            break

        slice_end = min(stop_at, clock() + COMPLEMENT_SLICE_S)
        try:
            fill = wait_for_fill(t, oid, slice_end, clock, sleep)
        except RejectedNoFill:
            last_reason = "maker_rejected_zero_fill"
            continue
        if fill is not None:
            return {"status": "MATCHED", "fill": fill,
                    "mode": "maker", "attempt": attempt,
                    "taker_fee": D("0")}
        try:
            cancel_exact(t, oid)
            # Cancel races are checked authoritatively.
            try:
                late = authoritative_fill(_order_status(t, oid))
            except RejectedNoFill:
                late = None
            if late is not None:
                return {"status": "MATCHED", "fill": late,
                        "mode": "maker_cancel_race", "attempt": attempt,
                        "taker_fee": D("0")}
        except Exception as exc:
            last_reason = f"cancel:{type(exc).__name__}"
            break
        last_reason = "maker_slice_no_fill"

    return {"status": "ONE_SIDED_HOLD", "reason": last_reason,
            "bound": str(bound), "attempts": MAX_COMPLEMENT_ATTEMPTS}


def run_campaign(t: Optional[Transport] = None,
                 clock=time.monotonic, sleep=time.sleep) -> dict:
    slug = os.getenv(MARKET_ENV, "").strip()
    report: dict[str, Any] = {
        "campaign": "pmus-q1-v4", "slug": slug, "qty": QTY,
        "realized_pnl": None,
        "labels": "submitted->filled->settled->realized; fees/rebates separate",
    }
    if not armed():
        return {**report, "status": "DISARMED",
                "note": "dual-key absent/mismatched; no network activity"}
    if not has_credentials():
        return {**report, "status": "NO_CREDENTIALS"}
    if not gate_asserted(slug):
        return {**report, "status": "GATE_NOT_CLEARED"}
    if realized_today() <= DAILY_LOSS_STOP:
        return {**report, "status": "DAILY_LOSS_STOP"}

    t = t or Transport()
    first_wait = float(os.getenv(FIRST_WAIT_ENV, "180"))
    deadline_s = float(os.getenv(DEADLINE_ENV, "300"))
    start = clock()
    stop_at = start + deadline_s
    first_cost: Optional[D] = None
    try:
        reason = preflight(t, slug)
        if reason:
            return {**report, "status": "PREFLIGHT_FAILED", "reason": reason}
        entry = outcome_bbo(t, slug, "OUTCOME_SIDE_YES")["bid"]
        if not entry_ok(entry):
            return {**report, "status": "ENTRY_OUT_OF_BAND", "bid": str(entry)}
        try:
            first_id = place_limit_buy(t, slug, "OUTCOME_SIDE_YES", entry,
                                       "first_leg", post_only=True)
            fill1 = wait_for_fill(t, first_id,
                                  min(start + first_wait, stop_at),
                                  clock, sleep)
        except RejectedNoFill as rej:
            return {**report, "status": "REJECTED_NO_FILL",
                    "reason": str(rej), "residual": 0}
        except Halt as halt:
            return {**report, "status": "NO_FILL_SAFE_REFUSAL",
                    "reason": str(halt), "residual": 0}

        if fill1 is None:
            ok = cancel_exact(t, first_id)
            try:
                late = authoritative_fill(_order_status(t, first_id))
            except RejectedNoFill:
                late = None
            if late is None:
                return {**report, "status": "NO_FILL",
                        "cancel_verified": ok, "residual": 0}
            fill1 = late
            ledger_append({"kind": "filled", "stage": "first_leg_late",
                           "price": str(fill1["price"])})

        first_cost = fill1["price"]
        ledger_append({"kind": "filled", "stage": "first_leg",
                       "price": str(first_cost),
                       "maker_rebate_separate": str(maker_rebate(first_cost))})

        comp = manage_complement(t, slug, first_cost, stop_at,
                                 clock=clock, sleep=sleep)
        if comp.get("status") != "MATCHED":
            return _residual_report(
                report, "COMPLEMENT_UNFILLED_RESIDUAL_HOLD", first_cost,
                str(comp.get("reason") or "completion_deadline"), t, slug,
                complement_bound=str(complement_bound(first_cost)),
                complement_attempts=comp.get("attempts"),
            )

        fill2 = comp["fill"]
        pair_cost = first_cost + fill2["price"]
        taker_cost = D(str(comp.get("taker_fee") or 0))
        edge_after_fees = ONE - pair_cost - taker_cost
        if edge_after_fees < MIN_EDGE:
            # Should be structurally impossible because taker path is gated
            # and maker has zero taker fee. Fail loudly rather than flatter P&L.
            raise Halt(f"POST_FILL_EDGE_VIOLATION:{edge_after_fees}")
        ledger_append({"kind": "filled", "stage": "complement",
                       "price": str(fill2["price"]),
                       "mode": comp.get("mode"),
                       "taker_fee": str(taker_cost),
                       "maker_rebate_separate":
                       str(D("0") if taker_cost else maker_rebate(fill2["price"]))})
        return {
            **report, "status": "MATCHED",
            "first_cost": str(first_cost),
            "comp_cost": str(fill2["price"]),
            "pair_cost": str(pair_cost),
            "completion_mode": comp.get("mode"),
            "completion_attempt": comp.get("attempt"),
            "taker_fee": str(taker_cost),
            "locked_edge_after_fees_pre_rebate": str(edge_after_fees),
            "rebates_separate": str(
                maker_rebate(first_cost) +
                (D("0") if taker_cost else maker_rebate(fill2["price"]))),
            "settlement": "PENDING — realized only after authoritative settlement reconcile",
            "residual": 0,
        }
    except Halt as halt:
        ledger_append({"kind": "halt", "reason": str(halt)})
        if first_cost is not None:
            return _residual_report(report, "HALT_WITH_RESIDUAL_HOLD",
                                    first_cost, str(halt), t, slug)
        return {**report, "status": "HALT", "reason": str(halt),
                "residual": 0}
    except Exception as exc:
        reason = f"UNEXPECTED_{type(exc).__name__}:{str(exc)[:160]}"
        ledger_append({"kind": "halt", "reason": reason})
        if first_cost is not None:
            return _residual_report(report, "HALT_WITH_RESIDUAL_HOLD",
                                    first_cost, reason, t, slug)
        return {**report, "status": "HALT", "reason": reason,
                "residual": 0}
    finally:
        cancel_all_deadman(t, slug)


def main() -> int:
    report = run_campaign()
    print(json.dumps(report, indent=1, default=str))
    ledger_append({"kind": "campaign_report", **report})
    safe = {"MATCHED", "NO_FILL", "DISARMED", "REJECTED_NO_FILL",
            "NO_FILL_SAFE_REFUSAL", "COMPLEMENT_UNFILLED_RESIDUAL_HOLD"}
    return 0 if report.get("status") in safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
