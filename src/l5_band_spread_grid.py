"""Does the live universe contain a cell where the economics clear?

OWNER 2026-09-11: "We aren't looking for 'which band has the most trades';
we're looking for states where the spread itself is large enough to pay for the
observed one-sided-fill risk... If there is no meaningful cell where the
economics clear, then we should stop trying to force this exact venue/market
class and use the L5 engine elsewhere."

Run against `analysis/l5/BAND_SPREAD_PREREGISTRATION.md`.

WHAT THIS MEASURES, AND WHAT IT REFUSES TO
------------------------------------------
Every candidate the selector ADMITTED is logged with its book: bid, ask,
bid_touch_qty, state, p_buy. That is the live universe as the engine saw it,
and the grid over it needs no model of anything.

What it refuses to produce is a both-fill rate. We have never quoted both sides
at once, so there is no tape of it; the only way to a number would be to
multiply two one-sided fill rates, and independence is exactly what the
two-sided-flow hypothesis denies. A modelled both-fill rate would assume away
the question, so this module does not compute one, and a test enforces that.

THE ARITHMETIC THE GRID IS BUILT ON
-----------------------------------
From the entry study: we open as a maker at our own side's touch, so
completing at the complement's ask costs exactly $1.00 and all paired edge must
come from a maker complement fill inside the spread. Pairing both legs
passively costs `1 - spread`, so

    attainable gross edge  =  spread x q

and against a measured -$1.9120 per stranded market, the residual incidence a
cell can tolerate is `edge / (edge + 1.9120)`. A cell clears when that reaches
the 28.2% we actually run at.
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
from decimal import Decimal as D
from typing import Any, Iterable, Mapping, Optional

from src.l5_episode_ledger import iter_campaign_rows

# Measured population constants. They SCALE the bar; nothing is fitted to them.
STRANDED_COST = D("1.9120")      # mean cost of one stranded market, R5F cohort
OBSERVED_INCIDENCE = 0.2821      # residual incidence over filled episodes

# Pre-registered, nested as the owner named them.
BANDS = (("45-55", 0.45, 0.55), ("40-60", 0.40, 0.60),
         ("35-65", 0.35, 0.65), ("30-70", 0.30, 0.70))
SPREAD_BUCKETS = (("a <=0.01", 0.0, 0.01), ("b 0.01-0.02", 0.01, 0.02),
                  ("c 0.02-0.03", 0.02, 0.03), ("d 0.03-0.05", 0.03, 0.05),
                  ("e >0.05", 0.05, 9.99))
SIZES = (10.3, 5.75, 20.0)       # median matched; median planned; a larger rung


def _f(v: Any) -> Optional[float]:
    try:
        return float(str(v))
    except Exception:                                   # noqa: BLE001
        return None


def observations(paths: Iterable[str]) -> list:
    """One row per ADMITTED candidate observation, with its book.

    Admitted, not chosen: the question is what the universe offered, not what
    we picked out of it. Refused candidates are excluded because the strict
    evidence gate could not price them -- that exclusion is named in the
    coverage block rather than left implicit."""
    out = []
    for ts, obj in iter_campaign_rows(paths):
        if obj.get("kind") != "selection":
            continue
        for a in obj.get("admitted") or []:
            if not isinstance(a, Mapping):
                continue
            bid, ask = _f(a.get("bid")), _f(a.get("ask"))
            if bid is None or ask is None or ask <= bid:
                continue
            out.append({
                "t": ts, "slug": a.get("slug"),
                "bid": bid, "ask": ask,
                "mid": round((bid + ask) / 2, 6),
                "spread": round(ask - bid, 6),
                "bid_touch_qty": _f(a.get("bid_touch_qty")),
                "state": a.get("state"), "state_n": a.get("state_n"),
                "p_buy": a.get("p_buy"),
                "pair_at_touch": _f(a.get("pair_at_touch")),
            })
    return out


def in_band(mid: float, lo: float, hi: float) -> bool:
    return lo <= mid <= hi


def bucket_of(spread: float) -> str:
    for name, lo, hi in SPREAD_BUCKETS:
        if lo < spread <= hi or (lo == 0.0 and spread <= hi):
            return name
    return SPREAD_BUCKETS[-1][0]


def break_even_incidence(spread: float, q: float) -> float:
    """The residual rate this cell's edge could tolerate.

    Note what is NOT here: no fill probability, no both-fill assumption. This
    is the edge the book makes available if the pair completes passively,
    against the measured cost when it does not."""
    edge = D(str(spread)) * D(str(q))
    return float(edge / (edge + STRANDED_COST))


def required_spread(q: float) -> float:
    """Spread needed at size q for the cell to clear at observed incidence."""
    need = D(str(OBSERVED_INCIDENCE / (1 - OBSERVED_INCIDENCE))) * STRANDED_COST
    return float(need / D(str(q)))


def _median(vals: list) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def cell(rows: list, q: float) -> dict:
    spreads = [r["spread"] for r in rows]
    med = _median(spreads)
    depth = _median([r["bid_touch_qty"] for r in rows
                     if r["bid_touch_qty"] is not None])
    out = {
        "observations": len(rows),
        "markets": len(({r["slug"] for r in rows})),
        "median_spread": med,
        "median_bid_touch_qty": depth,
        "best_pair_cost": (round(1 - med, 4) if med is not None else None),
        "attainable_edge_usd": (round(med * q, 4) if med is not None else None),
    }
    if med is None:
        out.update({"break_even_incidence": None, "clears": None})
        return out
    be = break_even_incidence(med, q)
    out["break_even_incidence"] = round(be, 4)
    out["clears"] = be >= OBSERVED_INCIDENCE
    # how far the cell is from clearing, in spread terms -- more useful than a
    # bare False when every cell is False
    out["spread_shortfall_x"] = round(required_spread(q) / med, 2) if med else None
    return out


def grid(rows: list, q: float) -> dict:
    out = {}
    for name, lo, hi in BANDS:
        band_rows = [r for r in rows if in_band(r["mid"], lo, hi)]
        cells = {}
        for bname, _, _ in SPREAD_BUCKETS:
            cells[bname] = cell([r for r in band_rows
                                 if bucket_of(r["spread"]) == bname], q)
        out[name] = {"all": cell(band_rows, q), "by_spread": cells}
    return out


def best_cells(rows: list, q: float, min_markets: int = 5) -> list:
    """Cells that clear AND are not a rounding error.

    Pre-registration 4: a cell holding a handful of observations across one or
    two markets is not an opportunity set, so distinct MARKETS governs."""
    found = []
    g = grid(rows, q)
    for band, blk in g.items():
        for bucket, c in blk["by_spread"].items():
            if c.get("clears") and (c.get("markets") or 0) >= min_markets:
                found.append({"band": band, "spread_bucket": bucket, **c})
    return found


def study(paths: Iterable[str]) -> dict:
    rows = observations(paths)
    return {
        "question": ("does the live universe contain a band x spread cell "
                     "whose spread pays for the observed one-sided-fill risk?"),
        "prereg": "analysis/l5/BAND_SPREAD_PREREGISTRATION.md",
        "coverage": {
            "admitted_observations": len(rows),
            "distinct_markets": len({r["slug"] for r in rows}),
            "excluded": ("refused candidates: the strict evidence gate could "
                         "not price them, so their books are not comparable"),
        },
        "constants": {
            "stranded_cost_usd": str(STRANDED_COST),
            "observed_residual_incidence": OBSERVED_INCIDENCE,
            "source": "measured R5F settled cohort; scales the bar, not fitted",
        },
        "required_spread_by_size": {str(q): round(required_spread(q), 4)
                                    for q in SIZES},
        "grids": {str(q): grid(rows, q) for q in SIZES},
        "clearing_cells": {str(q): best_cells(rows, q) for q in SIZES},
        "not_computed": {
            "both_fill_rate": (
                "NOT OBSERVABLE. We have never quoted both sides at once, so "
                "there is no tape of it. The only route to a number is the "
                "product of two one-sided fill rates, and independence is "
                "exactly what the two-sided-flow hypothesis denies -- so that "
                "number would assume away the question being asked."),
        },
    }


def render_markdown(res: Mapping[str, Any]) -> str:
    L = ["# Band x spread: does any cell clear?", ""]
    cov = res["coverage"]
    L.append(f"{cov['admitted_observations']} admitted observations across "
             f"{cov['distinct_markets']} distinct markets.")
    L.append("")
    for q, g in res["grids"].items():
        need = res["required_spread_by_size"][q]
        L += [f"## size q = {q} contracts (required spread {need})", "",
              "| band | bucket | obs | mkts | med spread | edge $ | "
              "break-even incidence | clears |", "|---|---|---:|---:|---:|---:|---:|---|"]
        for band, blk in g.items():
            for bucket, c in blk["by_spread"].items():
                if not c["observations"]:
                    continue
                L.append(f"| {band} | {bucket} | {c['observations']} | "
                         f"{c['markets']} | {c['median_spread']} | "
                         f"{c['attainable_edge_usd']} | "
                         f"{c['break_even_incidence']} | "
                         f"{'YES' if c['clears'] else 'no'} |")
        L.append("")
    return "\n".join(L)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    paths: list = []
    for pat in args.logs:
        paths.extend(sorted(_glob.glob(pat)) or [pat])
    res = study(paths)
    if args.out:
        from pathlib import Path
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "l5_band_spread_grid.json").write_text(
            json.dumps(res, indent=2, default=str) + "\n")
        (out / "BAND_SPREAD_GRID.md").write_text(render_markdown(res))
    print(render_markdown(res))
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(main())
