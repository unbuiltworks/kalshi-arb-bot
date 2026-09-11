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


def _val(x):
    """The venue wraps numbers as {"value": ...} in some payloads and not in
    others. Both shapes appear in the same gateway responses."""
    if isinstance(x, Mapping):
        x = x.get("value")
    return None if x is None else D(str(x))


def _touch_and_depth(levels, limit: Optional[D]):
    """Return (best ask, size at or inside ``limit``).

    Unknown/empty depth is deliberately None rather than zero so the
    migration gate fails closed when executable depth cannot be established.
    """
    best, total = None, D("0")
    if not levels:
        return None, None
    for lvl in levels:
        px = _val(lvl.get("price"))
        qty = _val(lvl.get("quantity") if lvl.get("quantity") is not None
                   else lvl.get("size"))
        if px is None or qty is None:
            continue
        best = px if best is None else min(best, px)
        if limit is not None and px <= limit:
            total += qty
    return best, (None if best is None else total)


def _level(price, depth):
    """Normalize one BBO level into the same shape as /book levels."""
    px, qty = _val(price), _val(depth)
    if px is None or qty is None:
        return None
    return {"price": {"value": str(px)}, "quantity": {"value": str(qty)}}


def read_book(t, slug: str) -> dict:
    """Read executable levels, failing over to the proven BBO payload.

    `/book` is retained as the richer first choice because, when populated,
    it lets the loss-cap gate sum only depth at or inside the explicit limit.
    LIVE 2026-09-11 it returned no usable offers for the migration market,
    while the continuously running L5 shadow kept receiving `/bbo` fields.
    Therefore an empty/unusable `/book` is not treated as a one-sided market:
    we immediately read `/bbo` and normalize bestBid/bestAsk plus their served
    depth into one level per side.

    The fallback changes plumbing only. The explicit IOC limit, <=$0.50
    incremental locked-loss cap, quantity cap, reconciliation checks, and
    combined-loss stop remain unchanged and fail closed if BBO depth is absent.
    """
    md = (q1._get_with_retry(t, f"/v1/markets/{slug}/book",
                             base=q1.GATEWAY) or {}).get("marketData") or {}
    offers, bids = md.get("offers") or [], md.get("bids") or []
    if offers:
        return {"offers": offers, "bids": bids, "source": "book"}

    bbo = (q1._get_with_retry(t, f"/v1/markets/{slug}/bbo",
                              base=q1.GATEWAY) or {}).get("marketData") or {}
    offer = _level(bbo.get("bestAsk"), bbo.get("askDepth"))
    bid = _level(bbo.get("bestBid"), bbo.get("bidDepth"))
    return {"offers": [offer] if offer else [],
            "bids": [bid] if bid else [], "source": "bbo",
            "state": bbo.get("state")}


def build_snapshot(t, slug: str, inv, combined_loss: D,
                   open_orders=None) -> dict:
    """Everything the gate needs, with the QUOTE read LAST.

    Order matters. The activity walk paginates the account's whole feed and
    took 31s at 02:43Z; with the quote stamped before it, the gate saw a
    31-second-old book and refused a snapshot it had itself made stale. The
    slow read happens first now, and the quote plus timestamp are taken
    together immediately before the gate runs.

    `open_orders` comes from the caller's fresh venue sweep -- the same read
    boot_assertions uses. This module previously probed for a q1 function
    that does not exist and recorded None, which failed closed on a book with
    no orders at all."""
    got = sa.traded_qty_with_evidence(slug, t)      # slow: do it FIRST
    venue_qty = got.get("qty")                      # qtyDecimal only, or None

    residual = inv.up - inv.dn
    units = min(abs(residual), mc.MAX_UNITS)
    avg_excess = inv.avg_dn() if residual < 0 else inv.avg_up()
    limit = mc.max_price(units, avg_excess) if units > 0 else D("0")

    ask = depth = None
    try:
        book = read_book(t, slug)
        if residual < 0:
            # excess DOWN/NO inventory -> buy YES from YES offers
            ask, depth = _touch_and_depth(book.get("offers") or [], limit)
        elif residual > 0:
            # excess UP/YES inventory -> buy NO. A YES bid at p is a NO ask
            # at 1-p with the same executable quantity.
            no_offers = []
            for lvl in book.get("bids") or []:
                px = _val(lvl.get("price"))
                qty = _val(lvl.get("quantity") if lvl.get("quantity") is not None
                           else lvl.get("size"))
                if px is not None and qty is not None:
                    no_offers.append({"price": {"value": str(ONE - px)},
                                      "quantity": {"value": str(qty)}})
            ask, depth = _touch_and_depth(no_offers, limit)
    except Exception:                                       # noqa: BLE001
        ask = depth = None
    quote_ts = time.time()                          # stamped WITH the quote
    return {
        "slug": slug,
        "qty_source": "qtyDecimal",     # venue_qty_total reads nothing else
        "venue_qty": venue_qty,
        "ledger_qty": str(inv.up + inv.dn),
        "up": str(inv.up), "dn": str(inv.dn), "residual": str(residual),
        "open_orders": open_orders,
        "quote_ts": quote_ts,
        "ask": None if ask is None else str(ask),
        "ask_depth": None if depth is None else str(depth),
        "avg_excess_cost": str(avg_excess),
        "combined_loss_usd": str(combined_loss),
        "max_price": str(limit),
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


def maybe_run(t, inv_for, combined_loss, open_orders=None) -> Optional[dict]:
    """Run the close iff PMUS_MIGRATION_CLOSE_SLUG names a market.

    Returns None when not requested, so the ordinary boot path is untouched
    by its presence.

    RE-ATTEMPTED, NOT RE-ORDERED. At 02:43Z the gate refused because the ask
    had moved 0.85 -> 0.87, putting the locked loss at 0.6831 against a 0.50
    cap. That is the bound doing its job -- and a close that is only ever
    tried once, at boot, would then never happen at all, since the probe
    halts and nothing tries again. So the caller may invoke this on its idle
    cycle: each invocation re-runs the nine preconditions from fresh reads
    and submits AT MOST one order, only when every one of them passes. The
    price bound is never relaxed to make a refusal go away."""
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
        snapshot=lambda: build_snapshot(t, slug, inv_for(slug), combined_loss,
                                        open_orders=open_orders),
        submit=lambda **kw: submit_marketable(t, **kw),
        fill_status=lambda oid: fill_status(t, oid),
        reconcile=_reconcile,
        cancel_resting=lambda: q1.cancel_all_deadman(t, slug),
        record=_record,
    )
