"""BOTH LANES, COMPARED ONLY WHERE MODEL A CAN TRADE.

The whole-window BTC15m promotion is withdrawn. A 15-minute contract's tape
is mostly flow the frozen policy never sees: `TTE_policy
quote_between_tte_s = [0, 300]` is a MEASURED L5 parameter and is not touched
here or anywhere. So every comparison below is restricted to **TTE <= 300**,
which is the only interval either lane can act in, and the restriction is
applied to the VENUE's tape as well as to ours.

This module measures the two running lanes. It chooses nothing, ranks
nothing and proposes nothing.
"""
from __future__ import annotations

import glob
import json
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the only interval the frozen policy quotes in. Read, never set.
REACHABLE_TTE_S = 300.0

#: a matched pair above this is a matched loss
PAR = D("1")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def quoting_window() -> Sequence[float]:
    """Model A's own quoting window, read from the frozen spec."""
    from src.gemini_execution import continuous_probe as CP
    lo, hi = CP.probe_params().get("TTE_policy")["quote_between_tte_s"]
    return (float(lo), float(hi))


def lane_windows(out_glob: str) -> List[Dict[str, Any]]:
    """Every completed window a running lane has persisted."""
    rows: List[Dict[str, Any]] = []
    for p in sorted(glob.glob(out_glob)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for w in (d.get("windows") or []):
            w = dict(w)
            w["_file"] = p
            rows.append(w)
    return rows


def tape_in_window(prints: Sequence[Mapping[str, Any]], *,
                   expiry_ms: Any) -> Dict[str, Any]:
    """The venue's own flow, restricted to the reachable interval."""
    exp = _dec(expiry_ms)
    n = 0
    notional = D("0")
    buys = sells = 0
    for t in prints or []:
        ms, px, amt = (_dec(t.get("utc_ms")), _dec(t.get("price")),
                       _dec(t.get("amount")))
        if None in (ms, px, amt) or exp is None:
            continue
        tte = float((exp - ms) / D("1000"))
        if tte > REACHABLE_TTE_S or tte < 0:
            continue
        n += 1
        notional += amt * px
        if str(t.get("type")) == "buy":
            buys += 1
        else:
            sells += 1
    return {"prints": n, "notional_usd": str(notional),
            "buy_prints": buys, "sell_prints": sells,
            "two_sided": bool(buys > 0 and sells > 0)}


def lane_census(windows: Sequence[Mapping[str, Any]],
                tapes: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """THE THIRTEEN COLUMNS, over the reachable interval only.

    Prospective: these are windows a running lane actually lived through,
    not a replay of a tape we fetched afterwards.
    """
    done = [w for w in windows if w.get("cycles")]
    quotes = first_fills = opp_after = comp_fills = 0
    matched_c = gross_c = resid_c = D("0")
    cap = D("0")
    par_pairs = 0
    prints = D("0")
    notional = D("0")
    two_sided = 0
    for w in done:
        sym = w.get("symbol")
        t = tapes.get(sym) or {}
        prints += D(int(t.get("prints") or 0))
        notional += _dec(t.get("notional_usd")) or D("0")
        two_sided += 1 if t.get("two_sided") else 0
        inv = w.get("inventory") or {}
        matched_c += _dec(inv.get("matched_qty")) or D("0")
        resid_c += abs(_dec(inv.get("directional_residual")) or D("0"))
        cap += _dec(inv.get("total_spend_usd")) or D("0")
        basis = _dec(inv.get("effective_matched_pair_cost"))
        if (_dec(inv.get("matched_qty")) or D("0")) > 0 and basis is not None \
                and basis <= PAR:
            par_pairs += 1
        # what the lane actually did, from its own recorded cycles
        for r in (w.get("cycles_detail") or []):
            quotes += len(r.get("model_a_wanted") or [])
        quotes += int(w.get("model_a_quotes") or 0)
        first_fills += int(w.get("first_fills") or 0)
        comp_fills += int(w.get("complement_fills") or 0)
        opp_after += int(w.get("opposite_flow_after_fill") or 0)
        gross_c += _dec(w.get("gross_contracts")) or D("0")
    return {
        "windows": len(done),
        "prints_in_reachable_window": int(prints),
        "notional_in_reachable_window_usd": str(notional),
        "two_sided_windows": two_sided,
        "two_sided_share": (round(two_sided / len(done), 3) if done else None),
        "model_a_quotes": quotes,
        "first_fills": first_fills,
        "opposite_flow_after_fill": opp_after,
        "complete_fills": comp_fills,
        "par_constrained_pairs": par_pairs,
        "matched_contracts": str(matched_c),
        "gross_contracts": str(gross_c),
        "matched_over_gross": (str(round(matched_c / gross_c, 4))
                               if gross_c > 0 else None),
        "terminal_residual_contracts": str(resid_c),
        "capital_at_risk_usd": str(cap),
        "reachable_tte_s": REACHABLE_TTE_S,
        "labels": ("PROSPECTIVE, and restricted to TTE <= 300 -- the only "
                   "interval the frozen policy quotes in. SHADOW: no order "
                   "existed and nothing is realized."),
    }


def the_sequence(windows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """THE TRIGGER: OPEN fill -> inventory change -> COMPLETE fill -> matched.

    Reported the moment it happens, and counted rather than described,
    because repetition is what would justify preparing a bounded live probe.
    """
    hits = []
    for w in windows:
        inv = w.get("inventory") or {}
        m = _dec(inv.get("matched_qty")) or D("0")
        if m <= 0:
            continue
        basis = _dec(inv.get("effective_matched_pair_cost"))
        hits.append({
            "symbol": w.get("symbol"), "cohort": w.get("cohort"),
            "matched": str(m),
            "effective_pair_basis": (str(basis) if basis is not None
                                     else None),
            "at_or_below_par": bool(basis is not None and basis <= PAR),
            "started_utc": w.get("started_utc"),
        })
    return {
        "occurrences": len(hits),
        "at_or_below_par": sum(1 for h in hits if h["at_or_below_par"]),
        "rows": hits,
        "is_the_trigger": bool(len(hits) >= 2),
        "why": ("one occurrence is an anecdote. Repetition of OPEN fill -> "
                "inventory change -> COMPLETE fill -> matched inventory is "
                "what would justify preparing a bounded live proposal, and "
                "nothing is prepared before it."),
    }
