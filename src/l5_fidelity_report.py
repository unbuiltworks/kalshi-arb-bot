"""The eleven-point fidelity report for the faithful L5 translation.

Fidelity is agreement with L5_NATIVE's SEQUENTIAL INVENTORY PATH, in both
directions. Reproducing MORE than L5 is a fidelity failure exactly as
reproducing less is: an engine that crosses zero where L5 did not has not
replicated him, it has done something else. No metric here rewards P&L, and
none is tuned.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def crossed_zero(path: Sequence[float]) -> bool:
    nz = [_sign(x) for x in path if _sign(x) != 0]
    return any(a != b for a, b in zip(nz, nz[1:]))


def overshoot(path: Sequence[float]) -> float:
    signs = [(x, _sign(x)) for x in path]
    nz = [s for _, s in signs if s != 0]
    if not any(a != b for a, b in zip(nz, nz[1:])):
        return 0.0
    first = nz[0]
    return max((abs(x) for x, s in signs if s != 0 and s != first), default=0.0)


def longest_run(sides: Sequence[str]) -> int:
    best = run = 0
    last = None
    for s in sides:
        run = run + 1 if s == last else 1
        last = s
        best = max(best, run)
    return best


def _corr(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Pearson correlation of two equal-length paths, or None."""
    n = min(len(a), len(b))
    if n < 3:
        return None
    a, b = list(a[:n]), list(b[:n])
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return round(cov / (va ** 0.5 * vb ** 0.5), 4)


def _resampled(track: Sequence[float], n: int) -> List[float]:
    """Hold-last-value resample of the track path onto n native steps."""
    if not track:
        return [0.0] * n
    out, j = [], 0
    for i in range(n):
        j = min(int(i * len(track) / n), len(track) - 1)
        out.append(track[j])
    return out


