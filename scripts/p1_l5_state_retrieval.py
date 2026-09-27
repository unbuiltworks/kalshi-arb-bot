"""P1: for every materially losing PMUS state, what did L5 do in comparable states?

DIAGNOSTIC. Retrieves nearest L5 BTC15m states from the raw activity corpus and
reports his SUBSEQUENT action. Selects nothing, tunes nothing, and touches no
part of the frozen n=25 arm.

MATERIALLY LOSING is defined WITHOUT reference to settlement, so nothing here is
fitted to outcomes:
  * a matched unit whose effective pair cost exceeds 1.00 -- known at the moment
    the second leg fills;
  * a terminal one-sided position, measured by its size, not by whether it won.

MATCHING. Imbalance is expressed in CLIPS (imbalance / the window's own typical
fill size) so a 50-contract PMUS imbalance and a 50-contract L5 imbalance are not
treated as comparable when his typical fill is 10 and ours is 50. Held-side basis
is in cents, the price move is his own-fill proxy (see the corpus module's stated
limits), and TTE is bucketed.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_sequence_corpus as C                          # noqa: E402
from scripts.audit_economic_complete import load, USABLE         # noqa: E402
from scripts.audit_completion_orderstate import walk             # noqa: E402

THETA, VARIANT, FILL = D("0.96"), "B", "queue_aware"
TTE_BUCKETS = ((0, 30), (30, 120), (120, 300), (300, 900))


def tte_bucket(s):
    if s is None:
        return None
    for lo, hi in TTE_BUCKETS:
        if lo <= s < hi:
            return "%d-%ds" % (lo, hi)
    return ">=900s"


def l5_pool():
    """L5 states, with imbalance normalised to clips by his own window size."""
    out = []
    for slug, seq in C.windows().items():
        sizes = sorted(float(e["size"]) for e in seq)
        typ = sizes[len(sizes) // 2] or 1.0
        for i, e in enumerate(seq):
            nxt = seq[i + 1] if i + 1 < len(seq) else None
            out.append({
                "slug": slug, "typ": typ,
                "imb_clips": float(e["imbalance_before"]) / typ,
                "held": e["held_before"],
                "basis": (float(e["held_basis_before"])
                          if e["held_basis_before"] is not None else None),
                "move": e["own_price_move"],
                "tte": e["tte_s"], "tte_b": tte_bucket(e["tte_s"]),
                "next_kind": (nxt["kind"] if nxt else C.CARRY),
                "next_delay_s": ((nxt["t"] - e["t"]) if nxt else None),
                "next_size_clips": ((float(nxt["size"]) / typ) if nxt else None),
                "terminal_imb": float(e["terminal_imbalance"]),
                "fills_in_window": e["n_fills_in_window"],
            })
    return out


def neighbours(pool, *, imb_clips, basis, tte, k=400):
    """Nearest L5 states. Distance is deliberately simple and is reported."""
    def d(r):
        if r["basis"] is None or r["imb_clips"] == 0:
            return None
        a = abs(r["imb_clips"] - imb_clips) / 2.0
        b = abs(r["basis"] - basis) / 0.10 if basis is not None else 0.0
        c = (abs((r["tte"] or 0) - tte) / 120.0) if tte is not None else 0.0
        return a + b + c
    scored = [(d(r), r) for r in pool]
    scored = [(x, r) for x, r in scored if x is not None]
    scored.sort(key=lambda p: p[0])
    return [r for _, r in scored[:k]]


def pmus_losing_states(clip):
    """Every materially losing state in the frozen windows, with its context."""
    by = load()
    out = []
    for slug, winner in USABLE.items():
        a = walk(by[slug], clip, THETA, VARIANT, winner)
        ev = a["events"]
        for i, e in enumerate(ev):
            if e["kind"] != "ACQUIRE":
                continue
            before = e.get("inv_after")
            # the state this acquisition ARRIVED in
            prev = ev[i - 1]["inv_after"] if i else {"up": D(0), "down": D(0)}
            imb = D(prev.get("up", 0)) - D(prev.get("down", 0))
            held = "up" if imb > 0 else ("down" if imb < 0 else None)
            out.append({
                "window": slug[-5:], "t": e["t"], "side": e["side"],
                "px": str(e["px"]), "imb_before": str(imb), "held": held,
                "imb_clips": float(imb) / float(clip),
                "inv_after": {k: str(v) for k, v in (before or {}).items()},
            })
    return out
