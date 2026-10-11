"""Did the Gemini surface fill on BOTH sides, or only one?

THE P0 THIS ANSWERS. An earlier reading -- 17 evidenced fills, zero
complements -- is ambiguous about architecture: a one-shot controller that
buys and then hunts a complement produces the same headline as a persistent
two-sided surface whose sell side simply never traded. Those are opposite
conclusions. One says the complement logic is broken; the other says the sell
side of the book never came to us.

So every count here is SPLIT BY SIDE. A fill total that is not split cannot
distinguish them, and that is the whole question.

Six of the seven surfaces quote BUY and SELL simultaneously
(`single_level` is bid-only by construction), so the split is also a check on
the surface definitions themselves: a two-sided surface with zero SELL
placements is a defect, not a market observation.

AND THE FILL SPLIT ALONE STILL CANNOT CLOSE IT. Zero SELL fills is consistent
with "the sell side was never placed" and with "it was placed, rested, and
nothing ever traded through it". The running shadow counts placements in
aggregate, so the tape does not say which -- so this module RECONSTRUCTS the
placement split by replaying the shadow's OWN `surface_quotes` against each
observation's recorded touch. Same function the shadow calls, so the surface
definitions are not restated here and cannot drift from it.

What reconstruction can and cannot reach, stated rather than blurred:

  placed / restable / book_reached   per side, from the recorded touch.
  trade_flow / queue-ahead           NOT per side. Prints are summarised as a
                                     count and per-quote queue depth is not
                                     emitted, so a per-side figure would be
                                     invented. It is reported as
                                     NOT_INSTRUMENTED, never as zero.

NOTHING IS PROMOTED. `VOLUME_AHEAD_CLEARED` is the only rung that counts as a
fill, and it is still a model: queue position is unpublished, so it assumes we
joined behind everything resting. It is evidence, never venue confirmation.
"""
from __future__ import annotations

import glob
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from src.gemini_l5_surface_shadow import surface_quotes

LABEL = "GEMINI_SURFACE_REPORT_V1"
STRICT = "VOLUME_AHEAD_CLEARED"
MARKS_S = (5, 15, 30, 60, 120, 300)


