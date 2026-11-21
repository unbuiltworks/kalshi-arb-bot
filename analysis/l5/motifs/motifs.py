"""Sequence motifs in L5's 5m windows, BTC5m and ETH5m kept separate.

WHY MOTIFS AND NOT A CLASSIFIER. The BTC15m switch signal is usable
(OOS AUC 0.674, n=446) and the 5m cohorts are not (1 and 4 test switches).
Forcing a 5m switch classifier on that evidence would be borrowing BTC15m
behaviour under a 5m label. Motifs describe what the 5m sequences ACTUALLY
are, which is answerable at the sample size we have.

STRUCTURE. Motifs 1-3 partition every window by its switch structure, so
their frequencies sum to 1. RAPID_LATE is a crosscutting overlay that can
co-occur with any of them and is reported separately rather than folded in.
Motif 5 is whatever the partition leaves lumped together; see `discover`.

NO OUTCOME LEAKAGE. The winning side enters only through redemption, after
the fact. It is never a feature of a motif.

A MISSING REDEEM ROW IS A LOSS, NOT MISSING DATA, AND CONFLATING THE TWO
INVERTS THE ANSWER. Redemption is complete whenever it pays: redeem_qty over
winning-side qty is 1.000 at p05, p50 and p95, at exactly $1.00 per share. So a
settled window with no REDEEM row is one where the held side lost and there was
nothing to redeem. Dropping those windows keeps only winners, and it bites
precisely on the one-sided motifs -- 99.5% of no-redeem windows are SINGLE_FILL
or SAME_SIDE_RUN. Scored as winners-only they return ~3,200 bp/$; scored with
their losses they do not. Windows settling inside TRUNCATION_S of the capture
end are genuinely indeterminate and are the only ones excluded.

FEES ARE IN THE TAPE AFTER ALL, AND THIS CORRECTS A STANDING ASSUMPTION.
`usdcSize` exceeds `price * size` on a BUY by exactly 0.07 * price * (1 - price)
per share, or by nothing at all -- the ratio is bimodal at 0.00 and 0.07 with
essentially nothing between. So `usdcSize` is the authoritative cash and the
wedge is a measurable per-fill fee. Costs here use usdcSize, which makes the
matched/residual decomposition reconcile to realized cash exactly; the wedge is
reported separately as `fee_usd`.

Spread, depth, queue position and the intra-window book remain genuinely absent
and are named UNKNOWN, never imputed as 0.
"""
from __future__ import annotations

import collections
import statistics
from typing import Any, Dict, List, Optional

from analysis.l5.redemption import audit as A

COHORTS = ("BTC5m", "ETH5m")
UNKNOWN_FROM_PUBLIC_TAPE = ("spread", "depth", "queue_position", "book_updates")
FEE_RATE = 0.07          # measured: fee_per_share = FEE_RATE * p * (1 - p)
LATE_FRACTION = 0.25          # final quarter of window life
LATE_QTY_SHARE = 0.50         # majority of size acquired there
LATE_MIN_FILLS = 3
TRUNCATION_S = 86400      # a redeem this close to capture end may simply not be in yet


