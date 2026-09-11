"""Venue wiring for the one-shot migration close.

The policy lives in pmus_migration_close, which holds no credentials and can
be exercised in full without them. This module is the only place the two meet:
it builds the fresh snapshot, submits the ONE marketable limit order, and
re-reads the venue afterwards.

WHY A SEPARATE SUBMIT. q1.place_limit_buy is post-only (participateDontInitiate)
and fixed at q=1, by design -- it is the R5F maker path and must stay that
way. OWNER: "Do not use maker-first if it means waiting again." So the close
sends its own body: marketable, quantity = the residual, and an explicit limit
price derived from the loss cap. The limit IS the bound; this is never a
market order.

ONE SHOT. Gated by PMUS_MIGRATION_CLOSE_SLUG, which names the exact market.
Running twice is harmless -- the second run finds residual 0, which has no
reducing side and refuses -- but it is not designed to be run twice.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Mapping, Optional

from src import pmus_fill_ledger as fl
from src import pmus_migration_close as mc
from src import pmus_q1_executor as q1
from src import pmus_settlement_auditor as sa

ENV_SLUG = "PMUS_MIGRATION_CLOSE_SLUG"
PREFIX = "PMUS_MIGRATION_CLOSE "
ONE = D("1")


def _log(row: Mapping[str, Any]) -> None:
    print(PREFIX + json.dumps(row, separators=(",", ":"), default=str),
          flush=True)


def _depth_at_or_below(t, slug: str, limit: D) -> Optional[D]:
    """Executable YES size at or inside our limit.

    PMUS serves sell depth under `offers`, NOT `asks` -- the parser bug this
    project already fixed once. None means we could not establish depth,
    which the gate treats as a failure, never as 'enough'."""
    try:
        book = q1._get_with_retry(
            t, f"/v1/markets/{slug}/orderbook", base=q1.GATEWAY) or {}
    except Exception:                                       # noqa: BLE001
        return None
    levels = ((book.get("orderbook") or book).get("offers") or [])
    total = D("0")
    for lvl in levels:
        px = lvl.get("price")
        px = D(str(px.get("value") if isinstance(px, Mapping) else px))
        if px <= limit:
            q = lvl.get("quantity") or lvl.get("size")
            total += D(str(q.get("value") if isinstance(q, Mapping) else q))
    return total


def build_snapshot(t, slug: str, inv, combined_loss: D) -> dict:
    """Everything the gate needs, read FRESH, now."""
    bbo = q1.fresh_bbo(t, slug)
    ask = bbo.get("ask")
    got = sa.traded_qty_with_evidence(slug, t)
    venue_qty = got.get("qty")          # qtyDecimal only, or None
    sweep = {}
    try:
        sweep = q1.fresh_open_orders(t, slug) if hasattr(
            q1, "fresh_open_orders") else {}
    except Exception:                                       # noqa: BLE001
        sweep = {}
    open_orders = sweep.get("ours")
    if open_orders is None and isinstance(sweep, Mapping) and not sweep:
        open_orders = None              # unknown -> the gate fails closed
    residual = inv.up - inv.dn
    units = min(abs(residual), mc.MAX_UNITS)
    avg_excess = inv.avg_dn() if residual < 0 else inv.avg_up()
    limit = mc.max_price(units, avg_excess) if units > 0 else D("0")
    return {
        "slug": slug,
        "qty_source": "qtyDecimal",     # venue_qty_total reads nothing else
        "venue_qty": venue_qty,
        "ledger_qty": str(inv.up + inv.dn),
        "up": str(inv.up), "dn": str(inv.dn), "residual": str(residual),
        "open_orders": open_orders,
        "quote_ts": time.time(),
        "ask": ask,
        "ask_depth": _depth_at_or_below(t, slug, limit),
        "avg_excess_cost": str(avg_excess),
        "combined_loss_usd": str(combined_loss),
    }


def submit_marketable(t, *, slug: str, side: str, limit_price: D,
                      quantity: D) -> dict:
    """ONE marketable limit buy. Not post-only, not a market order.

    The venue prices a NO order as 1 - price, the same convention the maker
    path uses; getting that backwards would send a wildly wrong price, so it
    is derived here exactly as q1 derives it."""
    wire_px = (ONE - limit_price) if side.upper().endswith("NO") else limit_price
    body = {"marketSlug": slug, "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(wire_px)},
            "quantity": {"value": str(quantity)},
            "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
            "participateDontInitiate": False,     # marketable, deliberately
            "outcomeSide": side,
            "action": "ORDER_ACTION_BUY",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}
    preview = t.request("POST", "/v1/order/preview", body)
    if preview.get("error"):
        raise RuntimeError(f"PREVIEW_REJECTED:{preview.get('error')}")
    q1.persist_intent({"stage": "migration_close", "slug": slug, "side": side,
                       "price": str(limit_price), "qty": str(quantity)})
    resp = t.request("POST", "/v1/orders", body)
    oid = str(resp.get("id") or resp.get("orderId")
              or (resp.get("order") or {}).get("id") or "")
    return {"id": oid, "preview": preview.get("preview")}


def fill_status(t, order_id: str) -> dict:
    order = q1._order_status(t, order_id)
    state = q1.order_state_str(order)
    filled = q1._filled_qty(order)
    px = None
    for k in ("avgPrice", "averagePrice", "avgPx"):
        v = order.get(k)
        if v is not None:
            px = (v.get("value") if isinstance(v, Mapping) else v)
            break
    return {"filled_qty": str(filled), "avg_price": px,
            "terminal": ("FILLED" in state or "CANCEL" in state
                         or "REJECT" in state or "EXPIRED" in state),
            "state": state}


def maybe_run(t, inv_for, combined_loss) -> Optional[dict]:
    """Run the close iff PMUS_MIGRATION_CLOSE_SLUG names a market.

    Returns None when not requested, so the ordinary boot path is untouched
    by its presence."""
    slug = (os.getenv(ENV_SLUG) or "").strip()
    if not slug:
        return None
    if t is None:
        row = {"kind": mc.KIND, "state": mc.REFUSED,
               "failed": ["no_transport"], "slug": slug}
        _log(row)
        return row

    def _reconcile() -> dict:
        after = sa.traded_qty_with_evidence(slug, t)
        inv2 = inv_for(slug)
        venue_q, ledger_q = after.get("qty"), str(inv2.up + inv2.dn)
        cmp_row = fl.venue_vs_ledger(venue_q, ledger_q)
        return {"residual": str(inv2.up - inv2.dn), "agrees": cmp_row["agrees"],
                "venue_qty": venue_q, "ledger_qty": ledger_q,
                "up": str(inv2.up), "dn": str(inv2.dn)}

    def _record(row):
        q1.ledger_append({**row, "spring": False})
        _log(row)

    return mc.run(
        slug=slug,
        snapshot=lambda: build_snapshot(t, slug, inv_for(slug), combined_loss),
        submit=lambda **kw: submit_marketable(t, **kw),
        fill_status=lambda oid: fill_status(t, oid),
        reconcile=_reconcile,
        cancel_resting=lambda: q1.cancel_all_deadman(t, slug),
        record=_record,
    )
