"""E1 FINANCIAL RECONCILIATION. Read-only, append-only sources only.

A position resolution in `cpc-btc-updown-15m-2026-10-08-1215z` proves we
HELD a position there, so at least one 1215z first leg filled. My earlier
"zero E1 fills" was scoped to the 1230z twenty -- the only orders I had
audited -- and the ledger's forty placements across two windows only
surfaced during the requirement-7 verification. This establishes the money.

THE SEVEN ITEMS, IN ORDER:

  1. every E1 order id, from the append-only ledgers on the mounted volume
  2. GET /v1/order/{id} for each: final state, filled qty, price, cumQty
  3. every matching /v1/portfolio/activities row, trades and resolutions
  4. balance reconciliation: start, debits, credits, fees, rebates,
     settlement, end
  5. confirmed realized net P&L, or the missing cashflow named explicitly
  6. whether any complement order was submitted or filled
  7. the 1230z set, and whether the cohort has any outstanding exposure

ACCOUNTING IS CONSERVATIVE AND FAILS CLOSED. A cashflow this script cannot
source is reported as MISSING, never netted to zero and never inferred from
a balance delta. Per CLAUDE.md, `REALIZED` requires a venue settlement or
cash statement; a balance change without sufficient source identity is not
realized profit and is labelled UNATTRIBUTED.

This process runs on the E1 service because that is where the volume is
mounted. It reads the ledgers, performs GETs, and writes nothing: no
ledger append, no latch, no window, no arming key, no order body.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src import pmus_auth as A

STATE_DIR = os.environ.get("E1_STATE_DIR", "/state/pmus-ws/e1_cohort")
LEDGERS = ("attempts.jsonl", "events.jsonl", "venue_money.jsonl",
           "settlement.jsonl", "progress.jsonl")
TAPE_DIRS = ("tape",)
SLUG_1215 = "cpc-btc-updown-15m-2026-10-08-1215z"
SLUG_1230 = "cpc-btc-updown-15m-2026-10-08-1230z"
SLUG_0100 = "cpc-btc-updown-15m-2026-10-08-0100z"
COHORT_SLUGS = (SLUG_0100, SLUG_1215, SLUG_1230)
REDACT = ("accountId", "account_id", "walletAddress", "address", "userId",
          "subaccountId", "ownerId")
ID_KEYS = ("orderId", "order_id", "venue_order_id", "id",
           "e1_pending_order_id")


def scrub(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: ("REDACTED" if k in REDACT else scrub(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o


def get(t, path):
    try:
        return {"ok": True, "body": t.request("GET", path)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "err": type(exc).__name__,
                "code": getattr(exc, "code", None)}


def jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:                                  # noqa: BLE001
                rows.append({"UNPARSABLE_LINE": ln[:200]})
                continue
            if isinstance(o, dict):
                rows.append(o)
    return rows


def harvest_ids(obj: Any, out: Dict[str, str], where: str) -> None:
    """Every plausible venue order id anywhere in a ledger row. Greedy on
    purpose: a missed id is an unaudited order."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ID_KEYS and isinstance(v, str) and v:
                out.setdefault(v, where)
            else:
                harvest_ids(v, out, where)
    elif isinstance(obj, list):
        for x in obj:
            harvest_ids(x, out, where)


def px(o: Any) -> Optional[str]:
    if isinstance(o, dict):
        return None if o.get("value") is None else str(o.get("value"))
    return None if o is None else str(o)


def dec(x: Any) -> Optional[D]:
    try:
        d = D(str(x))
    except Exception:                                          # noqa: BLE001
        return None
    return d if d.is_finite() else None


