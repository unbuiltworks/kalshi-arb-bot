"""BTC15m lifecycle phases -- boundaries PREREGISTERED, never searched.

Does the poor aggregate 1.01x transition lift hide phases where behaviour IS
predictable? The five slices below are fixed in
PREREGISTRATION_NEXTACTION.md ss9 and are not moved, merged or re-cut after
seeing results, and no alternative slicing is reported.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List

from analysis.l5.microstructure.clips import build_clips
from analysis.l5.microstructure.clipstate import transitions
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id

PHASES = (("OPENING", 0, 60), ("EARLY", 60, 300), ("MID", 300, 600),
          ("LATE", 600, 840), ("EXPIRY", 840, 900))
GAP_S = 10


def H(c: Counter) -> float:
    t = sum(c.values())
    return -sum((v / t) * math.log2(v / t) for v in c.values() if v) if t \
        else 0.0


def lift(seqs: List[List[str]]) -> Dict[str, Any]:
    uni = Counter()
    cond: Dict[str, Counter] = defaultdict(Counter)
    for s in seqs:
        for a in s:
            uni[a] += 1
        for a, b in zip(s, s[1:]):
            cond[a][b] += 1
    n = sum(uni.values())
    if n < 2:
        return {"n_transitions": n, "lift": None,
                "note": "too few transitions"}
    base = max(uni.values()) / n
    hit = sum(max(c.values()) for c in cond.values())
    tot = sum(sum(c.values()) for c in cond.values())
    cH = (sum(sum(c.values()) * H(c) for c in cond.values()) / tot
          if tot else None)
    return {
        "n_transitions": n, "n_ordered_pairs": tot,
        "base_rate_modal": round(base, 4),
        "first_order_accuracy": round(hit / tot, 4) if tot else None,
        "lift": round((hit / tot) / base, 3) if tot and base else None,
        "entropy_uncond_bits": round(H(uni), 3),
        "entropy_cond_bits": round(cH, 3) if cH is not None else None,
        "bits_gained": (round(H(uni) - cH, 3) if cH is not None else None),
        "distribution": {k: round(v / n, 4) for k, v in uni.most_common(4)},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--contract-length", default="15m")
    ap.add_argument("--window-s", type=int, default=900)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    ev = [json.loads(l) for l in open(a.events, encoding="utf-8")
          if l.strip()]
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    books, _ = load(a.parquet, contract_length=a.contract_length,
                    slugs=set(byw))

    per_phase: Dict[str, List[List[str]]] = {p[0]: [] for p in PHASES}
    whole: List[List[str]] = []
    for slug, seq in sorted(byw.items()):
        pairs, member = find_pairs(seq)
        start = int(slug.rsplit("-", 1)[1])
        passive = [r for r in seq if _id(r) not in member]
        clips = build_clips(passive, books, gap_s=GAP_S, slug=slug,
                            clen=a.contract_length, start=start)
        units = [{"kind": "CLIP", "market_slug": slug,
                  "outcome": c["outcome"], "price": c["price"],
                  "t_first": c["t_first"],
                  "secs_after_open_first": c["secs_after_open_first"],
                  "tte_at_first_s": c["tte_at_first_s"]} for c in clips]
        units += [{"kind": "INJECTION", "market_slug": slug,
                   "outcome": "both", "price": None, "t_first": p["up"]["t"],
                   "secs_after_open_first": p["up"]["secs_since_open"],
                   "tte_at_first_s": p["up"]["tte_s"]} for p in pairs]
        units.sort(key=lambda x: (x["t_first"], x["kind"]))
        lab = transitions(units)
        whole.append([u["transition"] for u in lab])
        for name, lo, hi in PHASES:
            per_phase[name].append(
                [u["transition"] for u in lab
                 if lo <= u["secs_after_open_first"] < hi])

    rep = {
        "contract_length": a.contract_length,
        "phases_preregistered": [{"name": n, "from_s": lo, "to_s": hi}
                                 for n, lo, hi in PHASES],
        "boundaries_were_not_searched": True,
        "no_alternative_slicing_reported": True,
        "WHOLE_WINDOW": lift(whole),
        "BY_PHASE": {n: lift(per_phase[n]) for n, _, _ in PHASES},
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "PHASES_%s.json" % a.contract_length),
              "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
