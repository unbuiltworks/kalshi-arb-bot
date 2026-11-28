"""What is one resting maker fill worth at settlement, by the state it was
quoted in?

OFFLINE. A portfolio's settlement P&L is the sum, over every contract
bought, of (settlement - price); pairing never enters it. So whether ANY
controller built from maker buys can be profitable comes down to one
measurable thing: is there a state -- price, time, depth, momentum, model
edge -- in which a resting bid's fills are worth more than they cost?

This quotes one bid a side continuously at a fixed depth behind the print,
fills it only when a later print trades `through` ticks beyond it, and
averages (settlement - price) per fill by the state the quote was live in.
Intervals are window-clustered. Same paths, ticks and fill rule as
`edge_comparison`. Places nothing; imported by nothing under `src/`.
"""
from __future__ import annotations

import collections
import math
import random
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

TICK = 0.01
EPS = 1e-9


def _past(path: Sequence[Tuple[float, float]], i: int, lag: float
          ) -> Optional[float]:
    t = path[i][0] - lag
    j = i
    while j > 0 and path[j][0] > t:
        j -= 1
    return path[j][1] if path[j][0] <= t else None


def fills(path: Sequence[Tuple[float, float]], winner: str, *, depth: int,
          through: int = 1,
          model: Optional[Callable[[float], Optional[float]]] = None
          ) -> List[Dict[str, Any]]:
    """Every fill of a continuously re-quoted bid on each side."""
    out: List[Dict[str, Any]] = []
    rest: Dict[str, Tuple[float, float, Dict[str, Any]]] = {}
    for i, (t, m) in enumerate(path):
        if t > 840.0:
            break
        m60 = _past(path, i, 60.0)
        p = model(t) if model is not None else None
        for side in ("UP", "DOWN"):
            mk = m if side == "UP" else 1.0 - m
            o = rest.get(side)
            if o is not None and t > o[1] and \
                    mk <= o[0] - through * TICK + EPS:
                won = (winner == "Up") == (side == "UP")
                out.append(dict(o[2], side=side, price=o[0], t=o[1],
                                value=(1.0 if won else 0.0) - o[0]))
                rest.pop(side)
                o = None
            q = math.floor((mk - 0.005 - depth * TICK) / TICK + EPS) * TICK
            if not (TICK - EPS <= q <= 0.99 + EPS) or t < 5.0:
                rest.pop(side, None)
                continue
            mom = None if m60 is None else (
                mk - (m60 if side == "UP" else 1.0 - m60))
            fair = None if p is None else (p if side == "UP" else 1.0 - p)
            state = {"momentum": mom,
                     "edge": None if fair is None else fair - q}
            placed = t if (o is None or abs(o[0] - q) > EPS) else o[1]
            rest[side] = (q, placed, state)
    return out


def price_band(q: float) -> str:
    for lo, hi in ((0, .2), (.2, .4), (.4, .5), (.5, .6), (.6, .8), (.8, 1.01)):
        if lo <= q < hi:
            return "price %.1f-%.1f" % (lo, min(hi, 1.0))
    return "price ?"


def time_band(t: float) -> str:
    return ("t 0-300" if t < 300 else "t 300-600" if t < 600 else "t 600-840")


def momentum_band(x: Optional[float]) -> str:
    if x is None:
        return "momentum n/a"
    return ("momentum <= -5c" if x <= -0.05 else "momentum -5..-1c"
            if x < -0.01 else "momentum flat" if x <= 0.01
            else "momentum +1..+5c" if x < 0.05 else "momentum >= +5c")


def edge_band(x: Optional[float]) -> Optional[str]:
    if x is None:
        return None
    return ("model edge < -10c" if x < -0.10 else "model edge -10..-3c"
            if x < -0.03 else "model edge -3..+3c" if x <= 0.03
            else "model edge +3..+10c" if x <= 0.10 else "model edge > +10c")


