"""Which execution failure actually happened on every real complement order.

OWNER GO (#167): attack the 38% execution/queue bucket of the R5F loss
attribution. "Reconstruct every real complement order after a first fill and
determine which failure actually occurred... using the actual orders that lost
our actual money, not shadow opportunities."

Rules frozen in analysis/l5/PREREG_EXECUTION_QUEUE_FAILURE.md BEFORE the
population was pulled and before any distribution was seen. This module
implements that file and adds nothing to it.

THE EVIDENCE CEILING, ENFORCED RATHER THAN DESCRIBED

The order record carries NO TRADE PRINTS. rest_book is an aggregate of QUOTES
(min_opp_ask, max_touch, polls). So "nobody ever traded at our price" is not
observable: we rest as maker and a taker can cross down to us without the
resting opposite ask ever moving. Category A is therefore named
QUOTE_NEVER_REACHED, for the thing actually measured, and a test pins the
name so it cannot quietly become "no trades".

B is a LOWER bound and A an UPPER bound, because we see only the crossings a
poll caught. classify() records the sampling cadence on every row so that gap
stays visible.

R50 IS DERIVED, NOT CHOSEN. The C/A boundary is the median rest_s of FILLED
completes in the same population. A hand-picked seconds threshold is the one
knob that would let the analyst tune the answer, so there is no constant here
to tune -- the population supplies it.

DOMINANCE IS NOT PLURALITY. verdict() refuses to name a dominant category
unless the leader's 95% Wilson lower bound clears the runner-up's point
estimate, and refuses entirely below N1 or above the N3 evidence ceiling.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

# --- frozen bars, prereg section 4 -----------------------------------------
MIN_CLASSIFIED = 200          # N1
MIN_POLLS = 5                 # prereg section 3, E test
MAX_UNKNOWN_SHARE = 0.40      # N3

D_REJECTED = "D_venue_rejection"
E_UNKNOWN = "E_insufficient_evidence"
B_QUEUE = "B_queue_or_liveness"
C_EARLY = "C_executor_ended_early"
A_UNREACHED = "A_quote_never_reached"

DECIDED = (A_UNREACHED, B_QUEUE, C_EARLY, D_REJECTED)

FIX_FOR = {
    A_UNREACHED: "complement pricing / aggressiveness",
    # prereg 8.2: B is internally UNOBSERVABLE. A crossed book with no fill is
    # queue priority OR order liveness OR a stale snapshot, and those imply
    # three different fixes. Without prints and fill IDs they cannot be
    # separated, so B does NOT license "fix the queue" -- it licenses the
    # instrumentation in prereg 8.3 and nothing else.
    B_QUEUE: "INSTRUMENT FIRST (prereg 8.3) -- queue vs liveness vs stale "
             "snapshot are indistinguishable without trade prints",
    C_EARLY: "our deadline / re-quote rule",
    D_REJECTED: "plumbing (rejection classes, task #93)",
}


def _f(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _filled(order: Mapping[str, Any]) -> bool:
    q = _f(order.get("filled_qty"))
    return q is not None and q > 0.0


def _rejected(order: Mapping[str, Any]) -> bool:
    return str(order.get("end_reason") or "").startswith("rejected")


# --- population, prereg section 2 -------------------------------------------
def load_sequence(path: Any) -> List[dict]:
    """Every submitted order in the persisted R5F sequence, with its session."""
    doc = json.load(open(path, "rt", encoding="utf-8"))
    out: List[dict] = []
    for slug, market in (doc.get("per_market") or {}).items():
        for row in (market.get("rows") or []):
            o = row.get("order")
            if not isinstance(o, Mapping):
                continue
            out.append({**o, "slug": slug,
                        "session_id": row.get("session_id"),
                        "t_utc": row.get("t_utc"),
                        "idx": row.get("idx")})
    return out


def load_campaign_sessions(paths: Iterable[Any]) -> List[dict]:
    """Sessions rebuilt from PMUS_L5_CAMPAIGN session_end records.

    The log-derived order records carry NO session_id -- neither the order nor
    its parent record has the field. Checked, not assumed. But a session_end
    record IS one session: it is emitted once when that session closes and its
    "orders" array is that session's orders in order.

    So session membership is reconstructed from the record structure rather
    than from a field that does not exist. This changes no rule in the prereg;
    the population is still "complements in a session where an open/extend
    actually filled". It only supplies the grouping the field would have.

    Records repeat across log pulls and across campaign restarts that replay
    durable state, so sessions are deduplicated on (slug, ordered order_ids).
    Deduplicating on slug alone would collapse genuinely distinct sessions in
    the same market; on order_id alone it would split a replayed session.
    """
    from src import l5_completion_economics as ce
    seen, out = set(), []
    for p in paths:
        for msg in ce._messages(p):
            if "session_end" not in msg:
                continue
            for rec in ce._objects(msg, "PMUS_L5_CAMPAIGN "):
                orders = [o for o in (rec.get("orders") or [])
                          if isinstance(o, Mapping)]
                if not orders:
                    continue
                sig = (rec.get("slug"),
                       tuple(o.get("order_id") for o in orders))
                if sig in seen:
                    continue
                seen.add(sig)
                out.append({"slug": rec.get("slug"), "orders": orders,
                            "inventory": rec.get("inventory") or {},
                            "filled_actions": rec.get("filled_actions")})
    return out


def had_a_first_fill(sess: Mapping[str, Any]) -> Optional[str]:
    """Did this session hold inventory from a filled first leg? How we know.

    Returns the evidence kind, or None. See prereg section 9: a session_end
    record emitted after a mid-session resume contains only the LATER orders,
    so the open/extend that created the inventory sits in an earlier record.
    Requiring the filling order in the same record excluded 66 of 98 sessions
    on a logging artifact rather than on economics.

    Inventory and filled_actions are the session's own account of the same
    fact and survive resumption, so they qualify a session too.
    """
    orders = list(sess.get("orders") or [])
    for o in orders:
        if str(o.get("relation")) in ("open", "extend") and _filled(o):
            return "filled_first_leg_in_record"
    inv = sess.get("inventory") or {}
    for k in ("spent", "up", "dn", "matched"):
        v = _f(inv.get(k))
        if v is not None and v > 0.0:
            return f"inventory.{k}"
    fa = sess.get("filled_actions")
    if isinstance(fa, (int, float)) and fa > 0:
        return "filled_actions"
    return None


def complements_from_sessions(sessions: Iterable[Mapping[str, Any]]) -> dict:
    """The prereg population, using reconstructed session grouping.

    "After a first fill" is honoured positionally: a complement counts only if
    a filled open/extend appears BEFORE it in that session's order list. A
    complement placed before any first leg filled is a different animal and is
    excluded and counted, exactly as the session_id path does.
    """
    kept, excluded = [], Counter()
    sessions_with_fill = 0
    evidence = Counter()
    for sess in sessions:
        orders = list(sess.get("orders") or [])
        why = had_a_first_fill(sess)
        if why is None:
            excluded["session_never_filled_a_first_leg"] += sum(
                1 for o in orders if str(o.get("relation")) == "complete")
            continue
        sessions_with_fill += 1
        evidence[why] += 1
        # Positional "prior" applies only when the filling leg is IN this
        # record. When the session was resumed carrying inventory, that
        # inventory existed before the record's first line, so every
        # complement in it is by construction after the first fill.
        first_fill_at = None
        if why == "filled_first_leg_in_record":
            for i, o in enumerate(orders):
                if str(o.get("relation")) in ("open", "extend") and _filled(o):
                    first_fill_at = i
                    break
        for i, o in enumerate(orders):
            if str(o.get("relation")) != "complete":
                continue
            if first_fill_at is not None and i < first_fill_at:
                excluded["complement_before_the_first_fill"] += 1
                continue
            kept.append({**o, "slug": sess.get("slug")})
    return {"orders": kept, "excluded": dict(excluded),
            "sessions_with_a_first_fill": sessions_with_fill,
            "qualifying_evidence": dict(evidence)}


def complements_after_first_fill(orders: Iterable[Mapping[str, Any]]) -> dict:
    """COMPLETE orders in sessions where an open/extend actually filled.

    The owner's population is "every real complement order AFTER A FIRST FILL".
    A complement placed in a session that never got inventory is a different
    animal and is excluded here rather than diluting the denominator.
    """
    rows = list(orders)
    first_fill: Dict[Any, Any] = {}
    for o in rows:
        if str(o.get("relation")) in ("open", "extend") and _filled(o):
            sid, t = o.get("session_id"), o.get("t_utc")
            if sid not in first_fill or (t is not None and str(t) < str(first_fill[sid])):
                first_fill[sid] = t
    kept, excluded = [], Counter()
    for o in rows:
        if str(o.get("relation")) != "complete":
            continue
        sid = o.get("session_id")
        if sid not in first_fill:
            excluded["session_never_filled_a_first_leg"] += 1
            continue
        kept.append(o)
    return {"orders": kept, "excluded": dict(excluded),
            "sessions_with_a_first_fill": len(first_fill)}


# --- the derived C/A boundary, prereg section 3 -----------------------------
def rest_benchmark(complements: Sequence[Mapping[str, Any]]) -> dict:
    """R50/R75 from FILLED completes. Derived by procedure, never chosen."""
    rs = sorted(x for x in (_f(o.get("rest_s")) for o in complements
                            if _filled(o)) if x is not None)
    if not rs:
        return {"r50": None, "r75": None, "n": 0,
                "why": "no filled complete carried a rest_s; C cannot be "
                       "separated from A"}
    return {"r50": median(rs), "r75": rs[(3 * len(rs)) // 4], "n": len(rs)}


# --- classification, prereg section 3 strict priority -----------------------
def classify(order: Mapping[str, Any], r50: Optional[float]) -> dict:
    """One order, one label. Priority order is part of the frozen rule."""
    rb = order.get("rest_book") or {}
    px = _f(order.get("px"))
    moa = _f(rb.get("min_opp_ask"))
    polls = rb.get("polls")
    rest_s = _f(order.get("rest_s"))
    out = {"order_id": order.get("order_id"), "slug": order.get("slug"),
           "stage": order.get("stage"), "px": order.get("px"),
           "rest_s": rest_s, "polls": polls,
           "min_opp_ask": rb.get("min_opp_ask"),
           "filled": _filled(order),
           "cadence_s": (rest_s / polls) if (rest_s and isinstance(polls, int)
                                             and polls > 0) else None}

    if _rejected(order):
        return {**out, "label": D_REJECTED,
                "why": str(order.get("end_reason")),
                "rejection_class": order.get("rejection_class")}

    if not rb or moa is None or px is None or not isinstance(polls, int) \
            or polls < MIN_POLLS:
        return {**out, "label": E_UNKNOWN,
                "why": ("no rest_book" if not rb else
                        "min_opp_ask missing" if moa is None else
                        "px missing" if px is None else
                        f"polls {polls} < {MIN_POLLS}")}

    if moa <= px:
        return {**out, "label": B_QUEUE,
                "why": f"opposite QUOTE reached {moa} against our {px} and we "
                       "did not fill. Something on our side failed; the "
                       "evidence cannot say which of queue priority, order "
                       "liveness, or a stale snapshot. Not proof a trade "
                       "occurred there, and NOT proof we would have filled",
                "unobservable": ["queue_priority", "order_liveness",
                                 "stale_or_async_snapshot"]}

    if r50 is None:
        return {**out, "label": E_UNKNOWN,
                "why": "no R50 benchmark, so C cannot be separated from A"}

    if rest_s is not None and rest_s < r50:
        return {**out, "label": C_EARLY,
                "why": f"rested {rest_s}s, under the {r50}s median that "
                       "filled completes needed; we never learned whether "
                       "the price was reachable"}

    return {**out, "label": A_UNREACHED,
            "why": f"opposite ask never got below {moa} against our {px} "
                   f"across {polls} polls in {rest_s}s -- the QUOTE never "
                   "reached us; this is not proof no trade occurred there"}


# --- verdict, prereg section 4 ----------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def evaluate(rows: Sequence[Mapping[str, Any]]) -> dict:
    counts = Counter(r["label"] for r in rows)
    total = len(rows)
    decided = sum(counts[k] for k in DECIDED)
    unknown_share = (counts[E_UNKNOWN] / total) if total else 0.0

    cad = [r["cadence_s"] for r in rows if r.get("cadence_s")]
    out: Dict[str, Any] = {
        "prereg": "analysis/l5/PREREG_EXECUTION_QUEUE_FAILURE.md",
        "orders": total,
        "counts": dict(counts),
        "decided": decided,
        "n1_required": MIN_CLASSIFIED,
        "unknown_share": round(unknown_share, 4),
        "n3_ceiling": MAX_UNKNOWN_SHARE,
        "sampling_cadence_s": {
            "n": len(cad),
            "median": round(median(cad), 3) if cad else None,
            "note": "B is a LOWER bound and A an UPPER bound at this cadence; "
                    "crossings between polls are invisible",
        },
    }

    if unknown_share >= MAX_UNKNOWN_SHARE:
        out["verdict"] = "EVIDENCE_LIMITED"
        out["why"] = (
            f"E is {unknown_share:.1%} of the population, at or above the "
            f"{MAX_UNKNOWN_SHARE:.0%} N3 ceiling. Per the prereg the "
            "deliverable is an instrumentation change, not a strategy change, "
            "however the rest splits. No dominance was computed.")
        return out

    if decided < MIN_CLASSIFIED:
        out["verdict"] = "UNDERPOWERED"
        out["deficit"] = MIN_CLASSIFIED - decided
        out["why"] = (
            f"N1 not met: {decided} of {MIN_CLASSIFIED} classified orders. "
            "Per the prereg an underpowered result reports the deficit and "
            "claims no dominance, so no dominance test was run.")
        return out

    ranked = sorted(((counts[k], k) for k in DECIDED), reverse=True)
    (k1, lab1), (k2, lab2) = ranked[0], ranked[1]
    lo1, hi1 = wilson(k1, decided)
    p2 = k2 / decided
    out["shares"] = {lab: round(counts[lab] / decided, 4) for lab in DECIDED}
    out["leader"] = {"label": lab1, "k": k1, "share": round(k1 / decided, 4),
                     "wilson_95": [round(lo1, 4), round(hi1, 4)]}
    out["runner_up"] = {"label": lab2, "k": k2, "share": round(p2, 4)}
    if lo1 > p2:
        out["verdict"] = "DOMINANT"
        out["dominant"] = lab1
        out["fix_implied"] = FIX_FOR[lab1]
        out["why"] = (f"{lab1} Wilson lower bound {lo1:.4f} clears the "
                      f"runner-up point estimate {p2:.4f}.")
        if lab1 == B_QUEUE:
            # prereg 8.2: dominance is established, the CAUSE is not.
            out["verdict"] = "DOMINANT_BUT_UNOBSERVABLE"
            out["unobservable"] = (
                "B dominates, but a crossed book with no fill is queue "
                "priority, order liveness, or a stale snapshot, and this "
                "evidence cannot separate them. Per prereg 8.2 the deliverable "
                "is the instrumentation spec in 8.3, NOT a queue fix.")
        out["no_fill_counterfactual"] = (
            "Per prereg 8.1 no category converts into recovered completions; "
            "recoverable_completions() raises.")
    else:
        out["verdict"] = "NO_DOMINANCE"
        out["why"] = (f"{lab1} leads at {k1 / decided:.1%} but its Wilson "
                      f"lower bound {lo1:.4f} does not clear {lab2} at "
                      f"{p2:.4f}. Per the prereg the bucket is heterogeneous "
                      "and no single fix is warranted.")
    return out


class FillAssumed(RuntimeError):
    """Raised rather than returning a number that assumes a fill."""


def recoverable_completions(*_a: Any, **_k: Any) -> None:
    """REFUSES. Prereg 8.1 -- there is no honest value to return.

    Converting a classification into "N of the 5.66 missing completions were
    recoverable" needs P(fill | condition), and every term is unmeasured: no
    prints, no depth, no queue position, no fill IDs. A caveated estimate here
    would be fill-assumed at source -- the same defect already recorded against
    the no_legal_maker_price counterfactual (+$7.69) and rejected for it.

    So the number is UNAVAILABLE, not discouraged. Raising beats returning a
    figure that would be quoted onward with its caveat stripped off.
    """
    raise FillAssumed(
        "recoverable-completion counts are fill-assumed and unavailable: the "
        "order record carries no trade prints, no depth, no queue position and "
        "no fill IDs. See PREREG_EXECUTION_QUEUE_FAILURE.md section 8.1. To "
        "make this answerable, instrument per section 8.3.")


def run(paths: Sequence[Any], log_paths: Sequence[Any] = ()) -> dict:
    """Sequence artifacts supply session_id; raw log pulls supply structure."""
    orders: List[dict] = []
    for p in paths:
        orders.extend(load_sequence(p))
    pop = complements_after_first_fill(orders)
    sessions = load_campaign_sessions(log_paths) if log_paths else []
    if sessions:
        from_logs = complements_from_sessions(sessions)
        # dedupe across the two sources on (order_id, stage), the same key
        # l5_completion_economics uses
        have = {(o.get("order_id"), o.get("stage")) for o in pop["orders"]}
        added = [o for o in from_logs["orders"]
                 if (o.get("order_id"), o.get("stage")) not in have]
        pop["orders"] = pop["orders"] + added
        pop["excluded"] = {**pop["excluded"], **from_logs["excluded"]}
        pop["sessions_with_a_first_fill"] += from_logs["sessions_with_a_first_fill"]
        pop["from_log_sessions"] = {"sessions": len(sessions),
                                    "complements": len(from_logs["orders"]),
                                    "new_after_dedup": len(added)}
    bench = rest_benchmark(pop["orders"])
    rows = [classify(o, bench["r50"]) for o in pop["orders"]]
    res = evaluate(rows)
    res["population"] = {"all_orders": len(orders),
                         "complements_after_first_fill": len(pop["orders"]),
                         "excluded": pop["excluded"],
                         "sessions_with_a_first_fill":
                             pop["sessions_with_a_first_fill"]}
    res["rest_benchmark"] = bench
    return res, rows


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    res, _ = run(sys.argv[1:])
    print(json.dumps(res, indent=1, default=str))
