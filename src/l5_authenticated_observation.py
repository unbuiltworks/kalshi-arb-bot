"""End-to-end proof that we are no longer blind during L5's opening sequence.

    window opens -> authenticated market/book observed -> authenticated trades
    observed -> SAME market identity -> L5 action received -> contemporaneous
    authenticated book attached to that action

The public gateway exposes a BTC15m market only partway into its own window,
so the observer was structurally blind exactly while L5 opens. That produced
NO_BOOK classifications which the authenticated tape contradicts. This module
measures whether the authenticated path fixes it, and it is written so that a
FAILURE IS VISIBLE rather than absorbed:

  * `first_book_latency_s`  window open -> first AUTHENTICATED two-sided book
  * `first_trade_latency_s` window open -> first AUTHENTICATED trade
  * `coverage_pct_of_window` share of the window a fresh book was held
  * `identity`              book, trades and L5 actions resolve to ONE market
  * `attachment`            every L5 action has a contemporaneous book

A window passes only when the book is observed NO LATER than the first trade.
If the venue is trading before we can see the book, we are still blind and the
window says so.

WHAT THIS DOES NOT DO. Nothing here supplies a price. Coverage is the union of
freshness intervals around book states we actually received -- it never
interpolates between two states, never backfills before the first one and
never derives a BBO from a trade.
"""
from __future__ import annotations

import collections
import datetime as dt
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

WINDOW_S = 900.0

#: an L5 action counts as covered when a book state exists within this many
#: seconds of our receipt of it, and a book state is treated as fresh for this
#: long after receipt when measuring coverage. The recorder's own snapshot
#: cadence drives this; wider would let a stale book masquerade as
#: contemporaneous.
ATTACH_S = 15.0

USABLE = "OK"

BLIND = "BLIND_AT_OPEN"
OK = "OK"
PARTIAL = "PARTIAL"

#: A window we only ever saw AFTER it settled. Series discovery resubscribes
#: long-expired BTC15m windows, and a settled market correctly reports
#: NO_BOOK -- it has no book because it is over, not because we were blind.
#: Scoring those as BLIND_AT_OPEN would bury the live windows under dozens of
#: meaningless failures and the gate could never pass. A window counts only
#: when we hold an observation inside its OWN fifteen minutes.
NOT_LIVE = "NOT_OBSERVED_LIVE"


