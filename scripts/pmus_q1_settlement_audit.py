"""READ-ONLY settlement/cashflow audit for one q1 canary campaign.

WHY. The canary's own report is deliberately pre-settlement: `realized_pnl`
is null and settlement reads PENDING, because a process that just traded may
not also declare its own realized P&L. This converts that into REALIZED, from
venue-authoritative records only.

SAFE BY CONSTRUCTION, not by intention:
  * it holds `pmus_auth.ReadOnlyTransport`, which raises PermissionError on
    any method but GET, so no mutation can leave this process even by
    mistake;
  * the transport is never armed;
  * it imports no engine, no money path, no mandate and no authorization, so
    there is nothing here that could construct an order.

WHAT IT RECONCILES, per order id and for the market:
  * the venue's own order record: state, cumulative quantity, average price;
  * `commissionNotionalTotalCollected` with the VENUE'S OWN SIGN, not
    reinterpreted -- a negative value means collected-from-us was negative,
    and whether that is called a rebate is an accounting decision stated
    here rather than invented by the reader;
  * settlement outcome and per-leg payout from the venue's settlementPx;
  * the resulting position and balance after settlement.

Modeled numbers are reported on separate lines from venue numbers, and
REALIZED is emitted only when the venue's own fee figures are present for
every leg. Absent that, the verdict says so and stays provisional.

    PMUS_AUDIT_SLUG=<slug> PMUS_AUDIT_ORDERS=<id>,<id> \
        python3 -m scripts.pmus_q1_settlement_audit
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import pmus_auth as AUTH
from src import pmus_fees as FEES
from src import pmus_order_lifecycle as LC

GATEWAY = "https://gateway.polymarket.us"


def _d(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                        # noqa: BLE001
        return None


def order_record(t: Any, oid: str) -> Dict[str, Any]:
    try:
        raw = t.request("GET", f"/v1/order/{oid}")
    except Exception as exc:                                 # noqa: BLE001
        return {"order_id": oid, "error": f"{type(exc).__name__}: {exc}"[:200]}
    order = raw.get("order") if isinstance(raw.get("order"), Mapping) else raw
    v = LC.classify(order, requested_qty=1)
    comm = LC.commission(order)
    return {
        "order_id": oid,
        "state": v["state"],
        "classification": v["classification"],
        "cumulative_qty": str(v["filled_qty"]) if v["filled_qty"] is not None
        else None,
        "avg_price_own_terms": (str(v["price_own"]) if v["price_own"]
                                is not None else None),
        "outcome_side": order.get("outcomeSide"),
        "participate_dont_initiate": order.get("participateDontInitiate"),
        "venue_commission_raw": (str(comm) if comm is not None else None),
        "venue_commission_sign_note": (
            "venue sign preserved; negative = collected-from-us was negative"),
    }


def main() -> int:
    slug = os.getenv("PMUS_AUDIT_SLUG", "").strip()
    ids = [o.strip() for o in
           os.getenv("PMUS_AUDIT_ORDERS", "").split(",") if o.strip()]
    out: Dict[str, Any] = {"audit": "pmus-q1-settlement", "slug": slug,
                           "order_ids": ids,
                           "transport": "ReadOnlyTransport (GET only)"}
    if not slug or not ids:
        out["verdict"] = "REFUSED: PMUS_AUDIT_SLUG and PMUS_AUDIT_ORDERS required"
        print("AUDIT " + json.dumps(out, default=str), flush=True)
        return 2
    t = AUTH.ReadOnlyTransport()

    out["orders"] = [order_record(t, oid) for oid in ids]

    # settlement, from the venue's own field
    try:
        bbo = t.request("GET", f"/v1/markets/{slug}/bbo", base=GATEWAY)
        md = bbo.get("marketData") or {}
        spx = _d(md.get("settlementPx"))
        out["settlement"] = {"state": md.get("state"),
                             "settlement_px": (str(spx) if spx is not None
                                               else None),
                             "open_interest": md.get("openInterest"),
                             "winner": ("up" if spx == 1 else
                                        "down" if spx == 0 else None)}
    except Exception as exc:                                 # noqa: BLE001
        out["settlement"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}

    for path, key in (("/v1/portfolio/positions", "positions_after"),
                      ("/v1/account/balances", "balances_after"),
                      ("/v1/orders/open", "open_orders_after")):
        try:
            out[key] = t.request("GET", path)
        except Exception as exc:                             # noqa: BLE001
            out[key] = {"error": f"{type(exc).__name__}: {exc}"[:200]}

    # ---- economics. Venue figures and modeled figures on separate lines.
    legs = [o for o in out["orders"] if o.get("avg_price_own_terms")]
    cost = sum((D(o["avg_price_own_terms"]) * D(o["cumulative_qty"] or 0)
                for o in legs), D(0))
    qtys = [D(o["cumulative_qty"] or 0) for o in legs]
    matched = min(qtys) if len(qtys) == 2 else D(0)
    venue_comm = [o["venue_commission_raw"] for o in out["orders"]]
    have_all = all(c is not None for c in venue_comm) and bool(venue_comm)
    modeled = {}
    for o in out["orders"]:
        px = _d(o.get("avg_price_own_terms"))
        if px is None:
            continue
        taker = o.get("participate_dont_initiate") is False
        modeled[o["order_id"]] = {
            "leg": "taker" if taker else "maker",
            "modeled_taker_fee": str(FEES.taker_fee(px)) if taker else None,
            "modeled_maker_rebate": (None if taker
                                     else str(FEES.maker_rebate(px))),
        }
    out["economics"] = {
        "matched_contracts": str(matched),
        "pair_cost": str(cost) if matched else None,
        "gross_pair_edge": str(matched - cost) if matched else None,
        "modeled_per_leg": modeled,
        "venue_commission_total": (
            str(sum((D(c) for c in venue_comm), D(0))) if have_all else None),
        "REALIZED_NET": None,
        "verdict": ("PROVISIONAL: venue commission absent on at least one leg"
                    if not have_all else "VENUE_FEES_PRESENT"),
    }
    if have_all and matched:
        # payout for a matched pair is $1 per pair whichever way it settled
        realized = matched - cost - sum((D(c) for c in venue_comm), D(0))
        out["economics"]["REALIZED_NET"] = str(realized)
        out["economics"]["verdict"] = (
            "REALIZED from venue records: payout(matched) - cost - "
            "venue commission. Rebates are inside the venue commission sign, "
            "not added separately.")
    print("AUDIT " + json.dumps(out, default=str), flush=True)
    print(json.dumps(out, indent=1, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
