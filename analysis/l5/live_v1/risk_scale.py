"""L5_CONTINUOUS_INVENTORY_LIVE_V1 — the risk scale, and whether it keeps the shape.

THE R1 DISTORTION, NAMED. R1 sized every order at the venue minimum, 0.02
contracts, whatever L5 did. Against an L5 median of 12.5 shares that is an
effective scale of ~0.0016, and the live fidelity gate measured exactly that:
`median_executed_over_l5_p50 = 0.002`. When every order is the same size the
inventory trajectory is not scaled down, it is FLATTENED -- relative sizing, the
thing the model actually predicts, is gone. A per-order nickel cap cannot
express "trade L5's shape, smaller".

WHAT REPLACES IT. One multiplier `k`, applied to L5's inferred desired size, and
derived from the risk ENVELOPE rather than picked:

    q_ours(t) = Q( k * q_L5(t) )

where Q is the venue's quantization (round to minimumTradeQty, floor at the
minimum, clamp at a per-leg bound). `k` is the largest scale whose worst
historical window still fits inside every owner-set dollar ceiling:

    k_residual = E_resid / max_w( peak residual DOLLARS in window w )
    k_exposure = E_expo  / max_w( total notional deployed in window w )
    k_daily    = L_day   / ( worst-window loss * windows per day )
    k          = min(...)          <- and the binding one is named

THE THING THAT HAD TO BE CHECKED, NOT ASSUMED. Quantization is not linear. Below
some scale every distinct L5 size collapses onto the venue minimum and the
trajectory shape dies -- which is precisely what R1 did. So there is a FLOOR on
k below which no envelope is small enough to be honest about, and finding it is
the point of this module. If the floor implies a bigger envelope than the owner
wants, that is a real finding to report, not a number to tune.

Scope caveat, stated rather than buried: the canonical corpus is L5's BTC/ETH
up-down windows. It is the same corpus the deployed behavioural model was fit
on, so it is the right basis for a shape claim -- but it is not sports, and this
module does not pretend it is.
"""
from __future__ import annotations

import csv
import gzip
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SEQUENCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "l5_canonical_sequence.csv.gz")

# The venue's quantization, from the live probe's own market spec.
VENUE_MIN_QTY = 0.01
VENUE_QTY_STEP = 0.01


# --------------------------------------------------------------------------
# the corpus

def load_windows(path: str = SEQUENCE,
                 limit: Optional[int] = None) -> Dict[str, List[dict]]:
    """L5's canonical trades grouped by window, in trade order."""
    out: Dict[str, List[dict]] = {}
    with gzip.open(path, "rt") as fh:
        for row in csv.DictReader(fh):
            try:
                rec = {
                    "seq": int(row["trade_seq"]),
                    "t": int(row["timestamp"]),
                    "outcome": row["outcome"],
                    "side": row["side"],
                    "price": float(row["price"]),
                    "qty": float(row["quantity"]),
                    "dollars": float(row["dollars"]),
                    "cum_up_qty": float(row["cum_up_qty"] or 0),
                    "cum_dn_qty": float(row["cum_down_qty"] or 0),
                    "cum_up_spend": float(row["cum_up_spend"] or 0),
                    "cum_dn_spend": float(row["cum_down_spend"] or 0),
                    "matched": float(row["matched_qty"] or 0),
                    "residual": float(row["residual_qty_signed"] or 0),
                }
            except (TypeError, ValueError):
                continue
            out.setdefault(row["window_id"], []).append(rec)
        for w in out.values():
            w.sort(key=lambda r: r["seq"])
    if limit is not None:
        out = dict(list(out.items())[:limit])
    return out


