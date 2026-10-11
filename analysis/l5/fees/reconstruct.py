"""Canonical fee reconstruction from the public tape.

THE RELATIONSHIP. On a BUY the cash actually debited exceeds notional:

    fee_usd = usdcSize - price * size

and the proposed model is

    fee_usd = FEE_RATE * price * (1 - price) * size,   FEE_RATE = 0.07

This module tests that model against EVERY source-supported fill rather than
asserting it, and reports where it fails.

FEE-BEARING / FEE-FREE IS THE TERMINOLOGY. The tape distinguishes fills that
carry the wedge from fills that do not. That is NOT maker/taker. No fill is
relabelled on this evidence; `L5_OPERATING_MODEL.md` 4.4 stands.

INCENTIVES STAY SEPARATE. Rebates and rewards are never netted into trading
economics here, and none is assumed until venue-credited.
"""
from __future__ import annotations

import collections
import datetime as dt
from typing import Any, Dict, Iterable, List, Optional

from analysis.l5.redemption import audit as A

FEE_RATE = 0.07
BEARING_CUT = 0.5        # in units of FEE_RATE; the distribution is bimodal at 0 and 1
TOL_USD_PER_SHARE = 1e-6


def _month(ts: int) -> str:
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m")


def fills(cohorts: Optional[Iterable[str]] = None) -> List[dict]:
    """Every TRADE row with the wedge measured and the model evaluated."""
    keep = set(cohorts) if cohorts else None
    out: List[dict] = []
    for r in A.iter_activity():
        if str(r.get("type") or "").upper() != "TRADE":
            continue
        w = A.window_of(r)
        if not w:
            continue
        cohort = f"{w[0].upper()}{w[1]}"
        if keep and cohort not in keep:
            continue
        px = float(r.get("price") or 0)
        sz = float(r.get("size") or 0)
        usd = float(r.get("usdcSize") or 0)
        if not (sz > 0 and 0 < px < 1):
            continue
        side = str(r.get("side") or "").upper()
        # signed so a COST is positive on both sides: a buyer pays more than
        # notional, a seller receives less.
        wedge = (usd - px * sz) if side == "BUY" else (px * sz - usd)
        per_share = wedge / sz
        pred = FEE_RATE * px * (1 - px)
        out.append({
            "cohort": cohort, "t": int(r.get("timestamp") or 0),
            "month": _month(int(r.get("timestamp") or 0)),
            "side": side, "price": px, "size": sz, "usdc": usd,
            "notional": px * sz,
            "fee_usd": wedge,
            "fee_per_share": per_share,
            "fee_rate_implied": per_share / (px * (1 - px)),
            "model_fee_usd": pred * sz,
            "model_abs_err_per_share": abs(per_share - pred),
            "fee_bearing": (per_share / (px * (1 - px))) > FEE_RATE * BEARING_CUT,
        })
    out.sort(key=lambda x: x["t"])
    return out


def model_test(rows: List[dict]) -> Dict[str, Any]:
    """Does 0.07*p*(1-p) hold on the fills that carry a wedge at all?"""
    bearing = [r for r in rows if r["fee_bearing"]]
    free = [r for r in rows if not r["fee_bearing"]]
    err = sorted(r["model_abs_err_per_share"] for r in bearing)
    free_abs = sorted(abs(r["fee_per_share"]) for r in free)
    def qq(xs, p): return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    return {
        "n_fills": len(rows),
        "n_fee_bearing": len(bearing),
        "n_fee_free": len(free),
        "fee_bearing_pct": (round(100.0 * len(bearing) / len(rows), 2) if rows else None),
        "model": f"fee_usd = {FEE_RATE} * p * (1-p) * shares",
        "bearing_abs_err_per_share_p50": qq(err, .50),
        "bearing_abs_err_per_share_p95": qq(err, .95),
        "bearing_abs_err_per_share_max": (err[-1] if err else None),
        "bearing_within_1e-6": (sum(1 for e in err if e < TOL_USD_PER_SHARE)
                                if err else 0),
        "free_abs_fee_per_share_max": (free_abs[-1] if free_abs else None),
        "labels": ("fee-bearing vs fee-free is what the tape shows. It is NOT "
                   "maker vs taker and no fill is relabelled on this evidence."),
    }


def by(rows: List[dict], key: str, redeem: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    g: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    out: Dict[str, Any] = {}
    for k, rs in g.items():
        buys = [r for r in rs if r["side"] == "BUY"]
        gross = sum(r["notional"] for r in buys)
        fees = sum(r["fee_usd"] for r in rs)
        cash_out = sum(r["usdc"] for r in buys)
        cash_in = sum(r["usdc"] for r in rs if r["side"] == "SELL")
        red = redeem.get(k, {"usd": 0.0, "n": 0})
        out[k] = {
            "fill_count": len(rs),
            "fee_bearing_pct": round(100.0 * sum(1 for r in rs if r["fee_bearing"]) / len(rs), 2),
            "gross_acquisition_usd": round(gross, 2),
            "reconstructed_fees_usd": round(fees, 2),
            "fees_bp_of_gross": (round(1e4 * fees / gross, 1) if gross else None),
            "settlement_redemption_usd": round(red["usd"], 2),
            "redeem_rows": red["n"],
            "trading_net_usd": round(red["usd"] + cash_in - cash_out, 2),
            "incentives_usd": None,
            "incentives_reason": ("kept separate and never netted in; none "
                                 "assumed until venue-credited"),
        }
    return dict(sorted(out.items()))


def redeem_by(key: str, cohorts: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, float]]:
    keep = set(cohorts) if cohorts else None
    g: Dict[str, Dict[str, float]] = collections.defaultdict(lambda: {"usd": 0.0, "n": 0})
    for r in A.iter_activity():
        if str(r.get("type") or "").upper() != "REDEEM":
            continue
        w = A.window_of(r)
        if not w:
            continue
        cohort = f"{w[0].upper()}{w[1]}"
        if keep and cohort not in keep:
            continue
        k = cohort if key == "cohort" else _month(int(r.get("timestamp") or 0))
        g[k]["usd"] += float(r.get("usdcSize") or 0)
        g[k]["n"] += 1
    return g
