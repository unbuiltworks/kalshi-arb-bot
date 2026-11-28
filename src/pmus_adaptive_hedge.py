"""Adaptive underdog / opposite-side hedge for the trading-first controller.

PURE arithmetic, no I/O, no state of its own. One implementation shared by
the engine (`pmus_sports_two_sided`, PMUS_RN1_TF_ADAPTIVE_HEDGE, default
off) and the offline harness (`pmus_directional_backtest`, policy key
`adaptive_hedge`).

Today a completion is "the whole imbalance (one clip at a time) at the pair
cap", optionally repriced DOWN to fair + a fixed give. This rule instead
prices and sizes the completion from the INDEPENDENT fair and the inventory:

PRICE   cap = fair_hedge_side + give, floored to the tick, where

            give = weaken_k * max(0, weakening - weaken_dead)
                 + expo_usd * min(1, unpaired_cost / unpaired_cap)
                 - edge_k   * max(0, held_edge)
            clamped to [give_min, give_max]

        weakening  = fair of the HELD side when the unpaired inventory was
                     acquired (quantity-weighted, `note_entry`) minus its
                     fair now;
        held_edge  = fair of the held side now minus its average cost.
        While the held leg still has positive edge and has NOT weakened
        (past the dead band), give is at most 0: a discount is demanded.
        The bid is min(today's completion price, cap): never raised.

SIZE    q in {0} U [min_qty .. imbalance] maximising, over the two
        outcomes with the fair as probability (exact arithmetic),

            U(q) = E[P&L(q)] - risk_aversion * Var-per-contract^2-term
                 = f_o * W_o(q) + f_s * W_s(q)
                   - risk_aversion * f_o * f_s * (W_o(q) - W_s(q))^2

        W_o(q) = pos_o - cost - q*h,  W_s(q) = pos_s + q - cost - q*h.
        The order is min(clip, q*). q* = 0 = hold the residual (no forced
        pairing). A hedge that would LOWER min(W_o, W_s) is never placed.

Completions stay exempt from the edge gate. No fair, any unusable input or
any error: the caller keeps today's completion unchanged.
Everything computed here is a decision input; nothing is realized P&L.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

ONE, ZERO = D(1), D(0)

DEFAULTS: Dict[str, Any] = {
    "give_max": "0.01",       # never above fair + this (and the pair cap)
    "give_min": "-0.02",      # deepest discount demanded
    "weaken_k": "0.5",        # give per $ the held fair fell since entry
    "weaken_dead": "0.01",    # fair moves inside this are not weakening
    "expo_usd": "0.01",       # give at unpaired cost == the unpaired cap
    "edge_k": "1",            # discount per $ of held edge vs cost
    "risk_aversion": "0.005",  # $ per contract^2 of outcome variance
}


def _d(x: Any) -> D:
    return x if isinstance(x, D) else D(str(x))


def _cfg(cfg: Optional[Mapping[str, Any]]) -> Dict[str, D]:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg or {}).items()
                if v is not None and k in DEFAULTS})
    return {k: _d(v) for k, v in out.items()}


def note_entry(ref: Optional[Mapping[str, Any]], held: Optional[str],
               unpaired: Any, fair_held: Any) -> Optional[Dict[str, str]]:
    """Quantity-weighted fair of the held side at acquisition of the
    CURRENT unpaired inventory. `ref` is the previous value (or None);
    returns the new one (strings: JSON-safe). Flat or side change resets;
    growth blends the new contracts in at the fair now; a shrink keeps the
    entry fair. With no fair now the reference is returned unchanged, so
    contracts acquired meanwhile are blended at the next known fair."""
    try:
        u = _d(unpaired)
        if held not in ("YES", "NO") or u <= 0:
            return None
        r = dict(ref) if ref and ref.get("side") == held else None
        if fair_held is None:
            return r
        f = _d(fair_held)
        if not (ZERO < f < ONE):
            return r
        if r is None:
            return {"side": held, "qty": str(u), "fair": str(f)}
        q0, f0 = _d(r["qty"]), _d(r["fair"])
        if u > q0 > 0:
            f0 = (f0 * q0 + f * (u - q0)) / u
        return {"side": held, "qty": str(u), "fair": str(f0)}
    except Exception:                                     # noqa: BLE001
        return None


def outcomes(pos_o: D, pos_s: D, cost: D, q: D, h: D) -> tuple:
    """(P&L if the held side wins, P&L if the hedge side wins) after buying
    q of the hedge side at h. Exact; settlement pays 1 per contract."""
    c = cost + q * h
    return pos_o - c, pos_s + q - c


def utility(pos_o: D, pos_s: D, cost: D, q: D, h: D, f_o: D,
            lam: D) -> D:
    wo, ws = outcomes(pos_o, pos_s, cost, q, h)
    f_s = ONE - f_o
    return f_o * wo + f_s * ws - lam * f_o * f_s * (wo - ws) ** 2


def give(*, held_edge: D, weakening: D, exposure: D,
         cfg: Optional[Mapping[str, Any]] = None) -> D:
    """See module docstring. `exposure` = unpaired cost / unpaired cap."""
    c = _cfg(cfg)
    weak = max(ZERO, weakening - c["weaken_dead"])
    x = min(ONE, max(ZERO, exposure))
    g = c["weaken_k"] * weak + c["expo_usd"] * x - \
        c["edge_k"] * max(ZERO, held_edge)
    if held_edge > 0 and weak <= 0:
        g = min(g, ZERO)                 # still good, not weakened: discount
    return max(c["give_min"], min(c["give_max"], g))


def plan(*, side: str, px: Any, qty: Any, pos: Mapping[str, Any],
         cost: Mapping[str, Any], fair_side: Any, tick: Any, clip: Any,
         min_qty: Any, pair_cap: Any, unpaired_cap_usd: Any,
         entry_fair_held: Any = None,
         cfg: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The adaptive completion for `side` (the lighter side).

    `px`, `qty` = today's completion (pair-cap price, min(clip, imbalance)).
    Returns {"px", "qty", "hold": bool, ...diagnostics}: hold=True means no
    completion bid now. Returns None when the rule cannot be applied (the
    caller then keeps today's completion unchanged): no/unusable fair, not
    an imbalance on the other side, or any error."""
    try:
        if fair_side is None or side not in ("YES", "NO"):
            return None
        o = "NO" if side == "YES" else "YES"
        t = _d(tick)
        f_s = _d(fair_side)
        if not (ZERO < f_s < ONE) or t <= 0:
            return None
        f_o = ONE - f_s
        p_o, p_s = _d(pos.get(o) or 0), _d(pos.get(side) or 0)
        c_o, c_s = _d(cost.get(o) or 0), _d(cost.get(side) or 0)
        need = p_o - p_s
        if need <= 0 or p_o <= 0:
            return None
        px0, q0 = _d(px), _d(qty)
        mq, cl, pc = _d(min_qty), _d(clip), _d(pair_cap)
        if px0 <= 0 or q0 <= 0 or mq <= 0 or cl <= 0:
            return None
        c = _cfg(cfg)
        avg_o = c_o / p_o
        held_edge = f_o - avg_o
        weakening = ZERO if entry_fair_held is None else \
            _d(entry_fair_held) - f_o
        ucap = _d(unpaired_cap_usd)
        expo = (need * avg_o / ucap) if ucap > 0 else ONE
        g = give(held_edge=held_edge, weakening=weakening, exposure=expo,
                 cfg=cfg)
        cap = ((f_s + g) / t).to_integral_value(rounding="ROUND_FLOOR") * t
        h = min(px0, cap)                 # never raised
        diag = {"give": str(g), "cap": str(cap), "held_edge": str(held_edge),
                "weakening": str(weakening), "exposure": str(expo),
                "px_today": str(px0), "qty_today": str(q0),
                "imbalance": str(need)}
        if h <= 0 or h + avg_o > pc:      # pair cap is never exceeded
            if h > 0:
                return None               # today's price breaks it: not ours
            return dict(diag, px=None, qty=ZERO, hold=True, why="ah_no_price")
        tot = c_o + c_s
        lam = c["risk_aversion"]
        w0 = min(outcomes(p_o, p_s, tot, ZERO, h))
        u0 = utility(p_o, p_s, tot, ZERO, h, f_o, lam)
        best_q, best_u = ZERO, u0
        hi = max(need, mq)                # venue minimum: tiny overfill
        q = mq
        while q <= hi:
            if min(outcomes(p_o, p_s, tot, q, h)) >= w0:
                u = utility(p_o, p_s, tot, q, h, f_o, lam)
                if u > best_u:
                    best_q, best_u = q, u
            q += ONE
        if q - ONE < hi and hi == need:   # fractional imbalance: its exact end
            if min(outcomes(p_o, p_s, tot, need, h)) >= w0:
                u = utility(p_o, p_s, tot, need, h, f_o, lam)
                if u > best_u:
                    best_q, best_u = need, u
        diag.update(target=str(best_q), u_hold=str(u0), u_best=str(best_u),
                    worst_hold=str(w0))
        if best_q <= 0:
            return dict(diag, px=h, qty=ZERO, hold=True, why="ah_hold")
        oq = min(best_q, cl, max(q0, mq))  # never more than today's order
        if oq < mq:
            return dict(diag, px=h, qty=ZERO, hold=True, why="ah_hold")
        if min(outcomes(p_o, p_s, tot, oq, h)) < w0:
            return dict(diag, px=h, qty=ZERO, hold=True, why="ah_worst")
        return dict(diag, px=h, qty=oq, hold=False,
                    worst_after=str(min(outcomes(p_o, p_s, tot, oq, h))))
    except Exception:                                     # noqa: BLE001
        return None
