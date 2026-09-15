"""P1: what does the 15-second recovery actually BUY?

The chain carries L5's fills a measured 15.16 s ahead of the venue's own
publication (n=73, chain ahead on 73/73; readable at block + 0.37 s, published
at block + 15.90 s). That is a fact about observation. It is not yet a fact
about execution, and this module is the difference.

THE SAME FROZEN INTENT, TWO OBSERVATION CLOCKS.

    arm A  (feed)   observed when the venue published it
    arm B  (chain)  observed at the anchor's own block timestamp + 0.37 s

The intent is identical in both arms -- same anchor, same desired outcome, same
ceiling, same quantity. Nothing about the model changes. Only the moment we
could first act on it does.

NO HINDSIGHT, AND THE PAIRING IS WHERE IT WOULD CREEP IN. Arm B must be priced
against a book that existed at arm B's observation time -- not the book we read
15 s later. The shadow evaluates every live market on a ~14 s cycle and records
each read, so a snapshot near arm B's moment usually exists ALREADY, taken
while evaluating an earlier anchor. Arm B is priced from that recorded
snapshot, the pairing error is reported on every row, and a pair outside the
tolerance is UNKNOWN rather than approximated.

t_chain USES EACH ANCHOR'S OWN BLOCK TIME, not the 15.16 s average. The
activity feed's `timestamp` IS the block timestamp -- established when a
lead measurement that differenced it against itself returned 0.0 s for all 40
rows -- so the per-anchor chain-readable moment is exact.

WHAT CANNOT BE ANSWERED FROM THE STORE IS SAID SO. Arm B's evidenced fill needs
the prints between t_chain and the horizon. The store keeps the prints that
crossed ARM A's price; that list is a valid superset for arm B only when arm
B's price is at or above arm A's. Where it is not, the row reports
NOT_DETERMINABLE with the reason instead of a modelled fill.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

# measured: the chain is readable this long after the block timestamp
CHAIN_READABLE_S = 0.37
# a paired snapshot further than this from arm B's moment is not arm B's book
PAIR_TOL_S = 8.0
EVALS = "evaluations.jsonl"
ROUTES = "routes.jsonl"
MAKER_OUT = "maker_outcomes.jsonl"
EVOLUTION = "book_evolution.jsonl"


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


def join_px(place: Optional[dict]) -> Optional[str]:
    """The price a maker plan would rest at.

    THE KEY IS `price`, AND READING IT AS `join_price` MADE THE WHOLE
    PLACEABILITY COMPARISON VACUOUS: every row came back unplaceable, so the
    first live report showed A_placeable_frac 0.0 against 319 quotes that had
    plainly been placed. The failure branches of `maker_plan` do write a
    `reason` and no price, which is why the shape looked plausible. The
    producer's key is read here, and a test pins it against `maker_plan`
    itself rather than against a fixture I wrote.
    """
    if not place:
        return None
    return place.get("price")


def placements(rows: Sequence[dict]) -> List[dict]:
    """The maker-placement rows: one per (intent, venue) with the book we saw."""
    return [r for r in rows
            if str(r.get("schema", "")).endswith("/maker_placement")]


def evo_snapshots(rows: Sequence[dict], ceilings: Dict[str, Any]
                  ) -> List[dict]:
    """Book re-reads, reshaped into snapshots arm B can be priced from.

    The shadow re-reads both venues 30/60/120s after every intent. Those are
    real prospective reads of the rest side, and using them multiplies the
    snapshots available to arm B -- which mattered, because placement-time
    reads alone priced 72 of 640 rows.

    A re-read carries no order-size or fee check, so `submittable` here means
    only "there is a bid at or under the ceiling to join". Arm B's own
    reachability test is applied on top, exactly as for a placement row.
    """
    out = []
    for r in rows:
        if not str(r.get("schema", "")).endswith("/book_evolution"):
            continue
        t = r.get("observed_at")
        if t is None:
            continue
        ceil = r.get("ceiling")
        for venue in ("PMUS", "GEMINI"):
            v = r.get(venue) or {}
            if not v.get("rest_readable") or v.get("rest_best") is None:
                continue
            try:
                ok = D(str(v["rest_best"])) <= D(str(ceil))
            except Exception:                                 # noqa: BLE001
                continue
            out.append({"venue": venue, "market_key": r.get("market_key"),
                        "desired_outcome": r.get("desired_outcome"),
                        "placed_at": float(t),
                        "submittable": bool(ok),
                        "price": (str(v["rest_best"]) if ok else None),
                        "best_bid": str(v["rest_best"]),
                        "queue_ahead": v.get("rest_depth_at_best") or "0",
                        "source": "book_evolution re-read"})
    return out


def snapshots(places: Sequence[dict]) -> Dict[tuple, List[dict]]:
    """Recorded book reads, indexed by (venue, market, outcome), time-sorted.

    These ARE arm B's books: each was read prospectively while the shadow was
    evaluating some earlier anchor, and none of them knows about the anchor it
    is about to be paired with.
    """
    idx: Dict[tuple, List[dict]] = {}
    for p in places:
        if p.get("placed_at") is None:
            continue
        idx.setdefault((p.get("venue"), p.get("market_key"),
                        p.get("desired_outcome")), []).append(p)
    for v in idx.values():
        v.sort(key=lambda r: float(r["placed_at"]))
    return idx


def nearest(snaps: Sequence[dict], t: float) -> Optional[dict]:
    """The recorded read closest in time to `t`. None if there are none."""
    best, gap = None, None
    for s in snaps:
        g = abs(float(s["placed_at"]) - t)
        if gap is None or g < gap:
            best, gap = s, g
    return best


def _valid(age: float, valid_for: float) -> bool:
    return age <= valid_for


def compare_one(route: dict, place_a: dict, snaps: Sequence[dict],
                outcome_a: Optional[dict], *,
                valid_for_s: float = 30.0,
                tol_s: float = PAIR_TOL_S) -> Dict[str, Any]:
    """One intent under both clocks. Every field is prospective in both arms."""
    t_event = float(route.get("l5_event_t") or 0) or None
    t_a = float(place_a["placed_at"])
    if t_event is None:
        # the route row carries the age at decision, which recovers the event
        # time exactly without needing hindsight about it
        age_a = float(route.get("intent_age_at_decision_s") or 0)
        t_event = t_a - age_a
    else:
        age_a = t_a - t_event
    t_b = t_event + CHAIN_READABLE_S
    age_b = CHAIN_READABLE_S

    snap_b = nearest(snaps, t_b)
    pair_err = (abs(float(snap_b["placed_at"]) - t_b)
                if snap_b is not None else None)
    row: Dict[str, Any] = {
        "intent_id": route.get("intent_id"), "venue": place_a.get("venue"),
        "cohort": route.get("cohort"), "market_key": route.get("market_key"),
        "desired_outcome": route.get("desired_outcome"),
        "ceiling": route.get("max_acceptable_price"),
        "t_event": t_event, "t_feed": t_a, "t_chain": t_b,
        "recovery_s": round(t_a - t_b, 3),
        "A_age_s": round(age_a, 3), "B_age_s": round(age_b, 3),
        "A_valid_when_observable": _valid(age_a, valid_for_s),
        "B_valid_when_observable": _valid(age_b, valid_for_s),
        "A_placeable": (place_a.get("submittable") is True
                        and join_px(place_a) is not None),
        "A_join_price": join_px(place_a),
        "A_best_bid": place_a.get("best_bid"),
        "A_queue_ahead": place_a.get("queue_ahead"),
        "pair_error_s": (round(pair_err, 3) if pair_err is not None else None),
    }
    if snap_b is None or pair_err is None or pair_err > tol_s:
        row.update(B_state="UNKNOWN_NO_BOOK_AT_THAT_MOMENT",
                   B_placeable=None, B_join_price=None,
                   B_fill="NOT_DETERMINABLE",
                   why=("no recorded book read within "
                        f"{tol_s}s of the chain-clock moment; arm B is not "
                        "approximated from a later book"))
        return row
    row.update(
        B_state="PRICED_FROM_A_RECORDED_BOOK",
        B_placeable=(snap_b.get("submittable") is True
                     and join_px(snap_b) is not None),
        B_join_price=join_px(snap_b),
        B_best_bid=snap_b.get("best_bid"),
        B_queue_ahead=snap_b.get("queue_ahead"),
        B_book_t=snap_b.get("placed_at"),
    )
    row["B_reachable"] = _reachable(row["B_join_price"], route)
    row["A_reachable"] = _reachable(row["A_join_price"], route)
    row.update(_fill_arms(row, outcome_a))
    return row


def _reachable(join_price: Any, route: dict) -> Optional[bool]:
    """Could we rest at or under L5's own achieved price?"""
    if join_price is None:
        return False
    try:
        return D(str(join_price)) <= D(str(route.get("max_acceptable_price")))
    except Exception:                                         # noqa: BLE001
        return None


