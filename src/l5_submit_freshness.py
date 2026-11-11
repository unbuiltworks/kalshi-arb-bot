"""SHADOW-ONLY submit-time authenticated-freshness gate, and its replay.

WHAT THIS IS FOR. `max_quote_age_s = 5.0` is already declared on every
cross-shadow evaluation the engine emits, but nothing enforces it at the
SUBMISSION boundary: the book is read, a price is computed, and the order is
sent without re-asserting that the book is still young enough to have priced
it. The 2026-09-14T12:06Z rejections are what that looks like in production --
orders priced from a book 6.67 s and 8.07 s old, correctly rejected by the
venue as crossing.

The gate this module models is:

    fresh authenticated book -> recompute price -> verify maker/non-crossing
    -> existing risk/economic gates -> submit

NOTHING HERE TOUCHES EXECUTION. No pricing rule, no cancel rule, no gate, no
order path, no cap, no ceiling. It is a measurement module and a replay. The
decision to deploy is not its to make.

THE MEASUREMENT'S OWN LIMIT, STATED FIRST BECAUSE IT BOUNDS EVERY NUMBER
BELOW. We do not record the instant of submission alongside the book's
timestamp on a general order row. What we record is:

    authenticated_age_s     the book's age when the RECORDER served it
    probe_transport_delta_s probe_receipt_utc - status_utc
    age_at_probe_receipt_s  the sum: the book's age when WE received it

Submission happens strictly AFTER receipt (price computation, gate checks and
the network hop all follow it). So `age_at_probe_receipt_s` is a LOWER BOUND
on the true age at submission, never the value itself. Every "over threshold"
count below is therefore an UNDER-count, and is labelled as one. A row without
that field is UNKNOWN and is never imputed.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Iterable, Mapping, Optional, Sequence

PASS = "PASS"
BLOCK = "BLOCK_REFRESH_AND_RECOMPUTE"
UNKNOWN = "UNKNOWN_NO_AGE_EVIDENCE"

# The invariant the engine already declares on every evaluation it emits.
DECLARED_MAX_QUOTE_AGE_S = 5.0

# Thresholds swept in the replay. Chosen to bracket the declared value on both
# sides at roughly equal spacing. They are NOT chosen by scoring P&L: the
# question this module asks is whether enforcing the ALREADY-INTENDED
# invariant removes stale submissions without destroying legitimate fills,
# and a threshold picked by maximising replay profit would answer a different
# question and answer it dishonestly.
THRESHOLDS_S = (3.0, 4.0, 5.0, 6.0, 8.0, 10.0)

RELATIONS = ("open", "extend", "complete")


def _f(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def relation_of(order: Mapping[str, Any]) -> str:
    """OPEN / EXTEND / COMPLETE from the explicit field, else the stage name."""
    r = str(order.get("relation") or "").strip().lower()
    if r in RELATIONS:
        return r
    stage = str(order.get("stage") or "")
    for cand in RELATIONS:
        if f"spring_{cand}_" in stage:
            return cand
    return "other"


def age_at_submission(order: Mapping[str, Any]) -> dict:
    """Best available age of the book the order was priced from.

    Returns `age_s` with the `basis` that produced it and `is_lower_bound`
    set whenever the figure necessarily understates the truth.
    """
    book = order.get("recorder_book")
    book = book if isinstance(book, Mapping) else {}
    cso = book.get("cross_shadow_observation")
    cso = cso if isinstance(cso, Mapping) else {}

    def pick(key: str) -> Optional[float]:
        for src in (order, book, cso):
            if isinstance(src, Mapping):
                v = _f(src.get(key))
                if v is not None:
                    return v
        return None

    total = pick("age_at_probe_receipt_s") or pick("obs_age_at_probe_receipt_s")
    if total is not None:
        return {"age_s": total, "basis": "age_at_probe_receipt_s",
                "is_lower_bound": True, "unknown_reason": None}

    auth = pick("authenticated_age_s") or pick("obs_authenticated_age_s")
    delta = (pick("probe_transport_delta_s")
             or pick("obs_probe_transport_delta_s"))
    if auth is not None and delta is not None:
        return {"age_s": auth + delta, "basis": "authenticated+transport",
                "is_lower_bound": True, "unknown_reason": None}

    qa = pick("quote_age_s")
    if qa is not None:
        return {"age_s": qa, "basis": "quote_age_s",
                "is_lower_bound": True, "unknown_reason": None}

    if auth is not None:
        # The recorder's own age with NO transport term is not an age at
        # submission and must not be used as one -- transport has a median of
        # several seconds and dominates this figure.
        return {"age_s": None, "basis": "authenticated_age_s_only",
                "is_lower_bound": False,
                "unknown_reason": "transport delta missing; the recorder's "
                                  "own age is not an age at submission"}
    return {"age_s": None, "basis": None, "is_lower_bound": False,
            "unknown_reason": "no authenticated age evidence on this order"}


def gate(order: Mapping[str, Any], max_age_s: float = DECLARED_MAX_QUOTE_AGE_S
         ) -> dict:
    """Shadow decision for ONE submission. Never mutates anything."""
    a = age_at_submission(order)
    if a["age_s"] is None:
        return {"decision": UNKNOWN, "age_s": None, "basis": a["basis"],
                "threshold_s": max_age_s, "is_lower_bound": False,
                "reason": a["unknown_reason"]}
    over = a["age_s"] > max_age_s
    return {"decision": BLOCK if over else PASS, "age_s": a["age_s"],
            "basis": a["basis"], "threshold_s": max_age_s,
            "is_lower_bound": a["is_lower_bound"],
            "reason": (f"book {a['age_s']:.2f}s old at receipt "
                       f"> {max_age_s:.1f}s" if over else None)}


def outcome_of(order: Mapping[str, Any]) -> str:
    """What the submission actually did, from the order's own record."""
    end = str(order.get("end_reason") or "")
    filled = _f(order.get("filled_qty")) or 0.0
    if end.startswith("rejected"):
        return "rejected"
    if filled > 0:
        return "filled"
    return "no_fill_cancelled"