def main() -> int:
    t = A.ReadOnlyTransport()
    print("FINRECON_BEGIN now=%s state_dir=%s"
          % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), STATE_DIR))
    print("LABELS strict: submitted -> filled -> settled -> REALIZED; a "
          "balance change without source identity is UNATTRIBUTED")

    # ---------------------------------------------------------------- (1)
    try:
        listing = sorted(os.listdir(STATE_DIR))
    except Exception as exc:                                   # noqa: BLE001
        print("LEDGER_DIR_ERR %s" % type(exc).__name__)
        listing = []
    print("LEDGER_DIR %s" % json.dumps(listing[:60]))

    ids: Dict[str, str] = {}
    for name in LEDGERS:
        p = os.path.join(STATE_DIR, name)
        rows = jsonl(p)
        print("LEDGER %s rows=%d" % (name, len(rows)))
        for r in rows:
            harvest_ids(r, ids, name)
            s = json.dumps(scrub(r), default=str)
            if any(g in s for g in COHORT_SLUGS):
                print("LROW %s %s" % (name, s[:700]))
    for sub in TAPE_DIRS:
        d = os.path.join(STATE_DIR, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            rows = jsonl(os.path.join(d, name))
            print("TAPE %s/%s rows=%d" % (sub, name, len(rows)))
            for r in rows:
                harvest_ids(r, ids, "%s/%s" % (sub, name))
                s = json.dumps(scrub(r), default=str)
                if any(g in s for g in COHORT_SLUGS):
                    print("TROW %s/%s %s" % (sub, name, s[:700]))

    candidates = sorted(i for i in ids
                        if not i.startswith("LOCAL_INTENT:"))
    local_only = sorted(i for i in ids if i.startswith("LOCAL_INTENT:"))
    print("IDS_FROM_LEDGER %s" % json.dumps({
        "n_venue_ids": len(candidates), "n_local_intents": len(local_only),
        "venue_ids": candidates[:80],
        "sources": {i: ids[i] for i in candidates[:80]},
    }))

    # ---------------------------------------------------------------- (2)
    per_order: Dict[str, Dict[str, Any]] = {}
    for oid in candidates:
        r = get(t, "/v1/order/%s" % oid)
        if not r["ok"]:
            per_order[oid] = {"unreadable": r}
            print("ORDER %s UNREADABLE %s" % (oid, json.dumps(r)))
            continue
        o = (r["body"].get("order") if isinstance(r["body"], dict)
             else None) or r["body"]
        rec = {"state": o.get("state"), "slug": o.get("marketSlug"),
               "outcome_side": o.get("outcomeSide"),
               "qty": o.get("quantity"), "cum": o.get("cumQuantity"),
               "leaves": o.get("leavesQuantity"),
               "wire_price": px(o.get("price")),
               "avg_px": px(o.get("avgPx")),
               "create": o.get("createTime"), "update": o.get("updateTime")}
        per_order[oid] = rec
        print("ORDER %s %s" % (oid, json.dumps(rec, default=str)))

    by_slug: Dict[str, Dict[str, Any]] = {}
    for oid, rec in per_order.items():
        s = str(rec.get("slug"))
        b = by_slug.setdefault(s, {"n": 0, "cum": D("0"), "states": {},
                                   "ids": []})
        b["n"] += 1
        b["ids"].append(oid)
        c = dec(rec.get("cum"))
        if c is not None:
            b["cum"] += c
        st = str(rec.get("state"))
        b["states"][st] = b["states"].get(st, 0) + 1
    for s, b in sorted(by_slug.items()):
        print("BY_SLUG %s" % json.dumps({
            "slug": s, "orders": b["n"], "sum_cumQuantity": str(b["cum"]),
            "states": b["states"], "ids": b["ids"][:30]}, default=str))

    # ---------------------------------------------------------------- (3)
    cursor = ""
    acts: List[Dict[str, Any]] = []
    kinds: Dict[str, int] = {}
    for _ in range(20):
        path = "/v1/portfolio/activities?limit=50"
        if cursor:
            path += "&cursor=%s" % cursor
        r = get(t, path)
        if not r["ok"]:
            print("ACT_PAGE_ERR %s" % json.dumps(r))
            break
        body = r["body"] or {}
        rows = body.get("activities") or []
        for a in rows:
            kinds[str(a.get("type"))] = kinds.get(str(a.get("type")), 0) + 1
            s = json.dumps(scrub(a), default=str)
            if any(g in s for g in COHORT_SLUGS):
                acts.append(a)
                print("ACT %s" % s[:900])
        cursor = body.get("nextCursor") or ""
        if body.get("eof") or not cursor:
            break
    print("ACT_SUMMARY %s" % json.dumps({
        "cohort_rows": len(acts), "kinds_seen": kinds}, default=str))

    # ---------------------------------------------------------------- (4)
    debits = D("0")
    credits = D("0")
    fees = D("0")
    rebates = D("0")
    settlements = D("0")
    unattributed: List[Dict[str, Any]] = []
    for a in acts:
        s = json.dumps(a, default=str)
        got = False
        # every numeric field is reported rather than guessed at: this
        # prints the row's own money fields so the arithmetic is auditable
        # instead of asserted.
        money = {k: v for k, v in _flat(a).items()
                 if any(w in k.lower() for w in
                        ("amount", "cost", "proceed", "fee", "rebate",
                         "credit", "debit", "cash", "payout", "value",
                         "pnl", "notional"))}
        print("ACT_MONEY %s" % json.dumps({
            "type": a.get("type"), "money_fields": money}, default=str))
        for k, v in money.items():
            d = dec(v)
            if d is None:
                continue
            kl = k.lower()
            if "fee" in kl:
                fees += d
                got = True
            elif "rebate" in kl:
                rebates += d
                got = True
            elif "payout" in kl or "proceed" in kl or "resolution" in kl:
                settlements += d
                got = True
            elif "cost" in kl or "debit" in kl:
                debits += d
                got = True
            elif "credit" in kl:
                credits += d
                got = True
        if not got:
            unattributed.append({"type": a.get("type"),
                                 "row": json.dumps(a, default=str)[:300]})

    bal = get(t, "/v1/account/balances")
    print("BALANCES_NOW %s" % json.dumps(
        scrub(bal.get("body")) if bal["ok"] else bal, default=str)[:700])

    print("CASHFLOW %s" % json.dumps({
        "debits_sourced": str(debits), "credits_sourced": str(credits),
        "fees_sourced": str(fees), "rebates_sourced": str(rebates),
        "settlement_sourced": str(settlements),
        "rows_with_no_sourceable_money": len(unattributed),
        "unattributed_rows": unattributed[:10],
        "RULE": "a cashflow this script cannot source is MISSING, never "
                "netted to zero and never inferred from a balance delta",
    }, default=str))

    # ---------------------------------------------------------------- (5)
    settle_rows = jsonl(os.path.join(STATE_DIR, "settlement.jsonl"))
    realized_rows = [r for r in settle_rows
                     if str(r.get("label")) == "REALIZED"]
    print("REALIZED %s" % json.dumps({
        "settlement_ledger_rows": len(settle_rows),
        "rows_labelled_REALIZED": len(realized_rows),
        "confirmed_realized_net_pnl_usd":
            None if not realized_rows else "see rows",
        "rows": [json.dumps(scrub(r), default=str)[:300]
                 for r in realized_rows[:10]],
        "IF_ZERO": "no venue settlement or cash statement has been recorded "
                   "for this cohort, so no amount may be called REALIZED",
    }, default=str))

    # ---------------------------------------------------------------- (6)
    comp_markers = ("complement", "COMPLEMENT", "C1", "tranche_plan")
    comp_hits = []
    for name in ("attempts.jsonl", "events.jsonl"):
        for r in jsonl(os.path.join(STATE_DIR, name)):
            s = json.dumps(r, default=str)
            if any(m in s for m in comp_markers):
                comp_hits.append({"ledger": name, "row": s[:400]})
    print("COMPLEMENT %s" % json.dumps({
        "ledger_rows_mentioning_a_complement": len(comp_hits),
        "rows": comp_hits[:10],
        "reading": "a submitted complement would appear as its own venue "
                   "order id above; none in by_slug means none submitted",
    }, default=str))

    # ---------------------------------------------------------------- (7)
    oo = get(t, "/v1/orders/open")
    pos = get(t, "/v1/portfolio/positions")
    print("EXPOSURE_NOW %s" % json.dumps({
        "open_orders": (scrub(oo["body"]) if oo["ok"] else oo),
        "positions": (scrub(pos["body"]) if pos["ok"] else pos),
    }, default=str)[:800])
    print("FINRECON_END")
    return 0


def _flat(o: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(_flat(v, "%s.%s" % (prefix, k) if prefix else str(k)))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(_flat(v, "%s[%d]" % (prefix, i)))
    else:
        out[prefix] = o
    return out


if __name__ == "__main__":
    raise SystemExit(main())