def _fill_arms(row: Dict[str, Any], outcome_a: Optional[dict]) -> Dict[str, Any]:
    """Arm A's verdict as recorded. Arm B's only where the stored prints are a
    valid superset for its price -- never modelled."""
    a = None if outcome_a is None else outcome_a.get("evidenced_fill")
    out: Dict[str, Any] = {"A_fill": ("FILLED" if a is True else
                                      "NO_FILL" if a is False else "UNKNOWN")}
    pb, pa = row.get("B_join_price"), row.get("A_join_price")
    if outcome_a is None or pb is None or pa is None:
        out["B_fill"] = "NOT_DETERMINABLE"
        out["B_fill_why"] = "no arm-A print record to test arm B's price against"
        return out
    try:
        if D(str(pb)) < D(str(pa)):
            out["B_fill"] = "NOT_DETERMINABLE"
            out["B_fill_why"] = (
                "arm B would rest BELOW arm A, and the stored prints are only "
                "those that crossed arm A -- a subset, so absence there is not "
                "evidence of no fill")
            return out
    except Exception:                                         # noqa: BLE001
        out["B_fill"] = "NOT_DETERMINABLE"
        return out
    prints = outcome_a.get("evidence") or []
    t_b = row["t_chain"]
    crossed = [p for p in prints
               if float(p.get("t") or 0) > t_b
               and D(str(p["price"])) <= D(str(pb))]
    thru = sum((D(str(p["size"])) for p in crossed), D("0"))
    ahead = D(str(row.get("B_queue_ahead") or "0"))
    qty = D(str(outcome_a.get("quote_qty") or "0"))
    got = min(max(D("0"), thru - ahead), qty)
    if not prints:
        out["B_fill"] = "UNKNOWN"
        out["B_fill_why"] = "arm A saw no prints either; neither arm is decided"
        return out
    out["B_fill"] = "FILLED" if (qty > 0 and got >= qty) else "NO_FILL"
    out["B_traded_through"] = str(thru)
    out["B_prints_after_chain_clock"] = len(crossed)
    out["B_fill_why"] = ("decided from the prints arm A recorded, restricted to "
                         "those after the chain-clock moment; valid because "
                         "arm B rests at or above arm A's price")
    return out


