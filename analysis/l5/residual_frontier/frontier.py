"""The residual / pair-cost frontier: what temporary directional inventory
does an L5 5m cycle require to reach each level of matched-pair economics, and
what does it cost when the repair never arrives?

THE CLONE IS FAIL-CLOSED. It follows L5's own fill sequence in order and takes
the next fill ONLY while the resulting absolute residual stays inside the cap.
The moment the next fill would breach the cap it STOPS and holds whatever book
it has to the real settlement outcome. It never takes a fill L5 did not take,
never reorders, and never assumes a later fill.

EXACT DECOMPOSITION, not an approximation. Splitting cost between the matched
and the residual legs at each side's own average price is exact:
    matched_pnl  = matched * (1 - (avg_up + avg_dn))
    residual_pnl = residual * [residual side won] - residual * avg(residual side)
    matched_pnl + residual_pnl == settlement - cost      (asserted in tests)

FEES ARE NOT SEPARABLE from this tape: `usdcSize` is the cash that moved and
carries no fee field. They are reported UNKNOWN rather than invented, so every
net figure here is gross of any unobserved fee.
"""
from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence

CAPS = (2, 4, 6, 8, 10, 12, 15, 20, 30, None)      # None = unrestricted
PAIR_LEVELS = (0.90, 0.92, 0.94, 0.96)


def _state(up: float, dn: float, cup: float, cdn: float) -> Dict[str, Any]:
    tot = up + dn
    matched = min(up, dn)
    resid = abs(up - dn)
    avg_up = (cup / up) if up else None
    avg_dn = (cdn / dn) if dn else None
    pair = (avg_up + avg_dn) if (avg_up is not None and avg_dn is not None) else None
    return {"up": up, "dn": dn, "cost": cup + cdn, "matched": matched,
            "residual": resid, "imbalance": ((up - dn) / tot) if tot else None,
            "avg_up": avg_up, "avg_dn": avg_dn, "pair_cost": pair}


def simulate(cycle: Dict[str, Any], cap: Optional[float],
             pair_ceiling: Optional[float] = None) -> Dict[str, Any]:
    """One fail-closed run. Returns the terminal book plus the per-fill trace.

    `pair_ceiling` adds the gate production actually runs: a fill that would
    push the effective pair cost (avg_up + avg_dn) above the ceiling is
    REFUSED, and the clone holds instead. Without it the clone merely copies
    L5 including the pairs he bought above par, which is not the policy we
    would ever run and makes the matched leg look worse than any gated
    version of it.
    """
    s: Sequence = cycle["s"]
    win = cycle["win"]
    end = cycle["end"]
    up = dn = cup = cdn = 0.0
    peak_cost = 0.0
    worst_resid = 0.0
    taken = 0
    trace: List[Dict[str, Any]] = []
    stopped_by_cap = False
    for (t, px, outcome, qty, usd) in s:
        nup, ndn = (up + qty, dn) if outcome == "Up" else (up, dn + qty)
        if cap is not None and abs(nup - ndn) > cap:
            stopped_by_cap = True
            break                      # FAIL CLOSED: hold what we have
        if pair_ceiling is not None:
            ncup = cup + (usd if outcome == "Up" else 0.0)
            ncdn = cdn + (0.0 if outcome == "Up" else usd)
            if nup > 0 and ndn > 0:
                npair = ncup / nup + ncdn / ndn
                if npair > pair_ceiling:
                    stopped_by_cap = True
                    break              # gate refuses: pair would exceed ceiling
        up, dn = nup, ndn
        if outcome == "Up":
            cup += usd
        else:
            cdn += usd
        taken += 1
        st = _state(up, dn, cup, cdn)
        peak_cost = max(peak_cost, st["cost"])
        worst_resid = max(worst_resid, st["residual"])
        trace.append({**st, "t": t, "tte_s": end - t, "actions": taken,
                      "px": px, "outcome": outcome})
    st = _state(up, dn, cup, cdn)
    # settlement of the book actually held
    won_qty = up if win == "Up" else dn
    settlement = won_qty
    net = settlement - st["cost"]
    matched, resid = st["matched"], st["residual"]
    matched_pnl = (matched * (1.0 - st["pair_cost"])) if st["pair_cost"] is not None else 0.0
    if resid > 0:
        r_is_up = up > dn
        avg_r = st["avg_up"] if r_is_up else st["avg_dn"]
        r_won = (win == "Up") if r_is_up else (win != "Up")
        residual_pnl = resid * (1.0 if r_won else 0.0) - resid * (avg_r or 0.0)
    else:
        residual_pnl = 0.0
    # the minimum pair cost this cycle could still have reached after the stop
    future = _min_future_pair(s, taken, up, dn, cup, cdn)
    return {"cid": cycle["cid"], "cohort": cycle["cohort"], "t0": cycle["t0"],
            "cap": cap, "actions_taken": taken, "actions_available": len(s),
            "stopped_by_cap": stopped_by_cap,
            "completed": taken == len(s),
            "matched": matched, "residual": resid,
            "terminal_residual": resid, "worst_residual": worst_resid,
            "imbalance": st["imbalance"], "pair_cost": st["pair_cost"],
            "capital_required": peak_cost, "cost": st["cost"],
            "settlement": settlement, "net": net,
            "matched_pnl": matched_pnl, "residual_pnl": residual_pnl,
            "fees": None,                       # UNKNOWN: not in this tape
            "min_future_pair": future, "trace": trace}


