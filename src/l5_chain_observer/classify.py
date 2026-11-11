"""Name the cause of every `chain_only` transaction. Gate G-C3.

WHY THIS IS NOT BOOKKEEPING. `chain_only` -- the chain carries a transaction
the compared feed window does not -- is expected and benign when it is
publication lag or a window boundary. It is a promotion blocker when it is
anything else, because a faster feed that reports fills the venue never
published is worse than a slow one: it would build inventory out of events
that did not happen to us.

Counting the set therefore proves nothing. The gate requires a NAMED,
REPRODUCIBLE cause per transaction, and `unresolved` must be zero. "Probably
lag" is not a cause, so the only way to reach `publication_lag` here is to
re-read the feed and FIND the transaction in it.

ORDER MATTERS. The cheap, certain checks run first: the feed is consulted
before the chain, and a receipt is only fetched for transactions the feed
genuinely does not have. An RPC failure leaves the transaction UNRESOLVED
rather than guessing a category -- the same rule as everywhere else in this
package.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from src.l5_chain_observer import decode as DE

# Exactly the categories fixed in PROMOTION_GATE_PREREGISTERED.md (plus the
# amendment of 2026-09-15 recorded there). Adding one silently is the failure
# this module is meant to prevent, so the gate checks membership of this tuple.
CATEGORIES = ("publication_lag", "window_edge", "non_trade_transfer",
              "other_market", "unresolved")
RESOLVED = tuple(c for c in CATEGORIES if c != "unresolved")


def classify_one(tx: str, *, feed: Dict[str, dict],
                 window: Tuple[float, float],
                 receipt_fn: Callable[[str], Any],
                 l5_topic: str, exchanges: Sequence[str],
                 cohort: Optional[Sequence[str]] = None,
                 feed_horizon_ts: Optional[float] = None,
                 block_ts: Optional[float] = None) -> Dict[str, Any]:
    """One chain_only transaction -> a named cause with its evidence.

    `feed` is the UNWINDOWED feed, because the question is whether the venue
    published this transaction at all -- not whether it published it inside the
    interval we happened to compare.
    """
    lo, hi = float(window[0]), float(window[1])
    row = feed.get(tx)
    if row is not None:
        ts = row.get("ts")
        ts = float(ts) if ts is not None else None
        if ts is None:
            return _c(tx, "unresolved",
                      "the venue published this transaction but with no "
                      "timestamp, so it cannot be placed relative to the "
                      "compared window", {"feed_row": row})
        if ts > hi:
            return _c(tx, "window_edge",
                      "the venue published it AFTER the compared window "
                      "closed; the block range is a superset of the time "
                      "window by construction (floor/ceil), so an edge block "
                      "can carry a later-timestamped fill",
                      {"feed_ts": ts, "window": [lo, hi],
                       "past_window_end_s": round(ts - hi, 3)})
        if ts < lo:
            return _c(tx, "window_edge",
                      "the venue published it BEFORE the compared window "
                      "opened; same floor/ceil superset, at the other edge",
                      {"feed_ts": ts, "window": [lo, hi],
                       "before_window_start_s": round(lo - ts, 3)})
        return _c(tx, "publication_lag",
                  "the venue has it now, inside the compared window, so the "
                  "comparison simply ran before publication",
                  {"feed_ts": ts, "window": [lo, hi]})

    # The venue does not have it. Before asking why, establish that the venue
    # COULD have been asked: its activity endpoint returns a bounded number of
    # recent fills, and a transaction older than the oldest row it returned is
    # outside its reach. Absence there is not evidence of absence.
    if (feed_horizon_ts is not None and block_ts is not None
            and block_ts < feed_horizon_ts):
        return _c(tx, "unresolved",
                  "older than the oldest fill the feed returned, so the feed "
                  "could not be asked about it; absence is not evidence here",
                  {"block_ts": block_ts, "feed_oldest_ts": feed_horizon_ts})

    try:
        rc = receipt_fn(tx)
    except Exception as exc:                                  # noqa: BLE001
        return _c(tx, "unresolved",
                  "the receipt could not be read, so the cause is UNKNOWN "
                  "rather than benign",
                  {"error": f"{type(exc).__name__}: {exc}"[:160]})
    legs = DE.decode_receipt(rc or {}, l5_topic=l5_topic, exchanges=exchanges)
    dec = DE.declined(rc or {}, l5_topic=l5_topic, exchanges=exchanges)
    if not legs:
        # WHAT WAS DECLINED IS PART OF THE ANSWER. "No fill leg" is only an
        # explanation if we can say which L5-bearing logs there were instead;
        # otherwise a signature that started carrying fills would show up here
        # as a benign transfer forever.
        return _c(tx, "non_trade_transfer",
                  "L5 appears in an exchange log but the transaction contains "
                  "no decodable fill leg, so there was no trade for the "
                  "venue's TRADE feed to publish",
                  {"logs": len((rc or {}).get("logs") or []),
                   "decoded_fill_legs": 0,
                   "declined_logs": dec})
    if cohort is not None:
        toks = {l["token_id"] for l in legs}
        if not (toks & set(str(c) for c in cohort)):
            return _c(tx, "other_market",
                      "a real fill, on a token outside the cohort this "
                      "comparison covers",
                      {"token_ids": sorted(toks), "cohort_size": len(cohort)})
    g = DE.groups(legs)
    return _c(tx, "unresolved",
              "a decodable L5 fill the venue has not published and no named "
              "cause accounts for -- this is the case the gate exists to "
              "catch",
              {"groups": [{"price": x["price"], "size": x["size"],
                           **x["group_key"]} for x in g],
               "declined_logs": dec})


def classify(txs: Sequence[str], **kw) -> list:
    return [classify_one(t, **kw) for t in txs]


def summarise(rows: Sequence[dict]) -> Dict[str, Any]:
    """Counts the gate reads. `classified` excludes `unresolved` deliberately:
    'unresolved' is a category, but it is not an explanation."""
    by: Dict[str, int] = {}
    for r in rows:
        by[r.get("category") or "unresolved"] = \
            by.get(r.get("category") or "unresolved", 0) + 1
    unresolved = by.get("unresolved", 0)
    unknown = sum(v for k, v in by.items() if k not in CATEGORIES)
    return {
        "n": len(rows),
        "by_category": dict(sorted(by.items())),
        "classified": len(rows) - unresolved - unknown,
        "unresolved": unresolved,
        "outside_permitted_categories": unknown,
        "all_classified": bool(rows) and unresolved == 0 and unknown == 0,
    }


def _c(tx: str, category: str, why: str,
       evidence: Dict[str, Any]) -> Dict[str, Any]:
    assert category in CATEGORIES, category
    return {"tx": tx, "category": category, "why": why, "evidence": evidence}