def report(out_dir: str, *, tol_s: float = PAIR_TOL_S) -> Dict[str, Any]:
    ev = _load(os.path.join(out_dir, EVALS))
    routes = {r.get("intent_id"): r for r in _load(os.path.join(out_dir, ROUTES))}
    outs: Dict[tuple, dict] = {}
    for o in _load(os.path.join(out_dir, MAKER_OUT)):
        outs[(o.get("intent_id"), o.get("venue"))] = o
    places = placements(ev)
    evo = evo_snapshots(_load(os.path.join(out_dir, EVOLUTION)), {})
    # arm B may be priced from EITHER a placement-time read or a scheduled
    # re-read; both are prospective, and neither knows about the anchor it is
    # about to be paired with
    snaps = snapshots(list(places) + evo)
    rows = []
    for p in places:
        r = routes.get(p.get("intent_id"))
        if not r or p.get("placed_at") is None:
            continue
        key = (p.get("venue"), p.get("market_key"), p.get("desired_outcome"))
        # arm B may only use reads STRICTLY EARLIER than arm A's own: pairing
        # against arm A itself, or anything after it, would be hindsight
        earlier = [s for s in snaps.get(key, [])
                   if float(s["placed_at"]) < float(p["placed_at"])]
        rows.append(compare_one(
            r, p, earlier, outs.get((p.get("intent_id"), p.get("venue"))),
            tol_s=tol_s))
    return {"computed_at": time.time(), "out_dir": out_dir,
            "placements": len(places), "evolution_snapshots": len(evo),
            "compared": len(rows),
            "chain_readable_s": CHAIN_READABLE_S, "pair_tol_s": tol_s,
            **summarise(rows), "rows": rows[-200:],
            "labels": (
                "the intent is FROZEN across arms: same anchor, same ceiling, "
                "same quantity, no model change. Only the observation moment "
                "differs. Arm B is priced from a book recorded BEFORE arm A's, "
                "so nothing in it is hindsight. The chain observer remains "
                "UNPROMOTED; this measures what promoting it would buy.")}


def summarise(rows: Sequence[dict]) -> Dict[str, Any]:
    n = len(rows)
    known = [r for r in rows if r.get("B_state") == "PRICED_FROM_A_RECORDED_BOOK"]
    rec = sorted(r["recovery_s"] for r in rows if r.get("recovery_s") is not None)

    def frac(pred, pool):
        return (round(sum(1 for r in pool if pred(r)) / len(pool), 4)
                if pool else None)

    gained = [r for r in known
              if r.get("B_placeable") and not r.get("A_placeable")]
    lost = [r for r in known
            if r.get("A_placeable") and not r.get("B_placeable")]
    fill_gain = [r for r in known
                 if r.get("B_fill") == "FILLED" and r.get("A_fill") != "FILLED"]
    fill_lost = [r for r in known
                 if r.get("A_fill") == "FILLED" and r.get("B_fill") == "NO_FILL"]
    return {
        "arm_B_priced": len(known),
        "arm_B_unknown": n - len(known),
        "median_recovery_s": (rec[len(rec) // 2] if rec else None),
        "A_valid_frac": frac(lambda r: r["A_valid_when_observable"], rows),
        "B_valid_frac": frac(lambda r: r["B_valid_when_observable"], rows),
        "A_placeable_frac": frac(lambda r: r["A_placeable"], known),
        "B_placeable_frac": frac(lambda r: r["B_placeable"], known),
        "A_reachable_frac": frac(lambda r: r.get("A_reachable"), known),
        "B_reachable_frac": frac(lambda r: r.get("B_reachable"), known),
        "placeable_only_under_chain": len(gained),
        "placeable_only_under_feed": len(lost),
        "filled_only_under_chain": len(fill_gain),
        "filled_only_under_feed": len(fill_lost),
        "fill_verdicts": dict(Counter(
            f"A={r.get('A_fill')}|B={r.get('B_fill')}" for r in known)),
        "B_unknown_reasons": dict(Counter(
            str(r.get("why") or r.get("B_fill_why"))[:60]
            for r in rows if r.get("B_state") != "PRICED_FROM_A_RECORDED_BOOK"
        ).most_common(3)),
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
