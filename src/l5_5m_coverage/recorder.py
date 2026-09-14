"""Continuous market-state capture around L5's 5m windows. READ ONLY.

WHY THIS AND NOT A 5m SWITCH CLASSIFIER. The BTC15m switch signal is usable
(OOS AUC 0.674 on n=446) and the 5m cohorts are not: 1 and 4 OOS switches.
Any 5m switch rule fitted today would be BTC15m behaviour wearing a 5m label.
This records the states that would make a 5m answer possible on its own
evidence, and nothing here decides, prices, sizes or submits anything.

THE LESSON FROM THE GEMINI COLLECTOR IS BUILT IN. That collector lost the
first 82 s of every window because discovery and subscription were fused to
one 45 s REST tick and the session period was an exact multiple of the window
period, so the loss phase-locked instead of averaging out. Here: windows are
enumerated from the clock rather than discovered (5m windows are at fixed
300 s epochs, so there is nothing to discover), capture opens at
`start - PRE_ROLL_S`, and the roll runs every loop iteration rather than on a
tick. `first_sample_lead_s` is recorded per window so a regression is visible
in the digest rather than inferred later.

STATES WHERE L5 DOES NOTHING ARE THE POINT. A switch model needs CONTINUE
examples, and a recorder that only wakes on a fill manufactures a dataset with
no negatives in it.

UNAVAILABLE IS NULL WITH A REASON. Depth, spread and flow come from the book
when the venue serves one; when it does not, the field is None beside an
explicit reason. Never a fabricated 0 -- a 0 spread reads as a locked market
and a 0 depth as an empty one.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence

SCHEMA = "l5_5m_coverage/v1"
ASSETS = ("btc", "eth")
HORIZON = "5m"
WINDOW_S = 300
PRE_ROLL_S = 120.0          # capture opens T-120s, per the research spec
SAMPLE_S = 1.0
RETURN_LAGS_S = (5, 15, 30, 60)

R_NO_BOOK = "book not two-sided: field not derivable"
R_NO_DEPTH = "venue served no size at the touch"
R_NO_HISTORY = "not enough prior samples at this lag yet"
R_NO_HELD = "no inventory held: pair undefined before a first leg exists"
R_NO_SOURCE_TS = "venue served no timestamp of its own on this payload"


def window_start(now: float, k: int = 0) -> int:
    """The k-th 5m boundary at or after `now`. Windows are at fixed epochs,
    so they are ENUMERATED, never discovered -- the Gemini late-subscribe
    defect cannot occur here by construction."""
    return int(now // WINDOW_S + (1 if now % WINDOW_S else 0) + k) * WINDOW_S


def slug(asset: str, start: int) -> str:
    return f"{asset}-updown-{HORIZON}-{start}"


def due_at(start: int) -> float:
    """When capture for this window must already be open."""
    return start - PRE_ROLL_S


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def book_state(book: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Both outcome prices, spread and depth, each with its own reason."""
    b = book or {}
    ub, ua = _f(b.get("up_bid")), _f(b.get("up_ask"))
    db, da = _f(b.get("down_bid")), _f(b.get("down_ask"))
    two_sided = ub is not None and ua is not None
    spread = (ua - ub) if two_sided else None
    # the pair a maker can attain by resting on both touches: bid + (1 - ask)
    # is what the venue would fill, so the attainable pair is 1 - spread.
    attainable = (1.0 - spread) if spread is not None else None
    return {
        "up_bid": ub, "up_ask": ua, "down_bid": db, "down_ask": da,
        "two_sided": two_sided,
        "spread": spread,
        "spread_reason": None if spread is not None else R_NO_BOOK,
        "attainable_pair_cost": attainable,
        "attainable_pair_cost_reason": None if attainable is not None else R_NO_BOOK,
        "depth_bid": _f(b.get("up_bid_size")),
        "depth_ask": _f(b.get("up_ask_size")),
        "depth_reason": (None if _f(b.get("up_bid_size")) is not None else R_NO_DEPTH),
    }


