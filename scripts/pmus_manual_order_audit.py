"""THE OWNER'S ACTUAL PMUS ORDER AND FILL HISTORY. Read-only, structurally so.

The owner reports repeatedly obtaining real fills by placing limit orders
manually at or below the best bid. This asks the VENUE what those orders were,
because the venue is the only authority on what exists.

It runs in the `pmus-readonly-recovery` image, which contains exactly one HTTP
object -- `pmus_auth.ReadOnlyTransport`, raising PermissionError on any method
but GET -- and deliberately omits `l5_pmus_transport`, every executor, every
order body and every ledger. It cannot place, cancel or amend anything, and it
touches no running service.

WHY A NEW SCRIPT AND NOT A CHANGE TO `pmus_order_history_audit`
---------------------------------------------------------------
That tool answered "did an order exist today", and for that its
`activities?limit=100` single page truncated to `rows[:8]` was enough. A
history reconstruction needs the opposite: EVERY row, oldest bounded by date,
and then the per-order detail that the activity rows do not carry. Its
attribution-by-quantity rule is imported from it rather than restated.

WHY THE ACTIVITY FEED
---------------------
PMUS serves no order-history collection. Established by this programme's own
probes, not assumed: `/v1/orders` answers **501** (a collection that exists
but will not GET), `/v1/orders/{id}` **404s**, `/v1/orders/open` carries only
what is live right now, and `/v1/mytrades` is a Gemini route. The single
account-scoped history route is `/v1/portfolio/activities`, cursor-walked.

THE WALK IS THE RISK, SO IT IS HARDENED THE WAY THE SETTLEMENT AUDITOR'S IS
---------------------------------------------------------------------------
`src/pmus_settlement_auditor.py` cannot be imported here -- it reaches
`pmus_q1_executor_v4`, which can POST, and an image that cannot mutate is
worth more than one that merely declines to. Its hard-won walk contract is
reproduced instead, including the lesson that cost it seven false
"PENDING_SETTLEMENT" verdicts:

  * `nextCursor` + `eof` drive the walk; the cursor parameter NAME is
    discovered from candidates because the venue's is undocumented;
  * **a failed request is not a wrong parameter name.** A 429 must never be
    read as "that cursor param does not work", because that ends a walk
    silently with a partial row set that then reads as authoritative absence.
    Errors are collected and the walk's `ended` reason is always reported;
  * the feed 429s after roughly twenty pages, so pages are spaced and a 429
    is retried with backoff rather than treated as the end;
  * **a truncated walk is never presented as a history.** If the walk ends on
    anything but `eof` / `older_than_since`, `complete` is false and every
    downstream count is labelled a LOWER BOUND.

WHAT IT OUTPUTS
---------------
One JSON blob between `MANUAL_AUDIT_BEGIN` / `MANUAL_AUDIT_END`:

  * every activity row, with its type, time, slug, side, price and quantity;
  * for every order id appearing anywhere in those rows, the venue's own
    `/v1/order/{id}`: limit price, quantity, cumQuantity, leavesQuantity,
    state, createTime, lastTransactTime, avgPx, commission and
    `participateDontInitiate` -- which is the venue's own record of whether
    the order was post-only, i.e. the maker/taker classification, taken from
    the venue rather than inferred from price;
  * resting duration per order, as lastTransactTime - createTime;
  * current open orders, positions, balances;
  * `/v1/markets/{slug}/bbo` per touched slug, for settlement state and price.

Secrets never leave: credentials are read by the auth module and never
printed, and account identifiers are scrubbed on the way out.

NOTHING HERE IS REALIZED P&L. Settlement-complete economics are computed by
the offline analyser, from these rows.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Set

from src import pmus_auth as A
from scripts.pmus_order_history_audit import attribute, scrub

#: oldest activity date to walk back to, inclusive. ISO date or timestamp.
SINCE = os.getenv("AUDIT_SINCE", "2026-09-20").strip()
#: hard page cap. The feed 429s around twenty pages; this bounds the read
#: rather than hiding the bound.
MAX_PAGES = int(os.getenv("AUDIT_MAX_PAGES", "40"))
PAGE_PAUSE_S = float(os.getenv("AUDIT_PAGE_PAUSE_S", "1.5"))
ORDER_PAUSE_S = float(os.getenv("AUDIT_ORDER_PAUSE_S", "0.5"))
#: cap on per-order GETs, so a huge history cannot run unbounded.
MAX_ORDER_READS = int(os.getenv("AUDIT_MAX_ORDER_READS", "200"))
RETRY_PAUSE_S = float(os.getenv("AUDIT_RETRY_PAUSE_S", "20"))

CURSOR_PARAM_CANDIDATES = ("cursor", "nextCursor", "pageToken", "page_token",
                           "next_cursor")
ACTIVITIES = "/v1/portfolio/activities"
#: keys whose values look like a venue order id
ORDER_ID_KEYS = ("orderId", "order_id", "orderID", "id", "clientOrderId",
                 "takerOrderId", "makerOrderId")


def _is_rate_limited(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    return code == 429 or "429" in str(exc)


def get(t: Any, path: str, base: str = A.API) -> Dict[str, Any]:
    """One GET, with a single 429 retry. Never silently swallows a failure."""
    for attempt in (1, 2):
        try:
            return {"ok": True, "body": t.request("GET", path, base=base)}
        except Exception as exc:                               # noqa: BLE001
            if _is_rate_limited(exc) and attempt == 1:
                time.sleep(RETRY_PAUSE_S)
                continue
            return {"ok": False, "status": getattr(exc, "code", None),
                    "err": "%s:%s" % (type(exc).__name__, str(exc)[:80]),
                    "rate_limited": _is_rate_limited(exc)}
    return {"ok": False, "err": "unreachable"}


def _row_time(r: Any) -> Optional[str]:
    if not isinstance(r, dict):
        return None
    for k in ("createTime", "timestamp", "time", "transactTime",
              "lastTransactTime", "createdAt"):
        v = r.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def walk_activities(t: Any) -> Dict[str, Any]:
    """Every activity row back to SINCE. Reports how the walk ended, always."""
    rows: List[dict] = []
    errors: List[str] = []
    cursor_param: Optional[str] = None
    r = get(t, "%s?limit=100" % ACTIVITIES)
    if not r["ok"]:
        return {"rows": [], "pages": 0, "ended": "first_page_failed",
                "errors": [r.get("err")], "cursor_param": None,
                "complete": False}
    resp = r["body"]
    pages = 1
    ended = "single_page"
    while True:
        page = resp.get("activities") if isinstance(resp, dict) else None
        page = [x for x in (page or []) if isinstance(x, dict)]
        rows.extend(dict(x) for x in page)
        eof = bool(resp.get("eof")) if isinstance(resp, dict) else True
        cursor = resp.get("nextCursor") if isinstance(resp, dict) else None
        oldest = _row_time(page[-1]) if page else None
        if eof or not cursor or not page:
            ended = ("eof" if eof else
                     ("no_cursor" if not cursor else "empty_page"))
            break
        if SINCE and oldest and oldest < SINCE:
            ended = "older_than_since"
            break
        if pages >= MAX_PAGES:
            ended = "max_pages"
            break
        time.sleep(PAGE_PAUSE_S)
        nxt = None
        names = ([cursor_param] if cursor_param else []) + [
            n for n in CURSOR_PARAM_CANDIDATES if n != cursor_param]
        for name in names:
            from urllib.parse import quote
            cand = get(t, "%s?limit=100&%s=%s"
                       % (ACTIVITIES, name, quote(str(cursor))))
            if not cand["ok"]:
                # A FAILED REQUEST IS NOT A WRONG PARAMETER NAME.
                errors.append("%s on cursor_param=%s"
                              % (cand.get("err"), name))
                break
            if isinstance(cand["body"], dict) and isinstance(
                    cand["body"].get("activities"), list):
                nxt, cursor_param = cand["body"], name
                break
        if nxt is None:
            ended = "error" if errors else "cursor_param_unknown"
            break
        resp = nxt
        pages += 1
    return {"rows": rows, "pages": pages, "ended": ended, "errors": errors,
            "cursor_param": cursor_param,
            "complete": ended in ("eof", "older_than_since"),
            "completeness_note": (
                "complete=false means this is a LOWER BOUND on the account's "
                "history and no absence may be read from it")}


def _order_ids(rows: Iterable[dict]) -> List[str]:
    """Every venue-order-shaped id anywhere in the rows, order preserved."""
    seen: Set[str] = set()
    out: List[str] = []

    def walk(node: Any, key: Optional[str] = None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            if key in ORDER_ID_KEYS and 6 <= len(node) <= 40:
                if node not in seen:
                    seen.add(node)
                    out.append(node)

    for r in rows:
        walk(r)
    return out


def _dur_s(a: Optional[str], b: Optional[str]) -> Optional[float]:
    if not a or not b:
        return None
    import datetime as dt

    def p(x: str):
        x = x.replace("Z", "+00:00")
        # the venue sends nanosecond precision; trim to microseconds
        if "." in x:
            head, rest = x.split(".", 1)
            frac = "".join(c for c in rest if c.isdigit())[:6]
            tail = rest[len(("".join(c for c in rest if c.isdigit()))):]
            x = "%s.%s%s" % (head, frac, tail or "+00:00")
        try:
            return dt.datetime.fromisoformat(x)
        except Exception:                                      # noqa: BLE001
            return None
    pa, pb = p(a), p(b)
    if pa is None or pb is None:
        return None
    return round((pb - pa).total_seconds(), 3)


def slim_order(o: Dict[str, Any]) -> Dict[str, Any]:
    px = o.get("price")
    avg = o.get("avgPx")
    com = o.get("commissionNotionalTotalCollected")
    create = o.get("createTime") or o.get("insertTime")
    last = o.get("lastTransactTime")
    return {
        "id": o.get("id") or o.get("orderId"),
        "slug": o.get("marketSlug"),
        "side": o.get("side"),
        "outcomeSide": o.get("outcomeSide"),
        "type": o.get("type"),
        "limit_price": (px.get("value") if isinstance(px, dict) else px),
        "quantity": o.get("quantity"),
        "cumQuantity": o.get("cumQuantity"),
        "leavesQuantity": o.get("leavesQuantity"),
        "state": o.get("state"),
        "createTime": create,
        "lastTransactTime": last,
        "resting_s": _dur_s(create, last),
        "avgPx": (avg.get("value") if isinstance(avg, dict) else avg),
        "commission": (com.get("value") if isinstance(com, dict) else com),
        #: THE VENUE'S OWN maker/taker record, not an inference from price.
        "participateDontInitiate": o.get("participateDontInitiate"),
        "timeInForce": o.get("timeInForce"),
        "ATTRIBUTED_TO_BY_SIZE": attribute(o),
    }


def main() -> int:
    t = A.ReadOnlyTransport()
    out: Dict[str, Any] = {
        "audit": "PMUS_MANUAL_ORDER_AUDIT",
        "READ_ONLY": True,
        "image_cannot_mutate": True,
        "transport": "src.pmus_auth.ReadOnlyTransport",
        "touches_no_running_service": True,
        "now_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "since": SINCE,
        "accounting": ("venue reads only. Nothing here is realized P&L; "
                       "settlement-complete economics are computed offline "
                       "from these rows."),
    }

    # 1. prove the transport cannot mutate, from inside the process
    try:
        A.ReadOnlyTransport().request("POST", "/v1/orders", {}, A.API)
        out["post_blocked"] = False
    except PermissionError:
        out["post_blocked"] = True
    except Exception as exc:                                   # noqa: BLE001
        out["post_blocked"] = "unexpected:%s" % type(exc).__name__

    # 2. the account-scoped history
    walk = walk_activities(t)
    out["activities_walk"] = {k: v for k, v in walk.items() if k != "rows"}
    out["activities"] = scrub(walk["rows"])
    out["activity_kinds"] = {}
    for r in walk["rows"]:
        k = str(r.get("type"))
        out["activity_kinds"][k] = out["activity_kinds"].get(k, 0) + 1

    # 3. per-order venue detail for every id the history references
    ids = _order_ids(walk["rows"])
    out["order_ids_seen"] = len(ids)
    out["order_reads_capped"] = len(ids) > MAX_ORDER_READS
    orders: List[Dict[str, Any]] = []
    unreadable: List[Dict[str, Any]] = []
    for oid in ids[:MAX_ORDER_READS]:
        time.sleep(ORDER_PAUSE_S)
        r = get(t, "/v1/order/%s" % oid)
        if not r["ok"]:
            unreadable.append({"id": oid, "err": r.get("err"),
                               "status": r.get("status")})
            continue
        body = r["body"]
        o = body.get("order") if isinstance(body, dict) else None
        orders.append(slim_order(scrub(o if isinstance(o, dict) else body)))
    out["orders"] = orders
    out["orders_unreadable"] = unreadable

    # 4. current state
    for path, label, base in (
            ("/v1/orders/open", "open_orders", A.API),
            ("/v1/portfolio/positions", "positions", A.API),
            ("/v1/account/balances", "balances", A.API)):
        r = get(t, path, base)
        out[label] = r if not r["ok"] else scrub(r["body"])

    # 5. settlement state for every slug the history touched
    slugs = sorted({str(o.get("slug")) for o in orders if o.get("slug")}
                   | {str(r.get("marketSlug")) for r in walk["rows"]
                      if isinstance(r, dict) and r.get("marketSlug")})
    out["slugs"] = slugs
    bbo: Dict[str, Any] = {}
    for s in slugs[:60]:
        if not s or s == "None":
            continue
        time.sleep(ORDER_PAUSE_S)
        r = get(t, "/v1/markets/%s/bbo" % s)
        if r["ok"] and isinstance(r["body"], dict):
            md = r["body"].get("marketData") or r["body"]
            bbo[s] = {k: md.get(k) for k in
                      ("marketSlug", "state", "settlementPx", "currentPx",
                       "lastTradePx", "bestBid", "bestAsk")}
        else:
            bbo[s] = {"unreadable": r.get("err")}
    out["market_state"] = bbo

    print("MANUAL_AUDIT_BEGIN")
    print(json.dumps(out, default=str))
    print("MANUAL_AUDIT_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
