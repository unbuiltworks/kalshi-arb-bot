"""What happens in the 30-60 second interval we never observed.

OWNER GO (#167): "compare <=30s with an L5-like >=60s persistence horizon and
measure what actually appears during the missing 30-60 second interval:
complement prints/opportunities, changing prices, deterioration, and flow.
No assumed fills."

THE GAP THIS EXISTS TO FILL
----------------------------
    L5's median complement gap        ~47 s
    longest WE ever rested an order   32.2 s   (over all 965 campaign orders)
    campaign orders resting >= 60 s   0

Our executor gave up before L5's typical complementary action even occurs.
That is a structural fidelity mismatch, and the campaign cannot speak to it
at all: there is no data past ~32 s, so `pmus_fill_model` correctly refuses
to estimate 60 s or 120 s. The only way to learn what lives in that interval
is to watch it forward, which is what this does.

OPPORTUNITIES, NOT FILLS
-------------------------
Nothing here asserts that an order would have filled. A resting maker order
fills when the book reaches its price AND queue position allows, and queue
position is not observable from a public book. So the recorded quantity is
an OPPORTUNITY: the opposite touch reached the price we would have been
resting at, and a print occurred in the same bucket. Both conditions
together, and the count is an UPPER BOUND on what persistence would have
captured -- the same bound `analysis/l5/COMPLETE_NO_FILL_FORENSICS.md`
already states for the historical replay. Reaching a price is not filling
it, and this module never says otherwise.

WHAT A CLOSED WINDOW REPORTS
-----------------------------
Per bucket (0-30 s, 30-60 s, 60-120 s), all measured, none assumed:

  * opportunities  -- touch reached our price with a print in the bucket
  * prints, shares -- contemporaneous flow, first-differenced
  * best complement price seen, and the deterioration from entry

The comparison the owner asked for falls straight out: bucket A is the
horizon we actually operated at, buckets B and C are the horizon L5 operates
at and we never reached. If B and C carry substantial opportunity, our
30-second deadline -- not our market selection and not the L5 brain -- is a
concrete reason the strategy failed while still looking behaviourally L5.
"""

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

BUCKETS = ((0.0, 30.0, "a_0_30s"),
           (30.0, 60.0, "b_30_60s"),
           (60.0, 120.0, "c_60_120s"))
WINDOW_S = 120.0
L5_MEDIAN_COMPLEMENT_GAP_S = 47.0
OUR_HISTORICAL_MAX_REST_S = 32.2

LABELS = {
    "opportunity": "the opposite touch reached our resting price AND a print "
                   "occurred in the same bucket; an UPPER BOUND on what "
                   "persistence would have captured, never a fill",
    "measured": "book prices and the venue's own cumulative share counter",
    "no_assumed_fills": "nothing here is a fill; queue position is not "
                        "observable from a public book",
}


def bucket_of(elapsed_s: float) -> Optional[str]:
    for lo, hi, name in BUCKETS:
        if lo <= elapsed_s < hi:
            return name
    return None


def open_window(t: float, complement_px: D, complement_side: str,
                slug: str) -> Dict[str, Any]:
    """Start watching from the moment a hypothetical first leg is taken."""
    return {
        "slug": slug, "opened_t": t, "complement_side": complement_side,
        "complement_px_at_entry": complement_px,
        "buckets": {name: {"observations": 0, "opportunities": 0, "prints": 0,
                           "shares": D(0), "best_complement_px": None}
                    for _, _, name in BUCKETS},
        "closed": False,
    }


def touch_reached(book: Mapping[str, Any], complement_side: str,
                  our_px: D) -> bool:
    """Did the opposite side come to our price?

    We would be resting a BUY of the complement at `our_px`. It becomes
    reachable when the complement's own ask falls to or below that price.
    On a YES-quoted book the NO ask is 1 - yes_bid, and the YES ask is quoted
    directly.
    """
    bid, ask = book.get("bid"), book.get("ask")
    if bid is None or ask is None:
        return False
    opp_ask = (D(1) - bid) if complement_side == "NO" else ask
    return opp_ask <= our_px


