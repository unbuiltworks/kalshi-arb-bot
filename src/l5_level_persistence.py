"""Level persistence and touch oscillation, measured WITHOUT any fill model.

This exists because the public gateway carries no individual prints, so no
causal fill can be earned from it (see `scripts/l5_public_l2_recorder.py`).
What it DOES carry is the full ladder, several times a second, and that is
enough to test the mechanism the frozen arms disagree about -- without
inferring a single fill:

* **CONTROL requotes**: the control quotes the touch, so every change in the
  own-terms touch price is one requote it would make, and one queue position
  it would forfeit.
* **Level survival**: how long a price, once quoted, remains present in the
  ladder. A held order can only keep earning position while its level exists.
* **Touch return (oscillation)**: how often the touch LEAVES a price and
  later COMES BACK to it. This is the crux. Every return is a level the
  control abandoned and then had to rejoin at the back of the queue, while a
  holding order would have kept its place the whole time.

No fills, no volume attribution, no P&L. Nothing here may be converted into
$/window; the venue's cumulative volume counter is far too coarse for that.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

#: a return that happens after this long is a new decision, not an oscillation
RETURN_HORIZON_S = 120.0

#: A feed that serves a CACHED book cannot measure level persistence at all:
#: the dwell times it yields are the cache TTL, not market behaviour. The
#: authenticated WS delivered 2,955 book messages in 5.01 min (~590/min); the
#: public gateway delivered 31 distinct book states in 15.0 min (~2/min). We
#: require at least this many genuinely DISTINCT book states per minute before
#: any persistence statistic may be believed.
MIN_DISTINCT_STATES_PER_MIN = 30.0


class FeedTooCoarse(Exception):
    """Raised instead of returning persistence stats computed off a cache."""


def _d(v) -> Optional[D]:
    if isinstance(v, dict):
        v = v.get("value")
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                        # noqa: BLE001
        return None


def own_touch(row: dict, side: str) -> Optional[D]:
    """The best price WE could join on `side`, in our own terms."""
    if side == "up":
        bids = row.get("bids") or []
        pxs = [p for p in (_d(b.get("px")) for b in bids) if p is not None]
        return max(pxs) if pxs else None
    offers = row.get("offers") or []
    pxs = [p for p in (_d(o.get("px")) for o in offers) if p is not None]
    return (D(1) - min(pxs)) if pxs else None


def level_prices(row: dict, side: str) -> set:
    """Every own-terms price currently DISPLAYED on our side of the ladder."""
    if side == "up":
        return {p for p in (_d(b.get("px")) for b in (row.get("bids") or []))
                if p is not None}
    return {D(1) - p
            for p in (_d(o.get("px")) for o in (row.get("offers") or []))
            if p is not None}


def feed_resolution(rows: Sequence[dict]) -> Dict[str, Any]:
    """How many genuinely distinct book states per minute this feed carried.

    Polling faster than the upstream cache inflates the row count without
    adding information, so we count DISTINCT ladder contents, not rows.
    """
    import json as _json
    seen, order = set(), 0
    ts = [r.get("recv_t") for r in rows if r.get("recv_t") is not None]
    for r in rows:
        if r.get("kind") == "settlement_poll":
            continue
        key = _json.dumps([r.get("bids"), r.get("offers")],
                          separators=(",", ":"), sort_keys=True)
        if key not in seen:
            seen.add(key)
            order += 1
    span = (max(ts) - min(ts)) if len(ts) > 1 else 0.0
    per_min = (order / (span / 60.0)) if span > 0 else 0.0
    return {"rows": len(rows), "distinct_states": order, "span_s": span,
            "distinct_states_per_min": per_min,
            "sufficient": per_min >= MIN_DISTINCT_STATES_PER_MIN}


def analyse(rows: Sequence[dict], *, side: str = "up",
            require_resolution: bool = True) -> Dict[str, Any]:
    """Mechanism statistics for one side of one window. No fills involved.

    Fails closed on a cached feed unless the caller explicitly opts out, in
    which case every statistic carries a CACHE_LIMITED label.
    """
    res = feed_resolution(rows)
    if require_resolution and not res["sufficient"]:
        raise FeedTooCoarse(
            "feed carried %.1f distinct book states/min (need >= %.0f); "
            "dwell times from it would be the cache TTL, not market behaviour"
            % (res["distinct_states_per_min"], MIN_DISTINCT_STATES_PER_MIN))
    touch_changes = 0
    cur: Optional[D] = None
    since: Optional[float] = None
    dwell: List[float] = []
    left_at: Dict[D, float] = {}
    returns = 0
    return_gaps: List[float] = []
    survived: Dict[D, float] = {}
    survival: List[float] = []
    n = 0
    for r in rows:
        if r.get("kind") == "settlement_poll":
            continue
        t = r.get("recv_t")
        if t is None:
            continue
        n += 1
        px = own_touch(r, side)
        if px is None:
            continue
        if cur is None:
            cur, since = px, t
        elif px != cur:
            touch_changes += 1
            if since is not None:
                dwell.append(t - since)
            left_at[cur] = t
            prev = left_at.get(px)
            if prev is not None and (t - prev) <= RETURN_HORIZON_S:
                returns += 1
                return_gaps.append(t - prev)
            cur, since = px, t
        # level survival: track how long each displayed own-terms price lasts
        present = level_prices(r, side)
        for p in list(survived):
            if p not in present:
                survival.append(t - survived.pop(p))
        for p in present:
            survived.setdefault(p, t)
    if since is not None and rows:
        last_t = max((r.get("recv_t") or 0) for r in rows)
        dwell.append(last_t - since)

    def _med(xs):
        return sorted(xs)[len(xs) // 2] if xs else None

    span = ((max((r.get("recv_t") or 0) for r in rows)
             - min((r.get("recv_t") or 0) for r in rows)) if rows else 0.0)
    return {
        "side": side,
        "snapshots": n,
        "span_s": span,
        #: one control requote per touch change, by construction
        "control_requotes": touch_changes,
        "control_requotes_per_min": (touch_changes / (span / 60.0)
                                     if span > 0 else None),
        "median_touch_dwell_s": _med(dwell),
        "mean_touch_dwell_s": (sum(dwell) / len(dwell)) if dwell else None,
        #: the crux: levels the control abandoned and then had to rejoin
        "touch_returns": returns,
        "touch_return_share": (returns / touch_changes) if touch_changes else None,
        "median_return_gap_s": _med(return_gaps),
        "median_level_survival_s": _med(survival),
        "levels_observed": len(survival),
        "distinct_states_per_min": res["distinct_states_per_min"],
        "fidelity": ("L2_OBSERVED__NO_FILLS_INFERRED" if res["sufficient"]
                     else "CACHE_LIMITED__NOT_MARKET_BEHAVIOUR"),
    }
