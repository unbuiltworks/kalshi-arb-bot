#!/usr/bin/env python3
"""Surgical current-PMUS REST compatibility patch for the staged q=1 executor.

This patches only /home/ssm-user/l5-pmus-q1-stage. It does not arm trading.
The canonical feature branch is tracked separately in issue #167.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

PATH = Path('/home/ssm-user/l5-pmus-q1-stage/src/pmus_q1_executor.py')
BACKUP = PATH.with_suffix('.py.d3614aed.bak')


def replace_between(text: str, start: str, end: str, replacement: str) -> str:
    a = text.find(start)
    if a < 0:
        raise SystemExit(f'PATCH_START_NOT_FOUND:{start}')
    b = text.find(end, a)
    if b < 0:
        raise SystemExit(f'PATCH_END_NOT_FOUND:{end}')
    return text[:a] + replacement.rstrip() + '\n\n' + text[b:]


src = PATH.read_text()
if not BACKUP.exists():
    BACKUP.write_text(src)

src = replace_between(src, 'def authoritative_fill(', '\n\ndef place_limit_buy', r'''def authoritative_fill(order: Mapping[str, Any]) -> Optional[dict]:
    """Venue-authoritative q=1 fill parsing for the current PMUS Order schema."""
    status = str(order.get("state") or order.get("status") or
                 order.get("orderStatus") or "")
    filled_qty = order.get("cumQuantity")
    if filled_qty is None:
        filled_qty = order.get("filledQuantity")
    if filled_qty is None:
        filled_qty = order.get("cumQty")
    if filled_qty is None:
        filled_qty = 0
    try:
        filled = D(str(filled_qty))
    except Exception:
        filled = D("0")
    if filled < QTY and "FILLED" not in status.upper():
        return None
    for key in ("avgPx", "avgPrice", "averagePrice", "avgExecutionPrice"):
        v = order.get(key)
        if isinstance(v, Mapping):
            v = v.get("value")
        if v is not None:
            try:
                return {"price": D(str(v)), "qty": filled or D(str(QTY))}
            except Exception:
                continue
    raise Halt("AMBIGUOUS_FILL_PRICE: order reports filled but no parseable average execution price")''')

src = replace_between(src, 'def place_limit_buy(', '\n\ndef cancel_exact', r'''def place_limit_buy(t: Transport, slug: str, outcome_side: str,
                    price: D, stage: str) -> str:
    """Preview-first, post-only, durable-intent, q=1 using current PMUS schema."""
    body = {"marketSlug": slug, "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(price), "currency": "USD"}, "quantity": QTY,
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": True,
            "outcomeSide": outcome_side,
            "action": "ORDER_ACTION_BUY",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}
    preview = t.request("POST", "/v1/order/preview", {"request": body})
    preview_order = preview.get("order") if isinstance(preview, Mapping) else None
    if not isinstance(preview_order, Mapping):
        raise Halt(f"PREVIEW_REJECTED:{stage}")
    if preview_order.get("marketSlug") not in (None, slug):
        raise Halt(f"PREVIEW_MARKET_MISMATCH:{stage}")
    persist_intent({"stage": stage, "slug": slug, "side": outcome_side,
                    "price": str(price), "qty": QTY,
                    "submitted_utc": datetime.now(timezone.utc).isoformat()})
    resp = t.request("POST", "/v1/orders", body)
    order_id = str(resp.get("id") or resp.get("orderId") or
                   (resp.get("order") or {}).get("id") or "")
    if not order_id:
        raise Halt(f"NO_ORDER_ID:{stage}")
    persist_intent({"stage": stage + ":accepted", "order_id": order_id})
    ledger_append({"kind": "submitted", "stage": stage, "slug": slug,
                   "side": outcome_side, "price": str(price),
                   "order_id": order_id})
    return order_id''')

src = replace_between(src, 'def cancel_exact(', '\n\ndef cancel_all_deadman', r'''def cancel_exact(t: Transport, order_id: str) -> bool:
    order = _order_status(t, order_id)
    slug = str(order.get("marketSlug") or
               ((order.get("marketMetadata") or {}).get("slug") if
                isinstance(order.get("marketMetadata"), Mapping) else "") or "")
    if not slug:
        raise Halt("CANCEL_MARKET_UNVERIFIED")
    t.request("POST", f"/v1/order/{order_id}/cancel", {"marketSlug": slug})
    for _ in range(10):
        order = _order_status(t, order_id)
        status = str(order.get("state") or order.get("status") or "").upper()
        if "CANCEL" in status or "EXPIRED" in status or "REJECT" in status:
            return True
        if "FILLED" in status:
            return False
        time.sleep(0.20)
    return False''')

src = replace_between(src, 'def cancel_all_deadman(', '\n\ndef wait_for_fill', r'''def cancel_all_deadman(t: Transport, slug: str) -> None:
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
                       "err": str(exc)[:120]})''')

src = replace_between(src, 'def preflight(', '\n\n# ------------------------- the one campaign', r'''def preflight(t: Transport, slug: str) -> Optional[str]:
    """Read-auth preflight using current PMUS balance/position response shapes."""
    bal = t.request("GET", "/v1/account/balances")
    power = None
    rows = bal.get("balances") if isinstance(bal, Mapping) else None
    if isinstance(rows, list):
        ordered = sorted((r for r in rows if isinstance(r, Mapping)),
                         key=lambda r: 0 if str(r.get("currency", "")).upper() == "USD" else 1)
        for row in ordered:
            v = row.get("buyingPower")
            if isinstance(v, Mapping):
                v = v.get("value")
            if v is not None:
                try:
                    power = D(str(v))
                    break
                except Exception:
                    continue
    if power is None and isinstance(bal, Mapping):
        for key in ("buyingPower", "availableBalance", "cashBalance"):
            v = bal.get(key)
            if isinstance(v, Mapping):
                v = v.get("value")
            if v is not None:
                try:
                    power = D(str(v))
                    break
                except Exception:
                    continue
    if power is None or power < MIN_BUYING_POWER:
        return f"buying_power_unverified_or_low:{power}"

    pos = t.request("GET", "/v1/portfolio/positions")
    positions = pos.get("positions") if isinstance(pos, Mapping) else None
    if isinstance(positions, Mapping):
        p = positions.get(slug)
        if isinstance(p, Mapping):
            raw = p.get("netPosition")
            if raw is None:
                raw = p.get("quantity")
            try:
                if D(str(raw or 0)) != 0:
                    return "existing_position_in_target_market"
            except Exception:
                return "target_position_unparseable"
    elif isinstance(positions, list):
        for p in positions:
            if not isinstance(p, Mapping) or p.get("marketSlug") != slug:
                continue
            raw = p.get("netPosition")
            if raw is None:
                raw = p.get("quantity")
            try:
                if D(str(raw or 0)) != 0:
                    return "existing_position_in_target_market"
            except Exception:
                return "target_position_unparseable"

    open_orders = t.request("GET", "/v1/orders/open")
    if open_orders.get("orders"):
        return "open_orders_present"
    bbo = t.request("GET", f"/v1/markets/{slug}/bbo",
                    base=GATEWAY).get("marketData") or {}
    bid = (bbo.get("bestBid") or {}).get("value")
    ask = (bbo.get("bestAsk") or {}).get("value")
    if not bid or not ask:
        return "bbo_not_two_sided"
    if not entry_ok(D(str(bid))):
        return f"entry_out_of_band:{bid}"
    return None


# ------------------------- the one campaign -------------------------''')

# Preserve fail-closed reporting for unexpected REST errors while still running deadman.
old = '''    except Halt as halt:\n        ledger_append({"kind": "halt", "reason": str(halt)})\n        return {**report, "status": "HALT", "reason": str(halt)}\n    finally:\n'''
new = '''    except Halt as halt:\n        ledger_append({"kind": "halt", "reason": str(halt)})\n        return {**report, "status": "HALT", "reason": str(halt)}\n    except Exception as exc:\n        reason = f"UNEXPECTED_{type(exc).__name__}:{str(exc)[:160]}"\n        ledger_append({"kind": "halt", "reason": reason})\n        return {**report, "status": "HALT", "reason": reason}\n    finally:\n'''
if old not in src:
    raise SystemExit('PATCH_EXCEPTION_BLOCK_NOT_FOUND')
src = src.replace(old, new, 1)

PATH.write_text(src)
os.chmod(PATH, 0o600)
print('PMUS_Q1_SCHEMA_V2_PATCH_OK sha256=' + hashlib.sha256(src.encode()).hexdigest())
