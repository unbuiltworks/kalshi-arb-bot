"""P2: counterfactual ablation. Real fills only; nothing is ever invented.

THE METHODOLOGICAL POINT THIS FILE IS BUILT AROUND. Removing an event changes
every subsequent state, so an event's CLASS and Δpair must be recomputed from
the RETAINED sequence, never inherited from the original path. An event that
was a residual extension in L5's real sequence can become a match expansion
once an earlier event is dropped. `replay()` therefore recomputes inventory,
VWAPs, matched quantity and the pair cost from scratch at every retained step.

NO EVENT IS EVER SUBSTITUTED. Dropping a fill removes it; it does not free
capital to buy something else, and no replacement fill is invented. Every
ablation is a strict subsequence of what actually happened.

TWO ANALYSES, KEPT APART:

  STATIC ORACLE  the keep decision reads the ORIGINAL path's realized Δpair.
                 Diagnostic only: the original Δpair depends on an event
                 ordering the ablated run no longer has.
  CAUSAL REPLAY  the keep decision sees only the retained state at that
                 instant. Executable.

Note that "would this event raise my pair cost?" is knowable BEFORE accepting
it -- the trader knows both running VWAPs and the offered price and size. So
rejecting worsening events is not inherently an oracle; what makes the static
version an oracle is that it scores against a path the ablation destroys.

SETTLEMENT. Only the winning token pays, $1 each, so payout is simply the
quantity held on the winning side. The winner is the outcome L5 redeemed;
windows where he redeemed nothing, or redeemed an outcome he never held, are
UNKNOWN and are excluded from settlement economics rather than guessed.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

from analysis.l5.inventory_trajectory.trajectory import load, window_end
from analysis.l5.next_side.features import aggregate_transactions

WINDOW_S = 900


def winners(activity: str) -> Dict[str, Optional[str]]:
    """slug -> winning outcome, or None when it cannot be established."""
    red: Dict[str, set] = {}
    qty: Dict[str, Dict[str, float]] = {}
    for line in open(activity):
        line = line.strip()
        if not line:
            continue
        for r in json.loads(line).get("payload") or []:
            s = str(r.get("eventSlug") or "")
            if not s.startswith("btc-updown-15m"):
                continue
            if r.get("type") == "REDEEM":
                red.setdefault(s, set()).add(r.get("outcome"))
            elif r.get("type") == "TRADE":
                q = qty.setdefault(s, {})
                q[r.get("outcome")] = q.get(r.get("outcome"), 0.0) + float(
                    r.get("size") or 0.0)
    out: Dict[str, Optional[str]] = {}
    for s in qty:
        o = red.get(s)
        if o and len(o) == 1:
            w = next(iter(o))
            # you can only redeem what you held; anything else is unexplained
            out[s] = w if qty[s].get(w, 0.0) > 0 else None
        else:
            out[s] = None
    return out


def replay(evs: Sequence[dict], keep: Callable[[dict, dict], bool],
           *, winner: Optional[str]) -> Dict[str, Any]:
    """Walk the events in order, recomputing EVERYTHING from what is retained."""
    up_q = dn_q = up_c = dn_c = 0.0
    run = 0
    prev_side: Optional[str] = None
    n_kept = 0
    pair_at_formation: Optional[float] = None
    for e in evs:
        side = e["side"]
        sz, usd = e["qty"], e["usdc"]
        up_v = (up_c / up_q) if up_q > 0 else None
        dn_v = (dn_c / dn_q) if dn_q > 0 else None
        pair = (up_v + dn_v) if (up_v is not None and dn_v is not None) else None
        n_up = up_q + (sz if side == "Up" else 0.0)
        n_dn = dn_q + (sz if side == "Down" else 0.0)
        n_uc = up_c + (usd if side == "Up" else 0.0)
        n_dc = dn_c + (usd if side == "Down" else 0.0)
        n_up_v = (n_uc / n_up) if n_up > 0 else None
        n_dn_v = (n_dc / n_dn) if n_dn > 0 else None
        n_pair = (n_up_v + n_dn_v) if (n_up_v is not None
                                       and n_dn_v is not None) else None
        # state as the trader would see it at this instant, from RETAINED history
        state = {
            "up_qty": up_q, "dn_qty": dn_q, "up_vwap": up_v, "dn_vwap": dn_v,
            "pair": pair, "matched": min(up_q, dn_q),
            "resid": up_q - dn_q,
            "run_len": (run + 1 if side == prev_side else 1),
            "after_switch": (prev_side is not None and side != prev_side),
            "n_kept": n_kept,
            "d_pair": ((n_pair - pair) if (n_pair is not None
                                           and pair is not None) else None),
            "d_matched": min(n_up, n_dn) - min(up_q, dn_q),
            "same_side_vwap": (up_v if side == "Up" else dn_v),
            "exec_price": (usd / sz) if sz > 0 else None,
        }
        if not keep(e, state):
            continue
        up_q, dn_q, up_c, dn_c = n_up, n_dn, n_uc, n_dc
        run = state["run_len"]
        prev_side = side
        n_kept += 1
        if pair_at_formation is None and n_pair is not None:
            pair_at_formation = n_pair

    spend = up_c + dn_c
    up_v = (up_c / up_q) if up_q > 0 else None
    dn_v = (dn_c / dn_q) if dn_q > 0 else None
    pair = (up_v + dn_v) if (up_v is not None and dn_v is not None) else None
    matched = min(up_q, dn_q)
    resid = abs(up_q - dn_q)
    tot = up_q + dn_q
    payout = None
    if winner in ("Up", "Down"):
        payout = up_q if winner == "Up" else dn_q
    matched_econ = (matched * (1.0 - pair)) if pair is not None else None
    total_econ = (payout - spend) if payout is not None else None
    resid_econ = ((total_econ - matched_econ)
                  if (total_econ is not None and matched_econ is not None)
                  else None)
    return {
        "events_retained": n_kept, "spend": spend,
        "up_qty": up_q, "dn_qty": dn_q,
        "matched_qty": matched, "residual_qty": resid,
        "normalized_imbalance": (resid / tot) if tot > 0 else None,
        "pair_at_formation": pair_at_formation, "final_pair_cost": pair,
        "settlement_cashflow": payout,
        "matched_economics": matched_econ,
        "residual_economics": resid_econ,
        "total_economics": total_econ,
        "return_per_dollar": ((total_econ / spend)
                              if (total_econ is not None and spend > 0) else None),
    }


# ------------------------------------------------------------------- keeps

def keep_all(e, s):
    return True


def static_drop(idx: set):
    """STATIC ORACLE: drop by position, decided from the ORIGINAL path."""
    box = {"i": -1}

    def f(e, s):
        box["i"] += 1
        return box["i"] not in idx
    return f


def causal_not_worsening_residual(e, s):
    d = s["d_pair"]
    return not (s["d_matched"] <= 0 and d is not None and d > 0)


def causal_not_improving_residual(e, s):
    d = s["d_pair"]
    return not (s["d_matched"] <= 0 and d is not None and d < 0)


def causal_no_residual(e, s):
    return s["d_matched"] > 0


def causal_not_worsening_expansion(e, s):
    d = s["d_pair"]
    return not (s["d_matched"] > 0 and d is not None and d > 0)


def causal_not_worsening_any(e, s):
    d = s["d_pair"]
    return not (d is not None and d > 0)


def causal_run_cap(n: int):
    return lambda e, s: s["run_len"] <= n


def causal_tte_stop(tte: float):
    return lambda e, s: (e["tte_s"] is None or e["tte_s"] >= tte)


def hindsight_progress_stop(total: int, frac: float):
    """NOT EXECUTABLE: a window's eventual event count is not knowable mid-window."""
    box = {"i": 0}
    lim = max(1, int(total * frac))

    def f(e, s):
        box["i"] += 1
        return box["i"] <= lim
    return f
