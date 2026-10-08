"""E1-ATTRIBUTED CASHFLOW, BY ORDER ID. Read-only.

THE ATTRIBUTION PROBLEM, STATED PLAINLY. The PMUS account is shared: the
activities feed carries 900+ trade rows and 92 position resolutions from
other strategies, at sizes like 16.21 and 23.49 shares. My first pass summed
the money fields of every row naming a cohort slug and got fees $1.4595 and
settlement $5.6900 -- numbers that CANNOT be E1's, because the trade rows it
summed carry the COUNTERPARTY's aggressor commission ($28.9200 on a single
order), while E1 was the resting maker. Those aggregates are withdrawn.

So this attributes by ORDER ID and nothing else. The forty ids come from
E1's own append-only tape. A trade row counts as E1's only if one of those
ids appears in it, and the row's OWN side is then located -- aggressor or
resting -- so the commission read is the one charged to US rather than to
whoever crossed us.

Account-level settlement is NOT divided up by guesswork. The position
resolution for a market is reported in full, and it is only treated as E1's
where its own net position matches E1's filled quantity for that market
exactly. Anything that cannot be tied to an id is labelled UNCONFIRMED and
reported as such, never netted into an E1 figure.

GETs only. Writes nothing.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src import pmus_auth as A

E1_1215Z = [
    "CZGVWJ5HTZ7D", "CZGVXST1EZ89", "CZGVXST7JZ89", "CZGW0DRSAZ7J",
    "CZGW0DS1WZ7J", "CZGW0TNF8Z7T", "CZGW3Q9SCZ8K", "CZGW3QA24Z8K",
    "CZGW4QWKMZ73", "CZGW4QWS6Z73", "CZGW4QWW2Z73", "CZGW4QWY6Z73",
    "CZGW4QX9YZ73", "CZGW596D2Z7Z", "CZGW596NAZ7Z", "CZGW59710Z7Z",
    "CZGW5PZY0Z8E", "CZGW5Q0DJZ8E", "CZGW6T12WZ84", "CZGW6T1G8Z84",
]
E1_1230Z = [
    "CZH22KCSPZ8H", "CZH22KD92Z8H", "CZH22KDH8Z8H", "CZH22KDJEZ8H",
    "CZH25AYD0Z84", "CZH26B7HCZ73", "CZH28KHW0Z7Z", "CZH28KJ1PZ7Z",
    "CZH28KJ6AZ7Z", "CZH28KJECZ7Z", "CZH2A4FWPZ7D", "CZH2A4G66Z7D",
    "CZH2E3NAMZ7J", "CZH2E3NVWZ7J", "CZH2F2FBEZ7T", "CZH2F2G70Z7T",
    "CZH2FP4R8Z8K", "CZH2FP5MCZ8K", "CZH2FP5QMZ8K", "CZH2HF6RAZ8E",
]
E1_IDS = set(E1_1215Z) | set(E1_1230Z)
SLUG_1215 = "cpc-btc-updown-15m-2026-10-08-1215z"
SLUG_1230 = "cpc-btc-updown-15m-2026-10-08-1230z"
SLUG_0100 = "cpc-btc-updown-15m-2026-10-08-0100z"
COHORT = (SLUG_0100, SLUG_1215, SLUG_1230)
REDACT = ("accountId", "account_id", "walletAddress", "address", "userId",
          "subaccountId", "ownerId")


def scrub(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: ("REDACTED" if k in REDACT else scrub(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o


def dec(x: Any) -> Optional[D]:
    if isinstance(x, dict):
        x = x.get("value")
    if x is None:
        return None
    try:
        d = D(str(x))
    except Exception:                                          # noqa: BLE001
        return None
    return d if d.is_finite() else None


def chunks(s: str, n: int = 1400):
    for i in range(0, len(s), n):
        yield s[i:i + n]


def main() -> int:
    t = A.ReadOnlyTransport()
    print("ATTRIB_BEGIN now=%s"
          % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    print("ATTRIB_RULE order-id match only; the earlier cohort-slug sums "
          "(fees 1.4595, settlement 5.6900) are WITHDRAWN as they included "
          "counterparty commissions and other strategies' rows")

    # pull every activity page once
    rows: List[Dict[str, Any]] = []
    cursor = ""
    for _ in range(40):
        path = "/v1/portfolio/activities?limit=50"
        if cursor:
            path += "&cursor=%s" % cursor
        try:
            body = t.request("GET", path) or {}
        except Exception as exc:                               # noqa: BLE001
            print("PAGE_ERR %s" % type(exc).__name__)
            break
        page = body.get("activities") or []
        rows.extend(page)
        cursor = body.get("nextCursor") or ""
        if body.get("eof") or not cursor:
            break
    print("PAGES_SCANNED rows=%d" % len(rows))

    # ---- trades that name an E1 order id, with OUR side's commission ----
    our_fee = D("0")
    our_fee_rows = 0
    matched_ids: Dict[str, List[str]] = {}
    for r in rows:
        if str(r.get("type")) != "ACTIVITY_TYPE_TRADE":
            continue
        tr = r.get("trade") or {}
        s = json.dumps(tr, default=str)
        hit = [i for i in E1_IDS if i in s]
        if not hit:
            continue
        for side_name in ("restingExecution", "aggressorExecution",
                          "passiveExecution", "makerExecution",
                          "takerExecution"):
            ex = tr.get(side_name)
            if not isinstance(ex, dict):
                continue
            oid = str(((ex.get("order") or {}).get("id")) or "")
            if oid not in E1_IDS:
                continue
            fee = (dec(ex.get("commissionNotionalCollected"))
                   or dec(ex.get("commission")) or D("0"))
            our_fee += fee
            our_fee_rows += 1
            matched_ids.setdefault(oid, []).append(side_name)
            print("E1_FILL %s" % json.dumps({
                "order_id": oid, "our_side": side_name,
                "trade_id": tr.get("id"),
                "last_px": dec(ex.get("lastPx")) and
                           str(dec(ex.get("lastPx"))),
                "last_qty": ex.get("lastQty") or ex.get("quantity"),
                "our_commission": str(fee),
                "slug": (ex.get("order") or {}).get("marketSlug"),
                "outcome_side": (ex.get("order") or {}).get("outcomeSide"),
            }, default=str))
    print("E1_FILL_SUMMARY %s" % json.dumps({
        "trade_rows_naming_an_e1_id": our_fee_rows,
        "distinct_e1_ids_matched": len(matched_ids),
        "our_commission_total_usd": str(our_fee),
        "ids_matched": sorted(matched_ids),
        "ids_never_seen_in_a_trade_row":
            sorted(i for i in E1_IDS if i not in matched_ids),
    }, default=str))

    # ---- position resolutions on cohort markets, printed in FULL ----
    for r in rows:
        if str(r.get("type")) != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        pr = r.get("positionResolution") or {}
        slug = str(pr.get("marketSlug"))
        if slug not in COHORT:
            continue
        full = json.dumps(scrub(pr), default=str)
        for i, part in enumerate(chunks(full)):
            print("PR_FULL %s part%d %s" % (slug, i, part))
        before = pr.get("beforePosition") or {}
        after = pr.get("afterPosition") or {}
        netdec = dec(before.get("netPositionDecimal")) \
            or dec(pr.get("netPositionDecimal"))
        cost = dec(before.get("cost")) or dec(pr.get("cost"))
        cash = dec(before.get("cashValue")) or dec(pr.get("cashValue"))
        fees = dec(before.get("fees")) or dec(pr.get("fees"))
        e1_qty = D("2.60") if slug == SLUG_1215 else D("0")
        print("PR_SUMMARY %s" % json.dumps({
            "slug": slug,
            "net_position_decimal": None if netdec is None else str(netdec),
            "e1_filled_qty_for_this_market": str(e1_qty),
            "matches_e1_exactly": netdec is not None and netdec == e1_qty,
            "cost_usd": None if cost is None else str(cost),
            "cash_value_usd": None if cash is None else str(cash),
            "settlement_fees_usd": None if fees is None else str(fees),
            "avg_px": str(dec(before.get("avgPx"))
                          or dec(pr.get("avgPx"))),
            "outcome": ((before.get("marketMetadata") or {})
                        .get("outcome")),
            "venue_realized_field": str(dec(before.get("realized"))),
            "after_cost": str(dec(after.get("cost"))),
            "after_net": after.get("netPositionDecimal")
                         or after.get("netPosition"),
            "ATTRIBUTION": ("E1_EXACT_MATCH" if (netdec is not None
                                                 and netdec == e1_qty
                                                 and e1_qty > 0)
                            else "NOT_E1_OR_UNCONFIRMED"),
        }, default=str))

    print("NOW_EXPOSURE %s" % json.dumps({
        "open_orders": scrub(t.request("GET", "/v1/orders/open")),
        "positions": scrub(t.request("GET", "/v1/portfolio/positions")),
    }, default=str)[:500])
    print("ATTRIB_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