def complement_price_now(book: Mapping[str, Any],
                         complement_side: str) -> Optional[D]:
    bid, ask = book.get("bid"), book.get("ask")
    if bid is None or ask is None:
        return None
    return (D(1) - ask) if complement_side == "NO" else bid


def observe(win: Mapping[str, Any], book: Mapping[str, Any], t: float,
            printed: bool, shares: D) -> Dict[str, Any]:
    """Fold one poll into the open window. Pure: returns a new window."""
    w = dict(win)
    w["buckets"] = {k: dict(v) for k, v in win["buckets"].items()}
    elapsed = t - float(win["opened_t"])
    if elapsed >= WINDOW_S:
        w["closed"] = True
        return w
    name = bucket_of(elapsed)
    if name is None:
        return w
    b = w["buckets"][name]
    b["observations"] += 1
    if printed:
        b["prints"] += 1
        b["shares"] += shares
    px = complement_price_now(book, str(win["complement_side"]))
    if px is not None:
        cur = b["best_complement_px"]
        b["best_complement_px"] = px if cur is None else min(cur, px)
    # an opportunity needs BOTH: the touch came to us, and something traded
    if printed and touch_reached(book, str(win["complement_side"]),
                                 D(str(win["complement_px_at_entry"]))):
        b["opportunities"] += 1
    return w


def summarise(win: Mapping[str, Any]) -> Dict[str, Any]:
    entry = D(str(win["complement_px_at_entry"]))
    out: Dict[str, Any] = {"slug": win["slug"],
                           "complement_side": win["complement_side"],
                           "complement_px_at_entry": str(entry),
                           "closed": bool(win.get("closed")),
                           "buckets": {}}
    for _, _, name in BUCKETS:
        b = win["buckets"][name]
        best = b["best_complement_px"]
        out["buckets"][name] = {
            "observations": b["observations"],
            "opportunities": b["opportunities"],
            "prints": b["prints"],
            "shares": str(b["shares"]),
            "best_complement_px": str(best) if best is not None else None,
            # negative = the complement got CHEAPER than where we entered
            "deterioration_vs_entry": (str(best - entry)
                                       if best is not None else None),
        }
    a = out["buckets"]["a_0_30s"]["opportunities"]
    beyond = (out["buckets"]["b_30_60s"]["opportunities"]
              + out["buckets"]["c_60_120s"]["opportunities"])
    out["opportunities_inside_our_old_deadline"] = a
    out["opportunities_we_never_waited_for"] = beyond
    out["share_beyond_30s"] = (round(beyond / (a + beyond), 4)
                               if (a + beyond) else None)
    out["labels"] = LABELS
    return out


def cohort(summaries: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Aggregate closed windows into the <=30s vs >=30s comparison."""
    tot = {name: {"observations": 0, "opportunities": 0, "prints": 0}
           for _, _, name in BUCKETS}
    for s in summaries:
        for _, _, name in BUCKETS:
            b = s["buckets"][name]
            for k in ("observations", "opportunities", "prints"):
                tot[name][k] += b[k]
    a = tot["a_0_30s"]["opportunities"]
    beyond = tot["b_30_60s"]["opportunities"] + tot["c_60_120s"]["opportunities"]
    return {
        "windows": len(summaries),
        "by_bucket": tot,
        "opportunities_inside_our_old_deadline": a,
        "opportunities_we_never_waited_for": beyond,
        "share_beyond_30s": round(beyond / (a + beyond), 4) if (a + beyond) else None,
        "l5_median_complement_gap_s": L5_MEDIAN_COMPLEMENT_GAP_S,
        "our_historical_max_rest_s": OUR_HISTORICAL_MAX_REST_S,
        "labels": LABELS,
    }