def quantiles(values: Sequence[float]) -> Optional[dict]:
    v = sorted(values)
    if not v:
        return None
    def q(p: float) -> float:
        return v[min(len(v) - 1, int(p * len(v)))]
    return {"n": len(v), "min": v[0], "p10": q(.10), "p25": q(.25),
            "p50": q(.50), "p75": q(.75), "p90": q(.90), "max": v[-1]}


def replay(orders: Iterable[Mapping[str, Any]],
           thresholds: Sequence[float] = THRESHOLDS_S) -> dict:
    """Evaluate the gate over historical submissions at several thresholds.

    `prevented` counts orders the venue REJECTED that the gate would have
    stopped -- a pure gain. `suppressed_fills` counts orders that FILLED and
    would have been stopped -- the cost, and the number that decides this.
    `blocked_no_fill` is neither: those attempts produced nothing, so
    stopping them forgoes no fill.
    """
    orders = list(orders)
    per_threshold = {}
    ages, by_rel_ages = [], {r: [] for r in RELATIONS}
    unknown = 0
    for o in orders:
        a = age_at_submission(o)
        if a["age_s"] is None:
            unknown += 1
            continue
        ages.append(a["age_s"])
        rel = relation_of(o)
        if rel in by_rel_ages:
            by_rel_ages[rel].append(a["age_s"])

    for t in thresholds:
        buckets = {"pass": 0, "block": 0, "unknown": 0,
                   "prevented": 0, "suppressed_fills": 0,
                   "blocked_no_fill": 0, "passed_rejections": 0,
                   "passed_fills": 0}
        per_rel = {r: dict(buckets) for r in RELATIONS}
        econ_blocked_fills, econ_prevented = [], []
        for o in orders:
            g = gate(o, t)
            rel = relation_of(o)
            tgt = per_rel.get(rel)
            out = outcome_of(o)
            if g["decision"] == UNKNOWN:
                buckets["unknown"] += 1
                if tgt is not None:
                    tgt["unknown"] += 1
                continue
            if g["decision"] == BLOCK:
                buckets["block"] += 1
                if tgt is not None:
                    tgt["block"] += 1
                if out == "rejected":
                    buckets["prevented"] += 1
                    if tgt is not None:
                        tgt["prevented"] += 1
                    econ_prevented.append(o)
                elif out == "filled":
                    buckets["suppressed_fills"] += 1
                    if tgt is not None:
                        tgt["suppressed_fills"] += 1
                    econ_blocked_fills.append(o)
                else:
                    buckets["blocked_no_fill"] += 1
                    if tgt is not None:
                        tgt["blocked_no_fill"] += 1
            else:
                buckets["pass"] += 1
                if tgt is not None:
                    tgt["pass"] += 1
                if out == "rejected":
                    buckets["passed_rejections"] += 1
                    if tgt is not None:
                        tgt["passed_rejections"] += 1
                elif out == "filled":
                    buckets["passed_fills"] += 1
                    if tgt is not None:
                        tgt["passed_fills"] += 1
        per_threshold[t] = {"overall": buckets, "by_relation": per_rel,
                            "suppressed_fill_pair_costs": quantiles(
                                [c for c in (_f(o.get("econ_pair_cost"))
                                             for o in econ_blocked_fills)
                                 if c is not None]),
                            "prevented_pair_costs": quantiles(
                                [c for c in (_f(o.get("econ_pair_cost"))
                                             for o in econ_prevented)
                                 if c is not None])}
    return {"submissions_seen": len(orders),
            "evaluable": len(ages),
            "unknown_no_age_evidence": unknown,
            "age_quantiles": quantiles(ages),
            "age_quantiles_by_relation": {
                r: quantiles(v) for r, v in by_rel_ages.items()},
            "thresholds": {str(t): per_threshold[t] for t in thresholds},
            "age_is_lower_bound": True,
            "labels": ("age_at_probe_receipt_s is a LOWER BOUND on age at "
                       "submission; every over-threshold count here is an "
                       "UNDER-count. SHADOW ONLY: no order path is touched.")}


def dedupe_rejections(rows: Iterable[Mapping[str, Any]]) -> dict:
    """Collapse the 3x-duplicated PMUS_ORDER_REJECTED telemetry BY ORDER ID.

    The emission itself is left alone and the raw rows are preserved: this is
    a READ-SIDE collapse, so historical evidence is never rewritten. One
    venue rejection is one rejection however many times it was printed.
    """
    rows = list(rows)
    by_id: dict = {}
    for r in rows:
        oid = r.get("order_id")
        if not oid:
            continue
        prev = by_id.get(oid)
        if prev is None:
            by_id[oid] = dict(r)
            by_id[oid]["_emissions"] = 1
        else:
            prev["_emissions"] += 1
    dupes = sum(v["_emissions"] - 1 for v in by_id.values())
    return {"raw_rows": len(rows), "distinct_order_ids": len(by_id),
            "duplicate_rows_dropped": dupes,
            "inflation_factor": (round(len(rows) / len(by_id), 4)
                                 if by_id else None),
            "emissions_per_order": sorted(
                {v["_emissions"] for v in by_id.values()}),
            "orders": by_id,
            "labels": ("READ-SIDE dedupe by venue order id. The raw rows are "
                       "preserved and the emitter is untouched.")}