def _tape(d: str) -> Optional[str]:
    hits = glob.glob(os.path.join(d, "gemini_surface_*.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def _rows(path: str) -> List[dict]:
    out: List[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _share(n: int, d: int) -> Optional[float]:
    return None if not d else round(n / d, 4)


def recycling(fills: Sequence[dict]) -> Dict[str, Any]:
    """Opposite-SIDE evidenced fill within each mark of an evidenced fill.

    Both legs must be evidenced. A resting opposite quote is not recycling,
    and neither is a print we did not clear the queue for."""
    fs = sorted(fills, key=lambda f: float(f.get("t") or 0))
    out: Dict[str, Any] = {"n_fills": len(fs)}
    for m in MARKS_S:
        hit = 0
        for i, f in enumerate(fs):
            t0, side = float(f.get("t") or 0), f.get("side")
            if any(g.get("side") != side
                   and 0 < float(g.get("t") or 0) - t0 <= m
                   for g in fs[i + 1:]):
                hit += 1
        out["n_opposite_within_%ds" % m] = hit
        out["p_opposite_within_%ds" % m] = _share(hit, len(fs))
    out["labels"] = ("both legs EVIDENCED. A resting opposite quote is not an "
                     "interaction.")
    return out


def _native_placements(obs: Sequence[dict]) -> Optional[Dict[str, Any]]:
    """The split as the shadow itself counted it, if this segment has it.

    Counters are cumulative, so the LAST observation carrying them is the
    total; summing across observations would count a surviving quote once per
    observation it lived through.
    """
    last = None
    for o in obs:
        if any("quotes_placed_by_side" in (b or {})
               for b in (o.get("surfaces") or {}).values()):
            last = o
    if last is None:
        return None
    per: Dict[str, Dict[str, Any]] = {}
    for name, b in (last.get("surfaces") or {}).items():
        placed = (b or {}).get("quotes_placed_by_side") or {}
        rungs = (b or {}).get("rungs_by_side") or {}
        per[name] = {
            "BUY": int(placed.get("BUY", 0)),
            "SELL": int(placed.get("SELL", 0)),
            "BUY_restable": int(rungs.get("BUY|RESTABLE", 0)),
            "SELL_restable": int(rungs.get("SELL|RESTABLE", 0)),
            "BUY_book_reached": int(rungs.get("BUY|book_reached_quote", 0)),
            "SELL_book_reached": int(rungs.get("SELL|book_reached_quote", 0)),
            "BUY_trade_flow": int(rungs.get("BUY|trade_flow_through_quote", 0)),
            "SELL_trade_flow": int(
                rungs.get("SELL|trade_flow_through_quote", 0)),
            "queue_ahead": (b or {}).get("queue_ahead_by_side"),
        }
    keys = ("BUY", "SELL", "BUY_restable", "SELL_restable",
            "BUY_book_reached", "SELL_book_reached",
            "BUY_trade_flow", "SELL_trade_flow")
    tot = {k: sum(int(v.get(k, 0)) for v in per.values()) for k in keys}
    return {
        "by_surface": per, "totals": tot, "observations_unreadable": 0,
        "sell_ever_placed": tot["SELL"] > 0,
        "method": "recorded by the shadow itself, split by side at placement",
        "labels": [
            "counted by the surface as it ran, not reconstructed.",
            "trade_flow IS available per side here, unlike a reconstructed "
            "segment, because the shadow saw the prints and the tape does not "
            "carry them.",
        ],
    }


def placements_by_side(obs: Sequence[dict]) -> Dict[str, Any]:
    """Replay the shadow's own surface_quotes against each recorded touch.

    This is what separates a broken sell side from a market that never hit
    one. It calls the SHADOW's function, so a surface definition changed there
    changes here too and the two cannot silently disagree.

    A placement here is a quote the surface WANTED at that observation, not a
    distinct order: the shadow keeps a wanted quote alive across observations
    rather than re-placing it. So these are per-side presence counts, and they
    are labelled as such.
    """
    # A segment recorded by an instrumented shadow carries the split
    # natively. Prefer it: it is what the surface DID, where reconstruction
    # is only what it would have wanted. Fall back for older segments, which
    # is the only reason reconstruction exists.
    native = _native_placements(obs)
    if native is not None:
        return native

    per: Dict[str, Dict[str, int]] = {}
    unreadable = 0
    for o in obs:
        bb, aa = o.get("best_bid"), o.get("best_ask")
        if bb is None or aa is None:
            unreadable += 1
            continue
        try:
            bb_d, aa_d = D(str(bb)), D(str(aa))
        except (ArithmeticError, ValueError):
            unreadable += 1
            continue
        for name, blob in (o.get("surfaces") or {}).items():
            res = D(str((blob or {}).get("residual") or "0"))
            c = per.setdefault(name, {"BUY": 0, "SELL": 0,
                                      "BUY_restable": 0, "SELL_restable": 0,
                                      "BUY_book_reached": 0,
                                      "SELL_book_reached": 0})
            for side, px in surface_quotes(name, bb_d, aa_d, res):
                c[side] += 1
                # the shadow's own two rules, not a second opinion
                if (side == "BUY" and px < aa_d) or (side == "SELL"
                                                     and px > bb_d):
                    c["%s_restable" % side] += 1
                if (side == "BUY" and bb_d <= px) or (side == "SELL"
                                                      and aa_d >= px):
                    c["%s_book_reached" % side] += 1
    tot = {k: sum(v[k] for v in per.values())
           for k in ("BUY", "SELL", "BUY_restable", "SELL_restable",
                     "BUY_book_reached", "SELL_book_reached")}
    return {
        "by_surface": per, "totals": tot,
        "observations_unreadable": unreadable,
        "sell_ever_placed": tot["SELL"] > 0,
        "method": ("reconstructed by replaying src.gemini_l5_surface_shadow."
                   "surface_quotes against each observation's recorded touch"),
        "labels": [
            "presence counts per observation, NOT distinct orders: a wanted "
            "quote persists across observations instead of being re-placed.",
            "trade_flow and queue-ahead are NOT reconstructible per side and "
            "are reported as NOT_INSTRUMENTED rather than as zero.",
        ],
    }


def _verdict(buy: int, sell: int, placed: Dict[str, Any]) -> str:
    """BUY_ONLY_FILLS is where the P0 used to stop, and it is ambiguous.

    A sell side that was never placed is a defect in our surface. A sell side
    that rested and was never traded through is an observation about the
    market. Opposite conclusions, opposite next actions, so they get opposite
    verdicts.
    """
    if buy and sell:
        return "TWO_SIDED_FILLS"
    if sell and not buy:
        return "SELL_ONLY_FILLS"
    sell_placed = bool((placed.get("totals") or {}).get("SELL"))
    if buy:
        return ("BUY_ONLY_FILLS_SELL_RESTED_UNFILLED" if sell_placed
                else "BUY_ONLY_FILLS_SELL_NEVER_PLACED")
    return ("NO_EVIDENCED_FILLS" if sell_placed
            else "NO_EVIDENCED_FILLS_AND_SELL_NEVER_PLACED")


def report(state_dir: str) -> Dict[str, Any]:
    path = _tape(state_dir)
    if not path:
        return {"label": LABEL, "ok": False, "why": "no gemini surface tape"}
    rows = _rows(path)
    start = next((r for r in rows if r.get("kind") == "session_start"), None)
    fills = [r for r in rows if r.get("kind") == "evidenced_fill"]
    obs = [r for r in rows if r.get("kind") == "observation"]

    by_surface: Dict[str, Dict[str, Any]] = {}
    for f in fills:
        s = by_surface.setdefault(f.get("surface"), {"BUY": 0, "SELL": 0})
        s[f.get("side", "?")] = s.get(f.get("side", "?"), 0) + 1

    # The rung ladder, as the surfaces themselves counted it. It is nested
    # under surfaces[name], and reading it from the row's top level -- where
    # there is no such key -- reported an empty ladder rather than a miss.
    # Taken from the LAST observation, because the shadow's counters are
    # cumulative and summing them across observations counts each quote once
    # per observation it survived.
    rungs: Dict[str, int] = {}
    if obs:
        for blob in (obs[-1].get("surfaces") or {}).values():
            for k, v in ((blob or {}).get("rungs") or {}).items():
                if isinstance(v, (int, float)):
                    rungs[k] = rungs.get(k, 0) + int(v)

    buy = sum(1 for f in fills if f.get("side") == "BUY")
    sell = sum(1 for f in fills if f.get("side") == "SELL")
    # Executable capacity, both directions. The shadow records the touch
    # PRICES but not the sizes resting at them, so a median size is not
    # available from this tape. Saying so beats an n=0 that reads like a
    # measured absence -- which is what reading a non-existent "touch" key
    # produced before.
    spreads: List[float] = []
    for o in obs:
        v = o.get("spread")
        if v is not None:
            try:
                spreads.append(float(v))
            except (TypeError, ValueError):
                pass

    def _med(xs):
        return round(sorted(xs)[len(xs) // 2], 6) if xs else None

    return {
        "label": LABEL, "ok": True, "tape": path, "rows": len(rows),
        "surfaces": (start or {}).get("surfaces"),
        "observations": len(obs),
        "evidenced_fills": len(fills),
        "fills_BUY": buy, "fills_SELL": sell,
        "two_sided": bool(buy and sell),
        "by_surface": by_surface,
        "rung_ladder": rungs,
        "recycling": recycling(fills),
        "placements": placements_by_side(obs),
        "capacity": {
            "median_spread": _med(spreads), "n_spreads": len(spreads),
            "median_bid_size": "NOT_INSTRUMENTED",
            "median_ask_size": "NOT_INSTRUMENTED",
            "queue_ahead_by_side": "NOT_INSTRUMENTED",
            "why": ("the shadow records touch PRICES, not the size resting "
                    "at them, and emits per-quote queue depth only on a "
                    "fill. A per-side figure would be invented."),
        },
        "verdict": _verdict(buy, sell, placements_by_side(obs)),
        "labels": [
            "Counts are SPLIT BY SIDE because an unsplit total cannot tell a "
            "broken complement from a sell side the market never hit.",
            "VOLUME_AHEAD_CLEARED only, and it is a model: queue position is "
            "unpublished, so it assumes we joined behind everything resting.",
            "A two-sided surface showing zero SELL placements would be a "
            "defect in the surface, not an observation about the market -- "
            "which is why the verdict now names which of the two it is.",
        ],
    }