def report(run: Mapping[str, Any], *, yes_value: Optional[D],
           lam: D) -> Dict[str, Any]:
    """The eleven points, for one window."""
    n = run["n_actions"]
    rep = run["reproduced"]
    nat_imb, trk_imb = run["native_imbalance"], run["track_imbalance"]
    nat_sides, trk_sides = run["native_sides"], run["track_sides"]

    up, dn = run["up"], run["dn"]
    matched = min(up, dn)
    residual = up - dn
    au = (run["cost_up"] / run["qty_up"]) if run["qty_up"] > 0 else None
    ad = (run["cost_dn"] / run["qty_dn"]) if run["qty_dn"] > 0 else None
    pair = (au + ad) if (au is not None and ad is not None) else None
    matched_pnl = ((D(1) - pair) * matched) if pair is not None else D(0)

    resid_pnl = D(0)
    if residual != 0 and yes_value is not None:
        if residual > 0:
            resid_pnl = yes_value * residual - (au or D(0)) * residual
        else:
            q = -residual
            resid_pnl = (D(1) - yes_value) * q - (ad or D(0)) * q

    # native terminal inventory at the same lambda, for a like-for-like read
    nat_final = nat_imb[-1] if nat_imb else 0.0
    nat_matched = None
    if nat_sides:
        nu = sum(1 for s in nat_sides if s == "UP")
        nd = len(nat_sides) - nu
        nat_matched = min(nu, nd)

    t_over, n_over = overshoot(trk_imb), overshoot(nat_imb)
    t_xz, n_xz = crossed_zero(trk_imb), crossed_zero(nat_imb)

    return {
        # 1
        "action_reproduction": {
            "l5_actions": n, "reproduced": rep,
            "rate_pct": round(100.0 * rep / n, 1) if n else None,
        },
        # 2
        "sequence_agreement": {
            "native_first_side": nat_sides[0] if nat_sides else None,
            "track_first_side": trk_sides[0] if trk_sides else None,
            "first_side_agrees": bool(nat_sides and trk_sides
                                      and nat_sides[0] == trk_sides[0]),
            "native_longest_run": longest_run(nat_sides),
            "track_longest_run": longest_run(trk_sides),
            "native_up_share_pct": (round(100.0 * nat_sides.count("UP")
                                          / len(nat_sides), 1)
                                    if nat_sides else None),
            "track_up_share_pct": (round(100.0 * trk_sides.count("UP")
                                         / len(trk_sides), 1)
                                   if trk_sides else None),
        },
        # 3
        "inventory_path_agreement": {
            "path_correlation": _corr(_resampled(trk_imb, len(nat_imb)),
                                      nat_imb),
            "native_final_imbalance": round(nat_final, 3),
            "track_final_imbalance": round(trk_imb[-1], 3) if trk_imb else 0.0,
            "native_max_abs_imbalance": round(max((abs(x) for x in nat_imb),
                                                  default=0.0), 3),
            "track_max_abs_imbalance": round(max((abs(x) for x in trk_imb),
                                                 default=0.0), 3),
            "amplitude_ratio": (round(max((abs(x) for x in trk_imb), default=0.0)
                                      / max((abs(x) for x in nat_imb), default=1.0), 3)
                                if nat_imb else None),
        },
        # 4
        "opposite_acquisition": {
            "native": len(set(nat_sides)) > 1,
            "track": len(set(trk_sides)) > 1,
            "agrees": (len(set(nat_sides)) > 1) == (len(set(trk_sides)) > 1),
        },
        # 5
        "zero_cross_overshoot": {
            "native_crossed": n_xz, "track_crossed": t_xz,
            "crossing_agrees": n_xz == t_xz,
            "native_overshoot": round(n_over, 3),
            "track_overshoot": round(t_over, 3),
            "direction": ("MATCH" if n_xz == t_xz else
                          ("TRACK_OVER_REPRODUCES" if t_xz and not n_xz
                           else "TRACK_UNDER_REPRODUCES")),
        },
        # 6
        "matched_vs_residual": {
            "track_matched": float(matched), "track_residual": float(residual),
            "track_residual_direction": ("UP" if residual > 0 else
                                         ("DOWN" if residual < 0 else "FLAT")),
            "native_matched_actions": nat_matched,
            "native_final_imbalance": round(nat_final, 3),
        },
        # 7
        "maker_taker_mix": dict(run["mode_mix"]),
        # 8
        "incremental_translation_cost": {
            "slippage_usd_vs_l5": round(float(run["slippage_usd"]), 4),
            "slippage_per_reproduced_action": (
                round(float(run["slippage_usd"]) / rep, 5) if rep else None),
            "committed_usd": round(float(run["committed_usd"]), 4),
            "note": ("cost of reaching the closest executable price rather "
                     "than L5's own; MEASURED, never capped"),
        },
        # 9
        "fees_and_rebates": {
            "taker_fees_usd": round(float(run["fees"]), 4),
            "maker_rebate_accrual_usd": round(float(run["rebates"]), 4),
            "kept_separate_from_trading_pnl": True,
        },
        # 10
        "complete_window_economics": {
            "pair_cost_final": (round(run["pair_cost_path"][-1], 4)
                                if run["pair_cost_path"] else None),
            "pair_cost_max": (round(max(run["pair_cost_path"]), 4)
                              if run["pair_cost_path"] else None),
            "pair_cost_min": (round(min(run["pair_cost_path"]), 4)
                              if run["pair_cost_path"] else None),
            "matched_pair_pnl_usd": round(float(matched_pnl), 4),
            "residual_pnl_usd": round(float(resid_pnl), 4),
            "total_settlement_implied_shadow_usd": round(
                float(matched_pnl + resid_pnl + run["rebates"] - run["fees"]), 4),
            "settlement_yes_value": str(yes_value),
        },
        # 11
        "irreducible_failures": {
            "by_layer": run["by_layer"],
            "detail": run["refused"],
            "note": ("PMUS_TRANSLATION refusals are venue/habitat facts no "
                     "change to our execution removes. SAFETY_OVERLAY "
                     "refusals are OUR controls and are never evidence about "
                     "L5 or about PMUS."),
        },
        "labels": ("SHADOW. No order exists. Nothing here is submitted, "
                   "filled, settled or realized."),
    }
