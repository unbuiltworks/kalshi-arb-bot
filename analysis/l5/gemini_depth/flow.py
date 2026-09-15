"""Flow, not snapshots: how much eligible liquidity ARRIVES over a window.

WHY A SNAPSHOT IS THE WRONG UNIT FOR L5. L5's BTC15m behaviour is a sequential
inventory process, so the question is not only "is q resting right now" but
"how much eligible size passes through this book, how fast, and at what price".
A venue can be 1 deep at every instant and still offer hundreds of contracts
over a window, or look 500 deep and be the same 500 re-quoted all window.

SNAPSHOTS CANNOT TELL THOSE APART. An order resting untouched and an order
cancelled and re-placed at the same price and size produce identical
snapshots. Only the event stream distinguishes them, which is why this reads
`place` / `cancel` / `trade` events rather than sampled books:

    offered_unique   size that ARRIVED at an eligible price (initial + place)
    resampled_naive  what you get by summing sampled resting size, shown only
                     to make the double-counting visible -- it is NOT a
                     capacity measure
    traded           size that actually changed hands at an eligible price

ATOMIC VERSUS INCREMENTAL IS AN INTENT DECISION, NOT A MEASUREMENT ONE. A
frozen intent's `incremental_qty` is the amount to acquire NOW; whether that
may be spread across several smaller fills over time is a policy question the
intent has to answer. This module reports the incremental result as a separate,
explicitly labelled number and never lets it stand in for atomic capacity. The
router is untouched and still requires atomic availability.

THE ACCUMULATION SIMULATION IS AN UPPER BOUND ON SPEED. It assumes we take
every eligible order the instant it appears, with no queue and no competition.
Observed `trade` volume is reported beside it precisely because someone else
was taking some of that liquidity.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

QTY = (D("1"), D("2"), D("5"), D("10"), D("20"))


def load(path: str) -> List[dict]:
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


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                         # noqa: BLE001
        return None


def window_flow(events: Sequence[dict], *, ceiling: D, side: str = "ask"
                ) -> Dict[str, Any]:
    """Eligible-liquidity flow on one side of one window, at one ceiling.

    `side="ask"` is the BUY case: we lift asks priced at or under the ceiling.

    THE LIMITATION THAT SHAPES EVERY NUMBER HERE. Gemini's v1 marketdata feed
    carries no order id, so a cancel followed by a place at the same price and
    size is indistinguishable from an order that never moved. Summing `place`
    deltas therefore does NOT measure unique liquidity: measured on a live
    BTC5m window it gave 37,805 contracts "offered" at <= 0.50 in 70 seconds
    against a cancel/place ratio of 0.994 and 50 contracts actually traded.
    That is one quote refreshed 159 times, not 37,805 contracts of supply.

    So the honest decomposition is reported instead, and `placed_gross` is kept
    only to make the churn visible:

        standing_*      eligible size actually RESTING, replayed from events
        net_new         placed minus cancelled -- what accumulated on net
        traded          size that actually changed hands at an eligible price
        churn_ratio     cancelled / placed; near 1.0 means refresh, not supply
    """
    resting: Dict[D, D] = {}
    placed_gross = D("0")
    cancelled = D("0")
    traded = D("0")
    n_place = n_cancel = n_trade = 0
    standing_series: List[tuple] = []
    arrivals: List[tuple] = []
    first_t = last_t = None

    for e in events:
        t = float(e.get("t") or 0)
        first_t = t if first_t is None else min(first_t, t)
        last_t = t if last_t is None else max(last_t, t)
        px = _d(e.get("price"))
        if px is None:
            continue
        if e.get("type") == "trade":
            n_trade += 1
            if px <= ceiling:
                traded += _d(e.get("amount")) or D("0")
            continue
        if e.get("side") != side:
            continue
        reason = e.get("reason")
        rem = _d(e.get("remaining"))
        delta = _d(e.get("delta")) or D("0")
        if reason in ("initial", "place"):
            if reason == "place":
                n_place += 1
            if px <= ceiling and delta > 0:
                placed_gross += delta
                arrivals.append((t, px, delta))
        elif reason == "cancel":
            n_cancel += 1
            if px <= ceiling:
                cancelled += abs(delta)
        if px <= ceiling:
            if rem is not None and rem > 0:
                resting[px] = rem
            else:
                resting.pop(px, None)
            standing_series.append(
                (t, sum(resting.values(), D("0"))))

    standing = [v for _, v in standing_series]
    net_new = placed_gross - cancelled
    return {
        "ceiling": str(ceiling), "side": side,
        "span_s": (round(last_t - first_t, 1)
                   if (first_t is not None and last_t is not None) else None),
        "standing_eligible_size": _quant(standing),
        "net_new_eligible": str(net_new),
        "traded_at_or_under_ceiling": str(traded),
        "placed_gross_NOT_A_CAPACITY_MEASURE": str(placed_gross),
        "cancelled": str(cancelled),
        "churn_ratio": (round(float(cancelled / placed_gross), 4)
                        if placed_gross > 0 else None),
        "events": {"place": n_place, "cancel": n_cancel, "trade": n_trade},
        "cancel_to_place_ratio": (round(n_cancel / n_place, 3)
                                  if n_place else None),
        "accumulation_upper_bound": _accumulate(arrivals),
        "accumulation_trade_limited": _trade_limited(traded, standing),
        "labels": ("churn_ratio near 1.0 means placements are REFRESHES of a "
                   "standing quote, not new supply -- the feed carries no "
                   "order id, so the two cannot be told apart directly. "
                   "accumulation_upper_bound assumes we take every placement "
                   "the instant it appears, with no queue and no competition, "
                   "and is therefore an upper bound on SPEED, not a forecast."),
    }


def _quant(vals: Sequence[D]) -> Optional[Dict[str, Any]]:
    v = sorted(float(x) for x in vals)
    if not v:
        return None

    def pick(p):
        return round(v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))], 3)
    return {"n": len(v), "p05": pick(0.05), "p50": pick(0.50),
            "p95": pick(0.95), "max": round(v[-1], 3), "min": round(v[0], 3)}


def _trade_limited(traded: D, standing: Sequence[D]) -> Dict[str, Any]:
    """The defensible bound: you cannot take more than rests plus what trades.

    A taker's realistic capture over a window is bounded by the size resting
    when they arrive plus the flow that actually changed hands while they
    waited. Everything between that and the raw placement flow is quote churn
    someone cancelled before anyone could hit it.
    """
    top = max(standing) if standing else D("0")
    cap = D(str(top)) + traded
    return {
        "resting_max": str(top),
        "traded": str(traded),
        "realistic_capture_ceiling": str(cap),
        "reachable": {str(q): bool(cap >= q) for q in QTY},
        "basis": ("size resting at its observed maximum, plus volume that "
                  "actually traded at an eligible price, over this span"),
    }


def _accumulate(arrivals: Sequence[tuple]) -> Dict[str, Any]:
    """Time and VWAP to reach each candidate size by INCREMENTAL acquisition.

    UPPER BOUND ON SPEED, and it must be read as one: it assumes we take every
    placement the instant it appears, with no queue position and no competing
    taker. Where churn_ratio is near 1.0 most of that "supply" was cancelled
    moments later, so this number says how fast the size FLASHED past, not how
    much we could have had -- `accumulation_trade_limited` is the defensible
    companion.

    Reported separately from atomic capacity and never substituted for it: a
    quantity assembled from several fills over time is a different economic
    object from one taken at a single instant, and only an intent whose
    semantics permit incremental acquisition may use it.
    """
    if not arrivals:
        return {q_str(q): None for q in QTY}
    t0 = arrivals[0][0]
    got, cost = D("0"), D("0")
    out: Dict[str, Any] = {}
    targets = list(QTY)
    for t, px, sz in arrivals:
        while targets and got + sz >= targets[0]:
            need = targets[0] - got
            out[q_str(targets[0])] = {
                "seconds_to_accumulate": round(t - t0, 2),
                "vwap": str(((cost + need * px) / targets[0]
                             ).quantize(D("0.000001"))),
                "mode": "INCREMENTAL -- several fills over time, UPPER BOUND",
            }
            targets.pop(0)
        got += sz
        cost += sz * px
    for q in targets:
        out[q_str(q)] = {"seconds_to_accumulate": None, "reached": str(got),
                         "vwap": None,
                         "mode": "not reached in the observed span"}
    return out


def q_str(q: D) -> str:
    return str(q)


def authorization_persistence(intents: Sequence[dict], *,
                              horizons_s: Sequence[float] = (30, 60, 120, 300)
                              ) -> Dict[str, Any]:
    """Would a LATER L5 intent still have authorized a slow accumulation?

    Incremental acquisition takes time, and an intent is a statement about one
    moment. If L5 wanted Up at <= 0.62 and thirty seconds later wanted Down, a
    purchase still being assembled was no longer authorized -- the fill would
    be ours, not a replication of L5.

    For each intent, the next intent in the SAME market at least `h` seconds
    later is checked for two things: same desired side, and a ceiling no lower
    than the original. Both must hold for the accumulation to have remained
    authorized, and a market with no later intent is reported as UNKNOWN rather
    than counted either way.
    """
    by_market: Dict[str, List[dict]] = defaultdict(list)
    for i in intents:
        by_market[str(i.get("market_key"))].append(i)
    for v in by_market.values():
        v.sort(key=lambda r: float(r.get("t_utc") or 0))

    out: Dict[str, Any] = {}
    for h in horizons_s:
        same_side = opposite = cheaper_ceiling = no_later = 0
        for rows in by_market.values():
            for k, cur in enumerate(rows):
                t0 = float(cur.get("t_utc") or 0)
                later = next((r for r in rows[k + 1:]
                              if float(r.get("t_utc") or 0) >= t0 + h), None)
                if later is None:
                    no_later += 1
                    continue
                if later.get("desired_outcome") != cur.get("desired_outcome"):
                    opposite += 1
                elif (_d(later.get("max_acceptable_price"))
                      or D("0")) < (_d(cur.get("max_acceptable_price"))
                                    or D("0")):
                    cheaper_ceiling += 1
                else:
                    same_side += 1
        decided = same_side + opposite + cheaper_ceiling
        out[str(h)] = {
            "still_authorized": same_side,
            "side_flipped": opposite,
            "ceiling_fell_below": cheaper_ceiling,
            "no_later_intent_UNKNOWN": no_later,
            "still_authorized_rate": (round(same_side / decided, 4)
                                      if decided else None),
            "basis": ("the next intent in the same market at least this many "
                      "seconds later; markets with no later intent are UNKNOWN "
                      "and excluded from the rate"),
        }
    return out


def report(out_dir: str, *, ceilings: Sequence[D],
           shadow_dir: Optional[str] = None) -> Dict[str, Any]:
    ev = load(os.path.join(out_dir, "events.jsonl"))
    if not ev:
        return {"events": 0, "verdict": "no event stream recorded yet"}
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for e in ev:
        by_sym[e.get("symbol")].append(e)

    # the ACTUAL L5 ceilings seen in the same cohorts, so "capacity through the
    # intent's maximum acceptable price" is answered against real intents
    # rather than an invented grid
    live_ceilings: Dict[str, List[str]] = defaultdict(list)
    if shadow_dir:
        for i in load(os.path.join(shadow_dir, "intents.jsonl")):
            if i.get("desired_outcome") == "Up":
                live_ceilings[str(i.get("cohort"))].append(
                    str(i.get("max_acceptable_price")))

    out: Dict[str, Any] = {"events": len(ev), "symbols": len(by_sym),
                           "l5_ceilings_seen": {k: sorted(set(v))[:12]
                                                for k, v in live_ceilings.items()},
                           "windows": {}}
    if shadow_dir:
        ints = load(os.path.join(shadow_dir, "intents.jsonl"))
        # SPLIT BY SOURCE, because they answer different questions. REPLAY
        # intents restate L5's OWN fills, so a side flip there is L5 changing
        # his mind. MODEL intents are our prediction, so a flip there is the
        # model alternating and says nothing about L5. Pooling them would let
        # model churn masquerade as trader behaviour.
        out["authorization_persistence"] = {
            src: authorization_persistence(
                [i for i in ints
                 if (i.get("extra") or {}).get("intent_source") == src])
            for src in ("REPLAY", "MODEL")}
        out["authorization_persistence"]["ALL"] = authorization_persistence(ints)
    for sym, rows in sorted(by_sym.items()):
        cohort = rows[0].get("cohort")
        cs = list(ceilings)
        for c in live_ceilings.get(str(cohort), []):
            d = _d(c)
            if d is not None and d not in cs:
                cs.append(d)
        out["windows"][sym] = {
            "cohort": cohort, "n_events": len(rows),
            "reasons": dict(Counter(f"{r.get('type')}/{r.get('reason')}"
                                    for r in rows)),
            "by_ceiling": {str(c): window_flow(rows, ceiling=c) for c in
                           sorted(cs)},
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-gemini-depth")
    ap.add_argument("--shadow-dir", default=None,
                    help="executable-shadow state dir, to read real L5 ceilings")
    ap.add_argument("--ceilings", default="0.30,0.50,0.70,0.90")
    a = ap.parse_args(argv)
    cs = [D(x) for x in a.ceilings.split(",") if x.strip()]
    print(json.dumps(report(a.out_dir, ceilings=cs, shadow_dir=a.shadow_dir),
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
