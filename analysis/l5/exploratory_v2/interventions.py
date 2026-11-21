"""EXPLORATORY_V2 -- how much of L5's flip/extension process do we actually need?

OWNER 2026-09-17. RESIDUAL_EXIT_FORENSICS_V1 established the behavioural fact
this rests on: on BTC15m L5 is an INVENTORY OSCILLATOR, not a flat-completion
controller -- 84.3% of imbalance episodes end by overshooting through zero and
1.2% by landing on it. That process is what builds his matched inventory, and
it is also what leaves him a median 141.8-share residual at every settlement,
worth -$144,065 across the sample.

So replication and monetization separate:

    FIDELITY  reproduce the observed flip/extension process.
    ADAPTED   keep the matched economics the oscillation creates WITHOUT
              inheriting the residual economics it also creates.

This module measures the trade between those two, and nothing else. It sets no
policy, changes no risk limit and moves no money.

WHAT THE COUNTERFACTUAL IS, AND WHAT IT IS NOT
----------------------------------------------
It is SUBTRACTIVE. Each intervention is a gate applied to L5's observed fill
sequence; a suppressed fill is dropped and the inventory walk continues without
it. Nothing is ever ADDED, no price is invented, and no fill is moved.

IT ASSUMES THE SURVIVING FILLS STILL HAPPEN, WHICH IS FALSE IN DETAIL. Had L5
not bought, the book, his queue position and the counterparties would all have
differed. There is no historical book in this dataset, so no better
counterfactual exists here. Read every number as "the economics of the trades
we would have kept", which is the standard did-not-take-these-trades account --
never as a simulation of a deployed policy.

The one thing this DOES answer honestly is the question the owner posed: how
much matched-inventory formation is destroyed per dollar of residual risk
removed. Both sides of that ratio are computed from the same kept sequence, so
a gate cannot look good by quietly deleting the cycling that creates the
matched inventory -- that shows up immediately as matched volume lost.

FEES ARE ALREADY IN. Costs are `usdcSize` throughout, which is fee-inclusive,
and matched_pnl + residual_pnl is algebraically equal to payout - spend. So
"total trading P&L" here is AFTER known fees, and the fee is also reported
separately as usdcSize - price*size.

INCENTIVES ARE NEVER IN. L5's rebates and rewards are account-level and are not
published per market; they are reported beside these numbers, never inside them
and never attributed to an episode.
"""
from __future__ import annotations

import collections
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from analysis.l5.residual_exit import forensics as F

QTY_TOL = F.QTY_TOL


def _u01(*parts: Any) -> float:
    """Deterministic uniform in [0,1) keyed on the fill's identity. A gate that
    suppresses probabilistically must be reproducible and must not depend on
    evaluation order, so no global RNG is used anywhere."""
    h = hashlib.blake2b("|".join(str(p) for p in parts).encode(),
                        digest_size=8).hexdigest()
    return int(h, 16) / float(1 << 64)


# ---------------------------------------------------------------------------
# the state a gate may look at. Deliberately small: everything in it is
# observable to US in real time, and NOTHING in it is a book quantity.

class State:
    __slots__ = ("cid", "i", "residual", "run_len", "run_side", "dpa_60s",
                 "p_up", "tte_s", "kept", "up", "dn")

    def __init__(self, cid, i, residual, run_len, run_side, dpa_60s, p_up,
                 tte_s, kept, up, dn):
        self.cid, self.i = cid, i
        self.residual = residual          # OUR residual, from kept fills only
        self.run_len = run_len            # OUR same-side run, from kept fills
        self.run_side = run_side
        self.dpa_60s = dpa_60s            # |Dp| over 60s. MAGNITUDE ONLY.
        self.p_up = p_up
        self.tte_s = tte_s
        self.kept = kept
        self.up, self.dn = up, dn


Gate = Callable[[State, Tuple], bool]     # True == keep the fill


def _relation(residual: float, side: str, sz: float) -> str:
    """What this fill would do to OUR inventory."""
    after = residual + (sz if side == "Up" else -sz)
    if abs(residual) <= QTY_TOL:
        return "OPEN"
    if abs(after) <= QTY_TOL:
        return "COMPLETE_FLAT"
    if (residual > 0) != (after > 0):
        return "SWITCH"
    return "EXTEND" if abs(after) > abs(residual) else "COMPLETE_PARTIAL"


# ---------------------------------------------------------------------------
# gates

def control() -> Gate:
    """Exact stochastic FIDELITY: take every fill L5 took."""
    return lambda st, f: True


