"""Why is BTC15m the exception? Per-window decomposition on corrected accounting.

CORRECTED ACCOUNTING MEANS THREE THINGS, all established elsewhere and treated
as authoritative here:

  * COST IS `usdcSize`, never price*size. The two differ by exactly
    0.07*p*(1-p) per share on a fee-bearing fill and by nothing on a fee-free
    one. Using usdcSize makes the matched/residual decomposition reconcile to
    realized cash exactly.
  * A MISSING REDEEM ROW IS A LOSS. Redemption is complete whenever it pays, so
    a long-settled window with no REDEEM row is one where the held side lost.
    Only the capture tail is indeterminate.
  * FEE-BEARING / FEE-FREE IS THE LABEL. It is not maker/taker; nothing here
    relabels it.

WHAT IS NOT AVAILABLE, and is named rather than imputed: L5's incentive credits
cannot be attributed per window from the public tape -- the venue publishes no
per-window reward row for another trader's account. Spread, depth, queue
position and the intra-window book are likewise absent.
"""
from __future__ import annotations

import collections
import statistics
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.redemption import audit as A

FEE_RATE = 0.07
BEARING_CUT = FEE_RATE * 0.5      # the wedge/[p(1-p)] ratio is bimodal at 0 and 0.07
TRUNCATION_S = 86400


def _bearing(px: float, sz: float, usd: float) -> bool:
    den = px * (1.0 - px)
    if den <= 0 or sz <= 0:
        return False
    return ((usd - px * sz) / sz) / den > BEARING_CUT


def build(cohort: str = "BTC15m") -> List[dict]:
    buys: Dict[str, list] = collections.defaultdict(list)
    meta: Dict[str, tuple] = {}
    redeem: Dict[str, dict] = {}
    sells: Dict[str, float] = collections.defaultdict(float)
    for r in A.iter_activity():
        t = str(r.get("type") or "").upper()
        cid = str(r.get("conditionId") or "")
        w = A.window_of(r)
        if not w or not cid:
            continue
        meta.setdefault(cid, w)
        if t == "TRADE":
            px = float(r.get("price") or 0)
            sz = float(r.get("size") or 0)
            usd = float(r.get("usdcSize") or 0)
            side = str(r.get("side") or "").upper()
            if side == "BUY" and px > 0 and sz > 0:
                buys[cid].append((int(r.get("timestamp") or 0), px,
                                  str(r.get("outcome")), sz, usd))
            elif side == "SELL":
                sells[cid] += usd
        elif t == "REDEEM":
            d = redeem.setdefault(cid, {"usd": 0.0, "outcome": None})
            d["usd"] += float(r.get("usdcSize") or 0)
            d["outcome"] = d["outcome"] or str(r.get("outcome"))

    capture_end = max((m[2] for m in meta.values()), default=0)
    out: List[dict] = []
    for cid, s in buys.items():
        asset, horizon, end = meta[cid]
        if f"{asset.upper()}{horizon}" != cohort:
            continue
        s.sort(key=lambda x: x[0])
        t0 = s[0][0]
        start = end - A.DURATION_S[horizon]

        up = sum(x[3] for x in s if x[2] == "Up")
        dn = sum(x[3] for x in s if x[2] != "Up")
        cu = sum(x[4] for x in s if x[2] == "Up")      # usdcSize: fee-inclusive
        cd = sum(x[4] for x in s if x[2] != "Up")
        qu = sum(x[1] * x[3] for x in s if x[2] == "Up")   # quoted notional
        qd = sum(x[1] * x[3] for x in s if x[2] != "Up")
        if up <= 0 and dn <= 0:
            continue
        avg_up = (cu / up) if up else None
        avg_dn = (cd / dn) if dn else None
        matched = min(up, dn)
        residual = up - dn
        tot = up + dn

        fee = (cu + cd) - (qu + qd)
        n_bear = sum(1 for x in s if _bearing(x[1], x[3], x[4]))

        red = redeem.get(cid) or {}
        truncated = (capture_end - end) < TRUNCATION_S
        if red.get("outcome"):
            won_up, src = red["outcome"] == "Up", "redeem_row"
        elif truncated:
            won_up, src = None, "indeterminate: capture tail"
        else:
            won_up, src = (dn > up), "no_redeem_implies_held_side_lost"

        if won_up is None:
            matched_pnl = residual_pnl = payout = None
        else:
            payout = up if won_up else dn
            matched_pnl = (matched * (1.0 - (avg_up + avg_dn))
                           if (matched > 0 and avg_up is not None and avg_dn is not None)
                           else 0.0)
            if residual == 0:
                residual_pnl = 0.0
            else:
                r_up = residual > 0
                r_avg = avg_up if r_up else avg_dn
                residual_pnl = (abs(residual) if r_up == won_up else 0.0) \
                    - abs(residual) * r_avg

        switches = sum(1 for i in range(1, len(s)) if s[i][2] != s[i - 1][2])
        # matched-fraction trajectory: the fraction matched after each fill
        u = d = 0.0
        traj = []
        for _t, _p, o, q, _u in s:
            if o == "Up":
                u += q
            else:
                d += q
            traj.append(2 * min(u, d) / (u + d) if (u + d) else 0.0)

        prices = [x[1] if x[2] == "Up" else 1.0 - x[1] for x in s]
        out.append({
            "cid": cid, "cohort": cohort, "t": t0,
            "window_start": start, "window_end": end,
            "first_tte_s": end - t0,
            "last_tte_s": end - s[-1][0],
            "n_fills": len(s), "switches": switches,
            "fills_per_min": len(s) / max((s[-1][0] - t0) / 60.0, 1 / 60.0),
            "n_fee_bearing": n_bear,
            "fee_bearing_frac": n_bear / len(s),
            "all_fee_free": n_bear == 0,
            "all_fee_bearing": n_bear == len(s),
            "fee_usd": fee,
            "gross_acquisition_usd": cu + cd,          # observed cashflow
            "quoted_notional_usd": qu + qd,
            "effective_pair_cost": (avg_up + avg_dn) if (avg_up is not None and avg_dn is not None) else None,
            "quoted_pair_cost": ((qu / up) + (qd / dn)) if (up and dn) else None,
            "avg_up": avg_up, "avg_dn": avg_dn,
            "matched_qty": matched,
            "residual_abs": abs(residual), "residual_signed": residual,
            "residual_relative": (residual / tot) if tot else None,
            "matched_fraction": (2 * matched / tot) if tot else None,
            "matched_fraction_traj": traj,
            "matched_pnl": matched_pnl,
            "residual_pnl": residual_pnl,
            "redemption_usd": red.get("usd", 0.0),
            "sell_usd": sells.get(cid, 0.0),
            "net_cash": ((red.get("usd", 0.0) + sells.get(cid, 0.0) - (cu + cd))
                         if won_up is not None else None),
            "outcome_source": src,
            "price_first": prices[0], "price_last": prices[-1],
            "price_min": min(prices), "price_max": max(prices),
            "price_path_range": max(prices) - min(prices),
            "incentives_usd": None,
            "incentives_reason": ("L5's reward/rebate credits are not published "
                                 "per window for another account; never imputed"),
        })
    out.sort(key=lambda r: r["t"])
    return out


