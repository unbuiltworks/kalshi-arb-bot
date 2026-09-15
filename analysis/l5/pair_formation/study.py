"""Why does L5 end up two-sided while our clone ends up naked?

THE FINDING THIS EXISTS TO EXPLAIN. 11 evidenced fills produced 7 settled
positions, 0 of them paired, $0.00 matched-pair P&L and -$102.54 of directional
residual. That is not L5's mechanism performing badly. It is a DIFFERENT
mechanism -- a directional strategy manufactured by sparse maker fills -- and
no amount of raising the generic fill rate is guaranteed to fix it.

So this does not measure fill rate. It measures PAIR FORMATION, one episode at
a time: for every first evidenced fill, what happened next, chronologically,
and exactly where the complement was lost.

    first fill -> resulting inventory -> next L5 intent(s) -> opposite-side
    desired price -> actual opposite book -> our hypothetical complement quote
    -> queue/flow evidence -> expiry/settlement

Every failure gets one of nine named causes. They are not severity levels; they
point at different work, and three of them are not our failure at all:

    NO_OPPOSITE_L5_INTENT      L5 never went the other way here. Nothing to
                               mirror -- our model is not late, L5 simply
                               stayed one-sided in this market.
    OPPOSITE_INTENT_EXPIRED    he did, but the intent had already decayed by
                               the time we could observe it.
    OPPOSITE_PRICE_UNREACHABLE there was a bid, above what the pair economics
                               allow. Paying it is forbidden.
    OPPOSITE_ORDER_UNSUBMITTABLE the venue would not accept the order at all
                               (minimum size, undeclared tick or fee).
    OPPOSITE_QUOTE_NO_FLOW     we rested at the right price and nothing traded
                               against it. Queue position is irrelevant here.
    OPPOSITE_QUEUE_BOUND       flow came and the size ahead of us took all of
                               it. The only bucket a better queue converts.
    OPPOSITE_QUOTE_CANCELLED_BY_NEW_INTENT a newer intent on the same side
                               superseded the resting quote.
    VENUE_LIQUIDITY_WITHDRAWAL the opposite side of the book was EMPTY. The
                               venue could not host the complement at any
                               price -- a TRANSFER FAILURE, not a tuning knob.
    DATA_UNKNOWN               we could not tell. Never folded into the others.

WHAT MAY NOT BE DONE ABOUT ANY OF THEM, and this module refuses to measure as
though it were allowed: widening the price, crossing the spread, aggregating
incompatible intents, extending an expired intent, or taking more residual
risk. If the destination venue cannot host L5's complement naturally, that is
recorded as a transfer failure and left as one.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

INTENTS = "intents.jsonl"
EVALS = "evaluations.jsonl"
MAKER_OUT = "maker_outcomes.jsonl"
EVOLUTION = "book_evolution.jsonl"
ROUTES = "routes.jsonl"

CAUSES = (
    "NO_OPPOSITE_L5_INTENT",
    "OPPOSITE_INTENT_EXPIRED",
    "OPPOSITE_PRICE_UNREACHABLE",
    "OPPOSITE_ORDER_UNSUBMITTABLE",
    "OPPOSITE_QUOTE_NO_FLOW",
    "OPPOSITE_QUEUE_BOUND",
    "OPPOSITE_QUOTE_CANCELLED_BY_NEW_INTENT",
    "VENUE_LIQUIDITY_WITHDRAWAL",
    "DATA_UNKNOWN",
)
# causes that are the VENUE's inability to host the behaviour rather than ours
TRANSFER_FAILURES = ("VENUE_LIQUIDITY_WITHDRAWAL", "OPPOSITE_ORDER_UNSUBMITTABLE")


def _load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def _flip(outcome: Any) -> Optional[str]:
    o = str(outcome)
    return "Down" if o == "Up" else ("Up" if o == "Down" else None)


def episodes(out_dir: str) -> List[Dict[str, Any]]:
    """One episode per (venue, market) that saw a first evidenced fill."""
    intents = sorted(_load(os.path.join(out_dir, INTENTS)),
                     key=lambda r: float(r.get("t_utc") or 0))
    ev = _load(os.path.join(out_dir, EVALS))
    places = [r for r in ev
              if str(r.get("schema", "")).endswith("/maker_placement")]
    outs = _load(os.path.join(out_dir, MAKER_OUT))
    evos = _load(os.path.join(out_dir, EVOLUTION))
    routes = {r.get("intent_id"): r for r in _load(os.path.join(out_dir, ROUTES))}

    intent_by_id = {i.get("id"): i for i in intents}
    place_by = {(p.get("intent_id"), p.get("venue")): p for p in places}
    outs_sorted = sorted((o for o in outs if o.get("placed_at") is not None),
                         key=lambda o: float(o["placed_at"]))

    seen: set = set()
    eps = []
    for o in outs_sorted:
        if o.get("evidenced_fill") is not True:
            continue
        key = (o.get("venue"), o.get("market_key"))
        if key in seen:
            continue                      # only the FIRST fill opens an episode
        seen.add(key)
        eps.append(_one_episode(o, key, intents=intents, outs=outs_sorted,
                                place_by=place_by, evos=evos, routes=routes,
                                intent_by_id=intent_by_id))
    return eps


def _one_episode(first: dict, key: Tuple[Any, Any], *, intents, outs,
                 place_by, evos, routes, intent_by_id) -> Dict[str, Any]:
    venue, market = key
    side = str(first.get("desired_outcome"))
    want = _flip(side)
    t0 = float(first["placed_at"])
    cohort = (routes.get(first.get("intent_id")) or {}).get("cohort")

    ep: Dict[str, Any] = {
        "venue": venue, "market_key": market, "cohort": cohort,
        "first_fill": {"outcome": side, "price": first.get("quote_price"),
                       "qty": first.get("quote_qty"), "t": t0,
                       "intent_id": first.get("intent_id")},
        # the inventory this fill created: one-sided by construction, and the
        # whole question is what happens to it next
        "inventory_after_first": {"matched": "0", "residual_side": side,
                                  "residual_qty": str(first.get("quote_qty"))},
    }

    # --- did L5 himself go the other way in this market, after our fill?
    # Anchored on L5's OWN event clock, taken from the intent that produced our
    # fill. Deriving it from our placement time instead would move the boundary
    # by the observation lag and silently drop complements that L5 formed while
    # we were still learning about the first leg.
    i0 = intent_by_id.get(first.get("intent_id")) or {}
    t_event0 = float(i0.get("t_utc") or 0)
    opp = [i for i in intents
           if i.get("market_key") == market
           and str(i.get("desired_outcome")) == want
           and float(i.get("t_utc") or 0) >= t_event0]
    ep["first_fill"]["l5_event_t"] = t_event0 or None
    ep["opposite_l5_intents"] = len(opp)
    if not opp:
        return _fail(ep, "NO_OPPOSITE_L5_INTENT",
                     "L5 never took the other side in this market after our "
                     "fill, so there was nothing to mirror. Our clone is not "
                     "late here; the source stayed one-sided")

    nxt = opp[0]
    ep["opposite_intent"] = {"t_utc": nxt.get("t_utc"),
                             "ceiling": nxt.get("max_acceptable_price"),
                             "qty": nxt.get("incremental_qty"),
                             "action": nxt.get("action"),
                             "id": nxt.get("id")}
    ep["t_first_fill_to_opposite_intent_s"] = round(
        float(nxt.get("t_utc") or 0) - t0, 1)

    r = routes.get(nxt.get("id")) or {}
    if r.get("intent_expired") is True:
        return _fail(ep, "OPPOSITE_INTENT_EXPIRED",
                     f"the complement intent had already decayed when we "
                     f"could price it (age "
                     f"{r.get('intent_age_at_decision_s')}s against "
                     f"{r.get('intent_valid_for_s')}s)")

    # --- did we produce a placement for it on THIS venue?
    p = place_by.get((nxt.get("id"), venue))
    if p is None:
        return _fail(ep, "DATA_UNKNOWN",
                     "no maker placement row for the complement intent on this "
                     "venue: we cannot say why, and will not guess")
    ep["opposite_placement"] = {"submittable": p.get("submittable"),
                                "price": p.get("price"),
                                "best_bid": p.get("best_bid"),
                                "queue_ahead": p.get("queue_ahead"),
                                "reason": p.get("reason"),
                                "placed_at": p.get("placed_at")}
    if p.get("placed_at") is not None:
        ep["t_opposite_intent_to_quote_s"] = round(
            float(p["placed_at"]) - float(nxt.get("t_utc") or 0), 1)

    if p.get("submittable") is not True:
        return _fail(ep, *_why_unsubmittable(p, evos, market, want))

    # --- a quote rested. Was it superseded before its horizon?
    later = [q for q in place_by.values()
             if q.get("venue") == venue and q.get("market_key") == market
             and str(q.get("desired_outcome")) == want
             and q.get("placed_at") is not None
             and float(q["placed_at"]) > float(p["placed_at"])]
    out = _outcome_for(outs, nxt.get("id"), venue)
    if out is None:
        if later:
            return _fail(ep, "OPPOSITE_QUOTE_CANCELLED_BY_NEW_INTENT",
                         "a newer intent on the same side placed another quote "
                         "before this one was decided")
        return _fail(ep, "DATA_UNKNOWN",
                     "the complement quote has no recorded verdict yet")

    ep["opposite_outcome"] = {"evidenced_fill": out.get("evidenced_fill"),
                              "traded_through": out.get("traded_through"),
                              "queue_ahead": out.get("queue_ahead"),
                              "reason": str(out.get("reason"))[:120]}
    if out.get("evidenced_fill") is True:
        return _paired(ep, first, out)
    if out.get("evidenced_fill") is None:
        if later:
            return _fail(ep, "OPPOSITE_QUOTE_CANCELLED_BY_NEW_INTENT",
                         "a newer intent superseded it before it was decided")
        return _fail(ep, "DATA_UNKNOWN",
                     "the complement quote's verdict could not be read: "
                     "UNKNOWN, never a no-fill")
    try:
        thru = D(str(out.get("traded_through") or "0"))
        ahead = D(str(out.get("queue_ahead") or "0"))
    except Exception:                                         # noqa: BLE001
        return _fail(ep, "DATA_UNKNOWN", "unparseable queue/flow figures")
    if thru <= 0:
        return _fail(ep, "OPPOSITE_QUOTE_NO_FLOW",
                     "we rested at an admissible price and nothing traded "
                     "against it. Queue position is irrelevant here")
    if thru <= ahead:
        return _fail(ep, "OPPOSITE_QUEUE_BOUND",
                     f"{thru} traded through and {ahead} was resting ahead of "
                     f"us: the flow arrived and was entirely absorbed")
    return _fail(ep, "OPPOSITE_QUEUE_BOUND",
                 "partial: the queue cleared but not for our whole size")


def _why_unsubmittable(p: dict, evos, market, want) -> Tuple[str, str]:
    """An unsubmittable complement has three quite different causes."""
    reason = str(p.get("reason") or "").lower()
    if "minimum order size" in reason or "tick" in reason or "fee" in reason:
        return ("OPPOSITE_ORDER_UNSUBMITTABLE",
                f"the venue would not accept the order: {p.get('reason')}")
    if p.get("best_bid") is None:
        return ("VENUE_LIQUIDITY_WITHDRAWAL",
                "the opposite side of the book was EMPTY: the venue could not "
                "host the complement at any price. A TRANSFER FAILURE, not a "
                "price we declined to pay")
    return ("OPPOSITE_PRICE_UNREACHABLE",
            f"a bid existed at {p.get('best_bid')} but above what the pair "
            f"economics allow; paying it is forbidden: {p.get('reason')}")


def _outcome_for(outs: Sequence[dict], intent_id, venue) -> Optional[dict]:
    for o in outs:
        if o.get("intent_id") == intent_id and o.get("venue") == venue:
            return o
    return None


def _fail(ep: Dict[str, Any], cause: str, why: str) -> Dict[str, Any]:
    assert cause in CAUSES, cause
    ep.update(paired=False, cause=cause, why=why,
              transfer_failure=cause in TRANSFER_FAILURES)
    return ep


def _paired(ep: Dict[str, Any], first: dict, out: dict) -> Dict[str, Any]:
    """A pair formed. Price it, because forming one badly is not success."""
    try:
        p1 = D(str(first.get("quote_price")))
        p2 = D(str(out.get("quote_price")))
        q = min(D(str(first.get("quote_qty"))), D(str(out.get("quote_qty"))))
    except Exception:                                         # noqa: BLE001
        return _fail(ep, "DATA_UNKNOWN", "a pair formed but could not be priced")
    ep.update(paired=True, cause=None, transfer_failure=False,
              effective_pair_cost=str((p1 + p2).quantize(D("0.000001"))),
              matched_qty=str(q),
              residual_duration_s=round(float(out.get("placed_at") or 0)
                                        - float(first.get("placed_at") or 0), 1),
              why="complement filled: matched inventory formed")
    return ep


def report(out_dir: str) -> Dict[str, Any]:
    eps = episodes(out_dir)
    return {"computed_at": time.time(), "out_dir": out_dir,
            "episodes": len(eps), **summarise(eps), "rows": eps[-100:],
            "labels": (
                "one episode per (venue, market) that saw a FIRST evidenced "
                "fill. The metric is pair formation, not fill rate: raising "
                "the generic fill rate does not by itself produce matched "
                "inventory. No cause here may be answered by widening price, "
                "crossing the spread, aggregating incompatible intents, "
                "extending an expired intent, or taking more residual risk.")}


def summarise(eps: Sequence[dict]) -> Dict[str, Any]:
    n = len(eps)
    paired = [e for e in eps if e.get("paired")]
    # the conditional the whole programme now turns on
    p_opp = (round(len(paired) / n, 4) if n else None)
    # ...and the same thing conditioned on a complement quote actually RESTING,
    # which separates "we never got to try" from "we tried and missed"
    rested = [e for e in eps if e.get("opposite_outcome") is not None]
    p_opp_given_quote = (round(len(paired) / len(rested), 4) if rested else None)

    def dist(field):
        v = sorted(e[field] for e in eps if e.get(field) is not None)
        if not v:
            return None
        return {"n": len(v), "p10": v[int(len(v) * 0.1)],
                "median": v[len(v) // 2], "p90": v[int(len(v) * 0.9)]}

    by: Dict[str, Counter] = {}
    for e in eps:
        k = f"{e.get('venue')}|{e.get('cohort')}"
        by.setdefault(k, Counter())[e.get("cause") or "PAIRED"] += 1
    costs = sorted(D(e["effective_pair_cost"]) for e in paired
                   if e.get("effective_pair_cost"))
    return {
        "paired": len(paired),
        "P_opposite_fill_given_first_fill": p_opp,
        "complement_quote_rested": len(rested),
        "P_opposite_fill_given_complement_rested": p_opp_given_quote,
        "causes": dict(Counter(e.get("cause") for e in eps if e.get("cause"))),
        "by_venue_cohort": {k: dict(v) for k, v in sorted(by.items())},
        "transfer_failures": sum(1 for e in eps if e.get("transfer_failure")),
        "t_first_fill_to_opposite_intent_s": dist(
            "t_first_fill_to_opposite_intent_s"),
        "t_opposite_intent_to_quote_s": dist("t_opposite_intent_to_quote_s"),
        "residual_duration_s": dist("residual_duration_s"),
        "effective_pair_cost": ({"n": len(costs),
                                 "median": str(costs[len(costs) // 2]),
                                 "min": str(costs[0]), "max": str(costs[-1])}
                                if costs else None),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-research/l5-shadow-state")
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args(argv)
    rep = report(a.out_dir)
    if not a.full:
        rep.pop("rows", None)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
