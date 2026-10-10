"""What the venue says about one order, one position and one account. GET only.

Probe 04 put a real order on PMUS -- venue id CQ5NPBADMYC3 -- and then could
not close it out: `cancel()` raised HTTPError and `reconcile_from_venue()`
raised TypeError, so the run ended with the exposure deliberately NOT
released and the store marked uncertain. That is the correct fail-closed
behaviour and it leaves a question only the venue can answer: did that order
fill?

This answers it and nothing else. It holds `pmus_auth.ReadOnlyTransport`,
which raises PermissionError on any method but GET, so the process cannot be
talked into sending anything. It creates no order, cancels nothing, claims no
authorization and writes no inventory state.

It also records the RESPONSE SHAPES verbatim. The TypeError is almost
certainly a shape mismatch -- `reconcile_from_venue` does `p["market"]` over
`positions()`, and a mapping response would iterate to strings -- but
"almost certainly" is how we got here. The fix will be written against what
the venue actually returns.

Deliberately NOT imported: `l5_pmus_transport`. It carries `_SignedHTTP`,
which can POST. An image that cannot mutate is better than one that merely
declines to.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_auth as Q1

#: the order Probe 04 left unresolved
ORDER_ID = os.getenv("RECOVERY_ORDER_ID", "CQ5NPBADMYC3").strip()

#: every venue order id to read in full, comma separated. Probe 05 filled
#: eighteen orders and its ACKs all said filled_qty 0, so the average fill
#: price and commission exist ONLY at the venue -- the journal never held
#: them. One GET each is the authoritative way to complete the per-order
#: table, and it is still GET.
ORDER_IDS = [x.strip() for x in
             os.getenv("RECOVERY_ORDER_IDS", "").split(",") if x.strip()]
MARKET = os.getenv("RECOVERY_MARKET",
                   "cpc-btc-updown-15m-2026-09-25-1300z").strip()

_SECRETISH = re.compile(
    r"(X-PM-Access-Key|X-PM-Signature|X-PM-Timestamp|Authorization)"
    r"\s*[:=]\s*\S+", re.I)


def redact(text: Any) -> str:
    """Strip anything header- or signature-shaped, and the credentials.

    Deliberately a local copy rather than an import: the module that owns the
    shared scrubber also owns a transport that can POST, and this image must
    not contain one. The duplication is four lines and is tested.
    """
    s = str(text)
    s = _SECRETISH.sub(r"\1: [REDACTED]", s)
    for env in (Q1.KEY_ENV, Q1.SECRET_ENV):
        v = os.getenv(env, "")
        if v and len(v) > 6:
            s = s.replace(v, "[REDACTED]")
    return s


def shape_of(v: Any, depth: int = 0) -> Any:
    """What the venue actually returned, structurally.

    `reconcile_from_venue` assumed a list of row mappings. Whether that is
    true is the whole question, so the shape is reported before anything
    tries to read fields out of it.
    """
    if depth > 3:
        return "..."
    if isinstance(v, dict):
        return {"type": "object", "keys": sorted(v)[:25],
                "values": {k: shape_of(v[k], depth + 1)
                           for k in sorted(v)[:6]}}
    if isinstance(v, list):
        return {"type": "array", "len": len(v),
                "first": shape_of(v[0], depth + 1) if v else None}
    return {"type": type(v).__name__}


def _utc() -> str:
    """One UTC timestamp format for every line this process prints.

    An account-wide read without a timestamp cannot be told apart from a
    two-day-old one, and that ambiguity is exactly what made the previous
    recovery evidence unusable.
    """
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(path: str, base: str = Q1.API) -> Dict[str, Any]:
    """One signed GET, and whatever came back, never raising."""
    http = Q1.ReadOnlyTransport()
    try:
        body = http.request("GET", path, None, base)
        return {"path": path, "ok": True, "status": 200, "as_of_utc": _utc(),
                "shape": shape_of(body), "body": body}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:                                      # noqa: BLE001
            pass
        return {"path": path, "ok": False, "status": exc.code,
                "as_of_utc": _utc(),
                "error": "HTTPError", "detail": redact(detail)}
    except Exception as exc:                                   # noqa: BLE001
        return {"path": path, "ok": False, "status": None,
                "as_of_utc": _utc(),
                "error": type(exc).__name__, "detail": redact(exc)[:300]}


def find_order(payloads: List[Dict[str, Any]]) -> List[dict]:
    """Every object anywhere in these responses that mentions the order id."""
    found: List[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if ORDER_ID in json.dumps(node, default=str):
                if not any(isinstance(v, (dict, list)) and
                           ORDER_ID in json.dumps(v, default=str)
                           for v in node.values()):
                    found.append(node)          # the innermost match
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for p in payloads:
        if p.get("ok"):
            walk(p.get("body"))
    return found


#: substring that identifies the intended BTC 15-minute up/down series on
#: PMUS slugs. Used only to FILTER an active-market listing -- the live
#: slug is never guessed or constructed, because a constructed slug that
#: 404s is indistinguishable from a market that does not exist.
BTC15M_MATCH = os.getenv("RECOVERY_BTC15M_MATCH", "btc-updown-15m").strip()

#: how many matching markets to report metadata for
BTC15M_MAX = int(os.getenv("RECOVERY_BTC15M_MAX", "6"))


def _market_rows(body: Any) -> List[Mapping[str, Any]]:
    """Market rows out of whatever envelope the listing came back in."""
    if isinstance(body, Mapping):
        for key in ("markets", "data", "results", "items"):
            v = body.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, Mapping)]
        return []
    if isinstance(body, list):
        return [r for r in body if isinstance(r, Mapping)]
    return []


def venue_meta() -> None:
    """minimumTradeQty and orderPriceMinTickSize, READ from the venue.

    These two numbers are currently code constants (VENUE_MIN_QTY 0.01,
    VENUE_TICK 0.01) whose only live corroboration is an HTTP 400 at
    quantity 0.005 -- i.e. the minimum is known to lie in (0.005, 0.05]
    and nothing narrower. The venue publishes both per market, so they are
    asked for rather than asserted. Still GET: a listing and one metadata
    read per candidate, no order of any kind.
    """
    listing = get("/v1/markets?active=true&closed=false&limit=200")
    rows = _market_rows(listing.get("body")) if listing.get("ok") else []
    hits = [r for r in rows
            if BTC15M_MATCH in str(r.get("slug") or "").lower()]
    print("PMUS_VENUE_META_SCAN " + redact(json.dumps(
        {"as_of_utc": _utc(), "listing_ok": listing.get("ok"),
         "listing_status": listing.get("status"),
         "listing_error": listing.get("error"),
         "rows_seen": len(rows), "btc15m_match": BTC15M_MATCH,
         "btc15m_rows": len(hits)}, default=str)), flush=True)

    for row in sorted(hits, key=lambda r: str(r.get("slug")))[:BTC15M_MAX]:
        slug = str(row.get("slug") or "")
        detail = get("/v1/market/slug/%s" % slug) if slug else {"ok": False}
        full = detail.get("body") if detail.get("ok") else None
        if isinstance(full, Mapping):
            inner = full.get("market")
            if isinstance(inner, Mapping):
                full = inner
        src = full if isinstance(full, Mapping) else row
        print("PMUS_VENUE_META " + redact(json.dumps(
            {"as_of_utc": _utc(), "slug": slug,
             "from": "market/slug" if isinstance(full, Mapping) else "listing",
             "minimumTradeQty": src.get("minimumTradeQty"),
             "orderPriceMinTickSize": src.get("orderPriceMinTickSize"),
             "feeCoefficient": src.get("feeCoefficient"),
             "status": src.get("status"), "active": src.get("active"),
             "closed": src.get("closed"),
             "ABSENT_MEANS_UNKNOWN_NOT_ZERO": True}, default=str)),
            flush=True)


def main() -> int:
    if not Q1.has_credentials():
        print("PMUS_RECOVERY " + json.dumps(
            {"LABEL": "READONLY_RECOVERY", "refused": "NO_CREDENTIALS"}),
            flush=True)
        return 0

    # the transport cannot mutate; prove it in the record rather than assert it
    can_mutate = True
    try:
        Q1.ReadOnlyTransport().request("POST", "/v1/orders", {}, Q1.API)
    except PermissionError:
        can_mutate = False
    except Exception:                                          # noqa: BLE001
        can_mutate = True                       # anything else means it tried

    event = MARKET[4:] if MARKET.startswith("cpc-") else MARKET
    reads = [
        get("/v1/orders/open"),
        get("/v1/orders"),
        get("/v1/order/%s" % ORDER_ID),
        get("/v1/orders/%s" % ORDER_ID),
        get("/v1/portfolio/positions"),
        get("/v1/positions"),
        get("/v1/account/balances"),
        get("/v1/mytrades"),
        get("/v1/trades"),
        get("/v1/events/slug/%s" % event),
        get("/v1/markets/%s/bbo" % MARKET),
    ]

    out = {
        "LABEL": "READONLY_RECOVERY",
        "as_of_utc": _utc(),
        "order_id": ORDER_ID,
        "market": MARKET,
        "post_capable": can_mutate,
        "transport": "ReadOnlyTransport",
        "api_key_fingerprint": _fingerprint(),
        "reads": [{k: v for k, v in r.items() if k != "body"} for r in reads],
        "order_matches": find_order(reads),
        "labels": ("GET only. This process holds a transport that raises on "
                   "any method but GET, creates no order, cancels nothing, "
                   "claims no authorization and writes no inventory state."),
    }
    print("PMUS_RECOVERY " + redact(json.dumps(out, default=str)), flush=True)

    # one line per order, so eighteen orders cannot truncate each other
    for oid in ORDER_IDS:
        r = get("/v1/order/%s" % oid)
        row = (r.get("body") or {}).get("order") if r.get("ok") else None
        if isinstance(row, Mapping):
            print("PMUS_ORDER " + redact(json.dumps({
                "id": row.get("id"),
                "marketSlug": row.get("marketSlug"),
                "side": row.get("side"),
                "outcomeSide": row.get("outcomeSide"),
                "intent": row.get("intent"),
                "state": row.get("state"),
                "price": (row.get("price") or {}).get("value"),
                "quantity": row.get("quantity"),
                "cumQuantity": row.get("cumQuantity"),
                "leavesQuantity": row.get("leavesQuantity"),
                "avgPx": (row.get("avgPx") or {}).get("value"),
                "commission": (row.get("commissionNotionalTotalCollected")
                               or {}).get("value"),
                "insertTime": row.get("insertTime"),
                "lastTransactTime": row.get("lastTransactTime"),
            }, default=str)), flush=True)
        else:
            print("PMUS_ORDER " + redact(json.dumps(
                {"id": oid, "unreadable": r.get("error"),
                 "status": r.get("status")}, default=str)), flush=True)
    if ORDER_IDS:
        print("PMUS_ORDERS %d" % len(ORDER_IDS), flush=True)

    # the account-wide answer on ONE line, stamped, so it cannot be confused
    # with an older run. Reservations are named explicitly: a missing field
    # is reported as absent, never as zero.
    bal = next((r for r in reads
                if r["path"] == "/v1/account/balances" and r.get("ok")), None)
    bbody = (bal or {}).get("body")
    if isinstance(bbody, Mapping):
        inner = bbody.get("balances")
        if isinstance(inner, Mapping):
            bbody = inner
    orders_open = next((r for r in reads
                        if r["path"] == "/v1/orders/open"), None)
    positions = next((r for r in reads
                      if r["path"] == "/v1/portfolio/positions"), None)
    _ABSENT = "ABSENT"
    print("PMUS_ACCOUNT_STATE " + redact(json.dumps({
        "as_of_utc": _utc(),
        "open_orders_read": {k: v for k, v in (orders_open or {}).items()
                             if k != "body"},
        "open_orders_body": (orders_open or {}).get("body"),
        "positions_read": {k: v for k, v in (positions or {}).items()
                           if k != "body"},
        "positions_body": (positions or {}).get("body"),
        "balances": bbody if isinstance(bbody, Mapping) else _ABSENT,
        "reservations": {
            k: (bbody.get(k, _ABSENT) if isinstance(bbody, Mapping)
                else _ABSENT)
            for k in ("balanceReservation", "depositReservation",
                      "bonusHold", "marginRequirement", "unsettledFunds",
                      "openOrders")},
        "api_errors": [{k: v for k, v in r.items() if k != "body"}
                       for r in reads if not r.get("ok")],
    }, default=str))[:12000], flush=True)

    venue_meta()

    # the bodies that decide the question, separately so one huge catalogue
    # response cannot push the verdict out of a truncated log line
    for r in reads:
        if r.get("ok") and r["path"] in (
                "/v1/orders/open", "/v1/portfolio/positions",
                "/v1/account/balances", "/v1/mytrades",
                "/v1/order/%s" % ORDER_ID,
                "/v1/markets/%s/bbo" % MARKET):
            print("PMUS_RECOVERY_BODY " + redact(json.dumps(
                {"path": r["path"], "body": r["body"]}, default=str))[:6000],
                flush=True)
    return 0


def _fingerprint() -> Optional[str]:
    import hashlib
    kid = os.getenv(Q1.KEY_ENV, "").strip()
    return hashlib.sha256(kid.encode()).hexdigest()[:12] if kid else None


if __name__ == "__main__":
    sys.exit(main())