def _min_future_pair(s, taken, up, dn, cup, cdn) -> Optional[float]:
    """Best pair cost still reachable had the clone been allowed to continue.
    Forward-looking by construction: an ANALYSIS field, never a decision input."""
    best = None
    u, d, cu, cd = up, dn, cup, cdn
    for (t, px, outcome, qty, usd) in s[taken:]:
        if outcome == "Up":
            u += qty; cu += usd
        else:
            d += qty; cd += usd
        if u > 0 and d > 0:
            p = cu / u + cd / d
            best = p if best is None else min(best, p)
    return best


def _q(xs, p):
    if not xs:
        return None
    v = sorted(xs)
    return v[max(0, min(len(v) - 1, int(p * len(v))))]


def summarise(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(runs)
    cost = sum(r["cost"] for r in runs)
    net = sum(r["net"] for r in runs)
    mp = sum(r["matched_pnl"] for r in runs)
    rp = sum(r["residual_pnl"] for r in runs)
    withpair = [r for r in runs if r["pair_cost"] is not None]
    nets = [r["net"] for r in runs]
    out = {
        "cycles": n,
        "matched_pair_pnl": round(mp, 2),
        "directional_residual_pnl": round(rp, 2),
        "fees": "UNKNOWN (not separable from this tape)",
        "capital_required": round(sum(r["capital_required"] for r in runs), 2),
        "peak_capital_one_cycle": round(max((r["capital_required"] for r in runs),
                                            default=0.0), 2),
        "net_pnl": round(net, 2),
        "net_bp_per_dollar": round(10000 * net / cost, 1) if cost else None,
        "pct_cycles_completed": round(100.0 * sum(1 for r in runs if r["completed"]) / n, 1),
        "worst_residual": round(max((r["worst_residual"] for r in runs), default=0.0), 2),
        "loss_p05": round(_q(nets, 0.05) or 0.0, 4),
        "loss_p25": round(_q(nets, 0.25) or 0.0, 4),
        "loss_p50": round(_q(nets, 0.50) or 0.0, 4),
        "loss_p95": round(_q(nets, 0.95) or 0.0, 4),
        "worst_cycle": round(min(nets), 2) if nets else None,
    }
    for lv in PAIR_LEVELS:
        out[f"pct_pair_le_{lv}"] = (
            round(100.0 * sum(1 for r in withpair if r["pair_cost"] <= lv) / n, 1))
    out["pct_with_any_matched"] = round(100.0 * len(withpair) / n, 1)
    return out
