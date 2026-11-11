"""P28 -- how much authentic L5 behaviour does the inherited sports band
suppress on PMUS BTC15m, and what eligibility rule does the evidence support?

The executor's band is 0.20-0.80. It was chosen for sports and it is applied
unchanged to a market whose observed median mid is ~0.16-0.22, so a large part
of the regime is refused before the controller can act. This module MEASURES
that suppression by replaying the frozen model under candidate bands. It
changes no executor constant, and a measurement is not a proposal to trade.

WHAT THE SWEEP MUST NOT DO. Picking the band that maximises quotes would be
fitting a parameter to three windows, which is the failure this programme
keeps writing down. So the sweep reports, for every candidate, BOTH the
behaviour admitted AND the pair economics that result -- because widening the
band is not free: a wider band admits prices at which a completed pair costs
MORE than the $1.00 it pays.

Everything downstream of a fill here is Track B's assumption, not evidence.
The band question and the fill question are kept apart on purpose.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src import pmus_btc15m_l5_shadow as sh          # noqa: E402
from src import pmus_l5_persistence_v2 as pv2        # noqa: E402

#: candidates, widest last. The inherited band is first so it is always the
#: reference row rather than one option among many.
CANDIDATES = [
    ("inherited_sports", D("0.20"), D("0.80")),
    ("0.15-0.85", D("0.15"), D("0.85")),
    ("0.10-0.90", D("0.10"), D("0.90")),
    ("0.05-0.95", D("0.05"), D("0.95")),
    ("venue_full", D("0.01"), D("0.99")),
]


def run(tape: str, band) -> Dict[str, Any]:
    model = pv2.load_v2()
    by = sh.load_tape(tape)
    windows = [sh.replay_window(slug, rs, model, assume_fills=True, band=band)
               for slug, rs in sorted(by.items())]
    quotes = holds = 0
    rels: Dict[str, int] = {}
    band_holds = 0
    pairs = 0
    pair_cost = D("0")
    residual = 0
    prices: List[D] = []
    for w in windows:
        up = dn = 0
        upc = dnc = D("0")
        for d in w.get("decisions") or []:
            if d.get("action") == "HOLD":
                holds += 1
                if d.get("reason") == "entry_band":
                    band_holds += 1
                continue
            quotes += 1
            rels[d["relation"]] = rels.get(d["relation"], 0) + 1
            prices.append(D(str(d["desired_price"])))
            if not d.get("assumed_filled"):
                continue
            px = D(str(d["desired_price"]))
            if d["side"] == "UP":
                up += 1
                upc += px
            else:
                dn += 1
                dnc += px
        m = min(up, dn)
        pairs += m
        if m:
            pair_cost += ((upc / up if up else D(0))
                          + (dnc / dn if dn else D(0))) * m
        residual += abs(up - dn)
    avg_pair = (pair_cost / pairs) if pairs else None
    return {
        "quotes": quotes, "holds": holds, "band_holds": band_holds,
        "relations": rels,
        "assumed_pairs": pairs,
        "avg_pair_cost": (str(avg_pair.quantize(D("0.0001")))
                          if avg_pair is not None else None),
        "pair_pnl_usd": (str((D(pairs) - pair_cost).quantize(D("0.0001")))
                         if pairs else "0.0000"),
        "pairs_clear_under_1": avg_pair is not None and avg_pair < D("1"),
        "pairs_clear_under_spring_096": (avg_pair is not None
                                         and avg_pair <= D("0.96")),
        "residual_units": residual,
        "desired_price_p10": str(_p(prices, 0.1)) if prices else None,
        "desired_price_p50": str(_p(prices, 0.5)) if prices else None,
        "desired_price_p90": str(_p(prices, 0.9)) if prices else None,
    }


def _p(a: List[D], q: float) -> D:
    b = sorted(a)
    return b[min(len(b) - 1, int(q * len(b)))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", default="data/pmus_btc15m/*.jsonl")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rows = []
    for name, lo, hi in CANDIDATES:
        r = run(a.tape, (lo, hi))
        r["band"] = name
        r["lo"], r["hi"] = str(lo), str(hi)
        rows.append(r)
    base = rows[0]
    for r in rows:
        r["quotes_vs_inherited"] = r["quotes"] - base["quotes"]
        r["suppression_of_this_band"] = (
            round(r["band_holds"] / (r["quotes"] + r["holds"]), 3)
            if (r["quotes"] + r["holds"]) else None)
    rep = {"label": "PMUS_BTC15M_BAND_COUNTERFACTUAL_V1",
           "executor_untouched": True,
           "fill_basis": "TRACK B ASSUMPTION, not evidence -- the economics "
                         "columns inherit it and are not realized",
           "candidates": rows}
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1)
    print("%-18s %-7s %-7s %-10s %-24s %-12s %-10s %s" %
          ("band", "quotes", "holds", "band_hold", "relations",
           "avg_pair", "clears<1", "<=0.96"))
    for r in rows:
        print("%-18s %-7s %-7s %-10s %-24s %-12s %-10s %s" %
              (r["band"], r["quotes"], r["holds"], r["band_holds"],
               json.dumps(r["relations"]), r["avg_pair_cost"],
               r["pairs_clear_under_1"], r["pairs_clear_under_spring_096"]))
    print("\ndesired-price distribution by band (p10/p50/p90):")
    for r in rows:
        print("  %-18s %s / %s / %s   pairs=%s residual=%s"
              % (r["band"], r["desired_price_p10"], r["desired_price_p50"],
                 r["desired_price_p90"], r["assumed_pairs"],
                 r["residual_units"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
