#!/usr/bin/env python3
"""Replay captured windows under a CHOSEN QUEUE MODEL. Offline, read-only.

WHAT THIS ANSWERS. The shadow's fill simulator modelled a NO rung as resting
at the FRONT of a queue it could not see, while a YES rung had to clear the
real displayed ladder. `tape_replay.size_ahead` now queues both legs against
the ladder an aggressor actually consumes. This replays the SAME captured
rungs, the SAME recorded depth and the SAME public tape under each model, so
the difference is the queue model and nothing else.

NOTHING IS RE-DECIDED. The rungs come from what the lane recorded wanting at
each cycle. The policy is not re-run, no price is changed and no parameter is
tuned -- replaying a fix must not also quietly replay a different strategy.

The tape is fetched from the venue rather than read from the evidence,
because the recorded flow keeps only the newest 40 prints per cycle while
`/v1/trades` serves the whole expired contract.

HYPOTHETICAL throughout. No order existed; nothing is submitted, filled,
settled, realized or a cost basis.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import hypothetical as HY
from src.gemini_execution import tape_replay as TR

MAKER_RATE = D("0.0175")

LABELS = ("HYPOTHETICAL. No order existed and nothing was submitted, filled, "
          "settled or realized. Queue position is the displayed size at "
          "placement; our own presence would have changed this flow.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def maker_fee(qty: D, price: D) -> D:
    return MAKER_RATE * qty * price * (D("1") - price)


def actions_of(cycle: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The rungs this cycle wanted, from either evidence schema."""
    acts = cycle.get("l5_action")
    if isinstance(acts, list) and acts:
        return [dict(a) for a in acts]
    dec = cycle.get("decision") or {}
    place = ((dec.get("plan") or {}).get("place") or [])
    out = []
    for p in place:
        r = p.get("rung") if isinstance(p, Mapping) else None
        if isinstance(r, Mapping):
            out.append(dict(r))
    if out:
        return out
    wanted = cycle.get("model_a_wanted")
    return [dict(a) for a in wanted] if isinstance(wanted, list) else []


def group_windows(rows: Sequence[Mapping[str, Any]]
                  ) -> Dict[str, List[Dict[str, Any]]]:
    """Cycles per contract, in cycle order."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if r.get("kind") == "window":
            continue
        sym = r.get("symbol")
        if not sym or r.get("tte_s") is None:
            continue
        by.setdefault(str(sym), []).append(dict(r))
    for sym in by:
        by[sym].sort(key=lambda c: (c.get("cycle") or 0, str(c.get("utc"))))
    return by


def _utc_ms(cycle: Mapping[str, Any]) -> Optional[D]:
    from datetime import datetime
    u = cycle.get("utc")
    if not u:
        return None
    try:
        return D(str(datetime.fromisoformat(
            str(u).replace("Z", "+00:00")).timestamp() * 1000))
    except (ValueError, TypeError):
        return None


def replay_window(symbol: str, cycles: Sequence[Mapping[str, Any]],
                  prints: Sequence[Mapping[str, Any]], *,
                  queue_mode: str, policy: str = "GEMINI_RECYCLE"
                  ) -> Dict[str, Any]:
    """One contract, one queue model. Rungs and depth are as recorded."""
    inv = CP.Inventory()
    hyp = HY.HypotheticalBook(symbol=symbol, policy=policy,
                              queue_mode=queue_mode)
    tape = sorted((dict(p) for p in prints),
                  key=lambda p: _dec(p.get("utc_ms")) or D("0"))
    prev = None
    for c in cycles:
        now = _utc_ms(c)
        if now is None:
            continue
        # THE PRINTS CAME FIRST -- they landed while the PREVIOUS cycle's
        # rungs were resting, exactly as the live loop walks them.
        slice_ = [p for p in tape
                  if (prev is None or (_dec(p.get("utc_ms")) or D("0")) > prev)
                  and (_dec(p.get("utc_ms")) or D("0")) <= now]
        try:
            hyp.consume(slice_, inv=inv, book=c.get("book") or {},
                        depth=c.get("depth"), tte_s=c.get("tte_s"))
            hyp.retarget(actions_of(c), now_ms=now, depth=c.get("depth"))
        except Exception as exc:                                 # noqa: BLE001
            return {"symbol": symbol, "error": "%s: %s"
                    % (type(exc).__name__, exc), "queue_mode": queue_mode}
        prev = now

    rep = hyp.report()
    ir = inv.report()
    basis = _dec(ir.get("effective_matched_pair_cost"))
    matched = _dec(ir.get("matched_qty")) or D("0")
    fee_pos = None
    if basis is not None and matched > 0 and D("0") < basis <= D("2"):
        half = basis / D("2")
        per_pair_fee = (maker_fee(D("1"), half) * D("2"))
        fee_pos = bool(basis + per_pair_fee <= D("1"))
    return {
        "symbol": symbol, "queue_mode": queue_mode, "cycles": len(cycles),
        "prints": len(tape),
        "rungs_rested": rep["rungs_rested"], "fills": rep["fills"],
        "first_fills": rep["first_fills"], "open_fills": rep["open_fills"],
        "extend_fills": rep["extend_fills"],
        "complete_fills": rep["complete_fills"],
        "fills_on_unknown_queue": rep["fills_resting_on_an_unknown_queue"],
        "matched_qty": str(matched),
        "effective_matched_pair_cost": (str(basis) if basis is not None
                                        else None),
        "fee_positive_pair": fee_pos,
        "terminal_residual": str(ir.get("directional_residual")),
        "labels": LABELS,
    }


def census(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Totals over replayed windows. Matched and residual are the metric."""
    ok = [r for r in rows if not r.get("error")]
    bases = sorted(_dec(r["effective_matched_pair_cost"]) for r in ok
                   if r.get("effective_matched_pair_cost")
                   and D("0") < (_dec(r["effective_matched_pair_cost"])
                                 or D("0")) <= D("2"))
    n = len(bases)
    med = (None if not n else
           (bases[n // 2] if n % 2 else (bases[n // 2 - 1] + bases[n // 2])
            / D("2")))
    resid = sum((abs(_dec(r["terminal_residual"]) or D("0")) for r in ok),
                D("0"))
    return {
        "windows": len(ok), "errors": sum(1 for r in rows if r.get("error")),
        "rungs_rested": sum(r["rungs_rested"] for r in ok),
        "fills": sum(r["fills"] for r in ok),
        "first_fills": sum(r["first_fills"] for r in ok),
        "extend_fills": sum(r["extend_fills"] for r in ok),
        "complete_fills": sum(r["complete_fills"] for r in ok),
        "windows_with_matched": sum(1 for r in ok
                                    if (_dec(r["matched_qty"]) or D("0")) > 0),
        "matched_qty": str(sum((_dec(r["matched_qty"]) or D("0")
                                for r in ok), D("0"))),
        "fee_positive_pairs": sum(1 for r in ok if r.get("fee_positive_pair")),
        "median_pair_basis": (str(med) if med is not None else None),
        "total_abs_terminal_residual": str(resid),
        "fills_on_unknown_queue": sum(r["fills_on_unknown_queue"] for r in ok),
        "labels": LABELS,
    }