def build(cohorts=COHORTS) -> List[dict]:
    """One record per market: the ordered fill sequence plus its economics."""
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
            side = str(r.get("side") or "").upper()
            px = float(r.get("price") or 0)
            if side == "BUY" and px > 0:
                buys[cid].append((int(r.get("timestamp") or 0), px,
                                  str(r.get("outcome")),
                                  float(r.get("size") or 0),
                                  float(r.get("usdcSize") or 0)))
            elif side == "SELL":
                sells[cid] += float(r.get("usdcSize") or 0)
        elif t == "REDEEM":
            d = redeem.setdefault(cid, {"qty": 0.0, "usd": 0.0, "outcome": None})
            d["qty"] += float(r.get("size") or 0)
            d["usd"] += float(r.get("usdcSize") or 0)
            d["outcome"] = d["outcome"] or str(r.get("outcome"))

    capture_end = max((m[2] for m in meta.values()), default=0)
    out: List[dict] = []
    for cid, s in buys.items():
        asset, horizon, end = meta[cid]
        cohort = f"{asset.upper()}{horizon}"
        if cohort not in cohorts:
            continue
        s.sort(key=lambda x: x[0])
        t0, p0, o0, _q0, _u0 = s[0]

        up_q = sum(x[3] for x in s if x[2] == "Up")
        dn_q = sum(x[3] for x in s if x[2] != "Up")
        # usdcSize is the cash actually paid, fee included -- not price*size
        up_c = sum(x[4] for x in s if x[2] == "Up")
        dn_c = sum(x[4] for x in s if x[2] != "Up")
        fee_usd = sum(x[4] - x[1] * x[3] for x in s)
        fee_fills = sum(1 for x in s
                        if x[3] > 0 and (x[4] - x[1] * x[3]) / x[3] > 0.0005)
        avg_up = (up_c / up_q) if up_q else None
        avg_dn = (dn_c / dn_q) if dn_q else None
        matched = min(up_q, dn_q)
        residual = up_q - dn_q

        # matched pair pays exactly 1 per pair at settlement, whichever side wins
        matched_pnl = (matched * (1.0 - (avg_up + avg_dn))
                       if (matched > 0 and avg_up is not None and avg_dn is not None)
                       else 0.0)
        red = redeem.get(cid) or {}
        truncated = (capture_end - end) < TRUNCATION_S
        if red.get("outcome"):
            won_up, outcome_src = red["outcome"] == "Up", "redeem_row"
        elif truncated:
            won_up, outcome_src = None, "indeterminate: settled inside the capture tail"
        else:
            # nothing redeemed on a long-settled window: the held side lost
            won_up, outcome_src = (dn_q > up_q), "no_redeem_implies_held_side_lost"
        if residual == 0 or won_up is None:
            residual_pnl = 0.0 if residual == 0 else None
        else:
            r_side_up = residual > 0
            r_avg = avg_up if r_side_up else avg_dn
            payoff = abs(residual) if (r_side_up == won_up) else 0.0
            residual_pnl = payoff - abs(residual) * r_avg

        # same-side run, first switch
        k = next((i for i in range(1, len(s)) if s[i][2] != o0), None)
        run_q = sum(x[3] for x in s[:k]) if k else sum(x[3] for x in s)
        run_c = sum(x[1] * x[3] for x in s[:k]) if k else sum(x[1] * x[3] for x in s)
        wavg_first = (run_c / run_q) if run_q else p0
        first_switch_pair = (round(wavg_first + s[k][1], 6) if k is not None else None)
        switches = sum(1 for i in range(1, len(s)) if s[i][2] != s[i - 1][2])

        # rapid late accumulation, measured against THIS window's own life
        life = max(end - t0, 1)
        cut = end - LATE_FRACTION * (end - t0) if end > t0 else end
        late_q = sum(x[3] for x in s if x[0] >= cut)
        tot_q = up_q + dn_q
        rapid_late = bool(len(s) >= LATE_MIN_FILLS and tot_q > 0
                          and late_q / tot_q >= LATE_QTY_SHARE)

        spend = sum(x[4] for x in s)
        out.append({
            "cid": cid, "cohort": cohort, "t": t0, "window_end": end,
            "first_tte_s": end - t0,
            "first_px": p0, "first_outcome": o0,
            "first_is_dearer": p0 > 0.5,
            "n_fills": len(s), "switches": switches,
            "matched_qty": round(matched, 6),
            "terminal_residual": round(abs(residual), 6),
            "matched_fraction": (round(2 * matched / tot_q, 6) if tot_q else None),
            "first_switch_pair_cost": first_switch_pair,
            "matched_pair_pnl": round(matched_pnl, 6),
            "residual_pnl": (None if residual_pnl is None else round(residual_pnl, 6)),
            "total_trading_cash": round(red.get("usd", 0.0)
                                        + sells.get(cid, 0.0) - spend, 6),
            "buy_usd": round(spend, 6),
            "span_s": s[-1][0] - t0,
            "rapid_late": rapid_late,
            "outcome_source": outcome_src,
            "fee_usd": round(fee_usd, 6),
            "fee_fill_share": round(fee_fills / len(s), 4),
        })
    out.sort(key=lambda r: r["t"])

    # pre-entry trajectory: the previous window of the SAME series, and only
    # when it had already ended before this window's first fill.
    prev: Dict[str, dict] = {}
    for r in out:
        p = prev.get(r["cohort"])
        ok = p is not None and p["window_end"] <= r["t"]
        pu = (p["first_px"] if p["first_outcome"] == "Up"
              else 1.0 - p["first_px"]) if ok else None
        cu = (r["first_px"] if r["first_outcome"] == "Up" else 1.0 - r["first_px"])
        r["pre_entry_move"] = (round(cu - pu, 6) if ok else None)
        r["pre_entry_move_reason"] = (None if ok else
                                      "no completed same-series predecessor")
        prev[r["cohort"]] = r
    return out


