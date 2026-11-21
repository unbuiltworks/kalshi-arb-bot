"""EXPERIMENT B -- why does L5 switch sides?

Three candidate explanations, tested against each other rather than assumed:
  ECONOMIC : the complement became cheap, so the attainable pair improved
  INVENTORY: the book became imbalanced, so the position demanded the other side
  INTERACTION: neither alone; the complement matters only at some inventory/TTE

THE FEATURE THAT MAKES THIS POSSIBLE is the market-wide tape. From L5's own
fills we only ever see the price of the side he DID take, so a switch and a
continue cannot be compared on complement price. The public tape carries the
complement's price at moments he did not trade it, which is exactly the
counterfactual the question needs.

No feature uses anything after the decision instant. TTE, inventory and prices
are all as-of the moment before the fill.
"""
from __future__ import annotations

import collections
import json
import math
import re
from typing import Any, Dict, List, Optional

L5 = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"


def _last_price_before(tape_by_outcome, outcome: str, t: int) -> Optional[float]:
    """Most recent traded price of `outcome` at or before t, from anyone."""
    arr = tape_by_outcome.get(outcome) or []
    lo, hi, best = 0, len(arr) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid][0] <= t:
            best = arr[mid][1]; lo = mid + 1
        else:
            hi = mid - 1
    return best


def build(recs: List[dict]) -> List[dict]:
    out: List[dict] = []
    for r in recs:
        tape = sorted(r["tape"], key=lambda x: int(x["timestamp"]))
        by = collections.defaultdict(list)
        for x in tape:
            try:
                by[str(x.get("outcome"))].append((int(x["timestamp"]), float(x["price"])))
            except (TypeError, ValueError):
                continue
        l5 = [x for x in tape
              if str(x.get("proxyWallet") or "").lower() == L5
              and str(x.get("side") or "").upper() == "BUY"]
        if len(l5) < 2:
            continue
        m = re.match(r"^(btc|eth)-updown-(\d+)m-(\d+)$", str(r["slug"]))
        dur = int(m.group(2)) * 60 if m else None
        start = int(m.group(3)) if m else None
        up = dn = cup = cdn = 0.0
        prev_t = None
        prev_comp: Dict[str, Optional[float]] = {}
        for x in l5:
            t = int(x["timestamp"]); px = float(x["price"])
            oc = str(x.get("outcome")); q = float(x.get("size") or 0)
            held_up = up > dn
            # the side he is NOT currently long; before any inventory exists
            # there is no complement, and the row is skipped rather than guessed
            if up == dn:
                comp_oc = None
            else:
                comp_oc = "Down" if held_up else "Up"
            if comp_oc is not None and prev_t is not None:
                comp_px = _last_price_before(by, comp_oc, t)
                held_avg = (cup / up) if held_up and up else ((cdn / dn) if dn else None)
                prior = prev_comp.get(comp_oc)
                tot = up + dn
                out.append({
                    "slug": r["slug"], "cohort": r["cohort"], "t": t,
                    "is_switch": 1 if oc == comp_oc else 0,
                    "complement_price": comp_px,
                    "d_complement": (None if (comp_px is None or prior is None)
                                     else round(comp_px - prior, 6)),
                    "pair_cost": (None if (comp_px is None or held_avg is None)
                                  else round(held_avg + comp_px, 6)),
                    "abs_residual": abs(up - dn),
                    "rel_imbalance": (abs(up - dn) / tot) if tot else None,
                    "tte_s": ((start + dur - t) if (start and dur) else None),
                    "since_prior_fill_s": t - prev_t,
                })
                if comp_px is not None:
                    prev_comp[comp_oc] = comp_px
            if oc == "Up":
                up += q; cup += px * q
            else:
                dn += q; cdn += px * q
            prev_t = t
    return out


def _auc(pairs) -> Optional[float]:
    pos = [p for p, y in pairs if y == 1]
    neg = [p for p, y in pairs if y == 0]
    if not pos or not neg:
        return None
    wins = sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pos for b in neg)
    return round(wins / (len(pos) * len(neg)), 4)
