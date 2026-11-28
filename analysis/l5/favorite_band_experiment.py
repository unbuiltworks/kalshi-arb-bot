"""Late-window favorite accumulation by 10-point price band -- OFFLINE ONLY.

A SEPARATE EXPERIMENT. It reads L5's public Polymarket history, changes no
configuration, and has no path to an order. It is not imported by anything
under ``src/`` and is not in the executor image.

QUESTION. If a maker buys the FAVORITE in the 60c, 70c, 80c or 90c band late
in a BTC15m window and holds to settlement, is the CONSERVATIVE expected value
per contract positive after fees and adverse selection?

WHAT "CONSERVATIVE" MEANS HERE, stated before the numbers:

  * the unit of inference is the WINDOW (one window is one outcome), so every
    interval is window-clustered -- reusing ``favorite_accumulation``;
  * the estimate used is the LOWER 95% bound, not the mean;
  * it must hold in BOTH chronological halves (first 60% of windows = IS, last
    40% = OOS), because a band chosen after seeing all the data is a fitted one;
  * fees: a maker fill on PMUS has carried 0.0000 commission on every live
    fill; the taker counterfactual subtracts the deployed worst case (0.01);
  * adverse selection: the edge is measured on L5's ACTUAL fills, so the
    selection L5 suffered is already inside it. OUR selection is not -- and
    our own venue-settled stranded legs at favorite prices LOST. That
    cross-check is reported and it can only veto, never promote.

A band is ``SUPPORTED`` only when the lower bound net of the fee is positive
in IS and in OOS. Anything else is ``NOT SUPPORTED``. There is no third word.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5 import favorite_accumulation as FA

#: the four bands the owner named, as [low, high). 90c runs to 0.99 because a
#: 1.00 print carries no edge by construction.
BANDS: Tuple[Tuple[str, float, float], ...] = (
    ("60c", 0.60, 0.70), ("70c", 0.70, 0.80),
    ("80c", 0.80, 0.90), ("90c", 0.90, 0.995))

#: seconds from window open. "late" is measured, not assumed, at three cuts.
TIMINGS: Tuple[Tuple[str, float], ...] = (
    ("all", 0.0), ("late>=600s", 600.0), ("late>=720s", 720.0))

SPLIT = 0.6
CALIBRATION = "analysis/l5/price_level_calibration.json"


def select(trades: Sequence[Dict[str, Any]], lo: float, hi: float,
           t_min: float) -> List[Dict[str, Any]]:
    """L5 BUY fills inside one price band at or after ``t_min`` seconds."""
    return [t for t in trades
            if t["side"] == "BUY" and lo <= t["px"] < hi
            and t_min <= t["t_off"] <= 900.0]


def split_windows(trades: Sequence[Dict[str, Any]],
                  frac: float = SPLIT) -> Tuple[set, set]:
    """Chronological IS/OOS split on WINDOWS, never on trades."""
    order = sorted({(t["ws"], t["slug"]) for t in trades})
    cut = int(len(order) * frac)
    return ({s for _w, s in order[:cut]}, {s for _w, s in order[cut:]})


def conservative(edge: Optional[Dict[str, Any]], fee: float) -> Dict[str, Any]:
    """Lower-bound EV per contract net of ``fee``. None-safe."""
    if edge is None:
        return {"windows": 0, "mean": None, "lower": None,
                "conservative_net": None, "positive": False}
    lower = edge["ci95"][0]
    return {"windows": edge["windows"], "contracts": edge["contracts"],
            "mean": edge["window_mean_edge"], "lower": lower,
            "upper": edge["ci95"][1],
            "conservative_net": lower - fee,
            "positive": bool(lower - fee > 0)}


def evaluate_band(trades: Sequence[Dict[str, Any]], lo: float, hi: float,
                  t_min: float, is_w: set, oos_w: set) -> Dict[str, Any]:
    sel = select(trades, lo, hi, t_min)
    out: Dict[str, Any] = {}
    for label, fee in (("maker", FA.MAKER_FEE),
                       ("taker", FA.TAKER_FEE_PER_CONTRACT)):
        full = conservative(FA.window_clustered_edge(sel), fee)
        ins = conservative(FA.window_clustered_edge(
            [t for t in sel if t["slug"] in is_w]), fee)
        oos = conservative(FA.window_clustered_edge(
            [t for t in sel if t["slug"] in oos_w]), fee)
        out[label] = {"all": full, "IS": ins, "OOS": oos,
                      "verdict": ("SUPPORTED" if ins["positive"]
                                  and oos["positive"] else "NOT SUPPORTED")}
    return out


def own_cross_check(path: str = CALIBRATION) -> Dict[str, Any]:
    """Our own venue-settled legs by price region. Can veto, never promote."""
    try:
        with open(path) as fh:
            d = json.load(fh)
        own = d["our_own_realized_cross_check"]
        return {"available": True,
                "mid_0.60_0.80": own.get("mid_0.60_0.80"),
                "favourite_ge_0.80": own.get("favourite_ge_0.80"),
                "legs_needed_for_2_sigma":
                    d["power_required"]["legs_needed_for_2_sigma"],
                "legs_held_at_favourite_prices":
                    d["power_required"]["legs_we_have_at_favourite_prices"]}
    except Exception as e:                                     # noqa: BLE001
        return {"available": False, "error": "%s" % type(e).__name__}


def run(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    is_w, oos_w = split_windows(trades)
    grid: Dict[str, Dict[str, Any]] = {}
    for band, lo, hi in BANDS:
        grid[band] = {name: evaluate_band(trades, lo, hi, t_min, is_w, oos_w)
                      for name, t_min in TIMINGS}
    supported = [(b, tm) for b in grid for tm in grid[b]
                 if grid[b][tm]["maker"]["verdict"] == "SUPPORTED"]
    own = own_cross_check()
    own_lost = False
    for k in ("mid_0.60_0.80", "favourite_ge_0.80"):
        try:
            own_lost = own_lost or float((own.get(k) or {})["per_unit"]) < 0
        except Exception:                                      # noqa: BLE001
            pass
    return {
        "evidence_class": "L5 POLYMARKET HISTORY -- not PMUS, not our fills",
        "windows": {"IS": len(is_w), "OOS": len(oos_w)},
        "grid": grid,
        "l5_history_supported_cells": supported,
        "own_cross_check": own,
        "own_realized_contradicts": own_lost,
        "promotion": "NO PROMOTION",
        "why": [
            "Polymarket fills, not PMUS: zero PMUS fill-conditioned outcomes "
            "at favorite prices that confirm it",
            "directional hold-to-settlement: no authorized action class",
            "our own venue-settled favorite-region legs LOST (underpowered)"
            if own_lost else "own cross-check unavailable or not negative",
            "enabling it needs separate owner approval regardless of result",
        ],
    }


def _fmt(x: Optional[float]) -> str:
    return "   n/a  " if x is None else "%+.4f" % x


def main() -> None:
    outcomes = FA.load_outcomes()
    trades = FA.load_trades(outcomes)
    r = run(trades)
    print("evidence:", r["evidence_class"])
    print("windows IS/OOS:", r["windows"])
    print("%-5s %-11s %5s %8s %8s | %5s %8s | %5s %8s | %s" % (
        "band", "timing", "win", "mean", "lower", "IS n", "IS low",
        "OOS n", "OOS low", "maker / taker"))
    for band, cells in r["grid"].items():
        for tm, c in cells.items():
            m = c["maker"]
            print("%-5s %-11s %5d %8s %8s | %5d %8s | %5d %8s | %s / %s" % (
                band, tm, m["all"]["windows"], _fmt(m["all"]["mean"]),
                _fmt(m["all"]["lower"]), m["IS"]["windows"],
                _fmt(m["IS"]["lower"]), m["OOS"]["windows"],
                _fmt(m["OOS"]["lower"]), m["verdict"], c["taker"]["verdict"]))
    print("L5-history supported cells:", r["l5_history_supported_cells"])
    print("own cross-check:", json.dumps(r["own_cross_check"]))
    print("PROMOTION:", r["promotion"])
    for w in r["why"]:
        print("  -", w)


if __name__ == "__main__":
    main()