def split(rows: List[dict], frac: float = 0.7):
    rows = sorted(rows, key=lambda r: r["t"])
    c = int(len(rows) * frac)
    return rows[:c], rows[c:]


def q(xs: Sequence[float], p: float) -> Optional[float]:
    v = sorted(x for x in xs if x is not None)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def agg(rows: List[dict]) -> Dict[str, Any]:
    r = [x for x in rows if x["net_cash"] is not None]
    if not r:
        return {"n": 0}
    spend = sum(x["gross_acquisition_usd"] for x in r)
    net = sum(x["net_cash"] for x in r)
    bps = [1e4 * x["net_cash"] / x["gross_acquisition_usd"]
           for x in r if x["gross_acquisition_usd"] > 0]
    return {
        "n": len(r),
        "net_cash": net,
        "gross_acquisition_usd": spend,
        "bp_per_dollar": (1e4 * net / spend) if spend else None,
        "fee_usd": sum(x["fee_usd"] for x in r),
        "matched_pnl": sum(x["matched_pnl"] for x in r),
        "residual_pnl": sum(x["residual_pnl"] for x in r),
        "redemption_usd": sum(x["redemption_usd"] for x in r),
        "fee_bearing_fill_frac": (sum(x["n_fee_bearing"] for x in r)
                                  / sum(x["n_fills"] for x in r)),
        "mean_bp": statistics.mean(bps) if bps else None,
        "median_bp": statistics.median(bps) if bps else None,
        "win_rate": sum(1 for x in bps if x > 0) / len(bps) if bps else None,
        "bp_p05": q(bps, .05), "bp_p25": q(bps, .25), "bp_p50": q(bps, .50),
        "bp_p75": q(bps, .75), "bp_p95": q(bps, .95),
        "incentives_usd": None,
        "incentives_reason": "not published per window for another account",
    }
