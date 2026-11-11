"""Reconstruct the daily stop INDEPENDENTLY, and conservatively.

WHY A SECOND CALCULATION EXISTS. The executor's `combined_loss` is

    realized_today()  +  sum(min(0, floor(inv)) for inv in held)

and it relies on an invariant stated in its own docstring: "a market that
settles leaves the HELD set and its result enters realized_today in the same
transition. Nothing is in both." The transition is implemented by two functions
in two modules, and they do not agree:

  * pmus_settlement_auditor.append_authoritative_ledger writes a `settled` row
    ALWAYS, and a `realized` row ONLY when the venue confirmed a number;
  * pmus_l5_continuous_session.unsettled_slugs drops a slug from the held set
    on `kind in ("settled","realized")` -- it does not check for a number;
  * pmus_q1_executor.realized_today sums ONLY `kind == "realized"`.

So a settlement the venue would not put a number on removes the market's
guaranteed-loss floor from the budget AND contributes nothing back. The dollars
leave both terms. Nothing is in either.

THE INVARIANT THIS RESTORES. Every submitted/fill-derived dollar stays
represented in exactly one state until a venue-confirmed settlement, redemption
or cashflow closes it. Flat, matched, absent from risk_set, or operationally
complete are NOT terminal states for accounting -- only a venue-confirmed
number is.

CONSERVATIVE MEANS CONSERVATIVE. Where the venue has confirmed a number we use
it. Where it has not, we keep charging the market's guaranteed settlement floor
-- min(up,dn) - spent, negatives only, the executor's own definition, which
reads no price and no mark. We never manufacture a P&L for an unconfirmed
settlement; we simply refuse to stop charging for it.

READ ONLY. This computes and compares. It places no order, cancels nothing, and
changes no limit.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.jsonl")


def _d(x: Any) -> D:
    try:
        return D(str(x))
    except Exception:
        return D("0")


def load(path: str = LEDGER) -> List[dict]:
    return [json.loads(l) for l in open(path) if l.strip()]


def market_state(rows: List[dict]) -> Dict[str, dict]:
    """Latest session inventory and latest settlement verdict per market."""
    out: Dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r.get("_ts", "")):
        slug = r.get("slug")
        if not slug:
            continue
        m = out.setdefault(slug, {"slug": slug, "inv": None, "settle": None,
                                  "last_ts": None, "first_ts": None})
        m["last_ts"] = r.get("_ts")
        if m["first_ts"] is None:
            m["first_ts"] = r.get("_ts")
        if r.get("kind") == "session_end" and r.get("inventory"):
            m["inv"] = r["inventory"]
        elif r.get("kind") == "settlement":
            m["settle"] = r
    return out


def floor_of(inv: Optional[dict]) -> Optional[D]:
    """min(up, dn) - cash paid: what the market settles to in its WORST branch.

    Reads no price, no book, no valuation model -- only contract counts and
    cash actually paid. That is what lets it sit in a risk stop without
    breaking "never infer P&L from a mark"."""
    if not inv:
        return None
    return min(_d(inv.get("up")), _d(inv.get("dn"))) - _d(inv.get("spent"))


def classify(m: dict) -> str:
    s = m.get("settle")
    if not s:
        return "NEVER_SETTLED" if m.get("inv") else "NO_FILLS"
    if s.get("settled") and s.get("realized_confirmed") and \
            s.get("realized_usd") is not None:
        return "CONFIRMED"
    if s.get("settled"):
        return "SETTLED_UNCONFIRMED"
    return "PENDING"


def reconcile(rows: List[dict], day: Optional[str] = None) -> Dict[str, Any]:
    st = market_state(rows)
    confirmed_today = D("0")
    charged: List[dict] = []
    dropped_by_executor: List[dict] = []
    conf_rows: List[dict] = []

    for slug, m in st.items():
        k = classify(m)
        s = m.get("settle") or {}
        fl = floor_of(m.get("inv"))
        if k == "CONFIRMED":
            ts = str(s.get("_ts") or "")
            if day is None or ts.startswith(day):
                confirmed_today += _d(s.get("realized_usd"))
                conf_rows.append({"slug": slug,
                                  "realized_usd": str(_d(s.get("realized_usd"))),
                                  "at": ts})
            continue
        if fl is None:
            continue
        neg = min(D("0"), fl)
        entry = {"slug": slug, "state": k, "floor": str(fl),
                 "charged": str(neg), "last_ts": m.get("last_ts"),
                 "inv": m.get("inv")}
        if neg < 0:
            charged.append(entry)
        # the executor stops charging these the moment a bare `settled` row
        # lands, even with no venue number attached
        if k == "SETTLED_UNCONFIRMED":
            dropped_by_executor.append(entry)

    unsettled_floor = sum((_d(e["charged"]) for e in charged), D("0"))
    conservative = confirmed_today + unsettled_floor
    executor_like = confirmed_today + sum(
        (_d(e["charged"]) for e in charged
         if e["state"] != "SETTLED_UNCONFIRMED"), D("0"))

    return {
        "day": day,
        "confirmed_realized_today": str(confirmed_today),
        "unsettled_floor_total": str(unsettled_floor),
        "conservative_combined_loss": str(conservative),
        "executor_semantics_combined_loss": str(executor_like),
        "difference": str(conservative - executor_like),
        "charged_markets": sorted(charged, key=lambda e: _d(e["charged"])),
        "dropped_by_executor": sorted(dropped_by_executor,
                                      key=lambda e: _d(e["charged"])),
        "confirmed_rows": conf_rows,
    }


# The executor's own last published figure, from its `risk_set` row. Recorded
# here as an OBSERVATION with its timestamp, not re-derived: comparing against a
# model of the executor would only prove the model agrees with itself.
EXECUTOR_OBSERVED = {
    "combined_loss_usd": "-23.2786",
    "daily_realized_pnl_usd": "-22.6851",
    "daily_loss_stop_usd": "-30.00",
    "risk_bearing": ["aec-atp-blaell-hirmor-2026-09-13"],
    "observed_at": "2026-09-15T10:59:42.221981604Z",
}


def main(argv=None) -> int:
    day = (argv or sys.argv[1:] or [None])[0]
    r = reconcile(load(), day)
    ex = _d(EXECUTOR_OBSERVED["combined_loss_usd"])
    cons = _d(r["conservative_combined_loss"])
    stop = _d(EXECUTOR_OBSERVED["daily_loss_stop_usd"])
    print("=" * 76)
    print("EXECUTOR vs CONSERVATIVE  (stop %s, UNCHANGED)" % stop)
    print("=" * 76)
    print("  executor combined_loss            : %14s   @ %s"
          % (ex, EXECUTOR_OBSERVED["observed_at"]))
    print("  conservative reconstruction       : %14s" % cons)
    print("  DIFFERENCE                        : %14s" % (cons - ex))
    print("  executor risk_bearing             : %s"
          % ", ".join(EXECUTOR_OBSERVED["risk_bearing"]))
    miss = [e for e in r["charged_markets"]
            if e["slug"] not in EXECUTOR_OBSERVED["risk_bearing"]]
    print("\n  MARKETS CAUSING THE DIFFERENCE -- charged by the conservative")
    print("  calculation, absent from the executor's risk_bearing, and with NO")
    print("  venue-confirmed terminal state:")
    tot = D("0")
    for e in miss:
        tot += _d(e["charged"])
        print("    %-46s %-14s %10s" % (e["slug"][:46], e["state"], e["charged"]))
    print("    %-46s %-14s %10s" % ("TOTAL", "", tot))
    print("\n  BREACH CHECK (conservative wins):")
    print("    executor     %s  %s -30.00" % (ex, "BREACHED" if ex <= stop else "under"))
    print("    conservative %s  %s -30.00" % (cons, "BREACHED" if cons <= stop else "under"))
    if cons <= stop:
        print("\n    *** CONSERVATIVE CALCULATION HAS BREACHED THE STOP ***")
        print("    No new campaign may start. Existing position management and")
        print("    settlement continue; no position is force-closed.")
    print()
    print("=" * 76)
    print("INDEPENDENT CONSERVATIVE DAILY-STOP RECONSTRUCTION")
    print("=" * 76)
    print(f"  day filter for confirmed realized : {r['day'] or '(all)'}")
    print(f"  confirmed realized                : {r['confirmed_realized_today']:>14}")
    print(f"  unsettled floor total (negatives) : {r['unsettled_floor_total']:>14}")
    print(f"  CONSERVATIVE combined loss        : {r['conservative_combined_loss']:>14}")
    print(f"  executor-semantics combined loss  : {r['executor_semantics_combined_loss']:>14}")
    print(f"  DIFFERENCE (conservative - exec)  : {r['difference']:>14}")
    if r["dropped_by_executor"]:
        print("\n  MARKETS CAUSING THE DIFFERENCE -- settled with no venue number, so")
        print("  the executor stopped charging their floor and no realized row")
        print("  replaced it. Their dollars are in NEITHER term:")
        for e in r["dropped_by_executor"]:
            print("    %-46s floor %10s  charged %10s" %
                  (e["slug"][:46], e["floor"], e["charged"]))
    print("\n  ALL MARKETS STILL CHARGED BY THE CONSERVATIVE CALCULATION:")
    for e in r["charged_markets"]:
        print("    %-46s %-20s charged %10s" %
              (e["slug"][:46], e["state"], e["charged"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