def _epoch(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return dt.datetime.fromisoformat(
            str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def window_start_epoch(slug: str) -> Optional[float]:
    """From the market slug: cpc-btc-updown-15m-YYYY-MM-DD-HHMMz."""
    try:
        d = slug[-16:-6]
        hhmm = slug[-5:-1]
        return dt.datetime.strptime("%s %s" % (d, hhmm), "%Y-%m-%d %H%M") \
            .replace(tzinfo=dt.timezone.utc).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def book_states(docs: Iterable[Mapping[str, Any]]) -> Dict[str, List[dict]]:
    """PMUS_BOOK_ROWS documents -> per-market states, time sorted."""
    out: Dict[str, List[dict]] = collections.defaultdict(list)
    for d in docs:
        for slug, rows in (d.get("markets") or {}).items():
            for r in rows or ():
                t = _epoch(r.get("t"))
                if t is None:
                    continue
                out[slug].append({"t": t, "bid": r.get("bid"),
                                  "ask": r.get("ask"),
                                  "bid_qty": r.get("bid_qty"),
                                  "ask_qty": r.get("ask_qty"),
                                  "status": r.get("status")})
    for k in out:
        out[k].sort(key=lambda r: r["t"])
    return dict(out)


def trade_times(docs: Iterable[Mapping[str, Any]]) -> Dict[str, List[float]]:
    """PMUS_TRADE_ROWS documents -> per-market print times, sorted."""
    out: Dict[str, List[float]] = collections.defaultdict(list)
    for d in docs:
        for slug, rows in (d.get("markets") or {}).items():
            for r in rows or ():
                t = _epoch(r.get("trade_time"))
                if t is None:
                    t = _epoch(r.get("t"))
                if t is not None:
                    out[slug].append(t)
    for k in out:
        out[k].sort()
    return dict(out)


def to_bbo(state: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """One authenticated book state -> the shape the FROZEN rule reads.

    The recorder names depth `bid_qty`/`ask_qty`; the frozen translation reads
    `bid_shares`/`ask_shares`. This is the only place that mapping lives, so a
    rename cannot silently turn into a NO_DEPTH refusal somewhere downstream.

    Returns None unless the state is a usable two-sided book. Depth that the
    venue did not serve stays None and the frozen rule refuses it as NO_DEPTH
    -- it is never replaced with a later or opposite-side value.
    """
    if not state or state.get("status") != USABLE:
        return None
    bid, ask = state.get("bid"), state.get("ask")
    if bid is None or ask is None:
        return None
    return {"bid": bid, "ask": ask,
            "bid_shares": state.get("bid_qty"),
            "ask_shares": state.get("ask_qty"),
            "t": state.get("t")}


def book_at(books: Sequence[Mapping[str, Any]], t: float,
            *, max_age_s: float = ATTACH_S) -> Optional[Dict[str, Any]]:
    """The usable book in force at `t`: the last state at or before it.

    Never a later state, so nothing an action is priced against can have been
    observed after the action. A state older than `max_age_s` is not in force
    and yields None rather than a stale price.
    """
    prior = [b for b in books
             if b.get("status") == USABLE and b["t"] <= t <= b["t"] + max_age_s]
    return to_bbo(prior[-1]) if prior else None


def _coverage(usable: Sequence[Mapping[str, Any]], ws: float) -> Dict[str, Any]:
    """Union of freshness intervals, clipped to the window.

    Each received state covers [t, t + ATTACH_S]. Nothing is filled in
    between two states beyond that, so a gap in the tape reads as a gap.
    """
    lo, hi = ws, ws + WINDOW_S
    spans: List[List[float]] = []
    for b in usable:
        a, z = max(lo, b["t"]), min(hi, b["t"] + ATTACH_S)
        if z <= a:
            continue
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], z)
        else:
            spans.append([a, z])
    covered = sum(z - a for a, z in spans)

    gaps = []
    cursor = lo
    for a, z in spans:
        if a > cursor:
            gaps.append(a - cursor)
        cursor = max(cursor, z)
    if cursor < hi:
        gaps.append(hi - cursor)
    return {"pct": round(100.0 * covered / WINDOW_S, 1),
            "max_gap_s": round(max(gaps), 1) if gaps else 0.0}


def evaluate(slug: str,
             books_by_market: Mapping[str, Sequence[Mapping[str, Any]]],
             trades_by_market: Mapping[str, Sequence[float]],
             l5_actions: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
    """The invariant for one window. Every absence is named.

    The per-market maps are passed whole, not pre-selected, so that identity
    is a real check: a book recorded under one market slug and trades under
    another shows up here as one side present and the other missing.
    """
    ws = window_start_epoch(slug)
    books = list(books_by_market.get(slug) or ())
    trades = list(trades_by_market.get(slug) or ())
    usable = [b for b in books if b.get("status") == USABLE]

    # Did we observe this window WHILE IT WAS OPEN? Anything else is a market
    # we met after it had already settled, and its emptiness says nothing
    # about our blindness.
    in_window = (lambda t: ws is not None and ws <= t <= ws + WINDOW_S)
    observed_live = (any(in_window(b["t"]) for b in books)
                     or any(in_window(t) for t in trades))
    if ws is None or not observed_live:
        return {
            "market": slug,
            "window_start_utc": (dt.datetime.fromtimestamp(
                ws, dt.timezone.utc).isoformat() if ws is not None else None),
            "first_book_latency_s": None,
            "first_trade_latency_s": None,
            "book_observed_before_first_trade": None,
            "usable_book_states": len(usable),
            "book_states_total": len(books),
            "authenticated_trades": len(trades),
            "coverage_pct_of_window": None,
            "l5_actions": len(l5_actions),
            "verdict": NOT_LIVE,
            "labels": ("only seen outside its own fifteen minutes; a settled "
                       "market has no book because it is over, so this is "
                       "NOT evidence either way and is excluded from the "
                       "gate"),
        }

    first_book = (usable[0]["t"] - ws) if (usable and ws is not None) else None
    first_trade = (trades[0] - ws) if (trades and ws is not None) else None
    cov = _coverage(usable, ws) if (usable and ws is not None) else {}

    attached = unattached = 0
    for a in l5_actions:
        t = _epoch(a.get("receipt_utc"))
        if t is not None and any(abs(b["t"] - t) <= ATTACH_S for b in usable):
            attached += 1
        else:
            unattached += 1

    action_slugs = {str((a.get("pmus") or {}).get("slug")) for a in l5_actions}
    action_slugs.discard("None")
    identity = {
        "market": slug,
        "book_present_for_market": bool(books),
        "trades_present_for_market": bool(trades),
        "book_and_trades_same_market": bool(books) and bool(trades),
        "l5_action_markets": sorted(action_slugs),
        "l5_actions_same_market": (not action_slugs
                                   or action_slugs == {slug}),
    }
    identity_ok = (identity["book_and_trades_same_market"]
                   and identity["l5_actions_same_market"])

    blind = (first_book is None
             or (first_trade is not None and first_book > first_trade))
    if blind:
        verdict = BLIND
    elif identity_ok and unattached == 0 and l5_actions:
        verdict = OK
    else:
        verdict = PARTIAL

    return {
        "market": slug,
        "window_start_utc": (dt.datetime.fromtimestamp(
            ws, dt.timezone.utc).isoformat() if ws is not None else None),
        "first_book_latency_s": (round(first_book, 1)
                                 if first_book is not None else None),
        "first_trade_latency_s": (round(first_trade, 1)
                                  if first_trade is not None else None),
        "book_observed_before_first_trade": (
            None if (first_book is None or first_trade is None)
            else first_book <= first_trade),
        "usable_book_states": len(usable),
        "book_states_total": len(books),
        "status_breakdown": dict(collections.Counter(
            b.get("status") for b in books)),
        "authenticated_trades": len(trades),
        "coverage_pct_of_window": cov.get("pct"),
        "max_book_gap_s": cov.get("max_gap_s"),
        "l5_actions": len(l5_actions),
        "actions_with_contemporaneous_book": attached,
        "actions_without": unattached,
        "identity": identity,
        "verdict": verdict,
        "labels": ("authenticated observation only; no BBO is derived from a "
                   "trade, interpolated or backfilled"),
    }


def summarize(per_window: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    # windows only ever seen after they settled are not evidence either way
    skipped = [w for w in per_window if w["verdict"] == NOT_LIVE]
    per_window = [w for w in per_window if w["verdict"] != NOT_LIVE]
    ok = [w for w in per_window if w["verdict"] == OK]
    fb = [w["first_book_latency_s"] for w in per_window
          if w["first_book_latency_s"] is not None]
    ft = [w["first_trade_latency_s"] for w in per_window
          if w["first_trade_latency_s"] is not None]

    def med(xs):
        return round(sorted(xs)[len(xs) // 2], 1) if xs else None

    blind = sum(1 for w in per_window if w["verdict"] == BLIND)
    return {
        "windows": len(per_window),
        "windows_not_observed_live": len(skipped),
        "clean": len(ok),
        "clean_windows": [w["market"] for w in ok],
        "median_first_book_latency_s": med(fb),
        "median_first_trade_latency_s": med(ft),
        "windows_blind_at_open": blind,
        "verdicts": dict(collections.Counter(
            w["verdict"] for w in per_window)),
        "gate": "PASS" if (ok and not blind) else "FAIL",
        "meaning": ("PASS requires the authenticated book to be observed no "
                    "later than the first authenticated trade in every "
                    "window. Anything else means we are still blind while L5 "
                    "opens."),
    }