def v2a_residual_cap(cap: float) -> Gate:
    """A: allow EXTEND until the PROPOSED post-fill residual breaches the cap.
    The test is on the proposed position, not the current one -- testing the
    current one lets a single large fill step straight over the cap."""
    def g(st: State, f: Tuple) -> bool:
        _t, _px, oc, sz, _usd = f
        if _relation(st.residual, oc, sz) != "EXTEND":
            return True
        return abs(st.residual + (sz if oc == "Up" else -sz)) <= cap
    return g


def v2b_linear_suppression(k: float) -> Gate:
    """B: suppress EXTEND with probability |residual| / k, so the brake comes
    on progressively instead of at a cliff."""
    def g(st: State, f: Tuple) -> bool:
        _t, _px, oc, sz, _usd = f
        if _relation(st.residual, oc, sz) != "EXTEND":
            return True
        p = min(1.0, abs(st.residual) / k) if k > 0 else 1.0
        return _u01(st.cid, st.i, "B", k) >= p
    return g


def v2c_run_cutoff(n: int) -> Gate:
    """C: suppress EXTEND once OUR same-side run has reached n fills.

    Run length is the strongest replicating predictor of L5's own EXTEND
    (Spearman 1.00 out of sample), which makes it the natural control -- but
    only if long runs are not themselves what builds the matched inventory.
    That is exactly what the sweep measures."""
    def g(st: State, f: Tuple) -> bool:
        _t, _px, oc, sz, _usd = f
        if _relation(st.residual, oc, sz) != "EXTEND":
            return True
        return not (st.run_side == oc and st.run_len >= n)
    return g


def v2d_vol_cutoff(v: float) -> Gate:
    """D: suppress EXTEND when |Dp| over the last 60s is at least v.

    MAGNITUDE ONLY. RESIDUAL_EXIT_FORENSICS_V1 found L5's extend rate rises
    symmetrically at both movement extremes, so sign(Dp) is not a favored-side
    signal and is not used here or anywhere in this module."""
    def g(st: State, f: Tuple) -> bool:
        _t, _px, oc, sz, _usd = f
        if _relation(st.residual, oc, sz) != "EXTEND":
            return True
        return st.dpa_60s is None or st.dpa_60s < v
    return g


def v2e_matched_preservation(near: float, substantial: float) -> Gate:
    """E: near flat, require more before building a substantial residual on the
    OTHER side.

    This is the one gate that is not only about EXTEND, and it has to be: the
    behaviour it targets is the overshoot through zero, which is by definition
    not an extension. When our inventory is at or near flat (|residual| <=
    near), any fill that would leave |residual| above `substantial` is
    suppressed. Matched inventory already built is what it preserves."""
    def g(st: State, f: Tuple) -> bool:
        _t, _px, oc, sz, _usd = f
        if abs(st.residual) > near:
            return True
        return abs(st.residual + (sz if oc == "Up" else -sz)) <= substantial
    return g


def v2f_hybrid(k: float, n: int, v: float, thresh: float = 1.0) -> Gate:
    """F: residual magnitude x run length x |Dp|, as one score.

    Multiplicative so that any single benign term keeps the gate open -- a long
    run in a quiet book with a small residual should not be suppressed just for
    being long."""
    def g(st: State, f: Tuple) -> bool:
        _t, _px, oc, sz, _usd = f
        if _relation(st.residual, oc, sz) != "EXTEND":
            return True
        prop = abs(st.residual + (sz if oc == "Up" else -sz))
        a = (prop / k) if k > 0 else 0.0
        b = ((st.run_len + 1) / n) if (n > 0 and st.run_side == oc) else (1.0 / n if n > 0 else 0.0)
        c = ((st.dpa_60s or 0.0) / v) if v > 0 else 0.0
        return (a * b * c) < thresh
    return g


# ---------------------------------------------------------------------------
# the walk

