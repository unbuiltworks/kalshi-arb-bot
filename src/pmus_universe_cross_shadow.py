"""UNIVERSE-WIDE <=0.97 CROSS SHADOW -- a compact, observation-only log row.

NOT DEPLOYED. This module is the artifact for a deployment decision that has
not been taken. It is pure: no network, no environment, no file I/O, no clock,
no venue call, no order path of any kind. It takes decision rows the recorder's
EXISTING multi-shadow calculation has already produced and returns one compact
dict for the caller to print. It changes no selection, no capacity, no status
semantics, no trading, and nothing about the live probe.

WHY IT EXISTS. The probe answers the <=0.97 question one market at a time, and
the recorder already runs the frozen L5 model across the whole seated universe.
The universe rows never reach a reader because the recorder's full status
document truncates mid-JSON at ~80 KB in the log stream. So this emits a row
BOUNDED BY BYTES, small enough to survive the stream intact.

THE FRESHNESS ASYMMETRY, STATED UP FRONT AND ENCODED IN THE OUTPUT. The
recorder HOLDS the book, so its own quote age carries no transport delay. The
probe pays one -- measured post-cutover at p50 6.1 s, max 17.9 s -- and that
delay is the single largest reason live rows read STALE. A verdict computed
here is therefore RECORDER-LOCAL and is an UPPER BOUND on what any consumer
could execute. Every row says so, and carries the consumer penalty the caller
supplies so the optimistic and realistic readings can never be confused.

EVERY FROZEN RULE IS REUSED, NOT REIMPLEMENTED. Depth mapping, age semantics,
fail-closed UNKNOWN/STALE handling, the single 0.97 arm and the fee/rebate
economics all come from pmus_cross_shadow_live. UNKNOWN and STALE never receive
a hypothetical fill, and `would_submit` is false on every path.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional

from src import pmus_cross_shadow_live as xs

SCHEMA = "pmus_universe_cross_shadow/v1"
KIND = "universe_cross_shadow"
#: hard byte budget for one emitted row. The recorder's status document
#: truncates around 80 KB in the log stream; this stays far inside that so a
#: row is never delivered half-parsed.
MAX_ROW_BYTES = 30_000
#: never emit more than this many per-market entries, whatever the seat count
MAX_MARKET_ENTRIES = 160
LABELS = ("SHADOW ONLY, RECORDER-LOCAL. No order is submitted, cancelled, "
          "repriced or replaced. Verdicts use the recorder's own quote age, "
          "which carries no consumer transport delay and is therefore an "
          "UPPER BOUND on consumer executability.")


def _book_from_decision(dec: Mapping[str, Any]) -> Optional[dict]:
    """The multi-shadow book, renamed onto the frozen observation keys.

    Returns None when the row carries no two-sided book -- absence is never
    filled in, and a missing quantity stays missing so the frozen depth rules
    can fail closed on it.
    """
    b = dec.get("book")
    if not isinstance(b, Mapping):
        return None
    return {"slug": dec.get("slug"),
            "bid": b.get("yes_bid"), "ask": b.get("yes_ask"),
            "bid_qty": b.get("yes_bid_qty"), "ask_qty": b.get("yes_ask_qty"),
            "both_sides_served": (b.get("yes_bid") is not None
                                  and b.get("yes_ask") is not None)}


def _residual(dec: Mapping[str, Any]) -> Any:
    inv = dec.get("inventory_before")
    if isinstance(inv, Mapping):
        return inv.get("residual")
    return None


def _held_avg_cost(dec: Mapping[str, Any], buys_up: Optional[bool]) -> Any:
    """Average cost of the SURPLUS side -- the side the pair is priced against.
    Buying UP means the surplus is DOWN, and vice versa."""
    inv = dec.get("inventory_before")
    if not isinstance(inv, Mapping) or buys_up is None:
        return None
    return inv.get("avg_dn") if buys_up else inv.get("avg_up")


def evaluate_decision(dec: Mapping[str, Any], *,
                      quote_age_s: Any = None) -> Optional[dict]:
    """One COMPLETE decision -> one frozen <=0.97 shadow verdict, or None.

    None means the row is not a COMPLETE state at all; it is not a refusal and
    must never be counted as one.
    """
    if str(dec.get("acted") or dec.get("sampled") or "") != "complete":
        return None
    residual = _residual(dec)
    buys_up = xs.complete_buys_up(residual)
    book = _book_from_decision(dec)
    intended = dec.get("intended_qty")
    if intended is None:
        beh = dec.get("behavioral")
        intended = beh.get("shares") if isinstance(beh, Mapping) else None
    return xs.evaluate(
        book=book,
        buys_up=bool(buys_up) if buys_up is not None else False,
        intended_qty=intended,
        held_avg_cost=_held_avg_cost(dec, buys_up),
        held_residual=residual,
        model_relation="complete",
        maker_price=(dec.get("behavioral") or {}).get("px")
        if isinstance(dec.get("behavioral"), Mapping) else None,
        quote_age_s=quote_age_s,
        book_source="recorder_multi_shadow")


def _entry(dec: Mapping[str, Any], verdict: Mapping[str, Any]) -> dict:
    """The compact per-market entry. Deliberately small: every field here has
    to survive a byte budget shared with up to 160 markets."""
    return {
        "slug": dec.get("slug"),
        "buys": verdict.get("order_buys"),
        "depth_field": verdict.get("executable_depth_field"),
        "depth": verdict.get("executable_depth"),
        "depth_status": verdict.get("executable_depth_status"),
        "px": verdict.get("executable_complement_price"),
        "qty": verdict.get("intended_qty"),
        "age_s": verdict.get("quote_age_s"),
        "verdict": verdict.get("verdict"),
        "stop": verdict.get("first_stop"),
        "pair": (verdict.get("economics") or {}).get("pair_cost"),
        "fee": (verdict.get("economics") or {}).get("confirmed_taker_fee"),
        "rebate_forgone": (verdict.get("economics") or {}).get(
            "maker_rebate_forgone"),
        "net": (verdict.get("economics") or {}).get("net_locked_pair_pnl"),
        "would_submit": False,
    }


def universe_row(decisions: Iterable[Mapping[str, Any]], *,
                 ages: Optional[Mapping[str, Any]] = None,
                 status_utc: Any = None,
                 consumer_age_penalty_s: Any = None,
                 max_bytes: int = MAX_ROW_BYTES) -> dict:
    """One compact, byte-bounded universe row. PURE.

    `ages` maps slug -> the recorder's own `last_authenticated_update_age_s`.
    A slug absent from it gets age None, which the frozen rules treat as
    UNKNOWN -- never as fresh.

    SUPPORTED entries are kept first when the budget binds, because a dropped
    SUPPORTED row would be the one row that mattered; the rollup counts every
    decision regardless of what fitted.
    """
    rollup = {"complete_states": 0, "supported": 0, "refused": 0, "unknown": 0}
    depth_status: dict = {}
    stops: dict = {}
    entries: list = []
    ages = ages or {}
    for dec in decisions:
        v = evaluate_decision(dec, quote_age_s=ages.get(dec.get("slug")))
        if v is None:
            continue
        rollup["complete_states"] += 1
        if v.get("verdict") == xs.SUPPORTED:
            rollup["supported"] += 1
        elif v.get("verdict") == xs.REFUSED:
            rollup["refused"] += 1
        else:
            rollup["unknown"] += 1
        ds = v.get("executable_depth_status") or "NO_BOOK"
        depth_status[ds] = depth_status.get(ds, 0) + 1
        stop = str(v.get("first_stop") or "?")
        for pre, lab in (("quote_age_", "quote_age_over_max"),
                         ("pair_", "pair_over_prospective_0.97"),
                         ("depth_", "depth_below_intended")):
            if stop.startswith(pre):
                stop = lab
                break
        stops[stop] = stops.get(stop, 0) + 1
        entries.append((v.get("verdict") == xs.SUPPORTED, _entry(dec, v)))

    entries.sort(key=lambda e: not e[0])          # SUPPORTED first
    kept = [e[1] for e in entries][:MAX_MARKET_ENTRIES]
    row = {
        "kind": KIND, "schema": SCHEMA,
        "shadow_only": True, "would_submit": False,
        "provenance": "RECORDER_LOCAL:" + xs.PROVENANCE,
        "prospective_arms": [str(a) for a in xs.PROSPECTIVE_THRESHOLDS],
        "excluded_prospective_arms": sorted(xs.EXCLUDED_PROSPECTIVE_THRESHOLDS),
        "live_pair_ceiling_unchanged": str(xs.LIVE_PAIR_CEILING),
        "max_quote_age_s": xs.MAX_QUOTE_AGE_S,
        "status_utc": status_utc if isinstance(status_utc, str) else None,
        "consumer_age_penalty_s": consumer_age_penalty_s,
        "age_basis": "recorder_local_no_transport_delay",
        "rollup": rollup, "depth_status": depth_status, "first_stop": stops,
        "markets": kept, "markets_emitted": len(kept),
        "markets_dropped_for_budget": 0,
        "labels": LABELS,
    }
    # bind the byte budget by dropping NON-supported entries from the tail
    while len(json.dumps(row, separators=(",", ":"),
                         default=str).encode("utf-8")) > max_bytes and row["markets"]:
        row["markets"] = row["markets"][:-1]
        row["markets_emitted"] = len(row["markets"])
        row["markets_dropped_for_budget"] += 1
    return row