def cell_stats(by_window: Dict[str, List[float]], n_boot: int = 600,
               seed: int = 3) -> Dict[str, Any]:
    ws = list(by_window.values())
    n = sum(x[1] for x in ws)
    mean = sum(x[0] for x in ws) / n
    rng = random.Random(seed)
    bs = []
    for _ in range(n_boot):
        a = b = 0.0
        for _i in range(len(ws)):
            x = ws[rng.randrange(len(ws))]
            a += x[0]
            b += x[1]
        if b:
            bs.append(a / b)
    bs.sort()
    return {"mean": mean, "lo": bs[int(.025 * len(bs))],
            "hi": bs[int(.975 * len(bs)) - 1], "fills": int(n),
            "windows": len(ws)}


def surface(ps: Dict[str, Any], slugs: Sequence[str], *, depth: int,
            through: int, models: Optional[Dict[str, Any]] = None,
            min_fills: int = 60) -> Dict[str, Dict[str, Any]]:
    cells: Dict[str, Dict[str, List[float]]] = collections.defaultdict(
        lambda: collections.defaultdict(lambda: [0.0, 0]))
    for s in slugs:
        mdl = models.get(s) if models else None
        for f in fills(ps[s]["path"], ps[s]["winner"], depth=depth,
                       through=through, model=mdl):
            keys = ["ALL", price_band(f["price"]), time_band(f["t"]),
                    momentum_band(f["momentum"]),
                    price_band(f["price"]) + " | " + time_band(f["t"])]
            eb = edge_band(f["edge"])
            if eb:
                keys += [eb, eb + " | " + momentum_band(f["momentum"])]
            for k in keys:
                c = cells[k][s]
                c[0] += f["value"]
                c[1] += 1
    return {k: cell_stats(v) for k, v in cells.items()
            if sum(x[1] for x in v.values()) >= min_fills}


def run() -> Dict[str, Any]:
    from analysis.l5 import edge_comparison as EC
    from analysis.l5 import favorite_accumulation as FA
    from analysis.l5 import pair_completion_replay as PR
    ps = PR.paths(FA.load_trades(FA.load_outcomes()))
    mf = EC.load_model()
    models = {s: fn for s, fn in ((s, mf(s, w["ws"])) for s, w in ps.items())
              if fn is not None}
    order = sorted(ps, key=lambda s: ps[s]["ws"])
    cut = int(len(order) * 0.6)
    res: Dict[str, Any] = {"windows": {"IS": cut, "OOS": len(order) - cut},
                           "surfaces": {}}
    for through in (1, 0):
        for depth in (0, 2, 5, 10):
            for half, sl in (("IS", order[:cut]), ("OOS", order[cut:])):
                res["surfaces"]["through%d/depth%d/%s" % (
                    through, depth, half)] = surface(
                        ps, sl, depth=depth, through=through, models=models)
    # a cell counts only if its LOWER bound is above zero in BOTH halves
    res["cells_positive_in_both_halves"] = {}
    for through in (1, 0):
        for depth in (0, 2, 5, 10):
            a = res["surfaces"]["through%d/depth%d/IS" % (through, depth)]
            b = res["surfaces"]["through%d/depth%d/OOS" % (through, depth)]
            res["cells_positive_in_both_halves"][
                "through%d/depth%d" % (through, depth)] = sorted(
                    k for k in a if k in b and a[k]["lo"] > 0
                    and b[k]["lo"] > 0)
    return res


def main() -> None:
    import json
    import os
    r = run()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "maker_fill_value_surface.json"), "w") as fh:
        json.dump(r, fh, indent=1, sort_keys=True)
    print("windows:", r["windows"])
    for key, cells in r["surfaces"].items():
        print("\n== %s ==" % key)
        for k in sorted(cells):
            c = cells[k]
            print("  %-44s %+.4f [%+.4f, %+.4f] fills %5d win %3d%s" % (
                k, c["mean"], c["lo"], c["hi"], c["fills"], c["windows"],
                "   <== lower bound > 0" if c["lo"] > 0 else ""))
    print("\ncells with a positive lower bound in BOTH halves:")
    for k, v in r["cells_positive_in_both_halves"].items():
        print("  %-18s %s" % (k, v or "none"))


if __name__ == "__main__":
    main()