def replay_window(cid: str, w: dict, capture_end: int, gate: Gate) -> Optional[dict]:
    fills = w["fills"]
    if not fills:
        return None
    _asset, horizon, end = w["meta"]
    won_up, src = F.settlement(w, capture_end)

    up = dn = upu = dnu = 0.0
    quoted = 0.0
    kept = dropped = flips = 0
    max_res = 0.0
    run_side, run_len = None, 0
    p_hist: List[Tuple[int, float]] = []
    kept_sides: List[str] = []

    for i, (t, px, oc, sz, usd) in enumerate(fills):
        p_up = px if oc == "Up" else 1.0 - px
        dpa = None
        for tt, pp in reversed(p_hist):
            if t - tt >= 60:
                dpa = abs(p_up - pp)
                break
        p_hist.append((t, p_up))          # the print tape is observed whether
                                          # or not we trade on it
        res = up - dn
        st = State(cid, i, res, run_len, run_side, dpa, p_up, end - t,
                   kept, up, dn)
        if not gate(st, (t, px, oc, sz, usd)):
            dropped += 1
            continue
        if oc == "Up":
            up += sz
            upu += usd
        else:
            dn += sz
            dnu += usd
        quoted += px * sz
        kept += 1
        after = up - dn
        if abs(res) > QTY_TOL and abs(after) > QTY_TOL and (res > 0) != (after > 0):
            flips += 1
        max_res = max(max_res, abs(after))
        run_len = (run_len + 1) if run_side == oc else 1
        run_side = oc
        kept_sides.append(oc)

    if kept == 0:
        return {"cid": cid, "kept": 0, "dropped": dropped, "won_up": won_up,
                "outcome_source": src, "matched": 0.0, "matched_pnl": 0.0,
                "residual": 0.0, "residual_pnl": 0.0, "spend": 0.0,
                "fee": 0.0, "max_res": 0.0, "flips": 0, "pair_cost": None,
                "n_observed": len(fills)}

    avg_up = (upu / up) if up > 0 else None
    avg_dn = (dnu / dn) if dn > 0 else None
    matched = min(up, dn)
    residual = up - dn
    pair_cost = ((avg_up + avg_dn) if (avg_up is not None and avg_dn is not None)
                 else None)
    if won_up is None:
        matched_pnl = residual_pnl = None
    else:
        matched_pnl = (matched * (1.0 - pair_cost)
                       if (matched > 0 and pair_cost is not None) else 0.0)
        if abs(residual) <= QTY_TOL:
            residual_pnl = 0.0
        else:
            r_up = residual > 0
            r_avg = (avg_up if r_up else avg_dn) or 0.0
            residual_pnl = ((abs(residual) if r_up == won_up else 0.0)
                            - abs(residual) * r_avg)
    return {
        "cid": cid, "kept": kept, "dropped": dropped, "n_observed": len(fills),
        "won_up": won_up, "outcome_source": src,
        "matched": matched, "pair_cost": pair_cost, "matched_pnl": matched_pnl,
        "residual": residual, "residual_pnl": residual_pnl,
        "spend": upu + dnu, "fee": (upu + dnu) - quoted,
        "max_res": max_res, "flips": flips,
    }


