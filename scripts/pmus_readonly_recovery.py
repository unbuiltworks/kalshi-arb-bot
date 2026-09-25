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


def get(path: str, base: str = Q1.API) -> Dict[str, Any]:
    """One signed GET, and whatever came back, never raising."""
    http = Q1.ReadOnlyTransport()
    try:
        body = http.request("GET", path, None, base)
        return {"path": path, "ok": True, "status": 200,
                "shape": shape_of(body), "body": body}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:                                      # noqa: BLE001
            pass
        return {"path": path, "ok": False, "status": exc.code,
                "error": "HTTPError", "detail": redact(detail)}
    except Exception as exc:                                   # noqa: BLE001
        return {"path": path, "ok": False, "status": None,
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
