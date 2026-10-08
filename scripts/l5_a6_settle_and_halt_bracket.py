"""READ-ONLY: close out A6's fill, and bracket when the 15m halt began.

TWO QUESTIONS, ONE DEPLOYMENT, NO WRITES.

(1) A6 IS THE ONLY FILL WE HAVE EVER HAD and its own record leaves the ledger
    open: "The open item is settlement and cash confirmation, plus whether a
    maker rebate is credited against this fill. Until both, nothing here is
    realized." Order `CYKE4XSQ0YGW`, 0.01 DOWN at own-side 0.6700 on
    `cpc-btc-updown-15m-2026-10-07-0200z`, ORDER_STATE_FILLED 02:00:15.69Z.
    This asks the venue for the settlement cashflow and for any credited
    rebate, and reports the venue's OWN fields.

    It deliberately does NOT use `src/pmus_settlement_auditor.py`, which would
    be the natural reuse: that module imports `pmus_q1_executor_v4`, and
    pulling a second order-capable executor into the E1 image would widen a
    surface the E1 Dockerfile bounds on purpose. A read-only GET of the same
    routes costs less than that.

(2) WHEN DID 15m STOP TRADING? 15m traded at 01:00-02:00Z on 2026-10-08 (the
    cohort authorized `...-0100z`, market_id 1274078) and was wholly halted by
    10:45Z. Walking every 15-minute window of the day and recording status,
    closed/active and whether `/bbo` answers brackets the transition to within
    one window -- which is what decides whether to WAIT for a recurring halt
    or to treat the product as retired.

IT CANNOT PLACE AN ORDER OR WRITE ANYTHING. The only transport is
`pmus_auth.ReadOnlyTransport`, which raises on any method but GET. It writes
no ledger, touches no latch, consumes no window and reads no arming key.
`POST /v1/prediction-markets/positions/settled` would answer (1) more directly
and is deliberately NOT called: this process cannot POST.

Secrets never leave: credentials are read by the auth module, never printed.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from src import pmus_auth as A

A6_SLUG = "cpc-btc-updown-15m-2026-10-07-0200z"
A6_ORDER = "CYKE4XSQ0YGW"
DAY = "2026-10-08"
REDACT = ("accountId", "account_id", "walletAddress", "address", "userId")


def get(t, path, base=A.API):
    try:
        return {"ok": True, "body": t.request("GET", path, base=base)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "err": "%s:%s" % (
            type(exc).__name__, getattr(exc, "code", "") or str(exc)[:50])}


def scrub(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: ("REDACTED" if k in REDACT else scrub(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o


def rows_of(body: Any, *keys: str) -> List[Dict[str, Any]]:
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


def mentions(row: Dict[str, Any], needle: str) -> bool:
    return needle.lower() in json.dumps(row, default=str).lower()


def main() -> int:
    t = A.ReadOnlyTransport()
    out: Dict[str, Any] = {
        "probe": "A6_SETTLEMENT_AND_HALT_BRACKET",
        "READ_ONLY": True, "cannot_place_an_order": True,
        "writes_nothing": True,
        "now_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # ---------------------------------------------------------------- (1)
    a6: Dict[str, Any] = {"slug": A6_SLUG, "order_id": A6_ORDER}
    # settlement price lives on /bbo per l5_pmus_transport's own note
    r = get(t, "/v1/markets/%s/bbo" % A6_SLUG, A.GATEWAY)
    md = (r.get("body") or {}).get("marketData") if r["ok"] else None
    a6["bbo_settlement"] = (
        {"settlementPx": (md or {}).get("settlementPx"),
         "state": (md or {}).get("state"),
         "status": (md or {}).get("status")} if md else r)

    # our own fills, with the venue's fee field
    r = get(t, "/v1/mytrades?limit=200")
    if r["ok"]:
        rws = rows_of(r["body"], "trades", "myTrades", "data")
        hits = [scrub(x) for x in rws
                if mentions(x, A6_SLUG) or mentions(x, A6_ORDER)]
        a6["mytrades"] = {"n_rows": len(rws), "a6_rows": hits[:4]}
    else:
        a6["mytrades"] = r

    # settlement cashflow: POSITION_RESOLUTION in the activity walk
    acts: List[Dict[str, Any]] = []
    page = get(t, "/v1/portfolio/activities?limit=200")
    if page["ok"]:
        acts = rows_of(page["body"], "activities", "data", "rows")
        a6["activities"] = {
            "n_rows": len(acts),
            "kinds": sorted({str(x.get("type") or x.get("activityType")
                                 or x.get("kind")) for x in acts})[:12],
            "a6_rows": [scrub(x) for x in acts
                        if mentions(x, A6_SLUG)][:6],
            "position_resolutions": [
                scrub(x) for x in acts
                if "POSITION_RESOLUTION" in json.dumps(x, default=str)][:4],
        }
    else:
        a6["activities"] = page

    # any credited rebate / reward
    for path, label in (("/v1/incentives/earnings", "earnings"),
                        ("/v1/incentives", "incentives")):
        r = get(t, path)
        if r["ok"]:
            rws = rows_of(r["body"], "earnings", "incentives", "data")
            a6[label] = {"n_rows": len(rws),
                         "a6_rows": [scrub(x) for x in rws
                                     if mentions(x, A6_SLUG)][:4],
                         "sample": [scrub(x) for x in rws[:2]]}
        else:
            a6[label] = r
    a6["LABEL"] = ("venue fields only. Nothing here is called realized "
                   "unless the venue states a settled cashflow.")
    out["a6"] = a6

    # ---------------------------------------------------------------- (2)
    bracket: List[Dict[str, Any]] = []
    for h in range(0, 13):
        for m in (0, 15, 30, 45):
            slug = "cpc-btc-updown-15m-%s-%02d%02dz" % (DAY, h, m)
            ev = get(t, "/v1/events/slug/%s" % slug[len("cpc-"):], A.GATEWAY)
            row: Dict[str, Any] = {"w": "%02d%02dz" % (h, m)}
            if not ev["ok"]:
                row["event"] = ev["err"]
            else:
                body = ev["body"] or {}
                e = body.get("event") or body
                mk = None
                for cand in (e.get("markets") or []):
                    if str(cand.get("slug")) == slug:
                        mk = cand
                        break
                if mk is None:
                    row["event"] = "SLUG_NOT_IN_EVENT"
                else:
                    row["status"] = mk.get("status")
                    row["closed"] = mk.get("closed")
                    row["active"] = mk.get("active")
                    row["id"] = mk.get("id")
            b = get(t, "/v1/markets/%s/bbo" % slug, A.GATEWAY)
            if b["ok"]:
                bmd = (b["body"] or {}).get("marketData") or {}
                row["bbo"] = "OK"
                row["settlementPx"] = bmd.get("settlementPx")
                row["bbo_state"] = bmd.get("state")
            else:
                row["bbo"] = b["err"]
            bracket.append(row)
    out["halt_bracket"] = bracket
    out["halt_bracket_note"] = (
        "a window that TRADED and settled should carry a settlementPx and/or "
        "a distinct state; one that never opened should not. The boundary "
        "between those two shapes is when 15m stopped trading.")

    print("A6HB_BEGIN")
    print(json.dumps(out, default=str))
    print("A6HB_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
