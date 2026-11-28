#!/usr/bin/env python3
"""Reconcile venue-confirmed BTC15m settlements, split by basis quality.

Research/accounting only. Reads a committed harvest of L5_LIVE_SETTLED rows;
opens no socket, places no order, writes no ledger the executor reads.

WHY THE BASIS SPLIT IS THE WHOLE POINT. When a leg's true fill price could
not be attested, the accounting substitutes a $1/share PLACEHOLDER, which is
the worst price a contract can cost. That is deliberate and conservative, and
it means a placeholder window's loss is an UPPER BOUND, not a measured loss.
Summing the two together produces a number that looks like realized loss and
is not one: in the prior ledger the split was -$1.69 measured against -$51.58
placeholder, so 97% of the apparent loss was a bound rather than an
observation. Any statement about per-pair or per-residual economics must come
from attested windows only.

HOW PLACEHOLDER WINDOWS ARE IDENTIFIED. The settled telemetry rows carry no
flag, so the detector is structural: a leg whose cost divided by quantity is
EXACTLY 1.0000. Validated against the 106 windows in
analysis/l5/btc15m_live_ledger.json, which do carry the ledger's own
`cost_basis_placeholder` boolean: 17 true positives, 0 false positives,
0 false negatives. `tests/test_l5_btc15m_campaign_reconcile.py` pins that
agreement so the detector cannot silently drift from the ledger's own
labelling.

Run: PYTHONPATH=. python3 analysis/l5/btc15m_campaign_reconcile.py
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Optional, Sequence

HARVEST = "data/l5/ops/btc15m_settled_20261012_20261105.jsonl"
LEDGER = "analysis/l5/btc15m_live_ledger.json"
#: The pair-cost ceiling in force in the deployed configuration.
PAIR_CEILING = D("0.96")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def load_harvest(path: str = HARVEST) -> Dict[str, Dict[str, Any]]:
    """{market: row}, last row per market wins (rows are re-emitted)."""
    out: Dict[str, Dict[str, Any]] = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            out[str(row["market"])] = row
    return out


def is_placeholder_basis(row: Dict[str, Any]) -> bool:
    """A leg priced at exactly $1.00/share is the unattested placeholder."""
    for q, c in (("up_qty", "up_cost"), ("dn_qty", "dn_cost")):
        qty, cost = D(str(row[q])), D(str(row[c]))
        if qty > 0 and cost / qty == 1:
            return True
    return False


def day(market: str) -> str:
    m = DATE_RE.search(market)
    return m.group(1) if m else "?"


def summarize(rows: Sequence[Dict[str, Any]], label: str) -> Dict[str, Any]:
    def T(field: str) -> D:
        return sum((D(str(r[field])) for r in rows), D(0))

    traded = [r for r in rows
              if D(str(r["up_qty"])) + D(str(r["dn_qty"])) > 0]
    with_pair = [r for r in traded if D(str(r["matched_qty"])) > 0]
    with_res = [r for r in traded if D(str(r["residual_qty"])) > 0]
    res_won = [r for r in with_res if D(str(r["residual_pnl"])) > 0]
    mq, rq = T("matched_qty"), T("residual_qty")
    mp, rp = T("matched_pnl"), T("residual_pnl")
    per_pair = (mp / mq) if mq else None
    per_res = (rp / rq) if rq else None
    # Breakeven: how many matched pairs must each residual unit be carried by.
    required = None
    if per_pair is not None and per_res is not None and per_pair > 0:
        required = (-per_res) / per_pair
    over = [r for r in with_pair
            if D(str(r["matched_cost"])) / D(str(r["matched_qty"])) > PAIR_CEILING]
    return {
        "label": label,
        "windows": len(rows), "traded": len(traded),
        "cost_total": str(T("cost_total")),
        "redemption_total": str(T("redemption_total")),
        "settlement_pnl": str(T("settlement_pnl")),
        "matched_qty": str(mq), "matched_pnl": str(mp),
        "residual_qty": str(rq), "residual_pnl": str(rp),
        "net_per_matched_pair": None if per_pair is None else str(per_pair),
        "net_per_residual_unit": None if per_res is None else str(per_res),
        "matched_to_residual_achieved": str(mq / rq) if rq else None,
        "matched_to_residual_required": None if required is None else str(required),
        "paired_fill_rate": (len(with_pair) / len(traded)) if traded else None,
        "windows_with_pair": len(with_pair),
        "windows_with_residual": len(with_res),
        "residual_win_rate": (len(res_won) / len(with_res)) if with_res else None,
        "windows_pair_cost_over_ceiling": len(over),
    }


def rescue_decomposition(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Split matched pairs into profit pairs and rescue pairs.

    A COMPLETED PAIR IS NOT ONE THING. src/l5_two_sided_probe.py enforces the
    pair-cost cap on the completion path and then deliberately overrides it:
    when the opening discipline refuses on COMPLEMENT_PAIR_ABOVE_CAP, the
    completion is re-asked against "does completing at this price beat
    carrying the residual to settlement, inside the approved budget", and is
    permitted on that answer alone. The comment names the cost of getting it
    wrong -- window 1245z lost the whole of its -$0.7264 by stranding a leg.

    So a pair completed above the cap is a RESCUE: it earns about nothing
    gross and exists to avoid a residual unit worth about -$0.47. Averaging
    rescues into "net per matched pair" understates the spread the
    profit-seeking pairs actually capture AND hides the loss avoidance the
    rescues actually deliver. Both are reported separately here.

    CLASSIFICATION IS WINDOW-LEVEL AND THAT IS A REAL LIMIT. `matched_cost`
    is blended: it equals matched_qty * (mean UP price + mean DOWN price),
    verified on every over-cap window. Where a window has exactly one fill
    per side the blended figure IS the pair cost and the split is exact;
    with several rounds it is an average that can straddle the cap, so those
    windows are counted but flagged `ambiguous`.
    """
    profit: List[Dict[str, Any]] = []
    rescue: List[Dict[str, Any]] = []
    ambiguous = 0
    for r in rows:
        mq = D(str(r["matched_qty"]))
        if mq <= 0:
            continue
        per_pair = D(str(r["matched_cost"])) / mq
        single = (D(str(r["up_qty"])) == 1 and D(str(r["dn_qty"])) == 1
                  and mq == 1)
        if not single:
            ambiguous += 1
        (rescue if per_pair > PAIR_CEILING else profit).append(r)

    def bucket(sub: Sequence[Dict[str, Any]], label: str) -> Dict[str, Any]:
        q = sum((D(str(r["matched_qty"])) for r in sub), D(0))
        p = sum((D(str(r["matched_pnl"])) for r in sub), D(0))
        return {"label": label, "windows": len(sub), "matched_qty": str(q),
                "matched_pnl": str(p),
                "net_per_pair": str(p / q) if q else None}

    return {"profit_pairs": bucket(profit, "pair cost <= %s" % PAIR_CEILING),
            "rescue_pairs": bucket(rescue, "pair cost > %s (rescue)" % PAIR_CEILING),
            "windows_ambiguous_blend": ambiguous,
            "note": "rescue pairs earn ~0 gross and exist to avoid a residual "
                    "unit; they are loss avoidance, not spread capture"}