def run(gate: Gate, windows: Dict[str, dict], capture_end: int) -> dict:
    rows = [r for r in (replay_window(c, w, capture_end, gate)
                        for c, w in windows.items()) if r]
    det = [r for r in rows if r["matched_pnl"] is not None]
    n_obs = sum(r["n_observed"] for r in rows)
    n_kept = sum(r["kept"] for r in rows)
    res_abs = sorted(abs(r["residual"]) for r in det)
    mx = sorted(r["max_res"] for r in det)
    return {
        "windows": len(rows),
        "fills_observed": n_obs, "fills_kept": n_kept,
        "fills_retained": round(n_kept / n_obs, 4) if n_obs else None,
        "matched_volume": sum(r["matched"] for r in det),
        "matched_pnl": sum(r["matched_pnl"] for r in det),
        "residual_pnl": sum(r["residual_pnl"] for r in det),
        "trading_pnl_after_fees": sum(r["matched_pnl"] + r["residual_pnl"]
                                      for r in det),
        "fee_usd": sum(r["fee"] for r in det),
        "spend_usd": sum(r["spend"] for r in det),
        "bp_per_dollar": (1e4 * sum(r["matched_pnl"] + r["residual_pnl"]
                                    for r in det)
                          / sum(r["spend"] for r in det)
                          if sum(r["spend"] for r in det) else None),
        "abs_residual_at_settlement_sum": sum(res_abs),
        "abs_residual_at_settlement_median": (res_abs[len(res_abs) // 2]
                                              if res_abs else None),
        "max_residual_median": mx[len(mx) // 2] if mx else None,
        "max_residual_p95": (mx[int(0.95 * len(mx))] if mx else None),
        "flips_total": sum(r["flips"] for r in det),
        "flips_per_window": (round(sum(r["flips"] for r in det) / len(det), 3)
                             if det else None),
        "windows_left_flat": sum(1 for r in det if abs(r["residual"]) <= QTY_TOL),
        "median_pair_cost": F._med([r["pair_cost"] for r in det]),
    }


# ---------------------------------------------------------------------------
# the sweep

def build_grid() -> List[Tuple[str, dict, Gate]]:
    g: List[Tuple[str, dict, Gate]] = [("CONTROL", {"desc": "exact FIDELITY"},
                                        control())]
    for c in (10, 25, 50, 100, 200, 400):
        g.append(("V2A_cap_%d" % c, {"cap": c}, v2a_residual_cap(float(c))))
    for k in (25, 50, 100, 200, 400):
        g.append(("V2B_linear_k%d" % k, {"k": k}, v2b_linear_suppression(float(k))))
    for n in (2, 3, 4, 6, 8, 12):
        g.append(("V2C_run_%d" % n, {"n": n}, v2c_run_cutoff(n)))
    for v in (0.02, 0.05, 0.10, 0.20):
        g.append(("V2D_vol_%.2f" % v, {"abs_dp_60s": v}, v2d_vol_cutoff(v)))
    for near, sub in ((5, 25), (5, 50), (10, 50), (10, 100), (25, 100)):
        g.append(("V2E_near%d_sub%d" % (near, sub),
                  {"near": near, "substantial": sub},
                  v2e_matched_preservation(float(near), float(sub))))
    for k, n, v, th in ((100, 4, 0.05, 1.0), (100, 4, 0.05, 2.0),
                        (200, 6, 0.10, 1.0), (50, 3, 0.02, 1.0)):
        g.append(("V2F_k%d_n%d_v%.2f_t%.1f" % (k, n, v, th),
                  {"k": k, "n": n, "abs_dp_60s": v, "threshold": th},
                  v2f_hybrid(float(k), n, v, th)))
    return g


def split_windows(windows: Dict[str, dict], frac: float = 0.7):
    order = sorted(windows, key=lambda c: windows[c]["meta"][2])
    cut = int(len(order) * frac)
    return ({c: windows[c] for c in order[:cut]},
            {c: windows[c] for c in order[cut:]})


def frontier(base: dict, r: dict) -> dict:
    """Matched economics retained per dollar of residual risk removed.

    `residual risk removed` is the IMPROVEMENT in residual P&L, which is the
    only residual quantity denominated in dollars. Exposure removed (shares of
    settlement residual) is reported beside it because the two can disagree:
    a gate can shed exposure and still not improve the P&L if it sheds the
    winning side too."""
    d_matched = base["matched_pnl"] - r["matched_pnl"]           # >0 == lost
    d_resid = r["residual_pnl"] - base["residual_pnl"]           # >0 == improved
    d_expo = (base["abs_residual_at_settlement_sum"]
              - r["abs_residual_at_settlement_sum"])             # >0 == removed
    return {
        "matched_volume_retained": (round(r["matched_volume"]
                                          / base["matched_volume"], 4)
                                    if base["matched_volume"] else None),
        "matched_pnl_retained": (round(r["matched_pnl"] / base["matched_pnl"], 4)
                                 if base["matched_pnl"] else None),
        "matched_pnl_lost_usd": d_matched,
        "residual_pnl_improved_usd": d_resid,
        "residual_exposure_removed_shares": d_expo,
        "matched_lost_per_residual_dollar_saved": (round(d_matched / d_resid, 4)
                                                   if abs(d_resid) > 1e-9 else None),
        "net_gain_usd": d_resid - d_matched,
        "fills_retained": r["fills_retained"],
        "capital_turnover_retained": (round(r["spend_usd"] / base["spend_usd"], 4)
                                      if base["spend_usd"] else None),
    }


def sweep(cohort: str = "BTC15m") -> dict:
    windows, capture_end = F.load_windows(cohort)
    tr, oos = split_windows(windows)
    out: Dict[str, Any] = {
        "cohort": cohort, "n_windows": len(windows),
        "n_train": len(tr), "n_oos": len(oos),
        "counterfactual": ("SUBTRACTIVE on L5's observed fills. Assumes the "
                           "surviving fills still happen, which is false in "
                           "detail -- no historical book exists here. Read as "
                           "'the economics of the trades we would have kept'."),
        "fees": ("usdcSize is fee-inclusive, so trading_pnl_after_fees is "
                 "already net of the fee actually paid; fee_usd is reported "
                 "separately as usdcSize - price*size."),
        "incentives": ("NOT included anywhere. L5's rebates/rewards are "
                       "account-level and are never attributed to a market."),
        "results": {},
    }
    grid = build_grid()
    for scope, W in (("all", windows), ("train", tr), ("oos", oos)):
        base = run(control(), W, capture_end)
        rows = {}
        for name, params, gate in grid:
            r = run(gate, W, capture_end)
            r["params"] = params
            r["frontier"] = frontier(base, r) if name != "CONTROL" else None
            rows[name] = r
        out["results"][scope] = rows
    return out
