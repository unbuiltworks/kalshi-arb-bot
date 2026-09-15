"""Build the P2 economic ledger from the shadow's own stores.

intent -> submitted-shadow quote -> EVIDENCED fill -> inventory ->
settlement -> realized-shadow economics.

APPEND-ONLY, and it re-derives rather than accumulates: every run reads the
full maker-outcome history and rewrites its conclusions, so a settlement that
arrives later cannot leave a stale OPEN row behind it. The ledger file keeps
every version with the time it was computed, so the record of what we believed
when is preserved alongside what we believe now.

THE FILTER IS THE WHOLE THING. Of everything in `maker_outcomes.jsonl`, only
rows with `evidenced_fill is True` become positions. `None` is carried to the
report as UNKNOWN with its reason -- a quote resting across a restart, prints
that could not be read -- and never counted as either outcome. That census is
printed beside the economics, because "3 settled positions" means something
different when 200 quotes were undecided than when 4 were.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from src.l5_shadow import ledger as LG

LEDGER = "economics.jsonl"
MAKER_OUT = "maker_outcomes.jsonl"
ROUTES = "routes.jsonl"


def _load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def fills_from(outcomes: Sequence[dict], routes: Sequence[dict],
               *, clock: str = "feed") -> List[dict]:
    """Evidenced fills only, priced at the quote and costed with venue fees."""
    cohort = {r.get("intent_id"): r.get("cohort") for r in routes}
    out = []
    for o in outcomes:
        if o.get("evidenced_fill") is not True:
            continue
        qty = D(str(o.get("quote_qty") or "0"))
        px = D(str(o.get("quote_price") or "0"))
        if qty <= 0 or not (D("0") < px < D("1")):
            continue
        out.append({
            "venue": o["venue"], "market_key": o.get("market_key"),
            "cohort": cohort.get(o.get("intent_id")),
            "outcome": o.get("desired_outcome"), "qty": str(qty),
            "price": str(px), "fee": str(maker_fee(o["venue"], qty, px)),
            "intent_id": o.get("intent_id"), "t": o.get("placed_at"),
            "clock": clock,
        })
    return out


def maker_fee(venue: str, qty: D, price: D) -> D:
    """What the VENUE charges a maker. Not a model, and not zero by default.

    Polymarket declares `takerOnly: true`, so a maker pays nothing there.
    Gemini charges makers a ceil-cent fee; the September waiver is deliberately
    NOT applied, because assuming a waiver we have not been granted would
    flatter every Gemini figure in this ledger.

    THE ARGUMENT ORDER IS (price, quantity), and it was wrong here first.
    The vendored formula is 0.0175 * C * P * (1-P); swapped it evaluates
    0.0175 * P * C * (1-C). At any realistic size the vendored guard rejects
    it outright, because a quantity above 1 is not a valid price -- so the
    mistake is loud rather than silent, and below one share the ceil-cent
    floor makes both answers a penny anyway. The sign check below is defence
    in depth for a venue whose formula has no such guard, not a fix for this
    one.
    """
    if venue == "PMUS":
        return D("0")
    from src.l5_shadow import terms as T
    t = T.gemini_terms()
    fn = getattr(t, "maker_fee", None)
    if fn is None:
        raise ValueError("GEMINI maker fee is undeclared; refusing to assume 0")
    fee = D(str(fn(price, qty)))          # (price, quantity)
    if fee < 0:
        raise ValueError(f"negative maker fee {fee} for qty={qty} px={price}: "
                         "the fee function was called wrongly")
    return fee


def census(outcomes: Sequence[dict]) -> Dict[str, Any]:
    """How the quotes were decided. Printed beside the economics so a small
    number of positions is never read as a small number of chances."""
    c: Counter = Counter()
    reasons: Counter = Counter()
    for o in outcomes:
        v = o.get("evidenced_fill")
        k = "FILLED" if v is True else ("NO_FILL" if v is False else "UNKNOWN")
        c[f"{o.get('venue')}:{k}"] += 1
        if v is None:
            reasons[str(o.get("reason"))[:70]] += 1
    return {"by_venue_verdict": dict(sorted(c.items())),
            "unknown_reasons": dict(reasons.most_common(5)),
            "labels": ("UNKNOWN is not a no-fill. It is a quote whose verdict "
                       "we could not read -- across a restart, or with prints "
                       "we could not fetch -- and counting it either way would "
                       "manufacture evidence")}


# one settlement lookup per distinct market, bounded so a periodic report can
# never turn into an unbounded burst of requests as history accumulates
MAX_RESOLUTIONS = 60


def build(out_dir: str, *, resolver=None, clock: str = "feed",
          now: Optional[float] = None,
          max_resolutions: int = MAX_RESOLUTIONS) -> Dict[str, Any]:
    t = time.time() if now is None else now
    outs = _load(os.path.join(out_dir, MAKER_OUT))
    routes = _load(os.path.join(out_dir, ROUTES))
    fills = fills_from(outs, routes, clock=clock)
    pos = LG.positions(fills)
    if resolver is None:
        from src.l5_shadow import venues as V
        resolver = V.pm_settlement
    seen: Dict[str, Any] = {}
    rows, unlooked = [], 0
    # newest first: a market that just settled is the one whose result is new
    order = sorted(pos.items(), key=lambda kv: -(kv[1].get("last_t") or 0))
    for key, p in order:
        mk = p["market_key"]
        if mk not in seen:
            if len(seen) >= max_resolutions:
                # NOT resolved-as-open: the lookup was not made, and the row
                # says so rather than implying the venue had not settled it
                unlooked += 1
                rows.append({**LG.settle(p, None), "computed_at": t,
                             "resolution": "NOT_LOOKED_UP (report bound)"})
                continue
            try:
                seen[mk] = resolver(mk)
            except Exception:                                 # noqa: BLE001
                seen[mk] = None
        rows.append({**LG.settle(p, seen[mk]), "computed_at": t})
    return {
        "computed_at": t, "out_dir": out_dir, "clock": clock,
        "maker_quotes_decided": len(outs),
        "evidenced_fills": len(fills),
        "positions": len(rows),
        "markets_not_looked_up": unlooked,
        # a fill rate this low is itself the explanation for the pair rate: two
        # legs in ONE market is the square of a small number
        "evidenced_fill_rate": (round(len(fills) / len(outs), 4)
                                if outs else None),
        "one_sided_positions": sum(1 for r in rows
                                   if D(r.get("matched_qty") or "0") <= 0),
        "paired_positions": sum(1 for r in rows
                                if D(r.get("matched_qty") or "0") > 0),
        "census": census(outs),
        "by_venue": LG.roll_up(rows, by="venue"),
        "by_cohort": LG.roll_up(rows, by="cohort"),
        "by_clock": LG.roll_up(rows, by="clock"),
        "rows": rows,
        "labels": ("realized_shadow is a SETTLED COUNTERFACTUAL: evidenced by "
                   "public prints, settled by the venue's own resolution, and "
                   "never submitted. It is not realized profit. Rebates and "
                   "rewards are 0 because a shadow account is credited "
                   "nothing -- not because they were estimated at 0."),
    }


def write(out_dir: str, rep: Dict[str, Any]) -> str:
    p = os.path.join(out_dir, LEDGER)
    with open(p, "a") as fh:
        fh.write(json.dumps(rep, default=str) + "\n")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-research/l5-shadow-state")
    ap.add_argument("--clock", default="feed")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    rep = build(a.out_dir, clock=a.clock)
    if not a.no_write:
        write(a.out_dir, rep)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