def reconcile(harvest: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    rows = list((harvest if harvest is not None else load_harvest()).values())
    attested = [r for r in rows if not is_placeholder_basis(r)]
    placeholder = [r for r in rows if is_placeholder_basis(r)]
    nov = [r for r in rows if day(r["market"]) >= "2026-11-05"]
    nov_att = [r for r in nov if not is_placeholder_basis(r)]
    prior = [r for r in rows if day(r["market"]) < "2026-11-05"]
    prior_att = [r for r in prior if not is_placeholder_basis(r)]
    out: Dict[str, Any] = {
        "provenance": {
            "harvest": HARVEST, "rows": len(rows),
            "evidence_class": "VENUE_CONFIRMED_LIVE",
            "source": "L5_LIVE_SETTLED telemetry, service l5-pmus-live",
            "span": [min(day(r["market"]) for r in rows),
                     max(day(r["market"]) for r in rows)],
            "note": "placeholder-basis windows carry a $1/share upper bound, "
                    "not a measured loss; they are reported separately and "
                    "never mixed into per-unit economics",
        },
        "by_date": dict(sorted(collections.Counter(
            day(r["market"]) for r in rows).items())),
        "counts": {"settled": len(rows), "attested_basis": len(attested),
                   "placeholder_basis": len(placeholder)},
        "summaries": {},
    }
    for key, subset, label in (
            ("all_mixed", rows, "ALL rows (mixed basis -- NOT a measured loss)"),
            ("attested_all", attested, "attested basis, all dates"),
            ("placeholder_all", placeholder, "placeholder basis (upper bound)"),
            ("attested_prior", prior_att, "attested, through 2026-11-04"),
            ("attested_campaign", nov_att, "attested, 2026-11-05 campaign"),
            ("campaign_mixed", nov, "2026-11-05 campaign, all rows"),
    ):
        if subset:
            out["summaries"][key] = summarize(subset, label)
    return out


def ledger_agreement(path: str = LEDGER) -> Optional[Dict[str, Any]]:
    """Check the detector against the ledger's own placeholder labelling."""
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        windows = json.load(fh).get("windows") or []
    tp = fp = tn = fn = 0
    for w in windows:
        flagged = bool(w.get("cost_basis_placeholder"))
        detected = is_placeholder_basis(w)
        if flagged and detected:
            tp += 1
        elif flagged:
            fn += 1
        elif detected:
            fp += 1
        else:
            tn += 1
    return {"windows": len(windows), "true_positive": tp, "false_negative": fn,
            "false_positive": fp, "true_negative": tn,
            "exact": fn == 0 and fp == 0}


def main() -> int:
    out = reconcile()
    out["detector_vs_ledger"] = ledger_agreement()
    H = load_harvest()
    att = [r for r in H.values() if not is_placeholder_basis(r)]
    out["rescue_decomposition"] = {
        "attested_all": rescue_decomposition(att),
        "attested_campaign": rescue_decomposition(
            [r for r in att if day(r["market"]) >= "2026-11-05"]),
    }
    os.makedirs("analysis/l5/btc15m_signal", exist_ok=True)
    with open("analysis/l5/btc15m_signal/campaign_reconcile.json", "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    p = out["provenance"]
    print("%d settled windows, %s -> %s   attested %d, placeholder %d"
          % (p["rows"], p["span"][0], p["span"][1],
             out["counts"]["attested_basis"], out["counts"]["placeholder_basis"]))
    a = out["detector_vs_ledger"]
    if a:
        print("placeholder detector vs the ledger's own flag: %d/%d true positive, "
              "%d false pos, %d false neg -> %s"
              % (a["true_positive"], a["true_positive"] + a["false_negative"],
                 a["false_positive"], a["false_negative"],
                 "EXACT" if a["exact"] else "DISAGREES"))
    for key in ("all_mixed", "placeholder_all", "attested_all",
                "attested_prior", "attested_campaign"):
        s = out["summaries"].get(key)
        if not s:
            continue
        print()
        print("=== %s" % s["label"])
        print("  windows %d traded %d   cost %s  redeemed %s  settled net %s"
              % (s["windows"], s["traded"], s["cost_total"],
                 s["redemption_total"], s["settlement_pnl"]))
        print("  matched %s contracts -> %s      per pair     %s"
              % (s["matched_qty"], s["matched_pnl"],
                 (s["net_per_matched_pair"] or "n/a")[:9]))
        print("  residual %s units    -> %s      per unit     %s"
              % (s["residual_qty"], s["residual_pnl"],
                 (s["net_per_residual_unit"] or "n/a")[:9]))
        print("  matched:residual achieved %s   required %s"
              % ((s["matched_to_residual_achieved"] or "n/a")[:6],
                 (s["matched_to_residual_required"] or "n/a")[:6]))
        print("  paired-fill rate %s   residual win rate %s   pairs over ceiling %d"
              % ("n/a" if s["paired_fill_rate"] is None
                 else "%.1f%% (%d/%d)" % (100 * s["paired_fill_rate"],
                                          s["windows_with_pair"], s["traded"]),
                 "n/a" if s["residual_win_rate"] is None
                 else "%.1f%%" % (100 * s["residual_win_rate"]),
                 s["windows_pair_cost_over_ceiling"]))
    print()
    print("A COMPLETED PAIR IS NOT ONE THING: profit pairs vs rescue pairs")
    for scope, dec in out["rescue_decomposition"].items():
        print("  -- %s (%d windows with a blended cost straddling the cap)"
              % (scope, dec["windows_ambiguous_blend"]))
        for key in ("profit_pairs", "rescue_pairs"):
            b = dec[key]
            print("     %-26s windows %3d  pairs %8s  pnl %10s  per pair %s"
                  % (b["label"], b["windows"], b["matched_qty"],
                     str(D(b["matched_pnl"]).quantize(D("0.0001"))),
                     (b["net_per_pair"] or "n/a")[:9]))
    print()
    print("wrote analysis/l5/btc15m_signal/campaign_reconcile.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
