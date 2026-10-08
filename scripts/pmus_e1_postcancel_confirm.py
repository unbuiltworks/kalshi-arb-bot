"""POST-CANCEL VENUE CONFIRMATION for the 20 E1 orders. Read-only.

The owner's instruction: "Do not treat a successful cancellation API response
as proof until venue state confirms it." So this re-reads each of the 20 order
ids individually from the venue, plus open orders, trades, positions and
balances, and prints ONE SHORT LINE PER RECORD.

One line per record is the point. The earlier audit emitted a single large
JSON object; Railway truncates a log line at 81,920 characters and drops it,
so the post-cancel run came back empty. Short lines survive.

Runs in `pmus-readonly-recovery`, whose image contains only
`pmus_auth.ReadOnlyTransport` (PermissionError on any method but GET) and no
executor, order body or ledger. It cannot place, amend or cancel anything.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from src import pmus_auth as A

E1_IDS = [
    "CZH22KCSPZ8H", "CZH22KD92Z8H", "CZH22KDH8Z8H", "CZH22KDJEZ8H",
    "CZH25AYD0Z84", "CZH26B7HCZ73", "CZH28KHW0Z7Z", "CZH28KJ1PZ7Z",
    "CZH28KJ6AZ7Z", "CZH28KJECZ7Z", "CZH2A4FWPZ7D", "CZH2A4G66Z7D",
    "CZH2E3NAMZ7J", "CZH2E3NVWZ7J", "CZH2F2FBEZ7T", "CZH2F2G70Z7T",
    "CZH2FP4R8Z8K", "CZH2FP5MCZ8K", "CZH2FP5QMZ8K", "CZH2HF6RAZ8E",
]
TERMINAL = ("ORDER_STATE_CANCELED", "ORDER_STATE_CANCELLED",
            "ORDER_STATE_EXPIRED", "ORDER_STATE_REJECTED")


def get(t, path):
    try:
        return {"ok": True, "body": t.request("GET", path, base=A.API)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "err": "%s:%s" % (
            type(exc).__name__, getattr(exc, "code", "") or str(exc)[:60])}


def rows(body: Any, *keys: str) -> List[Dict[str, Any]]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for k in keys:
            v = body.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                return [v]
    return []


def px(o: Any) -> str:
    if isinstance(o, dict):
        return str(o.get("value"))
    return str(o)


def main() -> int:
    t = A.ReadOnlyTransport()
    print("CONFIRM_BEGIN now=%s"
          % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    states: Dict[str, str] = {}
    cum = 0.0
    for oid in E1_IDS:
        r = get(t, "/v1/order/%s" % oid)
        if not r["ok"]:
            states[oid] = "UNREADABLE"
            print("ORDER %s UNREADABLE %s" % (oid, r["err"]))
            continue
        o = (r["body"].get("order") if isinstance(r["body"], dict)
             else None) or r["body"]
        st = str(o.get("state"))
        c = float(o.get("cumQuantity") or 0)
        cum += c
        states[oid] = st
        print("ORDER %s %s qty=%s cum=%s leaves=%s px=%s side=%s slug=%s"
              % (oid, st, o.get("quantity"), o.get("cumQuantity"),
                 o.get("leavesQuantity"), px(o.get("price")),
                 o.get("outcomeSide"), o.get("marketSlug")))

    r = get(t, "/v1/orders/open")
    openn = rows(r["body"], "orders", "data") if r["ok"] else None
    print("OPEN_ORDERS n=%s ids=%s"
          % ("ERR" if openn is None else len(openn),
             [] if not openn else [x.get("id") for x in openn]))

    r = get(t, "/v1/mytrades?limit=100")
    trs = rows(r["body"], "trades", "myTrades", "data") if r["ok"] else []
    mine = [x for x in trs if str(x.get("orderId") or "") in set(E1_IDS)]
    print("TRADES_FOR_THE_20 n=%s rows=%s" % (len(mine), json.dumps(mine)[:600]))

    for path, keys in (("/v1/portfolio/positions",
                        ("positions", "availablePositions", "data")),
                       ("/v1/prediction-markets/positions",
                        ("positions", "availablePositions", "data")),
                       ("/v1/account/balances", ("balances",))):
        r = get(t, path)
        if not r["ok"]:
            print("GET %s ERR %s" % (path, r["err"]))
            continue
        rr = rows(r["body"], *keys)
        print("GET %s n=%s %s" % (path, len(rr), json.dumps(rr)[:500]))

    nonterm = {k: v for k, v in states.items()
               if v not in TERMINAL and v != "UNREADABLE"}
    print("VERDICT %s" % json.dumps({
        "ids_checked": len(E1_IDS),
        "state_histogram": {s: sum(1 for v in states.values() if v == s)
                            for s in sorted(set(states.values()))},
        "sum_cumQuantity": cum,
        "non_terminal_remaining": nonterm,
        "all_terminal": not nonterm,
        "open_orders_now": None if openn is None else len(openn),
        "VERIFIED_AGAINST": "GET /v1/order/{id} per id + GET /v1/orders/open",
        "NOT_a_cancel_api_response": True,
    }))
    print("CONFIRM_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
