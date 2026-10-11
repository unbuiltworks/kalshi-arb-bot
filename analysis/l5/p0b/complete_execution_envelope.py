"""P0-B: is the frozen L5 target price being used as the literal EXECUTION
price, and is that costing us fills?

THE QUESTION, stated so it cannot be fudged. Two different things are being
conflated today:

    MODEL DECISION      -- "COMPLETE the YES side"     (L5 behaviour; frozen)
    EXECUTION POLICY    -- "at exactly L5's touch"     (an implementation choice)

Nothing here proposes changing L5's action probabilities. It asks only whether
the SECOND one is leaving economically-legal fills on the table, and it answers
with the live tape rather than an argument.

WHAT THE TAPE CARRIES. Every resting order records `rest_book`, aggregated over
its whole resting window:

    min_opp_ask   the best the opposite ask ever reached while we rested
    max_touch     the highest L5's own touch got while we rested
    polls         how many observations that aggregate is built from

`min_opp_ask` is the decisive field: it is the closest the market ever came to
us. If min_opp_ask <= our price, a fill was available and we did not take it
(queue/latency). If min_opp_ask == our price + 1 tick, ONE TICK separated us
from a fill, and the only question left is whether that tick was economically
affordable.

CENSORING IS REPORTED, NOT HIDDEN. Unfilled orders are cancelled at their
deadline (~31 s), so P(fill within 60 s) cannot be observed for them: they are
CENSORED, not failures, beyond their own rest_s. Every probability below names
its denominator and the censoring that applies.

Read-only. Parses campaign logs; touches no order path and no venue.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Iterable, Optional

# THE VENUE TICK IS 0.005, NOT 0.01, AND GETTING THIS WRONG INVERTS THE RESULT.
# `orderPriceMinTickSize` reports 0.01, but 98 of 137 live COMPLETE prices are
# half-cent (0.555, 0.365, 0.445) and the venue advertises a 0.005..0.995 price
# range. With TICK=0.01 a genuine one-tick gap of $0.005 divides to 0.5 and
# int()-truncates to ZERO ticks -- which reported seven orders as "the book
# reached us and we still did not fill" (a queue/latency finding) when in fact
# the book stopped exactly one tick short of us every time. Measured on the
# live tape: gap <= 0 occurs ZERO times in 137 attempts.
TICK = D("0.005")
PAIR_CEILING = D("1.00")        # owner's hard pair ceiling
FEE_SAFE = D("0.98")            # observation / fee-safe bound


def _d(v: Any) -> Optional[D]:
    try:
        return None if v in (None, "") else D(str(v))
    except Exception:                                      # noqa: BLE001
        return None


def _ticks(a: Optional[D], b: Optional[D]) -> Optional[int]:
    """Signed tick distance, rounded AWAY FROM ZERO.

    Any non-zero gap must report as at least one tick in its own direction.
    Truncating toward zero is what turned a real $0.005 gap into "no gap" and
    misclassified seven near-misses as the book having reached us."""
    if a is None or b is None:
        return None
    q = (a - b) / TICK
    if q == int(q):
        return int(q)
    return int(q) + 1 if q > 0 else int(q) - 1


def load_sessions(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            j = json.loads(line)
        except Exception:                                  # noqa: BLE001
            continue
        if j.get("kind") == "session_end":
            out.append(j)
    return out


def held_avg_cost(sess: dict) -> tuple[Optional[D], Optional[str]]:
    """Average cost of the side we are long, and which side that is. This is
    what the complement price has to pair against."""
    inv = sess.get("resumed_inventory") or sess.get("inventory") or {}
    up, dn = _d(inv.get("up")), _d(inv.get("dn"))
    if up and up > 0 and (not dn or up > dn):
        return _d(inv.get("avg_up")), "up"
    if dn and dn > 0:
        return _d(inv.get("avg_dn")), "dn"
    return None, None


def attempts(sessions: Iterable[dict]) -> list[dict]:
    """Every real COMPLETE order, with the forensic fields joined."""
    rows: list[dict] = []
    for s in sessions:
        avg, side = held_avg_cost(s)
        for o in (s.get("orders") or []):
            if o.get("relation") != "complete":
                continue
            px = _d(o.get("px"))
            rb = o.get("rest_book") or {}
            mo = _d(rb.get("min_opp_ask"))
            l5 = _d(o.get("l5_touch_px"))
            book = o.get("recorder_book") or {}
            filled = _d(o.get("filled_qty")) or D("0")
            rows.append({
                "slug": s.get("slug"),
                "outcome": o.get("outcome"),
                "filled": filled > 0,
                "rest_s": float(o.get("rest_s") or 0.0),
                "end_reason": o.get("end_reason"),
                "px": px,
                "l5_touch_px": l5,
                "min_opp_ask": mo,
                "max_touch": _d(rb.get("max_touch")),
                "polls": rb.get("polls"),
                "yes_bid": _d(book.get("yes_bid")),
                "yes_ask": _d(book.get("yes_ask")),
                "fresh_opp_ask": _d(o.get("fresh_opp_ask")),
                "touch_join": o.get("touch_join"),
                "held_avg_cost": avg,
                "held_side": side,
                # DISTANCE: how far the book's best ever got from our price.
                # 0 means the book reached us; +1 means one tick short.
                "book_gap_ticks": _ticks(mo, px),
                # where we placed relative to L5's own touch
                "placement_vs_l5_ticks": _ticks(px, l5),
                "l5_touch_reached": o.get("l5_touch_reached_by_opposite_ask"),
            })
    return rows


def bucket(row: dict) -> str:
    g = row["book_gap_ticks"]
    if g is None:
        return "unknown"
    if g <= 0:
        return "BOOK_REACHED_US"
    if g == 1:
        return "1_TICK_SHORT"
    if g == 2:
        return "2_TICKS_SHORT"
    return "3+_TICKS_SHORT"


def fill_probabilities(rows: list[dict], horizons=(10.0, 30.0, 60.0)) -> dict:
    """P(fill within T), with the censoring stated.

    An unfilled order cancelled at its deadline tells us nothing about what
    would have happened after that deadline, so it is EXCLUDED from the
    denominator for horizons beyond its own rest_s rather than counted as a
    failure. Counting it as a failure would understate fill probability and
    make the execution policy look worse than the evidence supports."""
    out: dict[str, Any] = {}
    for h in horizons:
        filled_by = sum(1 for r in rows if r["filled"] and r["rest_s"] <= h)
        # at risk = filled before h, or rested at least h without filling
        at_risk = sum(1 for r in rows
                      if (r["filled"] and r["rest_s"] <= h)
                      or r["rest_s"] >= h)
        censored = len(rows) - at_risk
        out[f"p_fill_within_{int(h)}s"] = {
            "filled": filled_by, "at_risk": at_risk,
            "censored_before_horizon": censored,
            "p": round(filled_by / at_risk, 4) if at_risk else None,
        }
    out["p_fill_before_cancel"] = {
        "filled": sum(1 for r in rows if r["filled"]), "attempts": len(rows),
        "p": round(sum(1 for r in rows if r["filled"]) / len(rows), 4)
        if rows else None,
        "note": "every attempt, whatever its rest time; no censoring applied",
    }
    return out


def one_tick_counterfactual(row: dict) -> dict:
    """Would ONE more tick of aggression have been legal AND sufficient?

    Four independent tests, all of which must pass. `sufficient` is kept
    separate from `legal`: a tick that would have been affordable but still
    would not have been hit is not a missed fill, and conflating the two would
    manufacture an opportunity that never existed."""
    px, mo, avg = row["px"], row["min_opp_ask"], row["held_avg_cost"]
    ask = row["fresh_opp_ask"] or row["yes_ask"]
    if px is None:
        return {"evaluable": False, "why": "no price"}
    cf = px + TICK
    pair = (avg + cf) if avg is not None else None
    return {
        "evaluable": True,
        "counterfactual_px": str(cf),
        # 1. pair cost still within the hard ceiling
        "pair_at_cf": str(pair) if pair is not None else None,
        "pair_within_ceiling": (pair is not None and pair <= PAIR_CEILING),
        # 2. still fee-safe on the stated bound
        "fee_safe": (pair is not None and pair <= FEE_SAFE),
        # 3. still a resting maker order (strictly inside the opposite ask)
        "still_post_only": (ask is not None and cf < ask),
        # 4. book evidence that it would actually have been hit
        "book_would_have_reached": (mo is not None and mo <= cf),
        "book_gap_ticks": row["book_gap_ticks"],
    }


def summarise(rows: list[dict]) -> dict:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[bucket(r)].append(r)
    buckets = {}
    for name, rs in by.items():
        buckets[name] = {"attempts": len(rs), "filled": sum(1 for r in rs if r["filled"]),
                         **fill_probabilities(rs)}
    cf = [one_tick_counterfactual(r) for r in rows]
    ok = [c for c in cf if c.get("evaluable")]
    legal_and_sufficient = [
        c for c in ok
        if c["pair_within_ceiling"] and c["still_post_only"]
        and c["book_would_have_reached"]]
    return {
        "attempts": len(rows),
        "filled": sum(1 for r in rows if r["filled"]),
        "overall": fill_probabilities(rows),
        "by_distance": buckets,
        "placement_vs_l5_touch_ticks": dict(
            Counter(r["placement_vs_l5_ticks"] for r in rows).most_common()),
        "book_gap_ticks": dict(
            Counter(r["book_gap_ticks"] for r in rows).most_common(12)),
        "rest_s": {
            "median": round(statistics.median([r["rest_s"] for r in rows]), 2),
            "max": round(max(r["rest_s"] for r in rows), 2)} if rows else {},
        "one_tick_counterfactual": {
            "evaluable": len(ok),
            "pair_within_ceiling": sum(1 for c in ok if c["pair_within_ceiling"]),
            "fee_safe": sum(1 for c in ok if c["fee_safe"]),
            "still_post_only": sum(1 for c in ok if c["still_post_only"]),
            "book_would_have_reached": sum(
                1 for c in ok if c["book_would_have_reached"]),
            "ALL_FOUR_legal_and_sufficient": len(legal_and_sufficient),
            "share_of_attempts": round(len(legal_and_sufficient) / len(rows), 4)
            if rows else None,
        },
    }


def main(argv: list[str]) -> int:
    path = Path(argv[1])
    sessions = load_sessions(path)
    rows = attempts(sessions)
    rep = {"sessions": len(sessions), **summarise(rows)}
    print(json.dumps(rep, indent=1, default=str))
    out = Path(argv[2]) if len(argv) > 2 else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"report": rep,
             "attempts": [{k: str(v) if isinstance(v, D) else v
                           for k, v in r.items()} for r in rows]},
            indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