def returns(history: Sequence[Dict[str, Any]], now: float,
            mid: Optional[float]) -> Dict[str, Any]:
    """Mid-price return over each lag, as-of and never ahead of `now`."""
    out: Dict[str, Any] = {}
    for lag in RETURN_LAGS_S:
        past = None
        for s in reversed(history):
            if s["t"] <= now - lag:
                past = s.get("mid")
                break
        ok = past is not None and mid is not None and past != 0
        out[f"ret_{lag}s"] = (round(mid - past, 6) if ok else None)
        out[f"ret_{lag}s_reason"] = None if ok else R_NO_HISTORY
    return out


def inventory_state(up: float, dn: float, cost_up: float, cost_dn: float,
                    complement_price: Optional[float]) -> Dict[str, Any]:
    """L5's inventory as the switch question needs it."""
    matched = min(up, dn)
    residual = up - dn
    total = up + dn
    avg_up = (cost_up / up) if up else None
    avg_dn = (cost_dn / dn) if dn else None
    if up > dn:
        held, why = avg_up, ("held side UP" if avg_up is not None else R_NO_HELD)
    elif dn > up:
        held, why = avg_dn, ("held side DOWN" if avg_dn is not None else R_NO_HELD)
    else:
        held, why = None, R_NO_HELD
    pair = (held + complement_price
            if held is not None and complement_price is not None else None)
    return {
        "inv_up": up, "inv_dn": dn,
        "matched_qty": matched,
        "residual_abs": abs(residual),
        "residual_signed": residual,
        "residual_relative": (residual / total if total else None),
        "residual_relative_reason": (None if total else "no inventory yet"),
        "held_avg_cost": held, "held_avg_cost_reason": why,
        "held_pair_cost": pair,
        "held_pair_cost_reason": (None if pair is not None else
                                  (why if held is None else R_NO_BOOK)),
    }


def sample(*, asset: str, start: int, now: float, book: Optional[Dict[str, Any]],
           inv: Dict[str, Any], flow: Optional[Dict[str, Any]],
           history: Sequence[Dict[str, Any]],
           t_source: Optional[float] = None) -> Dict[str, Any]:
    """One complete market-state observation. L5 doing nothing is a valid
    and REQUIRED observation: it is the CONTINUE class."""
    bs = book_state(book)
    mid = ((bs["up_bid"] + bs["up_ask"]) / 2.0) if bs["two_sided"] else None
    prev_pair = next((s.get("attainable_pair_cost") for s in reversed(history)
                      if s.get("attainable_pair_cost") is not None), None)
    d_pair = (bs["attainable_pair_cost"] - prev_pair
              if bs["attainable_pair_cost"] is not None and prev_pair is not None
              else None)
    complement = bs["up_ask"] if (inv.get("dn", 0) > inv.get("up", 0)) else (
        (1.0 - bs["up_bid"]) if bs["up_bid"] is not None else None)
    rec: Dict[str, Any] = {
        "schema": SCHEMA, "asset": asset, "slug": slug(asset, start),
        # collection clock and venue clock are kept SEPARATE and never
        # substituted for one another: the gap between them is the only way to
        # tell a stale read from a quiet market.
        "t": now,
        "t_collected": now,
        "t_source": t_source,
        "t_source_reason": (None if t_source is not None else R_NO_SOURCE_TS),
        "source_age_s": (now - t_source) if t_source is not None else None,
        "window_start": start, "window_end": start + WINDOW_S,
        "tte_s": start + WINDOW_S - now,
        "pre_roll": now < start,
        "mid": mid,
        "delta_attainable_pair_cost": d_pair,
        "delta_attainable_pair_cost_reason": (None if d_pair is not None
                                              else R_NO_HISTORY),
        "flow_buy_qty": (flow or {}).get("buy_qty"),
        "flow_sell_qty": (flow or {}).get("sell_qty"),
        # a FAILED read and a QUIET venue are not the same observation, and
        # collapsing them makes a broken collector look like an idle market.
        "flow_reason": (None if flow else
                        "L5 activity read failed: fills UNKNOWN for this sample"),
        "l5_acted": (bool(flow.get("l5_fill")) if flow else None),
        "l5_acted_reason": (None if flow else
                            "cannot distinguish no-action from an unread feed"),
        "labels": ("OBSERVATION ONLY -- no order path, no decision, no state. "
                   "A window where L5 does nothing is recorded in full: those "
                   "are the CONTINUE examples a switch model cannot be fitted "
                   "without. UNKNOWN is null with a reason, never a zero."),
    }
    rec.update(bs)
    rec.update(returns(history, now, mid))
    rec.update(inventory_state(
        float(inv.get("up") or 0), float(inv.get("dn") or 0),
        float(inv.get("cost_up") or 0), float(inv.get("cost_dn") or 0),
        complement))
    return rec


