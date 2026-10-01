"""SIMULATOR-VALIDITY AUDIT and pair forensics for V1/V2/V3.

THE QUESTION THAT OUTRANKS THE ECONOMICS: does the replay grant a PERSISTED
quote a fill advantage we could not obtain in a live queue? V3 keeps the filled
side at its previous price. If the book then moves, that price is no longer the
touch, and three things follow mechanically in the frozen fill model:

  1. FILL CONDITION. `on_trade` fills a leg when `own <= leg.px` (own = the print
     in the side's own terms). A persisted BUY price that is ABOVE the current
     bid therefore satisfies the condition for MORE prints than a re-derived
     quote at the lower bid. That is extra aggression, not queue luck -- and the
     engine charges us `leg.px`, the worse price.
  2. QUEUE POSITION. `displayed_at` returns 0 when the price is absent from the
     ladder, documented as "the level is EMPTY, so we would be first in queue".
     A persisted price away from the touch is usually absent, so a persisted leg
     tends to get ahead0 = 0. Resting above the best bid does make you the best
     bid, so ahead0 = 0 is defensible -- but it must be MEASURED, not assumed.
  3. CROSSING -- the real modelling violation. Nothing in `_still_valid` checks
     whether a persisted price has become CROSSED (`leg.px >= own_ask`). A real
     resting buy at or above the ask executes immediately as a TAKER at the ask;
     the simulator keeps it resting as a MAKER at `leg.px`. Every such fill is
     mispriced and misclassified.

Run on DEVELOPMENT tape (Batch 2, 10Z-14Z). The 05Z-09Z OOS block is spent and is
NOT re-run for revised economics.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

sys.path.insert(0, ".")

from src.l5_continuous import (V1, V2, V3, V1ExtendOnly,  # noqa: E402
                               V2NoBasisGate, V3Continuous)
from src.l5_two_sided import ARM_REPLICATION, Leg, TwoSided  # noqa: E402

REP = ARM_REPLICATION
ONE = D(1)


class Forensic:
    """Records every fill with the state that made it possible. Observation
    only: it writes to its own ledger and no rule reads it."""

    def _init_forensic(self) -> None:
        self.ledger: List[Dict[str, Any]] = []
        self._persisted_px: Dict[str, Optional[D]] = {"up": None, "down": None}

    def _book_fill(self, side, qty, px, *, maker, evidence) -> None:
        leg = self.legs.get(side)
        touch = self.own_touch(side)
        oask = self.own_ask(side)
        persisted = (self._persisted_px.get(side) is not None and
                     self._persisted_px[side] == D(str(px)))
        self.ledger.append({
            "t": self.now, "side": side, "px": D(str(px)), "qty": D(str(qty)),
            "maker": maker,
            "leg_age_s": (self.now - leg.placed_t) if leg else None,
            "ahead0": (leg.ahead0 if leg else None),
            "persisted": persisted,
            "touch": touch,
            "px_vs_touch": (D(str(px)) - touch) if touch is not None else None,
            "own_ask": oask,
            #: a resting BUY at or above the executable ask would have crossed
            #: and executed as a TAKER in reality. The simulator does not.
            "crossed": (oask is not None and D(str(px)) >= oask),
            "resid_before": self.residual(),
            "matched_before": self.matched(),
            "secs_left": self.seconds_left(),
        })
        super()._book_fill(side, qty, px, maker=maker, evidence=evidence)


class FRep(Forensic, TwoSided):
    def __init__(self, **kw):
        super().__init__(arm=REP, **kw)
        self._init_forensic()


class FV1(Forensic, V1ExtendOnly):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._init_forensic()


class FV2(Forensic, V2NoBasisGate):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._init_forensic()


class FV3(Forensic, V3Continuous):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._init_forensic()

    def reconcile_quotes(self) -> None:
        """Tag which side is about to be replenished AT ITS OLD LEVEL."""
        pending = {s: self._filled_px.get(s) for s in ("up", "down")
                   if self.legs[s] is None and s in self._filled_px}
        super().reconcile_quotes()
        for s, p in pending.items():
            leg = self.legs.get(s)
            self._persisted_px[s] = (leg.px if leg is not None and
                                     leg.px == p else None)


ARMS = {REP: FRep, V1: FV1, V2: FV2, V3: FV3}


def med(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def run(paths, slug, winner, start):
    import scripts.l5_run_causal_ws as W
    W.SLICE = list(paths)
    events = W.events(slug)
    out = {}
    for name, cls in ARMS.items():
        c = cls(clip=D(1))
        c.window_end = start + 900.0
        for t, kind, row in events:
            if kind == "book":
                c.on_book(row, t)
            else:
                c.on_trade(row.get("px"), row.get("qty"), t)
        out[name] = {"ledger": c.ledger, "pairs": c.pairs,
                     "report": c.report(winner=winner)}
    return out


def main(argv) -> int:
    from scripts.l5_window_manifest import manifest
    man = manifest(argv[1:])
    usable = [w for w in man["windows"] if w["complete"]]
    print(f"DEVELOPMENT windows: {len(usable)} (Batch 2 tape; the OOS block is "
          f"spent and not re-run)")
    led = defaultdict(list)
    prs = defaultdict(list)
    for w in usable:
        r = run(w["raw_paths"], w["slug"], w["winner"], w["window_start"])
        for a in ARMS:
            led[a] += r[a]["ledger"]
            prs[a] += [(w["slug"], p) for p in r[a]["pairs"]]

    print("\n================ SIMULATOR-VALIDITY AUDIT ================")
    print("%-18s%8s%10s%10s%12s%12s%12s" % (
        "arm", "fills", "persist", "crossed", "cross_share", "ahead0=0",
        "med_age_s"))
    for a in ARMS:
        L = led[a]
        if not L:
            continue
        cr = sum(1 for x in L if x["crossed"])
        pe = sum(1 for x in L if x["persisted"])
        a0 = sum(1 for x in L if x["ahead0"] == 0)
        ages = [x["leg_age_s"] for x in L if x["leg_age_s"] is not None]
        print("%-18s%8d%10d%10d%12.4f%12.4f%12s" % (
            a, len(L), pe, cr, cr / len(L), a0 / len(L), med(ages)))

    print("\n  price relative to the executable touch at fill time")
    for a in ARMS:
        L = [x for x in led[a] if x["px_vs_touch"] is not None]
        if not L:
            continue
        above = sum(1 for x in L if x["px_vs_touch"] > 0)
        print(f"    {a:<18} fills priced ABOVE the touch: {above}/{len(L)} "
              f"= {above/len(L):.4f}   median px-touch = "
              f"{med([x['px_vs_touch'] for x in L])}")

    print("\n  PERSISTED vs DERIVED fills within V3")
    L = led[V3]
    for lab, sel in (("persisted", [x for x in L if x["persisted"]]),
                     ("derived", [x for x in L if not x["persisted"]])):
        if not sel:
            continue
        cr = sum(1 for x in sel if x["crossed"]) / len(sel)
        a0 = sum(1 for x in sel if x["ahead0"] == 0) / len(sel)
        pv = [x["px_vs_touch"] for x in sel if x["px_vs_touch"] is not None]
        print(f"    {lab:<10} n={len(sel):6d} crossed={cr:.4f} "
              f"ahead0=0 {a0:.4f} median px-touch={med(pv)} "
              f"maker_share={sum(1 for x in sel if x['maker'])/len(sel):.4f}")

    print("\n================ PAIR FORENSICS ================")
    print("%-18s%8s%12s%12s%12s%12s" % ("arm", "pairs", "med_pair_cost",
                                        "cost>1", "med_edge", "neg_edge"))
    for a in ARMS:
        P = [p for _, p in prs[a]]
        if not P:
            continue
        costs = [p.up_px + p.down_px for p in P]
        edges = [p.gross_edge() for p in P]
        over = sum(1 for c in costs if c > ONE)
        neg = sum(1 for e in edges if e < 0)
        print("%-18s%8d%12s%12s%12s%12s" % (
            a, len(P), med(costs), f"{over} ({over/len(P):.3f})",
            med(edges), f"{neg} ({neg/len(P):.3f})"))

    print("\n  worst five pairs by gross edge, per arm")
    for a in ARMS:
        P = sorted(prs[a], key=lambda sp: sp[1].gross_edge())[:5]
        if not P:
            continue
        print(f"    {a}")
        for slug, p in P:
            print(f"      {slug[-5:]} qty={p.qty} up_px={p.up_px} "
                  f"down_px={p.down_px} up_maker={p.up_maker} "
                  f"down_maker={p.down_maker} "
                  f"cost={p.up_px + p.down_px} edge={p.gross_edge()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
