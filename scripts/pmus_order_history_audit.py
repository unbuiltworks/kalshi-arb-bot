"""AUTHORITATIVE VENUE ORDER HISTORY. Read-only, and structurally so.

The owner reports a new order on Polymarket US during the live E1 run. My
network-flow analysis concluded zero submissions, and network-flow inference
is NOT authority: Railway's flow records are aggregated and best-effort
("flowState": "partial"), so an absent record is not proof of an absent
connection. I also generalised a 12:18-12:23 sample into a claim about the
whole run, which was wrong on its face because time kept moving.

So this asks the VENUE, which is the only authority on what orders exist.

It runs in the `pmus-readonly-recovery` image, which contains exactly one HTTP
object -- `pmus_auth.ReadOnlyTransport`, raising PermissionError on any method
but GET -- and deliberately omits `l5_pmus_transport`, every executor, every
order body and every ledger. It is incapable of placing, cancelling or
amending anything. **It does not touch the running E1 service.**

ATTRIBUTION IS BY QUANTITY, which is unambiguous here:

    0.13 shares -> E1          (its frozen first-leg size)
    0.01 shares -> XR2W / A6 or the micro probes
    1    share  -> Q1 / CASE1

combined with the slug's own date.

Secrets never leave: credentials are read by the auth module and never
printed; account identifiers are redacted on the way out.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from src import pmus_auth as A

TODAY = "2026-10-08"
E1_QTY = "0.13"
REDACT = ("accountId", "account_id", "walletAddress", "address", "userId",
          "subaccountId", "ownerId")


def get(t, path, base=A.API):
    try:
        return {"ok": True, "body": t.request("GET", path, base=base)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "err": "%s:%s" % (
            type(exc).__name__, getattr(exc, "code", "") or str(exc)[:60])}


def scrub(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: ("REDACTED" if k in REDACT else scrub(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o


def rows(body: Any, *keys: str) -> List[Dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    for k in keys:
        v = body.get(k)
        if isinstance(v, list):
            return v
    for v in body.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def qty_of(o: Dict[str, Any]) -> str:
    for k in ("quantity", "orderQty", "qty"):
        if o.get(k) is not None:
            return str(o[k])
    return "?"


def attribute(o: Dict[str, Any]) -> str:
    q = qty_of(o)
    try:
        from decimal import Decimal as D
        qd = D(q)
    except Exception:                                          # noqa: BLE001
        return "UNKNOWN_QTY:%s" % q
    if qd == D(E1_QTY):
        return "E1"
    if qd == D("0.01"):
        return "XR2W_A6_OR_MICRO_PROBE"
    if qd == D("1"):
        return "Q1_OR_CASE1"
    return "UNRECOGNISED_SIZE"


def slim(o: Dict[str, Any]) -> Dict[str, Any]:
    px = o.get("price")
    return {
        "id": o.get("id") or o.get("orderId"),
        "slug": o.get("marketSlug") or (o.get("marketMetadata") or {}).get("slug"),
        "outcomeSide": o.get("outcomeSide"),
        "action": o.get("action"),
        "side": o.get("side"),
        "price": (px.get("value") if isinstance(px, dict) else px),
        "quantity": qty_of(o),
        "cumQuantity": o.get("cumQuantity"),
        "leavesQuantity": o.get("leavesQuantity"),
        "state": o.get("state") or o.get("status"),
        "createTime": o.get("createTime") or o.get("insertTime"),
        "lastTransactTime": o.get("lastTransactTime"),
        "participateDontInitiate": o.get("participateDontInitiate"),
        "ATTRIBUTED_TO": attribute(o),
    }


def main() -> int:
    t = A.ReadOnlyTransport()
    out: Dict[str, Any] = {
        "audit": "PMUS_ORDER_HISTORY",
        "READ_ONLY": True,
        "image_cannot_mutate": True,
        "transport": "src.pmus_auth.ReadOnlyTransport",
        "does_not_touch_the_e1_service": True,
        "now_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "attribution_rule": {"0.13": "E1", "0.01": "XR2W/A6 or micro probe",
                             "1": "Q1/CASE1"},
    }

    for path, label, keys in (
            ("/v1/orders?limit=100", "orders", ("orders", "data")),
            ("/v1/orders/open", "open_orders", ("orders", "data")),
            ("/v1/mytrades?limit=100", "mytrades", ("trades", "myTrades",
                                                    "data")),
    ):
        r = get(t, path)
        if not r["ok"]:
            out[label] = r
            continue
        rws = rows(r["body"], *keys)
        slimmed = [slim(scrub(x)) for x in rws]
        out[label] = {
            "n": len(rws),
            "today": [s for s in slimmed
                      if TODAY in str(s.get("createTime"))
                      or TODAY in str(s.get("slug"))],
            "e1_attributed": [s for s in slimmed
                              if s["ATTRIBUTED_TO"] == "E1"],
            "all": slimmed[:12],
        }

    for path, label, keys in (
            ("/v1/portfolio/positions", "positions",
             ("positions", "availablePositions", "data")),
            ("/v1/prediction-markets/positions", "pm_positions",
             ("positions", "availablePositions", "data")),
            ("/v1/account/balances", "balances", ("balances",)),
            ("/v1/portfolio/activities?limit=100", "activities",
             ("activities", "data")),
    ):
        r = get(t, path)
        out[label] = (r if not r["ok"]
                      else {"n": len(rows(r["body"], *keys)),
                            "rows": scrub(rows(r["body"], *keys))[:8]})

    print("ORDERS_BEGIN")
    print(json.dumps(out, default=str))
    print("ORDERS_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