def digest(asset: str, start: int, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-window coverage, reported so a collection defect is visible in the
    digest rather than reconstructed months later from sparse windows."""
    if not samples:
        return {"schema": SCHEMA, "asset": asset, "slug": slug(asset, start),
                "n_samples": 0, "coverage": None,
                "coverage_reason": "no samples: capture never opened"}
    ts = [s["t"] for s in samples]
    gaps = [b - a for a, b in zip(ts, ts[1:])] or [0.0]
    span = ts[-1] - ts[0]
    return {
        "schema": SCHEMA, "asset": asset, "slug": slug(asset, start),
        "n_samples": len(samples),
        "first_sample_utc": ts[0],
        "last_sample_utc": ts[-1],
        "first_sample_lead_s": start - ts[0],      # >0 means we were early
        "last_sample_tte_s": start + WINDOW_S - ts[-1],
        "span_s": span,
        "max_gap_s": max(gaps),
        "coverage": (len(samples) * SAMPLE_S / (WINDOW_S + PRE_ROLL_S)),
        "two_sided_frac": sum(1 for s in samples if s["two_sided"]) / len(samples),
        "l5_fills": sum(1 for s in samples if s["l5_acted"]),
        "samples_with_unread_l5_feed": sum(1 for s in samples
                                           if s["l5_acted"] is None),
        "pre_roll_samples": sum(1 for s in samples if s["pre_roll"]),
    }


def dedupe_fills(responses: Sequence[Sequence[Any]]) -> List[Any]:
    """Multiset union across overlapping responses.

    L5 genuinely repeats identical fills -- same price, same size, same second
    -- so deduplicating by identity destroys real trades, and summing across
    overlapping pages double-counts them. The canonical rule this program uses
    everywhere: an item's multiplicity is the MAXIMUM seen in any SINGLE
    response, never the sum across responses and never one.
    """
    best: Dict[Any, int] = {}
    for resp in responses:
        seen: Dict[Any, int] = {}
        for item in resp:
            seen[item] = seen.get(item, 0) + 1
        for item, n in seen.items():
            if n > best.get(item, 0):
                best[item] = n
    out: List[Any] = []
    for item, n in best.items():
        out.extend([item] * n)
    return out


def run_digest(digests: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Service-level coverage, so a gap is visible while it is happening."""
    done = [d for d in digests if d.get("n_samples")]
    if not done:
        return {"schema": SCHEMA, "windows_captured": 0,
                "first_sample_utc": None, "last_sample_utc": None,
                "coverage_reason": "no window captured yet"}
    leads = sorted(d["first_sample_lead_s"] for d in done)
    cov = sorted(d["coverage"] for d in done)
    return {
        "schema": SCHEMA,
        "windows_captured": len(done),
        "windows_attempted": len(digests),
        "windows_missed": len(digests) - len(done),
        "first_sample_utc": min(d["first_sample_utc"] for d in done),
        "last_sample_utc": max(d["last_sample_utc"] for d in done),
        "first_sample_lead_s_p50": leads[len(leads) // 2],
        "first_sample_lead_s_min": leads[0],
        "coverage_p50": cov[len(cov) // 2],
        "coverage_min": cov[0],
        "max_gap_s": max(d["max_gap_s"] for d in done),
        "l5_fills": sum(d["l5_fills"] for d in done),
        # the signal that tells a quiet venue from a blocked feed. If this is
        # non-zero the l5_fills count above is an UNDERCOUNT, not an observation.
        "samples_with_unread_l5_feed": sum(d.get("samples_with_unread_l5_feed", 0)
                                           for d in done),
        "windows_with_no_l5_action": sum(
            1 for d in done
            if not d["l5_fills"] and not d.get("samples_with_unread_l5_feed")),
        "labels": ("windows_with_no_l5_action is not a failure: those are the "
                   "CONTINUE examples. A negative first_sample_lead_s IS a "
                   "failure -- capture opened after the window did."),
    }
