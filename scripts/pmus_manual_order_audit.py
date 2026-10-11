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


def _executions(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The one or two executions an ACTIVITY_TYPE_TRADE row embeds.

    THE FEED ALREADY CARRIES THE ORDERS. Each trade row holds an
    `aggressorExecution` and a `passiveExecution`, and each of those holds
    the FULL order -- price, quantity, cumQuantity, state, times, commission,
    avgPx, outcomeSide, intent and manualOrderIndicator -- plus `aggressor`
    and `lastShares` / `lastPx` for that execution. An earlier version of
    this script walked the feed and then issued a `/v1/order/{id}` GET for
    every id-shaped string it could find, which spent 163 of its 200 reads
    on trade ids that 404. None of that was ever necessary.
    """
    t = row.get("trade")
    if not isinstance(t, dict):
        return []
    out = []
    for key in ("aggressorExecution", "passiveExecution"):
        ex = t.get(key)
        if isinstance(ex, dict) and isinstance(ex.get("order"), dict):
            out.append({"role": key, "exec": ex, "order": ex["order"],
                        "trade_id": t.get("id")})
    return out


def _order_ids(rows: Iterable[dict]) -> List[str]:
    """Distinct ORDER ids, from the embedded executions only."""
    seen: Set[str] = set()
    out: List[str] = []
    for r in rows:
        for e in _executions(r):
            oid = e["order"].get("id")
            if isinstance(oid, str) and oid and oid not in seen:
                seen.add(oid)
                out.append(oid)
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
        "timeInForce": o.get("tif") or o.get("timeInForce"),
        #: THE VENUE'S OWN manual-vs-automated record. This is authoritative
        #: attribution and it outranks the size heuristic below, which was
        #: only ever a stand-in for a field we had not noticed the venue
        #: already carries (observed MANUAL_ORDER_INDICATOR_MANUAL /
        #: _AUTOMATIC on real rows, 2026-09-05).
        "manualOrderIndicator": o.get("manualOrderIndicator"),
        "action": o.get("action"),
        "intent": o.get("intent"),
        "insertTime": o.get("insertTime"),
        "commissionsBasisPoints": o.get("commissionsBasisPoints"),
        "makerCommissionsBasisPoints": o.get("makerCommissionsBasisPoints"),
        "ATTRIBUTED_TO_BY_SIZE": attribute(o),
    }


#: Railway truncates a single log line at 81,920 characters and the API
#: returns only the most recent entries, so ONE BIG JSON LINE loses its own
#: beginning -- which is exactly what happened on the first run: the blob came
#: back starting mid-object with no MANUAL_AUDIT_BEGIN to anchor it. Output is
#: therefore emitted as many SMALL, INDIVIDUALLY PARSEABLE lines, each tagged
#: with its section and index, so a dropped line costs one row rather than the
#: whole history and reassembly needs no concatenation.
CHUNK_CHARS = int(os.getenv("AUDIT_CHUNK_CHARS", "6000"))


def emit(out: Dict[str, Any]) -> None:
    """One line per row, each small enough to survive the log line cap."""
    #: `activities` is DELIBERATELY NOT emitted raw. 1,984 rows as log lines
    #: were dropped by the log pipeline on the 15:05Z run; the sequences
    #: built from them are the deliverable and are far smaller.
    big = ("sequences", "orders", "ownership_undetermined", "market_state",
           "positions", "open_orders", "balances", "slugs")
    head = {k: v for k, v in out.items() if k not in big}
    rows: List[Dict[str, Any]] = []
    for sect in big:
        v = out.get(sect)
        if isinstance(v, list):
            batch: List[Any] = []
            size = 0
            for item in v:
                enc = json.dumps(item, default=str)
                if batch and size + len(enc) > CHUNK_CHARS:
                    rows.append({"section": sect, "items": batch})
                    batch, size = [], 0
                batch.append(item)
                size += len(enc)
            if batch:
                rows.append({"section": sect, "items": batch})
        elif v is not None:
            rows.append({"section": sect, "value": v})
    head["sections"] = {k: (len(out[k]) if isinstance(out.get(k), list)
                            else 1) for k in big if out.get(k) is not None}
    head["row_count"] = len(rows)
    print("MA_HEAD " + json.dumps(head, default=str)[:CHUNK_CHARS * 2])
    for i, r in enumerate(rows):
        r["i"] = i
        r["n"] = len(rows)
        print("MA_ROW " + json.dumps(r, default=str))
    print("MA_DONE %d" % len(rows))


#: SETTLEMENT-ONLY MODE. A comma-separated slug list. When set, the script
#: reads `/v1/markets/{slug}/bbo` for exactly those markets and nothing else
#: -- no walk, no ownership probe. The full run's value is the history; once
#: the history is in hand, reconciling the settlement of a known set of
#: markets is ten GETs, and re-walking 1,984 rows and re-probing thousands of
#: order ids to get them is waste that also risks the rate limit that made
#: the 15:24Z run overrun.
SETTLE_SLUGS = [x.strip() for x in
                os.getenv("AUDIT_SETTLE_SLUGS", "").split(",") if x.strip()]


def settlement_only(t: Any) -> Dict[str, Any]:
    """Venue settlement state for a named set of markets. GET only."""
    out: Dict[str, Any] = {
        "audit": "PMUS_SETTLEMENT_ONLY", "READ_ONLY": True,
        "transport": "src.pmus_auth.ReadOnlyTransport",
        "now_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requested": len(SETTLE_SLUGS),
        "accounting": ("venue settlement reads only. A market whose state "
                       "cannot be read is reported UNREADABLE, never as "
                       "unsettled and never as zero."),
    }
    rows: Dict[str, Any] = {}
    for sl in SETTLE_SLUGS:
        time.sleep(ORDER_PAUSE_S)
        r = get(t, "/v1/markets/%s/bbo" % sl)
        if r["ok"] and isinstance(r["body"], dict):
            md = r["body"].get("marketData") or r["body"]
            rows[sl] = {k: md.get(k) for k in
                        ("marketSlug", "state", "settlementPx", "currentPx",
                         "lastTradePx", "bestBid", "bestAsk")}
        else:
            rows[sl] = {"UNREADABLE": r.get("err"), "status": r.get("status")}
    out["market_state"] = rows
    out["readable"] = sum(1 for v in rows.values() if "UNREADABLE" not in v)
    out["settled"] = sum(1 for v in rows.values()
                         if v.get("settlementPx") not in (None, "")
                         and "UNREADABLE" not in v)
    return out


def main() -> int:
    t = A.ReadOnlyTransport()
    if SETTLE_SLUGS:
        emit(settlement_only(t))
        return 0
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
    out["activity_rows_walked"] = len(walk["rows"])
    out["activity_kinds"] = {}
    for r in walk["rows"]:
        k = str(r.get("type"))
        out["activity_kinds"][k] = out["activity_kinds"].get(k, 0) + 1

    # 3. OWNERSHIP. The feed embeds both sides of every trade, so being in
    #    the row is not evidence that an order is ours. `/v1/order/{id}`
    #    answers 200 for our own orders and 404 for a counterparty's, so it
    #    is used as the ownership test and nothing else -- one GET per
    #    DISTINCT ORDER id, bounded.
    ids = _order_ids(walk["rows"])
    out["distinct_order_ids_in_feed"] = len(ids)
    out["ownership_probe_capped"] = len(ids) > MAX_ORDER_READS
    owned: Dict[str, Dict[str, Any]] = {}
    not_owned: List[str] = []
    probe_errors: List[Dict[str, Any]] = []
    for oid in ids[:MAX_ORDER_READS]:
        time.sleep(ORDER_PAUSE_S)
        r = get(t, "/v1/order/%s" % oid)
        if r["ok"]:
            body = r["body"]
            o = body.get("order") if isinstance(body, dict) else None
            owned[oid] = slim_order(scrub(o if isinstance(o, dict) else body))
        elif r.get("status") == 404:
            not_owned.append(oid)
        else:
            #: NOT a 404, so ownership is UNDETERMINED -- never recorded as
            #: not-ours. A rate limit is not a statement about ownership.
            probe_errors.append({"id": oid, "err": r.get("err"),
                                 "status": r.get("status")})
    out["orders"] = list(owned.values())
    out["orders_not_owned"] = len(not_owned)
    out["ownership_undetermined"] = probe_errors

    # 4. SAME-MARKET SEQUENCES, built here rather than shipped raw. 1,984
    #    activity rows as log lines were dropped by the log pipeline; the
    #    sequences are what the audit is for and they are two orders of
    #    magnitude smaller.
    seqs: Dict[str, Dict[str, Any]] = {}
    for row in walk["rows"]:
        if str(row.get("type")) != "ACTIVITY_TYPE_TRADE":
            continue
        for e in _executions(row):
            o, ex = e["order"], e["exec"]
            oid = str(o.get("id"))
            if oid not in owned:
                continue                      # not ours, or undetermined
            slug = str(o.get("marketSlug") or "")
            S = seqs.setdefault(slug, {"slug": slug, "fills": [],
                                       "order_ids": []})
            if oid not in S["order_ids"]:
                S["order_ids"].append(oid)
            px = ex.get("lastPx")
            S["fills"].append({
                "trade_id": e["trade_id"], "order_id": oid,
                "t": ex.get("transactTime"),
                "role": "AGGRESSOR" if ex.get("aggressor") else "PASSIVE",
                "aggressor_flag": ex.get("aggressor"),
                "exec_type": ex.get("type"),
                "shares": ex.get("lastShares"),
                "wire_px": (px.get("value") if isinstance(px, dict) else px),
                "commission": ((ex.get("commissionNotionalCollected") or {})
                               .get("value")),
                "intent": o.get("intent"),
                "outcomeSide": o.get("outcomeSide"),
                "side": o.get("side"),
                "manualOrderIndicator": o.get("manualOrderIndicator"),
            })
    for row in walk["rows"]:
        if str(row.get("type")) != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        r2 = row.get("positionResolution") or row.get("resolution") or row
        slug = str(r2.get("marketSlug") or r2.get("slug") or "")
        if slug:
            seqs.setdefault(slug, {"slug": slug, "fills": [],
                                   "order_ids": []})["resolution"] = scrub(r2)
    for S in seqs.values():
        S["fills"].sort(key=lambda f: str(f.get("t") or ""))
        S["n_fills"] = len(S["fills"])
    out["sequences"] = list(seqs.values())

    # 5. current state
    for path, label, base in (
            ("/v1/orders/open", "open_orders", A.API),
            ("/v1/portfolio/positions", "positions", A.API),
            ("/v1/account/balances", "balances", A.API)):
        r = get(t, path, base)
        out[label] = r if not r["ok"] else scrub(r["body"])

    # 6. settlement state for every slug the history touched
    #: SETTLEMENT IS READ FOR THE MARKETS THAT HAVE OUR FILLS FIRST.
    #: An alphabetical slice took 60 of 166 slugs and reached only 2 of the
    #: 32 markets with owned fills, so 30 filled markets had no settlement
    #: evidence at all -- which the analyser then could not tell apart from
    #: "still open". Filled markets are read first, and the cap is reported.
    with_fills = sorted((s2 for s2, S in seqs.items()
                         if s2 and S.get("fills")),
                        key=lambda k: -len(seqs[k]["fills"]))
    without = sorted(s2 for s2 in seqs if s2 and not seqs[s2].get("fills"))
    slugs = with_fills + without
    out["slugs"] = slugs
    out["slugs_with_owned_fills"] = len(with_fills)
    bbo: Dict[str, Any] = {}
    cap = int(os.getenv("AUDIT_MAX_BBO_READS", "120"))
    out["market_state_read_cap"] = cap
    out["market_state_capped"] = len(slugs) > cap
    for s in slugs[:cap]:
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

    emit(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