def motif(r: dict) -> str:
    """The partition. Exactly one label per window; frequencies sum to 1."""
    if r["switches"] == 0:
        return "SINGLE_FILL" if r["n_fills"] == 1 else "SAME_SIDE_RUN"
    return "DEARER_FIRST_COMPLEMENT" if r["first_is_dearer"] else "CHEAPER_FIRST_COMPLEMENT"


PARTITION = ("DEARER_FIRST_COMPLEMENT", "CHEAPER_FIRST_COMPLEMENT",
             "SAME_SIDE_RUN", "SINGLE_FILL")


def q(xs, p) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def summarize(rows: List[dict]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n": 0}
    def col(k): return [r[k] for r in rows if r.get(k) is not None]
    mp, rp = col("matched_pair_pnl"), col("residual_pnl")
    tc = col("total_trading_cash")
    spend = sum(col("buy_usd")) or None
    return {
        "n": n,
        "first_tte_s_p50": q(col("first_tte_s"), .5),
        "first_px_p50": q(col("first_px"), .5),
        "pre_entry_move_p50": q(col("pre_entry_move"), .5),
        "pre_entry_move_n": len(col("pre_entry_move")),
        "fills_p50": q(col("n_fills"), .5),
        "switches_p50": q(col("switches"), .5),
        "matched_fraction_p50": q(col("matched_fraction"), .5),
        "first_switch_pair_cost_p50": q(col("first_switch_pair_cost"), .5),
        "first_switch_pair_cost_n": len(col("first_switch_pair_cost")),
        "terminal_residual_p50": q(col("terminal_residual"), .5),
        "matched_pair_pnl_sum": round(sum(mp), 4),
        "residual_pnl_sum": round(sum(rp), 4) if rp else None,
        "residual_pnl_n": len(rp),
        "total_trading_cash_sum": round(sum(tc), 4),
        "total_trading_cash_mean": round(sum(tc) / len(tc), 6),
        "bp_per_dollar_spent": (round(1e4 * sum(tc) / spend, 2) if spend else None),
        "buy_usd_sum": round(sum(col("buy_usd")), 4),
        "rapid_late_pct": round(100.0 * sum(1 for r in rows if r["rapid_late"]) / n, 2),
        "fee_usd_sum": round(sum(col("fee_usd")), 4),
        "fee_fill_share_p50": q(col("fee_fill_share"), .5),
    }


def split(rows: List[dict], frac: float = 0.7):
    """Chronological, never random: train is strictly earlier than test."""
    rows = sorted(rows, key=lambda r: r["t"])
    cut = int(len(rows) * frac)
    return rows[:cut], rows[cut:]