def window_envelope(trades: Sequence[dict]) -> dict:
    """What ONE window of L5 behaviour would have cost, in dollars.

    `peak_residual_usd` is the worst-case directional loss: a residual that
    settles against you loses its full cost, so the exposure is the residual
    quantity valued at what was paid for it, taken at its WORST moment in the
    window rather than at the end.
    """
    peak_resid_usd = 0.0
    peak_resid_qty = 0.0
    for r in trades:
        resid = abs(r["residual"])
        up_q, dn_q = r["cum_up_qty"], r["cum_dn_qty"]
        # average cost of the side currently in surplus
        if r["residual"] > 0 and up_q > 0:
            px = r["cum_up_spend"] / up_q
        elif r["residual"] < 0 and dn_q > 0:
            px = r["cum_dn_spend"] / dn_q
        else:
            px = 0.0
        usd = resid * px
        if usd > peak_resid_usd:
            peak_resid_usd = usd
        if resid > peak_resid_qty:
            peak_resid_qty = resid
    last = trades[-1]
    return {
        "trades": len(trades),
        "notional_usd": last["cum_up_spend"] + last["cum_dn_spend"],
        "peak_residual_qty": peak_resid_qty,
        "peak_residual_usd": peak_resid_usd,
        "final_residual_qty": abs(last["residual"]),
        "median_qty": sorted(r["qty"] for r in trades)[len(trades) // 2],
    }


def corpus_envelope(windows: Dict[str, List[dict]]) -> dict:
    """The distribution the scale has to survive. Both the hard maximum and
    the high percentiles, because a single freak window should be visible as
    a choice rather than silently setting the whole programme's size."""
    rows = [window_envelope(t) for t in windows.values() if t]
    def pct(key: str, p: float) -> float:
        vals = sorted(r[key] for r in rows)
        return vals[min(len(vals) - 1, int(p * len(vals)))]
    return {
        "windows": len(rows),
        "notional_usd": {"p50": pct("notional_usd", .50),
                         "p90": pct("notional_usd", .90),
                         "p99": pct("notional_usd", .99),
                         "max": max(r["notional_usd"] for r in rows)},
        "peak_residual_usd": {"p50": pct("peak_residual_usd", .50),
                              "p90": pct("peak_residual_usd", .90),
                              "p99": pct("peak_residual_usd", .99),
                              "max": max(r["peak_residual_usd"] for r in rows)},
        "peak_residual_qty": {"p50": pct("peak_residual_qty", .50),
                              "p90": pct("peak_residual_qty", .90),
                              "p99": pct("peak_residual_qty", .99),
                              "max": max(r["peak_residual_qty"] for r in rows)},
    }


# --------------------------------------------------------------------------
# the scale

def quantize(q: float, min_qty: float = VENUE_MIN_QTY,
             step: float = VENUE_QTY_STEP,
             max_leg: Optional[float] = None) -> float:
    """The venue's own size grid. Rounds to the step, floors at the venue
    minimum (an order below it cannot be placed at all), clamps at a per-leg
    bound when one is set."""
    if q <= 0:
        return 0.0
    n = round(q / step)
    out = max(min_qty, n * step)
    if max_leg is not None:
        out = min(out, max_leg)
    return round(out, 10)


def risk_scale(envelope: dict, *, max_residual_usd: float,
               max_exposure_usd: float, daily_loss_stop_usd: float,
               windows_per_day: int = 1,
               basis: str = "max") -> dict:
    """The multiplier, and which ceiling produced it.

    `basis` picks which point of the historical distribution must fit: "max"
    is the hard guarantee (no historical window could ever have breached),
    "p99" accepts that the worst 1% of windows would have, which is a real
    risk decision and is therefore never the default.
    """
    notional = envelope["notional_usd"][basis]
    resid = envelope["peak_residual_usd"][basis]
    k_resid = max_residual_usd / resid if resid > 0 else float("inf")
    k_expo = max_exposure_usd / notional if notional > 0 else float("inf")
    # worst case for a campaign is losing the whole peak residual
    per_day = resid * max(1, windows_per_day)
    k_daily = daily_loss_stop_usd / per_day if per_day > 0 else float("inf")
    cands = {"residual_ceiling": k_resid, "exposure_ceiling": k_expo,
             "daily_loss_stop": k_daily}
    binding = min(cands, key=lambda n: cands[n])
    return {
        "k": cands[binding], "binding_constraint": binding,
        "candidates": cands, "basis": basis,
        "inputs": {"max_residual_usd": max_residual_usd,
                   "max_exposure_usd": max_exposure_usd,
                   "daily_loss_stop_usd": daily_loss_stop_usd,
                   "windows_per_day": windows_per_day},
        "historical_basis": {"notional_usd": notional,
                             "peak_residual_usd": resid},
    }


# --------------------------------------------------------------------------
# does the shape survive?

def _spearman(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    n = len(a)
    if n < 3:
        return None
    def rank(xs):
        order = sorted(range(n), key=lambda i: xs[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    ra, rb = rank(list(a)), rank(list(b))
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** .5
    db = sum((y - mb) ** 2 for y in rb) ** .5
    if da == 0 or db == 0:
        return None                # one side is constant: shape destroyed
    return num / (da * db)


def scaled_path(trades: Sequence[dict], k: float,
                min_qty: float = VENUE_MIN_QTY,
                max_leg: Optional[float] = None) -> dict:
    """Replay the window at scale k and report the executed path."""
    qs, inv, cum = [], [], 0.0
    for r in trades:
        q = quantize(k * r["qty"], min_qty=min_qty, max_leg=max_leg)
        qs.append(q)
        cum += q if r["outcome"] == "Up" else -q
        inv.append(cum)
    return {"qty": qs, "inventory": inv}


def shape_fidelity(trades: Sequence[dict], k: float,
                   min_qty: float = VENUE_MIN_QTY,
                   max_leg: Optional[float] = None) -> dict:
    """How much of L5's RELATIVE path survives quantization at scale k.

    Three separate questions, because they fail in different ways:
      size_rank      -- is relative sizing preserved at all? (Spearman)
      path_mad       -- after normalising BOTH paths by their own peak, how
                        far apart are the inventory trajectories? Scale is
                        divided out on purpose: we are asking about SHAPE.
      collapse_share -- fraction of orders pinned to the venue minimum. This
                        is the R1 failure mode, and at 1.0 the other two are
                        meaningless.
    """
    ours = scaled_path(trades, k, min_qty=min_qty, max_leg=max_leg)
    l5_q = [r["qty"] for r in trades]
    l5_inv, cum = [], 0.0
    for r in trades:
        cum += r["qty"] if r["outcome"] == "Up" else -r["qty"]
        l5_inv.append(cum)

    collapse = sum(1 for q in ours["qty"] if q <= min_qty) / len(ours["qty"])

    def norm(path):
        peak = max((abs(x) for x in path), default=0.0)
        return [x / peak for x in path] if peak > 0 else [0.0] * len(path)
    na, nb = norm(l5_inv), norm(ours["inventory"])
    path_mad = sum(abs(x - y) for x, y in zip(na, nb)) / len(na)

    return {
        "k": k, "n": len(trades),
        "size_rank": _spearman(l5_q, ours["qty"]),
        "path_mad": path_mad,
        "collapse_share": collapse,
        "l5_notional": sum(r["dollars"] for r in trades),
        "our_notional": sum(q * r["price"] for q, r in zip(ours["qty"], trades)),
    }


def sweep(windows: Dict[str, List[dict]], ks: Iterable[float],
          min_trades: int = 6, min_qty: float = VENUE_MIN_QTY,
          max_leg: Optional[float] = None) -> List[dict]:
    """Shape fidelity against scale, over every window long enough to have a
    shape at all. A 2-trade window cannot evidence a trajectory."""
    usable = [t for t in windows.values() if len(t) >= min_trades]
    out = []
    for k in ks:
        rows = [shape_fidelity(t, k, min_qty=min_qty, max_leg=max_leg)
                for t in usable]
        ranks = [r["size_rank"] for r in rows if r["size_rank"] is not None]
        out.append({
            "k": k,
            "windows_scored": len(rows),
            "windows_with_rank": len(ranks),
            "size_rank_mean": (sum(ranks) / len(ranks)) if ranks else None,
            "size_rank_p10": (sorted(ranks)[int(.1 * len(ranks))]
                              if ranks else None),
            "path_mad_mean": sum(r["path_mad"] for r in rows) / len(rows),
            "path_mad_p90": sorted(r["path_mad"] for r in rows)[
                int(.9 * len(rows))],
            "collapse_share_mean": sum(r["collapse_share"] for r in rows) / len(rows),
            "fully_collapsed_windows": sum(
                1 for r in rows if r["collapse_share"] >= 0.999) / len(rows),
            "median_our_notional": sorted(
                r["our_notional"] for r in rows)[len(rows) // 2],
        })
    return out


# The bar a scale must clear to be called shape-preserving. Set BEFORE the
# sweep was run, so the floor is a measurement rather than a choice made after
# seeing which number looked good.
SHAPE_THRESHOLDS = {
    "size_rank_mean_min": 0.90,
    "path_mad_mean_max": 0.05,
    "fully_collapsed_windows_max": 0.05,
}


def shape_preserved(row: dict, thresholds: dict = SHAPE_THRESHOLDS) -> bool:
    return (row["size_rank_mean"] is not None
            and row["size_rank_mean"] >= thresholds["size_rank_mean_min"]
            and row["path_mad_mean"] <= thresholds["path_mad_mean_max"]
            and row["fully_collapsed_windows"]
            <= thresholds["fully_collapsed_windows_max"])


def shape_floor(sweep_rows: Sequence[dict],
                thresholds: dict = SHAPE_THRESHOLDS) -> Optional[dict]:
    """The smallest swept k that still preserves the shape."""
    ok = [r for r in sweep_rows if shape_preserved(r, thresholds)]
    return min(ok, key=lambda r: r["k"]) if ok else None
