"""P13 -- how L5 actually acquires the COMPLEMENT, from canonical fills only.

WHAT THIS TESTS AND WHAT IT REFUSES TO TEST. An external analysis (Grok,
2026-09-23 sample) proposed that L5 accumulates one side first and then
acquires the opposite side dynamically, tolerating a temporary
inventory-weighted pair cost above $1.00. That is a HYPOTHESIS SOURCE, not
ground truth, so every claim below is recomputed against our own canonical
history.

ONLY FILLS ARE OBSERVABLE. Resting orders, cancels, reprices and maker/taker
role are NOT in this feed, and nothing here infers them. A statement about
"waiting" or "aggression" would be an inference about orders from prints, and
this module does not make one.

THE UNIT IS A WINDOW. One eventSlug is one contract. `side` is the OUTCOME
bought (Up/Down); L5 is a buyer of both legs -- 75,862 BUY versus 2 SELL in
this snapshot -- so inventory only ever grows and the pair cost is a
size-weighted average of what each leg cost.
"""
from __future__ import annotations

import collections
import gzip
import json
import os
import statistics as stats
from typing import Any, Dict, Iterator, List

RAW = "data/l5/raw/activity.jsonl"
GAP = "data/l5/raw/activity_gap.jsonl.gz"
FAMILIES = ("btc-updown-5m", "btc-updown-15m")


def _fills(path: str) -> Iterator[Dict[str, Any]]:
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for f in d.get("payload") or []:
                yield f


def load_windows(paths=(RAW, GAP)) -> Dict[str, List[Dict[str, Any]]]:
    """Every BTC up/down window -> its BUY TRADE fills, time-ordered.

    DEDUPLICATED ON THE TRANSACTION, because the raw pages overlap: the
    gap-fill fetch re-requested ranges the first pass already had, and
    counting a fill twice would inflate both inventory and same-side depth.
    """
    seen = set()
    wins: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for p in paths:
        if not os.path.exists(p):
            continue
        for f in _fills(p):
            if f.get("type") != "TRADE" or f.get("side") != "BUY":
                continue
            slug = f.get("eventSlug") or ""
            if not slug.rsplit("-", 1)[0] in FAMILIES:
                continue
            key = (f.get("transactionHash"), f.get("asset"), f.get("timestamp"),
                   f.get("size"), f.get("price"))
            if key in seen:
                continue
            seen.add(key)
            wins[slug].append(f)
    for slug in wins:
        wins[slug].sort(key=lambda r: (r.get("timestamp") or 0))
    return dict(wins)


def _vwap(qty: float, spend: float):
    return None if qty <= 0 else spend / qty


def describe_window(fills: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The complement-acquisition shape of ONE window."""
    if not fills:
        return {}
    s0 = fills[0].get("outcome")
    t0 = fills[0].get("timestamp") or 0
    qty = collections.defaultdict(float)
    spend = collections.defaultdict(float)

    same_before = 0
    first_opp_i = None
    for i, f in enumerate(fills):
        if f.get("outcome") != s0:
            first_opp_i = i
            break
        same_before += 1

    out: Dict[str, Any] = {
        "slug": fills[0].get("eventSlug"),
        "family": (fills[0].get("eventSlug") or "").rsplit("-", 1)[0],
        "n_fills": len(fills),
        "first_side": s0,
        "same_side_fills_before_first_opposite": same_before,
        "ever_two_sided": first_opp_i is not None,
    }

    # inventory up to (and including) the first opposite fill
    for i, f in enumerate(fills):
        o = f.get("outcome")
        q = float(f.get("size") or 0)
        px = float(f.get("price") or 0)
        if first_opp_i is not None and i == first_opp_i:
            out["imbalance_before_first_opposite"] = qty[s0] - qty[
                "Up" if s0 == "Down" else "Down"]
            out["first_fill_price"] = float(fills[0].get("price") or 0)
            out["first_opposite_price"] = px
            out["latency_s_to_first_opposite"] = (f.get("timestamp") or 0) - t0
        qty[o] += q
        spend[o] += q * px
        if first_opp_i is not None and i == first_opp_i:
            u, d = _vwap(qty["Up"], spend["Up"]), _vwap(qty["Down"],
                                                        spend["Down"])
            out["pair_cost_after_first_opposite"] = (
                None if u is None or d is None else u + d)

    # the rest of the window: how the average pair cost moved, and whether
    # the net inventory ever crossed back through zero
    pair_series: List[float] = []
    net_signs = set()
    qty2 = collections.defaultdict(float)
    spend2 = collections.defaultdict(float)
    other = "Up" if s0 == "Down" else "Down"
    for f in fills:
        o = f.get("outcome")
        q = float(f.get("size") or 0)
        qty2[o] += q
        spend2[o] += q * float(f.get("price") or 0)
        u, d = _vwap(qty2["Up"], spend2["Up"]), _vwap(qty2["Down"],
                                                      spend2["Down"])
        if u is not None and d is not None:
            pair_series.append(u + d)
        net = qty2[s0] - qty2[other]
        net_signs.add(0 if abs(net) < 1e-9 else (1 if net > 0 else -1))

    if pair_series:
        out["pair_cost_min_after"] = min(pair_series)
        out["pair_cost_final"] = pair_series[-1]
        out["pair_cost_max_after"] = max(pair_series)
    out["inventory_crossed_zero"] = bool({1, -1} <= net_signs)
    out["final_qty_first_side"] = qty2[s0]
    out["final_qty_other"] = qty2[other]
    return out


def _q(vals, ps=(0.1, 0.25, 0.5, 0.75, 0.9)):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return {}
    out = {"n": len(v), "mean": round(sum(v) / len(v), 4),
           "min": round(v[0], 4), "max": round(v[-1], 4)}
    for p in ps:
        i = min(len(v) - 1, int(round(p * (len(v) - 1))))
        out["p%d" % int(p * 100)] = round(v[i], 4)
    return out


def report(windows: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    rows = [describe_window(f) for f in windows.values()]
    rows = [r for r in rows if r]
    out: Dict[str, Any] = {"label": "P13_L5_COMPLEMENT_V1",
                           "n_windows": len(rows),
                           "source": [RAW, GAP],
                           "by_family": {}}
    for fam in FAMILIES:
        fr = [r for r in rows if r["family"] == fam]
        two = [r for r in fr if r["ever_two_sided"]]
        pc = [r.get("pair_cost_after_first_opposite") for r in two]
        pc = [x for x in pc if x is not None]
        cell = {
            "n_windows": len(fr),
            "n_ever_two_sided": len(two),
            "frac_ever_two_sided": (round(len(two) / len(fr), 4)
                                    if fr else None),
            "same_side_fills_before_first_opposite": _q(
                [r["same_side_fills_before_first_opposite"] for r in two]),
            "latency_s_to_first_opposite": _q(
                [r.get("latency_s_to_first_opposite") for r in two]),
            "imbalance_before_first_opposite": _q(
                [r.get("imbalance_before_first_opposite") for r in two]),
            "pair_cost_after_first_opposite": _q(pc),
            "frac_pair_cost_after_first_opposite_gt": {
                "1.00": round(sum(1 for x in pc if x > 1.00) / len(pc), 4)
                        if pc else None,
                "1.05": round(sum(1 for x in pc if x > 1.05) / len(pc), 4)
                        if pc else None,
                "1.10": round(sum(1 for x in pc if x > 1.10) / len(pc), 4)
                        if pc else None},
            "pair_cost_min_after": _q([r.get("pair_cost_min_after")
                                       for r in two]),
            "pair_cost_final": _q([r.get("pair_cost_final") for r in two]),
            "frac_inventory_crossed_zero": (
                round(sum(1 for r in two if r["inventory_crossed_zero"])
                      / len(two), 4) if two else None),
            "n_fills_per_window": _q([r["n_fills"] for r in fr]),
        }
        out["by_family"][fam] = cell
    out["labels"] = (
        "FILLS ONLY. Resting orders, cancels, reprices and maker/taker role "
        "are not in this feed and are not inferred. `pair_cost` is the "
        "size-weighted average price of the Up leg plus that of the Down "
        "leg, i.e. what a matched pair cost on average -- not a P&L.")
    return out


def main() -> int:
    wins = load_windows()
    rep = report(wins)
    dest = "analysis/gemini/audit/p13_l5_complement.json"
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    print(json.dumps(rep, indent=2, default=str)[:4000])
    print("\nwrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
